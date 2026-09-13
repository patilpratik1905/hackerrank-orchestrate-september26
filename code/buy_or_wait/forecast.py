"""Deterministic financial-state reconstruction and a 90-day cash ledger.

This module owns evidence -> recurrence -> FX -> simulation traceability.  It
intentionally does not calculate capacity or select/rank payment plans.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from statistics import median
from typing import Iterable, Mapping, Sequence

from buy_or_wait.data import DatasetRepository, MissingRateError
from buy_or_wait.evidence import (
    EvidenceAction,
    EvidenceFact,
    EvidenceFactType,
    EvidenceRecurrence,
    EvidenceStatus,
)
from buy_or_wait.models import Category, Direction, EventStatus, FinancialEvent, RequestBundle


ZERO = Decimal("0")
DAY = timedelta(days=1)


class ForecastError(ValueError):
    """A source fact cannot be safely projected without guessing."""


class MovementKind(str, Enum):
    EVENT = "event"
    RECURRENCE = "recurrence"
    VARIABLE_ESSENTIAL = "variable_essential"
    PAYMENT = "hypothetical_payment"


class SpendingAction(str, Enum):
    STOP = "stop"
    REDUCE_TO = "reduce_to"


@dataclass(frozen=True, slots=True)
class ForecastConfig:
    """Globally applied, conservative recurrence assumptions.

    The horizon is inclusive: request_date through request_date + 90 days.
    Repeated dates within seven days identify weekly cadence; dates 25--35 days
    apart identify monthly cadence.  Other regular intervals are retained only
    when their median is 8--60 days and all observations are within tolerance.
    """

    horizon_days: int = 90
    min_recurrence_observations: int = 3
    weekly_min_days: int = 6
    weekly_max_days: int = 8
    monthly_min_days: int = 25
    monthly_max_days: int = 35
    interval_tolerance_days: int = 3
    variable_lookback_days: int = 90

    def __post_init__(self) -> None:
        if self.horizon_days < 1 or self.min_recurrence_observations < 2:
            raise ForecastError("forecast configuration requires a positive horizon and at least two observations")


@dataclass(frozen=True, slots=True)
class RecurrenceRule:
    rule_id: str
    user_id: str
    category: Category
    direction: Direction
    currency: object
    cadence_days: int
    amount: Decimal
    anchor_date: date
    source_event_ids: tuple[str, ...]
    fixed: bool
    confidence: Decimal
    rationale: str


@dataclass(frozen=True, slots=True)
class CashMovement:
    movement_date: date
    amount: Decimal
    direction: Direction
    kind: MovementKind
    source_id: str
    source_event_id: str | None
    evidence_ids: tuple[str, ...] = ()
    recurrence_rule_id: str | None = None
    fx_rate: Decimal | None = None
    raw_amount: Decimal | None = None
    raw_currency: object | None = None
    detail: str = ""

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.direction is Direction.CREDIT else -self.amount


@dataclass(frozen=True, slots=True)
class SpendingModification:
    event_id: str
    action: SpendingAction
    new_amount: Decimal | None = None

    def __post_init__(self) -> None:
        if self.action is SpendingAction.REDUCE_TO:
            if self.new_amount is None or self.new_amount < ZERO:
                raise ForecastError("reduce_to requires a nonnegative Decimal new_amount")
        elif self.new_amount is not None:
            raise ForecastError("stop does not accept a new amount")


@dataclass(frozen=True, slots=True)
class HypotheticalPayment:
    payment_date: date
    amount: Decimal
    payment_id: str = "payment"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount < ZERO:
            raise ForecastError("payment amount must be a finite nonnegative Decimal")


@dataclass(frozen=True, slots=True)
class ReconstructedState:
    request_id: str
    user_id: str
    request_date: date
    horizon_end: date
    starting_balance: Decimal
    minimum_balance: Decimal
    baseline_movements: tuple[CashMovement, ...]
    recurrence_rules: tuple[RecurrenceRule, ...]
    excluded_event_reasons: Mapping[str, str]
    resolved_event_amounts: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    movement: CashMovement
    balance_after: Decimal


@dataclass(frozen=True, slots=True)
class SimulationResult:
    entries: tuple[LedgerEntry, ...]
    ending_balance: Decimal
    minimum_balance_observed: Decimal
    minimum_balance_date: date
    safe: bool
    first_violation: LedgerEntry | None
    first_violation_reason: str | None


def _normalized_description(value: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _fact_sort_key(fact: EvidenceFact) -> tuple[date, int, str]:
    return (
        (fact.source_timestamp.date() if fact.source_timestamp else date.min),
        fact.provenance.row_number,
        fact.evidence_id,
    )


def _facts_by_event(facts: Iterable[EvidenceFact]) -> Mapping[str, tuple[EvidenceFact, ...]]:
    grouped: dict[str, list[EvidenceFact]] = defaultdict(list)
    for fact in facts:
        if fact.related_event_id:
            grouped[fact.related_event_id].append(fact)
    return {event_id: tuple(sorted(items, key=_fact_sort_key)) for event_id, items in grouped.items()}


def _excluded_by_evidence(event: FinancialEvent, facts: Sequence[EvidenceFact]) -> str | None:
    if event.status in {EventStatus.FAILED, EventStatus.CANCELLED, EventStatus.UNREALIZED}:
        return f"event status is {event.status.value}"
    if event.direction is Direction.NON_CASH:
        return "non-cash event"
    if any(
        fact.action is EvidenceAction.CANCELLATION or fact.status_change in {EvidenceStatus.CANCELLED, EvidenceStatus.FAILED}
        for fact in facts
    ):
        return "explicit evidence cancellation/failure"
    return None


def _effective_amount(
    event: FinancialEvent, facts: Sequence[EvidenceFact], resolved_amounts: Mapping[str, Decimal]
) -> Decimal:
    candidates = [
        fact for fact in facts
        if fact.amount is not None
        and fact.currency is event.currency
        and fact.fact_type in {
            EvidenceFactType.AMOUNT_RESOLUTION,
            EvidenceFactType.SALARY_CHANGE,
            EvidenceFactType.RENT_CHANGE,
            EvidenceFactType.OTHER_AMENDMENT,
        }
        and fact.action in {EvidenceAction.AMENDMENT, EvidenceAction.CONFIRMATION, EvidenceAction.RECURRENCE_CHANGE}
    ]
    if candidates:
        return candidates[-1].amount  # type: ignore[return-value]
    if event.amount is not None:
        return event.amount
    try:
        return resolved_amounts[event.event_id]
    except KeyError as exc:
        raise ForecastError(f"event {event.event_id} has no resolved amount") from exc


def _effective_date(event: FinancialEvent, facts: Sequence[EvidenceFact]) -> date | None:
    dated = [fact.settlement_date for fact in facts if fact.settlement_date is not None]
    return dated[-1] if dated else event.settlement_date


def _event_is_cash_future(event: FinancialEvent, settlement_date: date, request_date: date) -> bool:
    if settlement_date < request_date:
        return False
    if event.status is EventStatus.PENDING:
        return event.direction is Direction.DEBIT
    if event.status is EventStatus.SCHEDULED:
        return event.direction is Direction.DEBIT or (
            event.direction is Direction.CREDIT and event.category is Category.SALARY
        )
    return event.status is EventStatus.SETTLED


def _lifecycle_suppressed(events: Sequence[FinancialEvent], eligible_ids: set[str]) -> set[str]:
    """Keep the newest viable row in a same-direction future retry lifecycle."""

    suppressed: set[str] = set()
    by_id = {event.event_id: event for event in events}
    for event in events:
        if event.event_id not in eligible_ids or not event.linked_event_id:
            continue
        parent = by_id[event.linked_event_id]
        if parent.event_id in eligible_ids and parent.direction is event.direction:
            suppressed.add(parent.event_id)
    return suppressed


def _internal_transfer_ids(
    events: Sequence[FinancialEvent], facts: Iterable[EvidenceFact], resolved_amounts: Mapping[str, Decimal]
) -> set[str]:
    """Identify matched debit/credit pairs only when bank evidence says they are own-account transfers."""

    transfer_facts = [fact for fact in facts if fact.fact_type is EvidenceFactType.INTERNAL_TRANSFER]
    suppressed: set[str] = set()
    for fact in transfer_facts:
        if fact.source_timestamp is None:
            continue
        message_day = fact.source_timestamp.date()
        nearby: list[tuple[FinancialEvent, date, Decimal]] = []
        for event in events:
            if event.direction is Direction.NON_CASH or event.settlement_date is None:
                continue
            if abs((event.settlement_date - message_day).days) > 14:
                continue
            amount = event.amount if event.amount is not None else resolved_amounts.get(event.event_id)
            if amount is not None:
                nearby.append((event, event.settlement_date, amount))
        for debit, debit_date, debit_amount in nearby:
            if debit.direction is not Direction.DEBIT:
                continue
            for credit, credit_date, credit_amount in nearby:
                if (
                    credit.direction is Direction.CREDIT
                    and debit.currency is credit.currency
                    and debit_amount == credit_amount
                    and abs((debit_date - credit_date).days) <= 1
                ):
                    suppressed.update({debit.event_id, credit.event_id})
    return suppressed


def _home_amount(repository: DatasetRepository, bundle: RequestBundle, event: FinancialEvent, amount: Decimal, settlement_date: date) -> tuple[Decimal, Decimal | None]:
    if event.currency is bundle.profile.home_currency:
        return amount, None
    rate = repository.get_rate(settlement_date, event.currency, bundle.profile.home_currency)
    return rate.convert(amount), rate.rate


def _recurring_eligible(event: FinancialEvent) -> bool:
    return (
        event.status is EventStatus.SETTLED
        and event.direction is not Direction.NON_CASH
        and event.category not in {Category.WINDFALL, Category.INVESTMENT}
        and event.event_type.value not in {"refund", "investment_sale", "investment_purchase", "investment_valuation"}
    )


def detect_recurrence(
    bundle: RequestBundle,
    repository: DatasetRepository,
    facts: Iterable[EvidenceFact] = (),
    resolved_amounts: Mapping[str, Decimal] | None = None,
    config: ForecastConfig = ForecastConfig(),
) -> tuple[RecurrenceRule, ...]:
    """Return only globally supported fixed weekly/monthly/regular repetitions."""

    resolved_amounts = resolved_amounts or {}
    facts_by_event = _facts_by_event(facts)
    groups: dict[tuple[object, ...], list[tuple[FinancialEvent, date, Decimal]]] = defaultdict(list)
    for event in bundle.events:
        event_facts = facts_by_event.get(event.event_id, ())
        if not _recurring_eligible(event):
            continue
        settlement = _effective_date(event, event_facts)
        if settlement is None or settlement >= bundle.request.request_date:
            continue
        try:
            amount = _effective_amount(event, event_facts, resolved_amounts)
        except ForecastError:
            continue
        key = (event.category, event.direction, event.currency, _normalized_description(event.description))
        groups[key].append((event, settlement, amount))

    rules: list[RecurrenceRule] = []
    for key, occurrences in sorted(groups.items(), key=lambda item: str(item[0])):
        occurrences.sort(key=lambda item: item[1])
        if len(occurrences) < config.min_recurrence_observations:
            continue
        intervals = [(right[1] - left[1]).days for left, right in zip(occurrences, occurrences[1:])]
        cadence = int(median(intervals))
        if not all(abs(interval - cadence) <= config.interval_tolerance_days for interval in intervals):
            continue
        if config.weekly_min_days <= cadence <= config.weekly_max_days:
            label = "weekly"
        elif config.monthly_min_days <= cadence <= config.monthly_max_days:
            label = "monthly"
        elif 8 <= cadence <= 60:
            label = f"{cadence}-day"
        else:
            continue
        category, direction, currency, normalized = key
        # Use the latest amended amount for a supported fixed commitment.
        latest = occurrences[-1]
        source_ids = tuple(item[0].event_id for item in occurrences)
        rules.append(RecurrenceRule(
            rule_id=f"recurrence:{latest[0].event_id}", user_id=bundle.profile.user_id,
            category=category, direction=direction, currency=currency, cadence_days=cadence,
            amount=latest[2], anchor_date=latest[1], source_event_ids=source_ids,
            fixed=True, confidence=Decimal("0.90"),
            rationale=f"{len(occurrences)} settled {label} observations for {normalized}",
        ))
    return tuple(rules)


def _variable_essential_rules(
    bundle: RequestBundle, repository: DatasetRepository, facts: Iterable[EvidenceFact],
    resolved_amounts: Mapping[str, Decimal], fixed_rules: Sequence[RecurrenceRule], config: ForecastConfig,
) -> tuple[RecurrenceRule, ...]:
    """Forecast protected non-fixed debit categories from conservative 30-day totals."""

    fixed_ids = {event_id for rule in fixed_rules for event_id in rule.source_event_ids}
    facts_by_event = _facts_by_event(facts)
    cutoff = bundle.request.request_date - timedelta(days=config.variable_lookback_days)
    category_totals: dict[tuple[Category, object], list[tuple[Decimal, str]]] = defaultdict(list)
    for event in bundle.events:
        if event.event_id in fixed_ids or event.category not in bundle.profile.protected_categories:
            continue
        if event.status is not EventStatus.SETTLED or event.direction is not Direction.DEBIT:
            continue
        settlement = _effective_date(event, facts_by_event.get(event.event_id, ()))
        if settlement is None or not cutoff <= settlement < bundle.request.request_date:
            continue
        amount = _effective_amount(event, facts_by_event.get(event.event_id, ()), resolved_amounts)
        category_totals[(event.category, event.currency)].append((amount, event.event_id))
    rules: list[RecurrenceRule] = []
    for (category, currency), amounts in sorted(category_totals.items(), key=lambda item: (item[0][0].value, str(item[0][1]))):
        if len(amounts) < 2:
            continue
        # Upper median is robust to a lone outlier and conservative versus mean.
        sorted_amounts = sorted(amount for amount, _ in amounts)
        conservative = sorted_amounts[len(sorted_amounts) // 2]
        source_ids = tuple(event_id for _, event_id in amounts)
        rules.append(RecurrenceRule(
            rule_id=f"variable:{category.value}:{str(currency)}", user_id=bundle.profile.user_id,
            category=category, direction=Direction.DEBIT, currency=currency, cadence_days=30,
            amount=conservative, anchor_date=bundle.request.request_date, source_event_ids=source_ids,
            fixed=False, confidence=Decimal("0.70"),
            rationale="upper median protected-category settled debit amount over prior 90 days",
        ))
    return tuple(rules)


def reconstruct_state(
    bundle: RequestBundle,
    repository: DatasetRepository,
    facts: Iterable[EvidenceFact] = (),
    resolved_amounts: Mapping[str, Decimal] | None = None,
    config: ForecastConfig = ForecastConfig(),
) -> ReconstructedState:
    """Create baseline future movements without replaying past settled cash."""

    resolved_amounts = resolved_amounts or {}
    facts = tuple(fact for fact in facts if fact.user_id == bundle.profile.user_id)
    facts_by_event = _facts_by_event(facts)
    horizon_end = bundle.request.request_date + timedelta(days=config.horizon_days)
    excluded: dict[str, str] = {}
    internal_transfer_ids = _internal_transfer_ids(bundle.events, facts, resolved_amounts)
    eligible: list[tuple[FinancialEvent, date, Decimal, tuple[EvidenceFact, ...]]] = []
    for event in bundle.events:
        event_facts = facts_by_event.get(event.event_id, ())
        reason = _excluded_by_evidence(event, event_facts)
        if reason:
            excluded[event.event_id] = reason
            continue
        if event.event_id in internal_transfer_ids:
            excluded[event.event_id] = "matched own-account transfer confirmed by bank evidence"
            continue
        settlement = _effective_date(event, event_facts)
        if settlement is None:
            excluded[event.event_id] = "no settlement date for cash event"
            continue
        if not _event_is_cash_future(event, settlement, bundle.request.request_date):
            excluded[event.event_id] = "historical or unsupported pending/scheduled credit"
            continue
        if settlement > horizon_end:
            excluded[event.event_id] = "outside inclusive forecast horizon"
            continue
        amount = _effective_amount(event, event_facts, resolved_amounts)
        eligible.append((event, settlement, amount, event_facts))

    eligible_ids = {event.event_id for event, _, _, _ in eligible}
    for event_id in _lifecycle_suppressed(bundle.events, eligible_ids):
        excluded[event_id] = "superseded by linked viable lifecycle event"
    movements: list[CashMovement] = []
    for event, settlement, amount, event_facts in eligible:
        if event.event_id in excluded:
            continue
        home_amount, rate = _home_amount(repository, bundle, event, amount, settlement)
        movements.append(CashMovement(
            movement_date=settlement, amount=home_amount, direction=event.direction,
            kind=MovementKind.EVENT, source_id=event.event_id, source_event_id=event.event_id,
            evidence_ids=tuple(fact.evidence_id for fact in event_facts), fx_rate=rate,
            raw_amount=amount, raw_currency=event.currency, detail=f"{event.status.value} {event.description}",
        ))

    fixed_rules = detect_recurrence(bundle, repository, facts, resolved_amounts, config)
    variable_rules = _variable_essential_rules(bundle, repository, facts, resolved_amounts, fixed_rules, config)
    for rule in (*fixed_rules, *variable_rules):
        next_date = rule.anchor_date + timedelta(days=rule.cadence_days)
        while next_date < bundle.request.request_date:
            next_date += timedelta(days=rule.cadence_days)
        while next_date <= horizon_end:
            source_event_id = rule.source_event_ids[-1] if rule.source_event_ids else None
            # A known future row from this recurrence is more authoritative than a synthetic one.
            if not any(movement.movement_date == next_date and movement.direction is rule.direction for movement in movements):
                raw_event = repository.events_by_event_id[source_event_id] if source_event_id else None
                try:
                    if raw_event is not None:
                        home_amount, rate = _home_amount(repository, bundle, raw_event, rule.amount, next_date)
                    elif rule.currency is bundle.profile.home_currency:
                        home_amount, rate = rule.amount, None
                    else:
                        rate_obj = repository.get_rate(next_date, rule.currency, bundle.profile.home_currency)
                        home_amount, rate = rate_obj.convert(rule.amount), rate_obj.rate
                except MissingRateError:
                    # The contract prohibits interpolation or inversion.  Do not invent a
                    # foreign-currency recurrence when no dated rate was supplied.
                    excluded[f"{rule.rule_id}@{next_date.isoformat()}"] = "recurrence omitted: no exact FX rate"
                    next_date += timedelta(days=rule.cadence_days)
                    continue
                movements.append(CashMovement(
                    movement_date=next_date, amount=home_amount, direction=rule.direction,
                    kind=MovementKind.RECURRENCE if rule.fixed else MovementKind.VARIABLE_ESSENTIAL,
                    source_id=rule.rule_id, source_event_id=source_event_id,
                    recurrence_rule_id=rule.rule_id, fx_rate=rate, raw_amount=rule.amount,
                    raw_currency=rule.currency, detail=rule.rationale,
                ))
            next_date += timedelta(days=rule.cadence_days)
    return ReconstructedState(
        request_id=bundle.request.request_id, user_id=bundle.profile.user_id,
        request_date=bundle.request.request_date, horizon_end=horizon_end,
        starting_balance=bundle.profile.current_available_balance,
        minimum_balance=bundle.profile.minimum_balance_to_keep,
        baseline_movements=tuple(movements), recurrence_rules=tuple((*fixed_rules, *variable_rules)),
        excluded_event_reasons=excluded, resolved_event_amounts=dict(resolved_amounts),
    )


def _apply_modifications(movement: CashMovement, modifications: Mapping[str, SpendingModification]) -> CashMovement | None:
    if movement.direction is not Direction.DEBIT or movement.source_event_id is None:
        return movement
    modification = modifications.get(movement.source_event_id)
    if modification is None:
        return movement
    if modification.action is SpendingAction.STOP:
        return None
    assert modification.new_amount is not None
    if movement.raw_amount is None:
        raise ForecastError(f"cannot reduce movement {movement.source_id} without its raw amount")
    adjusted = modification.new_amount
    converted = adjusted if movement.fx_rate is None else adjusted * movement.fx_rate
    return CashMovement(
        movement_date=movement.movement_date, amount=converted, direction=movement.direction,
        kind=movement.kind, source_id=movement.source_id, source_event_id=movement.source_event_id,
        evidence_ids=movement.evidence_ids, recurrence_rule_id=movement.recurrence_rule_id,
        fx_rate=movement.fx_rate, raw_amount=adjusted, raw_currency=movement.raw_currency,
        detail=f"{movement.detail}; reduced by simulator input",
    )


def simulate(
    state: ReconstructedState,
    payments: Sequence[HypotheticalPayment] = (),
    modifications: Sequence[SpendingModification] = (),
) -> SimulationResult:
    """Run the single inclusive-horizon ledger, debits/payments before credits."""

    modification_by_event = {item.event_id: item for item in modifications}
    if len(modification_by_event) != len(modifications):
        raise ForecastError("a spending event may be modified at most once")
    movements = [
        adjusted for movement in state.baseline_movements
        if (adjusted := _apply_modifications(movement, modification_by_event)) is not None
    ]
    for payment in payments:
        if state.request_date <= payment.payment_date <= state.horizon_end and payment.amount:
            movements.append(CashMovement(
                movement_date=payment.payment_date, amount=payment.amount, direction=Direction.DEBIT,
                kind=MovementKind.PAYMENT, source_id=payment.payment_id, source_event_id=None,
                raw_amount=payment.amount, detail="hypothetical payment",
            ))
    def order(movement: CashMovement) -> tuple[date, int, str]:
        priority = 2 if movement.direction is Direction.CREDIT else (1 if movement.kind is MovementKind.PAYMENT else 0)
        return movement.movement_date, priority, movement.source_id
    balance = state.starting_balance
    minimum = balance
    minimum_date = state.request_date
    entries: list[LedgerEntry] = []
    violation: LedgerEntry | None = None
    violation_reason: str | None = None
    if balance < state.minimum_balance:
        violation_reason = (
            f"opening balance {balance} is below required minimum {state.minimum_balance}"
        )
    for movement in sorted(movements, key=order):
        balance += movement.signed_amount
        entry = LedgerEntry(movement, balance)
        entries.append(entry)
        if balance < minimum:
            minimum, minimum_date = balance, movement.movement_date
        if violation is None and balance < state.minimum_balance:
            violation = entry
            violation_reason = (
                f"balance {balance} fell below required minimum {state.minimum_balance}"
            )
    return SimulationResult(
        entries=tuple(entries), ending_balance=balance, minimum_balance_observed=minimum,
        minimum_balance_date=minimum_date,
        safe=violation is None and violation_reason is None,
        first_violation=violation,
        first_violation_reason=violation_reason,
    )
