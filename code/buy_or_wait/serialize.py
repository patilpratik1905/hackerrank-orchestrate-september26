"""Centralized output serializer with trace-driven explanation generation.

Converts structured recommendations (capacity + selected candidate) into exact
output rows conforming to the submission contract. Explanation generation
introduces no new financial reasoning — it uses only trace-supported facts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence

from buy_or_wait.candidates import Candidate
from buy_or_wait.capacity import CapacityResult
from buy_or_wait.forecast import SpendingModification
from buy_or_wait.models import Request

ZERO = Decimal("0")


def format_money(value: Decimal) -> str:
    """Canonical Decimal formatting: no trailing zeros, no scientific notation."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_plan_amount(value: Decimal) -> str:
    """Plan amounts: integral when whole, otherwise two decimals."""
    if value == value.to_integral_value():
        return format_money(value.to_integral_value())
    # Quantize only at the output boundary. Intermediate simulation values stay
    # at full Decimal precision, while plans use the challenge's cent format.
    return format(value.quantize(Decimal("0.01")), "f")


def format_spending_changes(candidate: Candidate | None) -> str:
    """Serialize spending changes to the required format."""
    if candidate is None or not candidate.spending_changes:
        return "none"
    values = []
    for action in candidate.spending_changes:
        if action.action.value == "stop":
            values.append(f"stop:{action.event_id}")
        else:
            values.append(f"reduce_to:{action.event_id}:{format_money(action.new_amount or ZERO)}")
    return "|".join(values)


def format_payment_plan(candidate: Candidate | None) -> str:
    """Serialize payment plan to chronological pipe-separated entries."""
    if candidate is None or not candidate.payments:
        return "none"
    entries = []
    for payment in candidate.payments:
        entries.append(f"{payment.payment_date.isoformat()}:{format_plan_amount(payment.amount)}")
    return "|".join(entries)


def generate_explanation(
    request: Request,
    capacity: CapacityResult,
    selected: Candidate | None,
) -> str:
    """Generate a concise, trace-driven explanation.

    Uses only trace-supported facts: amounts, dates, counts, minimum balance.
    Never invents facts or contradicts the structured recommendation.
    """
    safe = capacity.amount_safe_to_pay
    requested = request.requested_amount
    earliest = capacity.earliest_date_for_full_payment
    min_bal_text = ""
    if selected and selected.simulation:
        min_bal_text = f" The projected minimum balance is {format_money(selected.simulation.minimum_balance_observed)}."

    if selected is None:
        if earliest is None:
            return (
                f"No safe option completes the {format_money(requested)} request "
                f"within the forecast horizon. The amount safe to pay today is "
                f"{format_money(safe)}, which cannot be completed safely with any "
                f"available payment plan."
            )
        return (
            f"No safe option completes the {format_money(requested)} request "
            f"by the desired date. The amount safe to pay today is {format_money(safe)}."
        )

    method = selected.method
    changes = selected.spending_changes
    changes_text = ""
    if changes:
        parts = []
        for c in changes:
            if c.action.value == "stop":
                parts.append(f"stopping {c.event_id}")
            else:
                parts.append(f"reducing {c.event_id} to {format_money(c.new_amount or ZERO)}")
        changes_text = f" after {', '.join(parts)}"

    if method == "full_payment":
        if changes:
            return (
                f"Full payment of {format_money(requested)} is safe on {request.request_date.isoformat()}"
                f"{changes_text}, preserving the required minimum balance.{min_bal_text}"
            )
        return (
            f"Full payment of {format_money(requested)} is safe on {request.request_date.isoformat()} "
            f"while preserving the required minimum balance.{min_bal_text}"
        )

    if method == "partial_payment":
        remainder = requested - safe
        second_date = earliest.isoformat() if earliest else "a later date"
        return (
            f"A partial payment of {format_money(safe)} is safe now, with the remainder of "
            f"{format_money(remainder)} scheduled for {second_date}.{min_bal_text}"
        )

    if method == "installments":
        count = selected.payment_count
        total = selected.total_payable
        first = selected.first_payment_date
        first_text = first.isoformat() if first else "the first payment date"
        return (
            f"Installments of {count} payments totaling {format_money(total)} starting "
            f"{first_text} complete the request safely within the permitted duration.{min_bal_text}"
        )

    if method == "wait":
        wait_date = earliest.isoformat() if earliest else "a future date"
        return (
            f"Wait until {wait_date} to make the full payment of {format_money(requested)}, "
            f"preserving the required minimum balance.{min_bal_text}"
        )

    return f"The request of {format_money(requested)} cannot be safely completed."


def determine_status(selected: Candidate | None) -> str:
    """Determine affordability status from the selected candidate."""
    if selected is None:
        return "not_affordable"
    if selected.method == "full_payment":
        return "affordable_now" if not selected.spending_changes else "affordable_with_plan"
    if selected.method in {"partial_payment", "installments"}:
        return "affordable_with_plan"
    return "affordable_later"


def serialize_row(
    request: Request,
    capacity: CapacityResult,
    selected: Candidate | None,
) -> dict[str, str]:
    """Serialize one request's decision into the exact output row format."""
    return {
        "request_id": request.request_id,
        "amount_safe_to_pay": format_money(capacity.amount_safe_to_pay),
        "affordability_status": determine_status(selected),
        "recommended_payment_method": selected.method if selected else "not_recommended",
        "payment_plan": format_payment_plan(selected),
        "earliest_date_for_full_payment": (
            capacity.earliest_date_for_full_payment.isoformat()
            if capacity.earliest_date_for_full_payment
            else ""
        ),
        "spending_changes_needed": format_spending_changes(selected),
        "decision_explanation": generate_explanation(request, capacity, selected),
    }


def serialize_rows(
    results: Sequence[tuple[Request, CapacityResult, Candidate | None]],
) -> list[dict[str, str]]:
    """Serialize all results into output rows, sorted by request_id."""
    rows = [serialize_row(req, cap, sel) for req, cap, sel in results]
    rows.sort(key=lambda row: row["request_id"])
    return rows
