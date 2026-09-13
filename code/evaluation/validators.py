"""Deterministic structural validators for Buy or Wait? prediction rows.

These checks deliberately stop at structural and contract validation. Financial
feasibility is delegated to an optional callback that will be supplied by the
90-day simulator in a later implementation step.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from buy_or_wait.schema import OUTPUT_COLUMNS, REQUEST_INPUT_COLUMNS
from evaluation.data_audit import parse_decimal, parse_iso_date, read_csv_table

SAMPLE_INPUT_COLUMNS = REQUEST_INPUT_COLUMNS

ALLOWED_AFFORDABILITY_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}
ALLOWED_PAYMENT_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}
MONEY_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d{1,2})?$")
PAYMENT_ENTRY_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}):(?P<amount>(?:0|[1-9]\d*)(?:\.\d{1,2})?)$"
)
EVENT_ID_PATTERN = r"[^:|\s]+"
STOP_PATTERN = re.compile(rf"^stop:(?P<event_id>{EVENT_ID_PATTERN})$")
REDUCE_PATTERN = re.compile(
    rf"^reduce_to:(?P<event_id>{EVENT_ID_PATTERN}):"
    r"(?P<amount>(?:0|[1-9]\d*)(?:\.\d{1,2})?)$"
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str
    message: str
    request_id: str | None = None
    stage: str = "formatting"

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class PaymentEntry:
    payment_date: date
    amount: Decimal


@dataclass(frozen=True)
class SpendingAction:
    action: str
    event_id: str
    new_amount: Decimal | None = None


@dataclass(frozen=True)
class ValidationContext:
    requests_by_id: Mapping[str, Mapping[str, str]]
    profiles_by_user: Mapping[str, Mapping[str, str]]
    events_by_id: Mapping[str, Mapping[str, str]]
    options_by_request: Mapping[str, Sequence[Mapping[str, str]]]
    forecast_horizon_days: int = 90


@dataclass(frozen=True)
class RowValidation:
    request_id: str
    issues: tuple[ValidationIssue, ...]
    payment_schedule: tuple[PaymentEntry, ...]
    spending_actions: tuple[SpendingAction, ...]
    feasibility_status: str

    @property
    def passed(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "passed": self.passed,
            "feasibility_status": self.feasibility_status,
            "issues": [issue.to_dict() for issue in self.issues],
        }


FeasibilityValidator = Callable[
    [
        Mapping[str, str],
        Mapping[str, str],
        Sequence[PaymentEntry],
        Sequence[SpendingAction],
        ValidationContext,
    ],
    Iterable[ValidationIssue],
]


def split_pipe(value: str) -> set[str]:
    return {item for item in value.split("|") if item}


def load_validation_context(
    dataset_dir: Path, request_rows: Sequence[Mapping[str, str]]
) -> ValidationContext:
    """Load only raw metadata needed for validation, not production state."""

    _, profiles = read_csv_table(dataset_dir / "financial_profiles.csv")
    _, events = read_csv_table(dataset_dir / "financial_events.csv")
    _, options = read_csv_table(dataset_dir / "request_payment_options.csv")
    options_by_request: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for option in options:
        options_by_request[option["request_id"]].append(option)
    return ValidationContext(
        requests_by_id={row["request_id"]: row for row in request_rows},
        profiles_by_user={row["user_id"]: row for row in profiles},
        events_by_id={row["event_id"]: row for row in events},
        options_by_request=dict(options_by_request),
    )


def load_prediction_file(
    path: Path, expected_request_ids: set[str]
) -> tuple[list[dict[str, str]], list[ValidationIssue], tuple[str, ...]]:
    """Read a prediction CSV and validate its file-level contract."""

    headers, rows = read_csv_table(path)
    issues: list[ValidationIssue] = []
    if headers != OUTPUT_COLUMNS:
        issues.append(
            ValidationIssue(
                "output_columns",
                "file",
                f"expected {OUTPUT_COLUMNS}, found {headers}",
            )
        )

    ids = [row.get("request_id", "") for row in rows]
    counts = Counter(ids)
    for request_id, count in sorted(counts.items()):
        if request_id and count > 1:
            issues.append(
                ValidationIssue(
                    "duplicate_request_id",
                    "request_id",
                    f"found {count} rows",
                    request_id,
                )
            )
    blank_count = counts.get("", 0)
    if blank_count:
        issues.append(
            ValidationIssue(
                "blank_request_id",
                "request_id",
                f"found {blank_count} blank IDs",
            )
        )

    actual_ids = {request_id for request_id in ids if request_id}
    for request_id in sorted(expected_request_ids - actual_ids):
        issues.append(
            ValidationIssue(
                "missing_request_id",
                "request_id",
                "expected request has no prediction row",
                request_id,
            )
        )
    for request_id in sorted(actual_ids - expected_request_ids):
        issues.append(
            ValidationIssue(
                "unexpected_request_id",
                "request_id",
                "prediction row is not in the solved sample set",
                request_id,
            )
        )
    return rows, issues, headers


def _issue(
    issues: list[ValidationIssue],
    request_id: str,
    code: str,
    field: str,
    message: str,
    *,
    stage: str = "formatting",
) -> None:
    issues.append(ValidationIssue(code, field, message, request_id, stage))


def parse_money(
    value: str,
    *,
    request_id: str,
    field: str,
    issues: list[ValidationIssue],
    positive: bool = False,
) -> Decimal | None:
    if not MONEY_PATTERN.fullmatch(value):
        _issue(
            issues,
            request_id,
            "invalid_decimal",
            field,
            f"expected a nonnegative decimal with at most two places, found {value!r}",
        )
        return None
    try:
        amount = parse_decimal(value, allow_blank=False)
    except ValueError as exc:
        _issue(issues, request_id, "invalid_decimal", field, str(exc))
        return None
    assert amount is not None
    if positive and amount <= 0:
        _issue(
            issues,
            request_id,
            "nonpositive_payment",
            field,
            "payment amount must be greater than zero",
        )
        return None
    return amount


def parse_payment_plan(
    value: str, request_id: str
) -> tuple[tuple[PaymentEntry, ...], list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if value == "none":
        return (), issues
    if not value:
        _issue(
            issues,
            request_id,
            "blank_payment_plan",
            "payment_plan",
            "use a schedule or the literal 'none'",
        )
        return (), issues

    entries: list[PaymentEntry] = []
    for raw_entry in value.split("|"):
        match = PAYMENT_ENTRY_PATTERN.fullmatch(raw_entry)
        if not match:
            _issue(
                issues,
                request_id,
                "invalid_payment_entry",
                "payment_plan",
                f"invalid entry {raw_entry!r}",
            )
            continue
        try:
            payment_date = parse_iso_date(match.group("date"), allow_blank=False)
        except ValueError as exc:
            _issue(
                issues,
                request_id,
                "invalid_payment_date",
                "payment_plan",
                str(exc),
            )
            continue
        amount = parse_money(
            match.group("amount"),
            request_id=request_id,
            field="payment_plan",
            issues=issues,
            positive=True,
        )
        if payment_date is not None and amount is not None:
            entries.append(PaymentEntry(payment_date, amount))

    if len(entries) >= 2 and any(
        later.payment_date < earlier.payment_date
        for earlier, later in zip(entries, entries[1:])
    ):
        _issue(
            issues,
            request_id,
            "nonchronological_payment_plan",
            "payment_plan",
            "payment dates must be chronological",
        )
    return tuple(entries), issues


def parse_spending_changes(
    value: str,
    request: Mapping[str, str],
    profile: Mapping[str, str],
    context: ValidationContext,
) -> tuple[tuple[SpendingAction, ...], list[ValidationIssue]]:
    request_id = request["request_id"]
    issues: list[ValidationIssue] = []
    if value == "none":
        return (), issues
    if not value:
        _issue(
            issues,
            request_id,
            "blank_spending_changes",
            "spending_changes_needed",
            "use actions or the literal 'none'",
        )
        return (), issues

    raw_actions = value.split("|")
    if len(raw_actions) > 3:
        _issue(
            issues,
            request_id,
            "too_many_spending_changes",
            "spending_changes_needed",
            f"found {len(raw_actions)} actions; maximum is three",
        )

    protected = split_pipe(profile["expense_categories_to_protect"])
    reducible_categories = split_pipe(
        profile["expense_categories_user_is_willing_to_reduce"]
    )
    stoppable_categories = split_pipe(
        profile["expense_categories_user_is_willing_to_stop"]
    )
    actions: list[SpendingAction] = []
    seen_events: set[str] = set()

    for raw_action in raw_actions:
        stop_match = STOP_PATTERN.fullmatch(raw_action)
        reduce_match = REDUCE_PATTERN.fullmatch(raw_action)
        if not stop_match and not reduce_match:
            _issue(
                issues,
                request_id,
                "invalid_spending_change_grammar",
                "spending_changes_needed",
                f"invalid action {raw_action!r}",
            )
            continue

        action = "stop" if stop_match else "reduce_to"
        match = stop_match or reduce_match
        assert match is not None
        event_id = match.group("event_id")
        new_amount: Decimal | None = None
        if reduce_match:
            new_amount = parse_money(
                reduce_match.group("amount"),
                request_id=request_id,
                field="spending_changes_needed",
                issues=issues,
            )
        actions.append(SpendingAction(action, event_id, new_amount))

        if event_id in seen_events:
            _issue(
                issues,
                request_id,
                "duplicate_spending_event",
                "spending_changes_needed",
                f"event {event_id} is targeted more than once",
            )
        seen_events.add(event_id)

        event = context.events_by_id.get(event_id)
        if event is None:
            _issue(
                issues,
                request_id,
                "unknown_spending_event",
                "spending_changes_needed",
                f"event {event_id} does not exist",
            )
            continue
        if event["user_id"] != request["user_id"]:
            _issue(
                issues,
                request_id,
                "spending_event_wrong_user",
                "spending_changes_needed",
                f"event {event_id} belongs to another user",
            )
        if event["direction"] != "debit":
            _issue(
                issues,
                request_id,
                "spending_event_not_debit",
                "spending_changes_needed",
                f"event {event_id} is not a debit",
            )
        category = event["category"]
        if category in protected:
            _issue(
                issues,
                request_id,
                "protected_spending_change",
                "spending_changes_needed",
                f"category {category} is protected",
            )

        flexibility = event["flexibility"]
        if action == "stop":
            if flexibility not in {"stoppable", "reducible_or_stoppable"}:
                _issue(
                    issues,
                    request_id,
                    "event_not_stoppable",
                    "spending_changes_needed",
                    f"event {event_id} has flexibility {flexibility}",
                )
            if category not in stoppable_categories:
                _issue(
                    issues,
                    request_id,
                    "category_not_stoppable",
                    "spending_changes_needed",
                    f"user does not permit stopping {category}",
                )
        else:
            if flexibility not in {"reducible", "reducible_or_stoppable"}:
                _issue(
                    issues,
                    request_id,
                    "event_not_reducible",
                    "spending_changes_needed",
                    f"event {event_id} has flexibility {flexibility}",
                )
            if category not in reducible_categories:
                _issue(
                    issues,
                    request_id,
                    "category_not_reducible",
                    "spending_changes_needed",
                    f"user does not permit reducing {category}",
                )
            if new_amount is None:
                continue
            minimum_text = event["minimum_allowed_amount"]
            if minimum_text:
                minimum = parse_decimal(minimum_text, allow_blank=False)
                assert minimum is not None
                if new_amount < minimum:
                    _issue(
                        issues,
                        request_id,
                        "reduction_below_minimum",
                        "spending_changes_needed",
                        f"{new_amount} is below {event_id} minimum {minimum}",
                    )
            if event["amount"]:
                current = parse_decimal(event["amount"], allow_blank=False)
                assert current is not None
                if new_amount >= current:
                    _issue(
                        issues,
                        request_id,
                        "reduction_not_lower",
                        "spending_changes_needed",
                        f"{new_amount} must be lower than current amount {current}",
                    )

    return tuple(actions), issues


def _expected_installment_schedule(
    option: Mapping[str, str], request_id: str
) -> tuple[PaymentEntry, ...] | None:
    try:
        first = parse_iso_date(option["first_payment_date"], allow_blank=False)
        payment = parse_decimal(option["payment_amount"], allow_blank=False)
        count = int(option["number_of_payments"])
        frequency = int(option["payment_frequency_days"])
    except (ValueError, KeyError):
        return None
    assert first is not None and payment is not None
    return tuple(
        PaymentEntry(first + timedelta(days=frequency * index), payment)
        for index in range(count)
    )


def _validate_explanation(
    prediction: Mapping[str, str], request_id: str
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    explanation = prediction.get("decision_explanation", "")
    normalized = " ".join(explanation.lower().split())
    if not explanation.strip():
        _issue(
            issues,
            request_id,
            "empty_explanation",
            "decision_explanation",
            "explanation must be nonempty",
        )
        return issues
    if "\n" in explanation or "\r" in explanation:
        _issue(
            issues,
            request_id,
            "multiline_explanation",
            "decision_explanation",
            "explanation must be one concise line",
        )

    status = prediction.get("affordability_status", "")
    method = prediction.get("recommended_payment_method", "")
    negative_cues = (
        "do not make",
        "do not proceed",
        "none of the available options",
        "not affordable",
        "cannot be completed safely",
        "no safe option",
    )
    has_negative_cue = any(cue in normalized for cue in negative_cues)
    if status == "not_affordable" and not has_negative_cue:
        _issue(
            issues,
            request_id,
            "explanation_missing_not_affordable_reason",
            "decision_explanation",
            "not-affordable output needs a clear negative safety statement",
        )
    if status in {
        "affordable_now",
        "affordable_with_plan",
        "affordable_later",
    } and has_negative_cue:
        _issue(
            issues,
            request_id,
            "explanation_contradiction",
            "decision_explanation",
            "explanation rejects all payment despite an affordable status",
        )
    if method == "installments" and "installment" not in normalized:
        _issue(
            issues,
            request_id,
            "explanation_method_mismatch",
            "decision_explanation",
            "installment recommendation is not described",
        )
    if method == "partial_payment" and not any(
        cue in normalized for cue in ("partial", "remaining", "remainder")
    ):
        _issue(
            issues,
            request_id,
            "explanation_method_mismatch",
            "decision_explanation",
            "partial-payment recommendation is not described",
        )
    return issues


def validate_prediction_row(
    prediction: Mapping[str, str],
    context: ValidationContext,
    *,
    feasibility_validator: FeasibilityValidator | None = None,
) -> RowValidation:
    request_id = prediction.get("request_id", "")
    issues: list[ValidationIssue] = []
    request = context.requests_by_id.get(request_id)
    if request is None:
        _issue(
            issues,
            request_id,
            "unknown_request",
            "request_id",
            "cannot validate a row without matching request inputs",
        )
        return RowValidation(request_id, tuple(issues), (), (), "not_run")
    profile = context.profiles_by_user.get(request["user_id"])
    if profile is None:
        _issue(
            issues,
            request_id,
            "missing_profile",
            "request_id",
            f"profile for {request['user_id']} is unavailable",
        )
        return RowValidation(request_id, tuple(issues), (), (), "not_run")

    status = prediction.get("affordability_status", "")
    method = prediction.get("recommended_payment_method", "")
    if status not in ALLOWED_AFFORDABILITY_STATUSES:
        _issue(
            issues,
            request_id,
            "invalid_affordability_status",
            "affordability_status",
            f"invalid value {status!r}",
        )
    if method not in ALLOWED_PAYMENT_METHODS:
        _issue(
            issues,
            request_id,
            "invalid_payment_method",
            "recommended_payment_method",
            f"invalid value {method!r}",
        )

    requested_amount = parse_decimal(request["requested_amount"], allow_blank=False)
    assert requested_amount is not None
    safe_amount = parse_money(
        prediction.get("amount_safe_to_pay", ""),
        request_id=request_id,
        field="amount_safe_to_pay",
        issues=issues,
    )
    if safe_amount is not None and not Decimal("0") <= safe_amount <= requested_amount:
        _issue(
            issues,
            request_id,
            "safe_amount_out_of_bounds",
            "amount_safe_to_pay",
            f"must be between 0 and {requested_amount}",
            stage="capacity",
        )

    request_date = parse_iso_date(request["request_date"], allow_blank=False)
    deadline = parse_iso_date(request["desired_completion_date"], allow_blank=False)
    assert request_date is not None and deadline is not None
    horizon_end = request_date + timedelta(days=context.forecast_horizon_days)
    earliest_text = prediction.get("earliest_date_for_full_payment", "")
    earliest: date | None = None
    if earliest_text:
        try:
            earliest = parse_iso_date(earliest_text, allow_blank=False)
        except ValueError as exc:
            _issue(
                issues,
                request_id,
                "invalid_earliest_date",
                "earliest_date_for_full_payment",
                str(exc),
            )
        if earliest is not None and earliest < request_date:
            _issue(
                issues,
                request_id,
                "earliest_date_before_request",
                "earliest_date_for_full_payment",
                "date cannot precede request_date",
                stage="capacity",
            )
        if earliest is not None and earliest > horizon_end:
            _issue(
                issues,
                request_id,
                "earliest_date_after_horizon",
                "earliest_date_for_full_payment",
                f"date exceeds {context.forecast_horizon_days}-day horizon",
                stage="capacity",
            )

    schedule, schedule_issues = parse_payment_plan(
        prediction.get("payment_plan", ""), request_id
    )
    issues.extend(schedule_issues)
    actions, action_issues = parse_spending_changes(
        prediction.get("spending_changes_needed", ""), request, profile, context
    )
    issues.extend(action_issues)

    if schedule:
        if schedule[0].payment_date < request_date:
            _issue(
                issues,
                request_id,
                "payment_before_request",
                "payment_plan",
                "payment cannot precede request_date",
            )
        if schedule[-1].payment_date > deadline:
            _issue(
                issues,
                request_id,
                "plan_after_deadline",
                "payment_plan",
                "last payment exceeds desired_completion_date",
                stage="candidate eligibility",
            )
        if any(entry.payment_date > horizon_end for entry in schedule):
            _issue(
                issues,
                request_id,
                "payment_after_horizon",
                "payment_plan",
                f"payment exceeds {context.forecast_horizon_days}-day horizon",
                stage="candidate eligibility",
            )

    expected_status_by_method = {
        "partial_payment": "affordable_with_plan",
        "installments": "affordable_with_plan",
        "wait": "affordable_later",
        "not_recommended": "not_affordable",
    }
    if method in expected_status_by_method and status != expected_status_by_method[method]:
        _issue(
            issues,
            request_id,
            "status_method_mismatch",
            "affordability_status",
            f"{method} requires {expected_status_by_method[method]}",
            stage="candidate eligibility",
        )

    preferences = split_pipe(profile["payment_methods_user_will_consider"])
    required_preference = {
        "full_payment": "full_payment",
        "partial_payment": "partial_payment",
        "installments": "installments",
        "wait": "full_payment",
    }.get(method)
    if required_preference and required_preference not in preferences:
        _issue(
            issues,
            request_id,
            "payment_method_not_allowed",
            "recommended_payment_method",
            f"profile does not permit {required_preference}",
            stage="candidate eligibility",
        )

    changes_text = prediction.get("spending_changes_needed", "")
    if method == "full_payment":
        expected_full_status = (
            "affordable_now" if changes_text == "none" else "affordable_with_plan"
        )
        if status != expected_full_status:
            _issue(
                issues,
                request_id,
                "full_payment_status_mismatch",
                "affordability_status",
                f"full payment with this spending-change state requires {expected_full_status}",
                stage="candidate eligibility",
            )
        if len(schedule) != 1:
            _issue(
                issues,
                request_id,
                "full_payment_shape",
                "payment_plan",
                "full payment requires exactly one payment",
            )
        elif (
            schedule[0].payment_date != request_date
            or schedule[0].amount != requested_amount
        ):
            _issue(
                issues,
                request_id,
                "full_payment_shape",
                "payment_plan",
                "full payment must pay the requested amount on request_date",
            )
        if status == "affordable_now":
            if safe_amount is not None and safe_amount != requested_amount:
                _issue(
                    issues,
                    request_id,
                    "affordable_now_capacity_mismatch",
                    "amount_safe_to_pay",
                    "affordable_now requires the full requested amount to be safe",
                    stage="capacity",
                )
            if earliest != request_date:
                _issue(
                    issues,
                    request_id,
                    "affordable_now_date_mismatch",
                    "earliest_date_for_full_payment",
                    "affordable_now requires request_date",
                    stage="capacity",
                )

    elif method == "partial_payment":
        if request["allows_partial_payment"] != "true":
            _issue(
                issues,
                request_id,
                "partial_payment_not_allowed",
                "recommended_payment_method",
                "request does not allow partial payment",
                stage="candidate eligibility",
            )
        if safe_amount is not None and not Decimal("0") < safe_amount < requested_amount:
            _issue(
                issues,
                request_id,
                "partial_safe_amount_boundary",
                "amount_safe_to_pay",
                "partial payment requires 0 < safe amount < requested amount",
                stage="capacity",
            )
        if earliest is None:
            _issue(
                issues,
                request_id,
                "partial_missing_second_date",
                "earliest_date_for_full_payment",
                "partial payment requires a nonblank second-payment date",
            )
        elif earliest <= request_date or earliest > deadline:
            _issue(
                issues,
                request_id,
                "partial_second_date_boundary",
                "earliest_date_for_full_payment",
                "second payment must be after request_date and by the deadline",
                stage="candidate eligibility",
            )
        if len(schedule) != 2:
            _issue(
                issues,
                request_id,
                "partial_payment_shape",
                "payment_plan",
                "partial payment requires exactly two payments",
            )
        elif safe_amount is not None and earliest is not None:
            remainder = requested_amount - safe_amount
            if (
                schedule[0] != PaymentEntry(request_date, safe_amount)
                or schedule[1] != PaymentEntry(earliest, remainder)
                or sum((entry.amount for entry in schedule), Decimal("0"))
                != requested_amount
            ):
                _issue(
                    issues,
                    request_id,
                    "partial_payment_sum_or_schedule",
                    "payment_plan",
                    "payments must be safe-now on request_date plus the exact remainder on earliest date",
                )

    elif method == "installments":
        if not schedule:
            _issue(
                issues,
                request_id,
                "installment_plan_required",
                "payment_plan",
                "installments require a supplied schedule",
            )
        matching_options: list[Mapping[str, str]] = []
        for option in context.options_by_request.get(request_id, ()):
            if option["payment_method"] != "installments":
                continue
            expected_schedule = _expected_installment_schedule(option, request_id)
            if expected_schedule == schedule:
                matching_options.append(option)
        if not matching_options:
            _issue(
                issues,
                request_id,
                "installment_option_mismatch",
                "payment_plan",
                "schedule does not exactly match any supplied installment option",
                stage="candidate eligibility",
            )
        else:
            max_months_text = profile["max_installment_months"]
            if not max_months_text:
                _issue(
                    issues,
                    request_id,
                    "installments_disallowed_by_limit",
                    "payment_plan",
                    "profile has no installment-month allowance",
                    stage="candidate eligibility",
                )
            else:
                max_months = int(max_months_text)
                if all(int(option["number_of_payments"]) > max_months for option in matching_options):
                    _issue(
                        issues,
                        request_id,
                        "installment_month_limit",
                        "payment_plan",
                        f"payment count exceeds max_installment_months={max_months}",
                        stage="candidate eligibility",
                    )

    elif method == "wait":
        if len(schedule) != 1:
            _issue(
                issues,
                request_id,
                "wait_payment_shape",
                "payment_plan",
                "wait requires exactly one future full payment",
            )
        if earliest is None:
            _issue(
                issues,
                request_id,
                "wait_missing_date",
                "earliest_date_for_full_payment",
                "wait requires a nonblank earliest date",
            )
        elif earliest <= request_date or earliest > deadline:
            _issue(
                issues,
                request_id,
                "wait_date_boundary",
                "earliest_date_for_full_payment",
                "wait date must be after request_date and by the deadline",
                stage="candidate eligibility",
            )
        if len(schedule) == 1 and (
            earliest is None
            or schedule[0] != PaymentEntry(earliest, requested_amount)
        ):
            _issue(
                issues,
                request_id,
                "wait_payment_shape",
                "payment_plan",
                "wait plan must pay the requested amount on the earliest date",
            )
        if changes_text != "none":
            _issue(
                issues,
                request_id,
                "wait_with_spending_changes",
                "spending_changes_needed",
                "wait capacity is defined without optional spending changes",
                stage="candidate eligibility",
            )

    elif method == "not_recommended":
        if prediction.get("payment_plan", "") != "none":
            _issue(
                issues,
                request_id,
                "not_recommended_plan",
                "payment_plan",
                "not_recommended requires payment_plan=none",
            )
        if changes_text != "none":
            _issue(
                issues,
                request_id,
                "not_recommended_spending_changes",
                "spending_changes_needed",
                "not_recommended requires spending_changes_needed=none",
            )

    if method not in {"not_recommended", ""} and not schedule:
        _issue(
            issues,
            request_id,
            "recommended_plan_missing",
            "payment_plan",
            "a recommended method requires a payment schedule",
        )

    issues.extend(_validate_explanation(prediction, request_id))

    feasibility_status = "not_run"
    if feasibility_validator is not None:
        feasibility_status = "passed"
        feasibility_issues = list(
            feasibility_validator(request, prediction, schedule, actions, context)
        )
        if feasibility_issues:
            feasibility_status = "failed"
            issues.extend(feasibility_issues)

    return RowValidation(
        request_id,
        tuple(issues),
        schedule,
        actions,
        feasibility_status,
    )


def write_prediction_rows(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    """Test/evaluation helper; production output writing belongs in a later module."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({column: row.get(column, "") for column in OUTPUT_COLUMNS} for row in rows)
