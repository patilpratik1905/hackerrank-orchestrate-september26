"""Simulator-backed capacity measurements for a reconstructed request state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from buy_or_wait.forecast import HypotheticalPayment, ReconstructedState, SimulationResult, simulate


ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class CapacityResult:
    requested_amount: Decimal
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: date | None
    baseline_simulation: SimulationResult
    earliest_simulation: SimulationResult | None


def amount_safe_to_pay(state: ReconstructedState, requested_amount: Decimal) -> Decimal:
    """Return the largest immediate payment preserving the baseline cushion.

    A payment is a single negative movement, so every post-payment balance is the
    no-payment balance minus the same amount.  The minimum observed baseline is
    therefore the exact linear constraint; no binary search or rounding is needed.
    """

    if not isinstance(requested_amount, Decimal) or not requested_amount.is_finite() or requested_amount < ZERO:
        raise ValueError("requested_amount must be a finite nonnegative Decimal")
    baseline = simulate(state)
    cushion = baseline.minimum_balance_observed - state.minimum_balance
    if cushion <= ZERO or not baseline.safe:
        return ZERO
    return min(requested_amount, cushion)


def earliest_date_for_full_payment(state: ReconstructedState, requested_amount: Decimal) -> tuple[date | None, SimulationResult | None]:
    """Find the first no-change date whose complete horizon remains safe."""

    if not isinstance(requested_amount, Decimal) or not requested_amount.is_finite() or requested_amount < ZERO:
        raise ValueError("requested_amount must be a finite nonnegative Decimal")
    if requested_amount == ZERO:
        result = simulate(state)
        return (state.request_date if result.safe else None), result if result.safe else None
    current = state.request_date
    while current <= state.horizon_end:
        result = simulate(state, (HypotheticalPayment(current, requested_amount, "capacity_full_payment"),))
        if result.safe:
            return current, result
        current += timedelta(days=1)
    return None, None


def calculate_capacity(state: ReconstructedState, requested_amount: Decimal) -> CapacityResult:
    baseline = simulate(state)
    safe_amount = amount_safe_to_pay(state, requested_amount)
    earliest, earliest_result = earliest_date_for_full_payment(state, requested_amount)
    return CapacityResult(requested_amount, safe_amount, earliest, baseline, earliest_result)
