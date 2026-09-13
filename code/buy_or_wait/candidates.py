"""Candidate plan generation, simulator validation, and deterministic ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Iterable, Sequence

from buy_or_wait.capacity import CapacityResult
from buy_or_wait.forecast import (
    HypotheticalPayment, MovementKind, ReconstructedState, SimulationResult,
    SpendingAction, SpendingModification, simulate,
)
from buy_or_wait.models import Category, Direction, Flexibility, PaymentOptionMethod, PaymentPreference, RequestBundle


ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Candidate:
    method: str
    eligible: bool
    rejection_reasons: tuple[str, ...]
    payments: tuple[HypotheticalPayment, ...]
    option_id: str | None
    total_payable: Decimal
    spending_changes: tuple[SpendingModification, ...]
    simulation: SimulationResult | None
    trace_references: tuple[str, ...]

    @property
    def first_payment_date(self) -> date | None:
        return self.payments[0].payment_date if self.payments else None

    @property
    def last_payment_date(self) -> date | None:
        return self.payments[-1].payment_date if self.payments else None

    @property
    def payment_count(self) -> int:
        return len(self.payments)

    @property
    def safe(self) -> bool:
        return bool(self.simulation and self.simulation.safe)

    @property
    def completes_by_deadline(self) -> bool:
        return bool(self.payments and self.last_payment_date is not None)


def validate_candidate(candidate: Candidate, state: ReconstructedState, bundle: RequestBundle) -> tuple[str, ...]:
    """Pure structural checks shared by generation, ranking, and later output validation."""
    reasons: list[str] = []
    payments = candidate.payments
    if any(payment.amount <= ZERO for payment in payments):
        reasons.append("payment amounts must be positive")
    if any(payment.payment_date < state.request_date for payment in payments):
        reasons.append("payment schedule starts before request date")
    if any(payment.payment_date > state.horizon_end for payment in payments):
        reasons.append("payment schedule exceeds forecast horizon")
    if any(left.payment_date > right.payment_date for left, right in zip(payments, payments[1:])):
        reasons.append("payment schedule is not chronological")
    if payments and payments[-1].payment_date > bundle.request.desired_completion_date:
        reasons.append("payment schedule misses desired completion date")
    if candidate.method == "full_payment":
        if len(payments) != 1 or payments[0].amount != bundle.request.requested_amount:
            reasons.append("full payment must contain one exact requested-amount payment")
    elif candidate.method == "partial_payment":
        if len(payments) != 2 or sum((payment.amount for payment in payments), ZERO) != bundle.request.requested_amount:
            reasons.append("partial payment must contain two payments summing to requested amount")
    elif candidate.method == "installments":
        option = next((item for item in bundle.payment_options if item.payment_option_id == candidate.option_id), None)
        if option is None:
            reasons.append("installment option is missing")
        else:
            expected = _payment_schedule(option, state)
            if payments != expected:
                reasons.append("installment schedule does not match supplied option")
    elif candidate.method == "wait" and len(payments) != 1:
        reasons.append("wait must contain one full-payment movement")
    if len({change.event_id for change in candidate.spending_changes}) != len(candidate.spending_changes):
        reasons.append("an event may be changed only once")
    if len(candidate.spending_changes) > 3:
        reasons.append("spending changes exceed maximum of three")
    return tuple(reasons)


def _payment_schedule(option, state: ReconstructedState) -> tuple[HypotheticalPayment, ...]:
    frequency = option.payment_frequency_days
    if frequency is None:
        return ()
    return tuple(
        HypotheticalPayment(
            option.first_payment_date + timedelta(days=frequency * index),
            option.payment_amount,
            f"option:{option.payment_option_id}:{index + 1}",
        )
        for index in range(option.number_of_payments)
    )


def _legal_modifications(state: ReconstructedState, bundle: RequestBundle) -> tuple[SpendingModification, ...]:
    """Return one permitted, discrete action per projected recurring debit."""

    events = bundle.events
    by_id = {event.event_id: event for event in events}
    actions: list[SpendingModification] = []
    seen: set[tuple[str, SpendingAction]] = set()
    for movement in state.baseline_movements:
        if movement.kind not in {MovementKind.RECURRENCE, MovementKind.VARIABLE_ESSENTIAL} or movement.source_event_id is None:
            continue
        event = by_id.get(movement.source_event_id)
        if event is None or event.direction is not Direction.DEBIT:
            continue
        if event.category in bundle.profile.protected_categories:
            continue
        if event.flexibility in {Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE} and event.category in bundle.profile.stoppable_categories:
            key = (event.event_id, SpendingAction.STOP)
            if key not in seen:
                actions.append(SpendingModification(event.event_id, SpendingAction.STOP))
                seen.add(key)
        if event.flexibility in {Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE} and event.category in bundle.profile.reducible_categories:
            target = event.minimum_allowed_amount if event.minimum_allowed_amount is not None else Decimal("0")
            if target < (event.amount or Decimal("0")):
                key = (event.event_id, SpendingAction.REDUCE_TO)
                if key not in seen:
                    actions.append(SpendingModification(event.event_id, SpendingAction.REDUCE_TO, target))
                    seen.add(key)
    return tuple(actions)


def _modification_sets(state: ReconstructedState, bundle: RequestBundle) -> tuple[tuple[SpendingModification, ...], ...]:
    actions = _legal_modifications(state, bundle)
    # Cap the combinatorial search to the 18 largest distinct source events. A
    # source event changes every forecast occurrence, so this remains general and
    # bounded while preserving the smallest-set-first search order.
    unique = list(actions[:18])
    variants: list[tuple[SpendingModification, ...]] = [()]
    for size in range(1, min(3, len(unique)) + 1):
        variants.extend(
            combo for combo in combinations(unique, size)
            if len({action.event_id for action in combo}) == size
        )
    return tuple(variants)


def _candidate(
    method: str, state: ReconstructedState, bundle: RequestBundle, payments: Sequence[HypotheticalPayment],
    *, option_id: str | None = None, total_payable: Decimal, modifications: Sequence[SpendingModification] = (),
    rejection_reasons: Iterable[str] = (),
) -> Candidate:
    reasons = tuple(rejection_reasons)
    simulation = None
    if not reasons:
        simulation = simulate(state, tuple(payments), tuple(modifications))
        if not simulation.safe:
            reasons = (simulation.first_violation_reason or "simulator minimum-balance violation",)
    trace = tuple(item.source_id for item in state.baseline_movements)
    if simulation:
        trace_items = [*trace, *(payment.payment_id for payment in payments), *(change.event_id for change in modifications)]
        if option_id:
            trace_items.append(option_id)
        trace = tuple(trace_items)
    candidate = Candidate(method, not reasons, reasons, tuple(payments), option_id, total_payable, tuple(modifications), simulation, trace)
    structural = validate_candidate(candidate, state, bundle)
    if structural:
        candidate = Candidate(candidate.method, False, tuple((*candidate.rejection_reasons, *structural)), candidate.payments, candidate.option_id, candidate.total_payable, candidate.spending_changes, candidate.simulation, candidate.trace_references)
    return candidate


def generate_candidates(state: ReconstructedState, bundle: RequestBundle, capacity: CapacityResult) -> tuple[Candidate, ...]:
    request = bundle.request
    preferences = bundle.profile.payment_preferences
    candidates: list[Candidate] = []

    if PaymentPreference.FULL_PAYMENT in preferences:
        full_payment = (HypotheticalPayment(request.request_date, request.requested_amount, "full_payment"),)
        for modifications in _modification_sets(state, bundle):
            candidates.append(_candidate("full_payment", state, bundle, full_payment, total_payable=request.requested_amount, modifications=modifications))

    if PaymentPreference.PARTIAL_PAYMENT in preferences and request.allows_partial_payment and ZERO < capacity.amount_safe_to_pay < request.requested_amount and capacity.earliest_date_for_full_payment is not None and capacity.earliest_date_for_full_payment <= request.desired_completion_date:
        partial = (
            HypotheticalPayment(request.request_date, capacity.amount_safe_to_pay, "partial_now"),
            HypotheticalPayment(capacity.earliest_date_for_full_payment, request.requested_amount - capacity.amount_safe_to_pay, "partial_remainder"),
        )
        candidates.append(_candidate("partial_payment", state, bundle, partial, total_payable=request.requested_amount))

    if PaymentPreference.INSTALLMENTS in preferences:
        for option in bundle.payment_options:
            reasons: list[str] = []
            if option.payment_method is not PaymentOptionMethod.INSTALLMENTS:
                continue
            schedule = _payment_schedule(option, state)
            if not schedule:
                reasons.append("installment option has no payment frequency")
            if bundle.profile.max_installment_months is None:
                reasons.append("user does not consider installment duration")
            elif option.number_of_payments > bundle.profile.max_installment_months:
                reasons.append("option exceeds max_installment_months")
            if schedule and schedule[-1].payment_date > request.desired_completion_date:
                reasons.append("option finishes after desired completion date")
            if schedule and schedule[-1].payment_date > state.horizon_end:
                reasons.append("option finishes after forecast horizon")
            for modifications in _modification_sets(state, bundle):
                candidates.append(_candidate("installments", state, bundle, schedule, option_id=option.payment_option_id, total_payable=option.total_payable_amount, modifications=modifications, rejection_reasons=reasons))

    if PaymentPreference.FULL_PAYMENT in preferences and capacity.earliest_date_for_full_payment is not None and request.request_date < capacity.earliest_date_for_full_payment <= request.desired_completion_date:
        wait = (HypotheticalPayment(capacity.earliest_date_for_full_payment, request.requested_amount, "wait_full_payment"),)
        candidates.append(_candidate("wait", state, bundle, wait, total_payable=request.requested_amount))

    return tuple(candidates)


def rank_candidates(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    eligible = [candidate for candidate in candidates if candidate.eligible and candidate.safe and candidate.completes_by_deadline]
    return tuple(sorted(
        eligible,
        key=lambda candidate: (
            not candidate.completes_by_deadline,
            bool(candidate.spending_changes),
            candidate.total_payable,
            candidate.first_payment_date or date.max,
            candidate.payment_count,
            candidate.option_id or "",
            candidate.method,
        ),
    ))
