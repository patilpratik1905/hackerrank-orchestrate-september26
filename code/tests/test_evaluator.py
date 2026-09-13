from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from evaluation.data_audit import read_csv_table  # noqa: E402
from evaluation.main import (  # noqa: E402
    evaluate_file,
    project_expected_output,
    score_predictions,
)
from evaluation.validators import (  # noqa: E402
    OUTPUT_COLUMNS,
    ValidationContext,
    ValidationIssue,
    load_prediction_file,
    load_validation_context,
    parse_money,
    validate_prediction_row,
    write_prediction_rows,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"


class EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _, cls.samples = read_csv_table(DATASET_DIR / "sample_requests.csv")
        cls.sample_by_id = {row["request_id"]: row for row in cls.samples}
        cls.context = load_validation_context(DATASET_DIR, cls.samples)

    def prediction(self, request_id: str) -> dict[str, str]:
        return project_expected_output(self.sample_by_id[request_id])

    def issue_codes(
        self,
        prediction: dict[str, str],
        context: ValidationContext | None = None,
    ) -> set[str]:
        result = validate_prediction_row(prediction, context or self.context)
        return {issue.code for issue in result.issues}

    def assert_rejected(
        self,
        prediction: dict[str, str],
        expected_code: str,
        context: ValidationContext | None = None,
    ) -> None:
        self.assertIn(expected_code, self.issue_codes(prediction, context))

    def test_all_solved_rows_pass_applicable_structural_validation(self) -> None:
        results = [
            validate_prediction_row(project_expected_output(row), self.context)
            for row in self.samples
        ]
        failures = {
            result.request_id: [issue.code for issue in result.issues]
            for result in results
            if not result.passed
        }
        self.assertEqual(failures, {})
        self.assertTrue(all(result.feasibility_status == "not_run" for result in results))

    def test_reference_metrics_are_perfect_and_json_serializable(self) -> None:
        report = score_predictions(
            self.samples,
            [project_expected_output(row) for row in self.samples],
            [],
            self.context,
        )
        self.assertTrue(report["summary"]["structurally_valid"])
        metrics = report["metrics"]
        self.assertEqual(
            metrics["complete_structured_row_exact_match"]["accuracy"], 1.0
        )
        self.assertEqual(metrics["validator_pass_rate"]["accuracy"], 1.0)
        json.dumps(report)

    def test_explanation_is_checked_for_consistency_not_exact_prose(self) -> None:
        expected = self.sample_by_id["request_01"]
        row = self.prediction("request_01")
        row["decision_explanation"] = (
            "Pay the full amount today while keeping the required minimum protected."
        )
        report = score_predictions([expected], [row], [], self.context)
        self.assertEqual(
            report["metrics"]["decision_explanation_consistency"]["accuracy"],
            1.0,
        )
        self.assertEqual(report["mismatches"], [])

    def test_plan_string_metric_is_strict_while_validity_is_semantic(self) -> None:
        expected = self.sample_by_id["request_01"]
        row = self.prediction("request_01")
        row["amount_safe_to_pay"] = "25256.00"
        row["payment_plan"] = "2024-03-03:25256.00"
        report = score_predictions([expected], [row], [], self.context)
        metrics = report["metrics"]
        self.assertEqual(metrics["amount_safe_to_pay"]["exact_to_cent"]["accuracy"], 1.0)
        self.assertEqual(metrics["payment_plan"]["exact_string"]["accuracy"], 0.0)
        self.assertEqual(
            metrics["payment_plan"]["parsed_schedule_validity"]["accuracy"], 1.0
        )
        self.assertEqual(metrics["validator_pass_rate"]["accuracy"], 1.0)

    def test_evaluator_uses_only_the_25_solved_samples_as_labels(self) -> None:
        report = evaluate_file(DATASET_DIR, None, reference_check=True)
        self.assertEqual(report["summary"]["expected_rows"], 25)
        self.assertNotIn("request_26", self.context.requests_by_id)
        self.assertEqual(set(self.context.requests_by_id), set(self.sample_by_id))

    def test_valid_prediction_csv_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "predictions.csv"
            rows = [project_expected_output(row) for row in self.samples]
            write_prediction_rows(path, rows)
            loaded, issues, headers = load_prediction_file(
                path, set(self.sample_by_id)
            )
        self.assertEqual(headers, OUTPUT_COLUMNS)
        self.assertEqual(issues, [])
        self.assertEqual(loaded, rows)

    def test_file_contract_rejects_duplicate_missing_and_unexpected_ids(self) -> None:
        rows = [project_expected_output(row) for row in self.samples]
        rows.pop()
        duplicate = dict(rows[0])
        rows.append(duplicate)
        unexpected = dict(rows[1])
        unexpected["request_id"] = "request_999"
        rows.append(unexpected)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.csv"
            write_prediction_rows(path, rows)
            _, issues, _ = load_prediction_file(path, set(self.sample_by_id))
        codes = {issue.code for issue in issues}
        self.assertTrue(
            {"duplicate_request_id", "missing_request_id", "unexpected_request_id"}
            <= codes
        )

    def test_file_contract_rejects_wrong_column_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad_headers.csv"
            wrong_headers = (OUTPUT_COLUMNS[1], OUTPUT_COLUMNS[0], *OUTPUT_COLUMNS[2:])
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=wrong_headers)
                writer.writeheader()
            _, issues, _ = load_prediction_file(path, set())
        self.assertIn("output_columns", {issue.code for issue in issues})

    def test_decimal_boundaries_and_precision(self) -> None:
        issues: list[ValidationIssue] = []
        self.assertEqual(
            parse_money(
                "0", request_id="r", field="amount", issues=issues
            ),
            Decimal("0"),
        )
        self.assertEqual(
            parse_money(
                "10.99", request_id="r", field="amount", issues=issues
            ),
            Decimal("10.99"),
        )
        self.assertEqual(issues, [])
        for value in ("", "-1", "1.001", "NaN", "1,000"):
            with self.subTest(value=value):
                invalid: list[ValidationIssue] = []
                self.assertIsNone(
                    parse_money(
                        value,
                        request_id="r",
                        field="amount",
                        issues=invalid,
                    )
                )
                self.assertEqual(invalid[0].code, "invalid_decimal")

    def test_amount_status_and_date_corruptions_are_rejected(self) -> None:
        row = self.prediction("request_01")
        row["amount_safe_to_pay"] = "99999999.99"
        self.assert_rejected(row, "safe_amount_out_of_bounds")

        row = self.prediction("request_01")
        row["affordability_status"] = "maybe"
        self.assert_rejected(row, "invalid_affordability_status")

        row = self.prediction("request_01")
        row["recommended_payment_method"] = "credit_card"
        self.assert_rejected(row, "invalid_payment_method")

        row = self.prediction("request_01")
        row["earliest_date_for_full_payment"] = "03-03-2024"
        self.assert_rejected(row, "invalid_earliest_date")

    def test_blank_earliest_date_is_valid_when_no_safe_date_exists(self) -> None:
        result = validate_prediction_row(self.prediction("request_05"), self.context)
        self.assertTrue(result.passed)

    def test_nonchronological_schedule_is_rejected(self) -> None:
        row = self.prediction("request_02")
        entries = row["payment_plan"].split("|")
        row["payment_plan"] = "|".join(reversed(entries))
        self.assert_rejected(row, "nonchronological_payment_plan")

    def test_full_payment_shape_is_rejected(self) -> None:
        row = self.prediction("request_01")
        row["payment_plan"] = "2024-03-03:25255.99"
        self.assert_rejected(row, "full_payment_shape")

    def test_partial_payment_sum_and_schedule_are_rejected(self) -> None:
        row = self.prediction("request_19")
        row["payment_plan"] = "2024-09-04:28820|2024-09-15:10839.99"
        self.assert_rejected(row, "partial_payment_sum_or_schedule")

    def test_partial_payment_flag_is_enforced(self) -> None:
        row = self.prediction("request_19")
        requests = dict(self.context.requests_by_id)
        requests["request_19"] = {
            **requests["request_19"],
            "allows_partial_payment": "false",
        }
        context = replace(self.context, requests_by_id=requests)
        self.assert_rejected(row, "partial_payment_not_allowed", context)

    def test_installment_must_match_supplied_option(self) -> None:
        row = self.prediction("request_02")
        row["payment_plan"] = row["payment_plan"].replace("15952906.67", "15952906.66", 1)
        self.assert_rejected(row, "installment_option_mismatch")

    def test_installment_month_limit_boundary(self) -> None:
        row = self.prediction("request_02")
        profiles = dict(self.context.profiles_by_user)
        user_id = self.sample_by_id["request_02"]["user_id"]

        profiles_at_boundary = dict(profiles)
        profiles_at_boundary[user_id] = {
            **profiles[user_id],
            "max_installment_months": "3",
        }
        passing = validate_prediction_row(
            row, replace(self.context, profiles_by_user=profiles_at_boundary)
        )
        self.assertNotIn("installment_month_limit", {i.code for i in passing.issues})

        profiles_below_boundary = dict(profiles)
        profiles_below_boundary[user_id] = {
            **profiles[user_id],
            "max_installment_months": "2",
        }
        self.assert_rejected(
            row,
            "installment_month_limit",
            replace(self.context, profiles_by_user=profiles_below_boundary),
        )

    def test_payment_preference_is_enforced(self) -> None:
        row = self.prediction("request_12")
        row.update(
            {
                "affordability_status": "affordable_now",
                "recommended_payment_method": "full_payment",
                "payment_plan": "2026-04-05:65164",
                "spending_changes_needed": "none",
                "decision_explanation": "Pay ZAR 65,164 today.",
            }
        )
        self.assert_rejected(row, "payment_method_not_allowed")

    def test_not_recommended_requires_no_plan(self) -> None:
        row = self.prediction("request_05")
        row["payment_plan"] = "2025-11-06:100"
        self.assert_rejected(row, "not_recommended_plan")

    def test_deadline_is_enforced(self) -> None:
        row = self.prediction("request_03")
        row["earliest_date_for_full_payment"] = "2019-11-16"
        row["payment_plan"] = "2019-11-16:5491000"
        self.assert_rejected(row, "plan_after_deadline")
        self.assert_rejected(row, "wait_date_boundary")

    def test_spending_grammar_limit_duplicate_and_existence(self) -> None:
        row = self.prediction("request_06")
        row["spending_changes_needed"] = "delete:event_476"
        self.assert_rejected(row, "invalid_spending_change_grammar")

        row = self.prediction("request_06")
        row["spending_changes_needed"] = "stop:event_476|stop:event_476"
        self.assert_rejected(row, "duplicate_spending_event")

        row = self.prediction("request_06")
        row["spending_changes_needed"] = (
            "stop:event_476|stop:event_476|stop:event_476|stop:event_476"
        )
        self.assert_rejected(row, "too_many_spending_changes")

        row = self.prediction("request_06")
        row["spending_changes_needed"] = "stop:event_missing"
        self.assert_rejected(row, "unknown_spending_event")

    def test_spending_flexibility_permissions_and_minimum_are_enforced(self) -> None:
        row = self.prediction("request_11")
        row["spending_changes_needed"] = "reduce_to:event_989:665949.99"
        self.assert_rejected(row, "reduction_below_minimum")

        row = self.prediction("request_06")
        user_id = self.sample_by_id["request_06"]["user_id"]
        profiles = dict(self.context.profiles_by_user)
        profiles[user_id] = {
            **profiles[user_id],
            "expense_categories_user_is_willing_to_stop": "",
        }
        self.assert_rejected(
            row,
            "category_not_stoppable",
            replace(self.context, profiles_by_user=profiles),
        )

        events = dict(self.context.events_by_id)
        events["event_476"] = {**events["event_476"], "flexibility": "fixed"}
        self.assert_rejected(
            row,
            "event_not_stoppable",
            replace(self.context, events_by_id=events),
        )

        profiles = dict(self.context.profiles_by_user)
        profiles[user_id] = {
            **profiles[user_id],
            "expense_categories_to_protect": "streaming",
        }
        self.assert_rejected(
            row,
            "protected_spending_change",
            replace(self.context, profiles_by_user=profiles),
        )

    def test_explanation_empty_and_contradictory_are_rejected(self) -> None:
        row = self.prediction("request_01")
        row["decision_explanation"] = ""
        self.assert_rejected(row, "empty_explanation")

        row = self.prediction("request_01")
        row["decision_explanation"] = "Do not proceed; no safe option exists."
        self.assert_rejected(row, "explanation_contradiction")

        row = self.prediction("request_05")
        row["decision_explanation"] = "Pay in full today."
        self.assert_rejected(row, "explanation_missing_not_affordable_reason")

    def test_simulator_callback_is_explicit_and_not_faked(self) -> None:
        row = self.prediction("request_01")

        def reject_feasibility(*_args):
            return [
                ValidationIssue(
                    "simulator_unsafe",
                    "payment_plan",
                    "fixture simulator rejection",
                    "request_01",
                    "simulation",
                )
            ]

        without_simulator = validate_prediction_row(row, self.context)
        with_simulator = validate_prediction_row(
            row, self.context, feasibility_validator=reject_feasibility
        )
        self.assertEqual(without_simulator.feasibility_status, "not_run")
        self.assertEqual(with_simulator.feasibility_status, "failed")
        self.assertIn("simulator_unsafe", {issue.code for issue in with_simulator.issues})


if __name__ == "__main__":
    unittest.main()
