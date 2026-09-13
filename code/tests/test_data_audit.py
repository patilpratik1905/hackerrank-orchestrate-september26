from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from evaluation.data_audit import (  # noqa: E402
    duplicate_keys,
    missing_foreign_keys,
    parse_decimal,
    parse_iso_date,
    run_audit,
)
from tests.fixtures import scenario_catalog  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"


class AuditHelperTests(unittest.TestCase):
    def test_duplicate_keys_detected(self) -> None:
        rows = [{"id": "one"}, {"id": "two"}, {"id": "one"}]
        self.assertEqual(duplicate_keys(rows, ("id",)), [("one",)])

    def test_missing_relationship_detected(self) -> None:
        rows = [{"user_id": "known"}, {"user_id": "missing"}]
        self.assertEqual(
            missing_foreign_keys(rows, ("user_id",), {("known",)}),
            [("missing",)],
        )

    def test_optional_blank_relationship_is_not_an_error(self) -> None:
        rows = [{"request_id": ""}]
        self.assertEqual(
            missing_foreign_keys(rows, ("request_id",), set(), optional=True), []
        )

    def test_blank_decimal_is_none_not_zero(self) -> None:
        self.assertIsNone(parse_decimal("", allow_blank=True))
        self.assertNotEqual(parse_decimal("0", allow_blank=True), None)
        with self.assertRaises(ValueError):
            parse_decimal("", allow_blank=False)

    def test_decimal_precision_is_exact(self) -> None:
        self.assertEqual(
            parse_decimal("0.1", allow_blank=False)
            + parse_decimal("0.2", allow_blank=False),
            Decimal("0.3"),
        )

    def test_date_parsing_preserves_iso_date(self) -> None:
        self.assertEqual(parse_iso_date("2026-09-13", allow_blank=False), date(2026, 9, 13))
        self.assertIsNone(parse_iso_date("", allow_blank=True))


class FixtureTests(unittest.TestCase):
    def test_all_required_fixture_patterns_exist(self) -> None:
        expected = {
            "affordable_full_payment",
            "safe_installments",
            "wait_for_salary",
            "not_affordable",
            "valid_partial_payment",
            "flexible_spending",
            "blank_amount_image",
            "pending_debit",
            "pending_credit_refund",
            "cancelled_and_failed",
            "unrealized_investment",
            "linked_lifecycle",
            "foreign_currency",
            "refuses_full_payment",
        }
        self.assertEqual(set(scenario_catalog()), expected)

    def test_fixture_money_strings_preserve_decimal_precision(self) -> None:
        money_fields = {
            "current_available_balance",
            "minimum_balance_to_keep",
            "requested_amount",
            "amount",
            "minimum_allowed_amount",
            "payment_amount",
            "financing_fee",
            "total_payable_amount",
            "rate",
        }
        for scenario_name, scenario in scenario_catalog().items():
            records = []
            for value in scenario.values():
                if isinstance(value, dict):
                    records.append(value)
                elif isinstance(value, list):
                    records.extend(item for item in value if isinstance(item, dict))
            for record in records:
                for field in money_fields.intersection(record):
                    if record[field] != "":
                        with self.subTest(scenario=scenario_name, field=field):
                            self.assertIsInstance(Decimal(record[field]), Decimal)

    def test_fixture_dates_are_independently_parseable(self) -> None:
        date_fields = {
            "request_date",
            "desired_completion_date",
            "event_date",
            "settlement_date",
            "first_payment_date",
            "rate_date",
        }
        for scenario_name, scenario in scenario_catalog().items():
            records = []
            for value in scenario.values():
                if isinstance(value, dict):
                    records.append(value)
                elif isinstance(value, list):
                    records.extend(item for item in value if isinstance(item, dict))
            for record in records:
                for field in date_fields.intersection(record):
                    if record[field] != "":
                        with self.subTest(scenario=scenario_name, field=field):
                            self.assertIsInstance(date.fromisoformat(record[field]), date)


class RealDatasetAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_audit(DATASET_DIR)

    def test_real_dataset_has_no_audit_errors(self) -> None:
        errors = [
            issue for issue in self.result["issues"] if issue["severity"] == "error"
        ]
        self.assertEqual(errors, [])

    def test_required_relationships_are_complete(self) -> None:
        self.assertTrue(
            all(count == 0 for count in self.result["relationship_checks"].values())
        )

    def test_blank_amounts_have_exactly_one_image(self) -> None:
        self.assertEqual(self.result["blank_amount_events"], 16)
        self.assertEqual(self.result["blank_amount_events_without_image"], 0)
        self.assertEqual(self.result["blank_amount_events_with_non_unique_image"], 0)

    def test_all_foreign_cash_events_have_exact_rate(self) -> None:
        self.assertGreater(self.result["foreign_cash_events"], 0)
        self.assertEqual(self.result["foreign_cash_events_missing_rate"], 0)

    def test_all_linked_images_are_valid_png_files(self) -> None:
        inventory = self.result["png_inventory"]
        self.assertEqual(len(inventory), 16)
        self.assertTrue(all(item["exists"] for item in inventory))


if __name__ == "__main__":
    unittest.main()

