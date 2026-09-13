"""Score Buy or Wait? predictions against the 25 solved samples.

Examples from the repository root:

    python code/evaluation/main.py --predictions path/to/sample_predictions.csv
    python code/evaluation/main.py --reference-check \
        --json-output code/evaluation/sample_evaluation.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Sequence


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from evaluation.data_audit import read_csv_table  # noqa: E402
from evaluation.validators import (  # noqa: E402
    OUTPUT_COLUMNS,
    RowValidation,
    ValidationContext,
    ValidationIssue,
    load_prediction_file,
    load_validation_context,
    parse_payment_plan,
    validate_prediction_row,
)


STRUCTURED_FIELDS = (
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)


def project_expected_output(row: Mapping[str, str]) -> dict[str, str]:
    return {column: row[column] for column in OUTPUT_COLUMNS}


def _decimal_or_none(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _date_or_none(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _metric_count(matches: Sequence[bool]) -> dict[str, int | float]:
    passed = sum(matches)
    return {
        "matched": passed,
        "total": len(matches),
        "accuracy": _rate(passed, len(matches)),
    }


def score_predictions(
    expected_rows: Sequence[Mapping[str, str]],
    prediction_rows: Sequence[Mapping[str, str]],
    file_issues: Sequence[ValidationIssue],
    context: ValidationContext,
) -> dict[str, object]:
    """Return deterministic metrics and validation findings."""

    expected_by_id = {
        row["request_id"]: project_expected_output(row) for row in expected_rows
    }
    predictions_by_id: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in prediction_rows:
        predictions_by_id[row.get("request_id", "")].append(row)

    amount_matches: list[bool] = []
    amount_errors: list[Decimal] = []
    status_matches: list[bool] = []
    method_matches: list[bool] = []
    plan_matches: list[bool] = []
    plan_parse_validity: list[bool] = []
    earliest_matches: list[bool] = []
    earliest_presence_matches: list[bool] = []
    earliest_day_errors: list[int] = []
    spending_matches: list[bool] = []
    spending_structure_validity: list[bool] = []
    explanation_consistency: list[bool] = []
    complete_structured_matches: list[bool] = []
    validator_matches: list[bool] = []
    mismatches: list[dict[str, str]] = []
    validations: list[RowValidation] = []

    file_issues_by_request: dict[str, list[ValidationIssue]] = defaultdict(list)
    global_file_issues: list[ValidationIssue] = []
    for issue in file_issues:
        if issue.request_id:
            file_issues_by_request[issue.request_id].append(issue)
        else:
            global_file_issues.append(issue)

    for request_id in sorted(expected_by_id):
        expected = expected_by_id[request_id]
        candidates = predictions_by_id.get(request_id, [])
        if not candidates:
            missing_issue = ValidationIssue(
                "missing_request_id",
                "request_id",
                "expected request has no prediction row",
                request_id,
            )
            validations.append(
                RowValidation(request_id, (missing_issue,), (), (), "not_run")
            )
            amount_matches.append(False)
            status_matches.append(False)
            method_matches.append(False)
            plan_matches.append(False)
            plan_parse_validity.append(False)
            earliest_matches.append(False)
            earliest_presence_matches.append(False)
            spending_matches.append(False)
            spending_structure_validity.append(False)
            explanation_consistency.append(False)
            complete_structured_matches.append(False)
            validator_matches.append(False)
            for field in STRUCTURED_FIELDS:
                mismatches.append(
                    {
                        "request_id": request_id,
                        "field": field,
                        "expected": expected[field],
                        "actual": "<missing row>",
                    }
                )
            continue

        actual = candidates[0]
        row_validation = validate_prediction_row(actual, context)
        combined_issues = tuple(
            [*file_issues_by_request.get(request_id, []), *row_validation.issues]
        )
        if combined_issues != row_validation.issues:
            row_validation = RowValidation(
                request_id,
                combined_issues,
                row_validation.payment_schedule,
                row_validation.spending_actions,
                row_validation.feasibility_status,
            )
        validations.append(row_validation)

        expected_amount = _decimal_or_none(expected["amount_safe_to_pay"])
        actual_amount = _decimal_or_none(actual.get("amount_safe_to_pay", ""))
        amount_match = (
            expected_amount is not None
            and actual_amount is not None
            and expected_amount == actual_amount
        )
        amount_matches.append(amount_match)
        if expected_amount is not None and actual_amount is not None:
            amount_errors.append(abs(actual_amount - expected_amount))

        row_field_matches: dict[str, bool] = {
            "amount_safe_to_pay": amount_match,
        }
        for field, collector in (
            ("affordability_status", status_matches),
            ("recommended_payment_method", method_matches),
            ("payment_plan", plan_matches),
            ("earliest_date_for_full_payment", earliest_matches),
            ("spending_changes_needed", spending_matches),
        ):
            matches = actual.get(field, "") == expected[field]
            collector.append(matches)
            row_field_matches[field] = matches

        for field in STRUCTURED_FIELDS:
            if not row_field_matches[field]:
                mismatches.append(
                    {
                        "request_id": request_id,
                        "field": field,
                        "expected": expected[field],
                        "actual": actual.get(field, ""),
                    }
                )

        _, plan_parse_issues = parse_payment_plan(
            actual.get("payment_plan", ""), request_id
        )
        plan_parse_validity.append(not plan_parse_issues)

        expected_earliest = expected["earliest_date_for_full_payment"]
        actual_earliest = actual.get("earliest_date_for_full_payment", "")
        earliest_presence_matches.append(bool(expected_earliest) == bool(actual_earliest))
        expected_date = _date_or_none(expected_earliest)
        actual_date = _date_or_none(actual_earliest)
        if expected_date is not None and actual_date is not None:
            earliest_day_errors.append(abs((actual_date - expected_date).days))

        issue_fields = {issue.field for issue in row_validation.issues}
        spending_structure_validity.append("spending_changes_needed" not in issue_fields)
        explanation_consistency.append("decision_explanation" not in issue_fields)
        complete_structured_matches.append(all(row_field_matches.values()))
        validator_matches.append(row_validation.passed)

    amount_error_summary: dict[str, object] = {
        "comparable_rows": len(amount_errors),
        "mean_absolute_error": None,
        "median_absolute_error": None,
        "maximum_absolute_error": None,
    }
    if amount_errors:
        amount_error_summary.update(
            {
                "mean_absolute_error": str(
                    sum(amount_errors, Decimal("0")) / Decimal(len(amount_errors))
                ),
                "median_absolute_error": str(statistics.median(amount_errors)),
                "maximum_absolute_error": str(max(amount_errors)),
            }
        )

    row_issue_count = sum(len(validation.issues) for validation in validations)
    return {
        "summary": {
            "expected_rows": len(expected_rows),
            "prediction_rows": len(prediction_rows),
            "file_valid": not file_issues,
            "structurally_valid": not file_issues and row_issue_count == 0,
            "feasibility_validation": "not_run",
        },
        "metrics": {
            "amount_safe_to_pay": {
                "exact_to_cent": _metric_count(amount_matches),
                **amount_error_summary,
            },
            "affordability_status": _metric_count(status_matches),
            "recommended_payment_method": _metric_count(method_matches),
            "payment_plan": {
                "exact_string": _metric_count(plan_matches),
                "parsed_schedule_validity": _metric_count(plan_parse_validity),
            },
            "earliest_date_for_full_payment": {
                "exact_match": _metric_count(earliest_matches),
                "blank_nonblank": _metric_count(earliest_presence_matches),
                "both_dates_present": len(earliest_day_errors),
                "mean_absolute_day_error": (
                    statistics.mean(earliest_day_errors)
                    if earliest_day_errors
                    else None
                ),
                "median_absolute_day_error": (
                    statistics.median(earliest_day_errors)
                    if earliest_day_errors
                    else None
                ),
                "maximum_absolute_day_error": (
                    max(earliest_day_errors) if earliest_day_errors else None
                ),
            },
            "spending_changes_needed": {
                "exact_match": _metric_count(spending_matches),
                "structural_validity": _metric_count(spending_structure_validity),
            },
            "decision_explanation_consistency": _metric_count(
                explanation_consistency
            ),
            "complete_structured_row_exact_match": _metric_count(
                complete_structured_matches
            ),
            "validator_pass_rate": _metric_count(validator_matches),
        },
        "file_issues": [issue.to_dict() for issue in file_issues],
        "global_file_issues": [issue.to_dict() for issue in global_file_issues],
        "validation_issue_count": row_issue_count,
        "row_validations": [validation.to_dict() for validation in validations],
        "mismatches": mismatches,
    }


def render_human_report(report: Mapping[str, object]) -> str:
    summary = report["summary"]
    metrics = report["metrics"]
    assert isinstance(summary, Mapping) and isinstance(metrics, Mapping)

    def accuracy(path: Sequence[str]) -> str:
        current: object = metrics
        for key in path:
            assert isinstance(current, Mapping)
            current = current[key]
        assert isinstance(current, Mapping)
        return f"{current['matched']}/{current['total']} ({current['accuracy']:.1%})"

    amount = metrics["amount_safe_to_pay"]
    earliest = metrics["earliest_date_for_full_payment"]
    assert isinstance(amount, Mapping) and isinstance(earliest, Mapping)
    lines = [
        "Buy or Wait? sample evaluation",
        f"Rows: {summary['prediction_rows']} predictions / {summary['expected_rows']} expected",
        f"File valid: {summary['file_valid']}",
        f"Structurally valid: {summary['structurally_valid']}",
        f"Simulator feasibility: {summary['feasibility_validation']}",
        "",
        f"amount_safe_to_pay exact-to-cent: {accuracy(('amount_safe_to_pay', 'exact_to_cent'))}",
        "amount_safe_to_pay error: "
        f"MAE={amount['mean_absolute_error']}, "
        f"median={amount['median_absolute_error']}, "
        f"max={amount['maximum_absolute_error']}",
        f"affordability_status exact: {accuracy(('affordability_status',))}",
        "recommended_payment_method exact: "
        f"{accuracy(('recommended_payment_method',))}",
        f"payment_plan exact: {accuracy(('payment_plan', 'exact_string'))}",
        "payment_plan parsed validity: "
        f"{accuracy(('payment_plan', 'parsed_schedule_validity'))}",
        "earliest_date exact: "
        f"{accuracy(('earliest_date_for_full_payment', 'exact_match'))}",
        "earliest_date blank/nonblank: "
        f"{accuracy(('earliest_date_for_full_payment', 'blank_nonblank'))}",
        "earliest_date day error when both present: "
        f"mean={earliest['mean_absolute_day_error']}, "
        f"median={earliest['median_absolute_day_error']}, "
        f"max={earliest['maximum_absolute_day_error']}",
        "spending_changes exact: "
        f"{accuracy(('spending_changes_needed', 'exact_match'))}",
        "spending_changes structural validity: "
        f"{accuracy(('spending_changes_needed', 'structural_validity'))}",
        "explanation consistency: "
        f"{accuracy(('decision_explanation_consistency',))}",
        "complete structured rows exact: "
        f"{accuracy(('complete_structured_row_exact_match',))}",
        f"validator pass rate: {accuracy(('validator_pass_rate',))}",
    ]

    file_issues = report["file_issues"]
    row_validations = report["row_validations"]
    mismatches = report["mismatches"]
    assert isinstance(file_issues, list)
    assert isinstance(row_validations, list)
    assert isinstance(mismatches, list)
    if file_issues:
        lines.extend(["", "File validation issues:"])
        for issue in file_issues:
            lines.append(
                f"- {issue['code']} [{issue.get('request_id') or 'file'}]: "
                f"{issue['message']}"
            )
    row_issues = [
        issue
        for validation in row_validations
        for issue in validation["issues"]
    ]
    if row_issues:
        lines.extend(["", "Row validation issues:"])
        for issue in row_issues:
            lines.append(
                f"- {issue['request_id']} {issue['field']} {issue['code']}: "
                f"{issue['message']}"
            )
    if mismatches:
        lines.extend(["", "Field mismatches:"])
        for mismatch in mismatches:
            lines.append(
                f"- {mismatch['request_id']} {mismatch['field']}: "
                f"expected={mismatch['expected']!r}, actual={mismatch['actual']!r}"
            )
    return "\n".join(lines)


def evaluate_file(
    dataset_dir: Path,
    prediction_path: Path | None,
    *,
    reference_check: bool = False,
) -> dict[str, object]:
    _, sample_rows = read_csv_table(dataset_dir / "sample_requests.csv")
    context = load_validation_context(dataset_dir, sample_rows)
    expected_ids = {row["request_id"] for row in sample_rows}
    if reference_check:
        prediction_rows = [project_expected_output(row) for row in sample_rows]
        file_issues: list[ValidationIssue] = []
    else:
        assert prediction_path is not None
        prediction_rows, file_issues, _ = load_prediction_file(
            prediction_path, expected_ids
        )
    return score_predictions(sample_rows, prediction_rows, file_issues, context)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--predictions", type=Path)
    source.add_argument(
        "--reference-check",
        action="store_true",
        help="Validate the solved labels themselves without creating a copied CSV",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the human report",
    )
    args = parser.parse_args()

    report = evaluate_file(
        args.dataset_dir,
        args.predictions,
        reference_check=args.reference_check,
    )
    rendered_json = json.dumps(report, indent=2, sort_keys=True)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered_json + "\n", encoding="utf-8", newline="\n")
    print(rendered_json if args.json else render_human_report(report))
    summary = report["summary"]
    assert isinstance(summary, Mapping)
    return 0 if summary["structurally_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
