from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterator


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.data import (  # noqa: E402
    DuplicateKeyError,
    FieldParseError,
    InvalidLifecycleLinkError,
    MissingEvidenceError,
    MissingRateError,
    MissingReferenceError,
    load_dataset,
)
from buy_or_wait.models import (  # noqa: E402
    Category,
    Currency,
    FinancialPriority,
    OutputTemplateRow,
    PaymentPreference,
    Request,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"


@contextmanager
def copied_dataset() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as temp_dir:
        target = Path(temp_dir) / "dataset"
        shutil.copytree(DATASET_DIR, target)
        yield target


def rewrite_csv(
    path: Path, mutate: Callable[[list[dict[str, str]]], None]
) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or ())
        rows = [dict(row) for row in reader]
    mutate(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class RealDatasetLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = load_dataset(DATASET_DIR)

    def test_all_participant_rows_load_with_exact_counts(self) -> None:
        self.assertEqual(
            self.repository.summary(),
            {
                "profiles": 275,
                "events": 25342,
                "exchange_rates": 134,
                "requests": 250,
                "sample_requests": 25,
                "payment_options": 790,
                "messages": 215,
                "images": 16,
                "sample_labels": 25,
                "output_template_rows": 250,
            },
        )

    def test_primary_and_relationship_indexes_have_expected_counts(self) -> None:
        repository = self.repository
        self.assertEqual(len(repository.profiles_by_user_id), 275)
        self.assertEqual(len(repository.events_by_event_id), 25342)
        self.assertEqual(len(repository.requests_by_request_id), 250)
        self.assertEqual(len(repository.sample_requests_by_request_id), 25)
        self.assertEqual(len(repository.all_requests_by_request_id), 275)
        self.assertEqual(len(repository.payment_options_by_payment_option_id), 790)
        self.assertEqual(sum(map(len, repository.requests_by_user_id.values())), 250)
        self.assertEqual(
            sum(map(len, repository.sample_requests_by_user_id.values())), 25
        )
        self.assertEqual(len(repository.messages_by_message_id), 215)
        self.assertEqual(len(repository.images_by_image_id), 16)
        self.assertEqual(len(repository.output_template_by_request_id), 250)
        self.assertEqual(sum(map(len, repository.payment_options_by_request_id.values())), 790)
        self.assertEqual(sum(map(len, repository.messages_by_related_event_id.values())), 39)
        self.assertEqual(sum(map(len, repository.images_by_related_event_id.values())), 16)
        self.assertEqual(len(repository.rates_by_key), 134)

    def test_decimal_blank_zero_and_source_provenance(self) -> None:
        profile = self.repository.profiles_by_user_id["user_01"]
        self.assertEqual(profile.current_available_balance, Decimal("58481.1"))
        self.assertIsInstance(profile.current_available_balance, Decimal)
        self.assertIsNone(self.repository.events_by_event_id["event_253"].amount)
        zero_fee = self.repository.payment_options_by_payment_option_id[
            "payment_option_01"
        ].financing_fee
        self.assertEqual(zero_fee, Decimal("0"))
        self.assertIsInstance(zero_fee, Decimal)
        self.assertEqual(profile.source.filename, "financial_profiles.csv")
        self.assertEqual(profile.source.row_number, 2)
        output_row = self.repository.output_template_by_request_id["request_26"]
        self.assertIsInstance(output_row, OutputTemplateRow)
        self.assertEqual(output_row.source.filename, "output.csv")

    def test_pipe_lists_are_immutable_typed_sets(self) -> None:
        profile = self.repository.profiles_by_user_id["user_01"]
        self.assertIsInstance(profile.financial_priorities, frozenset)
        self.assertEqual(
            profile.financial_priorities,
            frozenset(
                {FinancialPriority.EDUCATION, FinancialPriority.DEBT_REPAYMENT}
            ),
        )
        self.assertIn(Category.RENT, profile.protected_categories)
        self.assertEqual(
            profile.payment_preferences,
            frozenset({PaymentPreference.FULL_PAYMENT}),
        )

    def test_exact_directed_fx_lookup_and_conversion(self) -> None:
        rate_date = date(2023, 10, 15)
        rate = self.repository.get_rate(rate_date, Currency.EUR, Currency.ZAR)
        self.assertEqual(rate.rate, Decimal("20"))
        self.assertEqual(
            self.repository.convert(
                Decimal("10.25"), rate_date, Currency.EUR, Currency.ZAR
            ),
            Decimal("205.00"),
        )
        self.assertEqual(
            self.repository.convert(
                Decimal("10.25"), rate_date, Currency.EUR, Currency.EUR
            ),
            Decimal("10.25"),
        )
        with self.assertRaises(MissingRateError):
            self.repository.convert(
                Decimal("10"), date(1900, 1, 1), Currency.ZAR, Currency.EUR
            )

    def test_request_bundle_is_complete_and_uses_exact_rate_objects(self) -> None:
        bundle = self.repository.bundle_for("request_48")
        self.assertEqual(bundle.request.request_id, "request_48")
        self.assertEqual(bundle.profile.user_id, bundle.request.user_id)
        self.assertEqual(len(bundle.events), 129)
        self.assertEqual(len(bundle.payment_options), 4)
        self.assertEqual(len(bundle.messages), 1)
        self.assertEqual(len(bundle.images), 1)
        self.assertEqual(len(bundle.required_exchange_rates), 6)
        for rate in bundle.required_exchange_rates:
            self.assertIs(
                rate,
                self.repository.rates_by_key[
                    (rate.rate_date, rate.from_currency, rate.to_currency)
                ],
            )

    def test_sample_labels_are_separate_from_request_inputs(self) -> None:
        self.assertEqual(len(self.repository.sample_labels_by_request_id), 25)
        sample_request = self.repository.sample_requests_by_request_id["request_01"]
        self.assertIsInstance(sample_request, Request)
        self.assertFalse(hasattr(sample_request, "amount_safe_to_pay"))
        self.assertNotIn("request_01", self.repository.requests_by_request_id)
        self.assertNotIn("request_26", self.repository.sample_labels_by_request_id)


class MalformedDatasetTests(unittest.TestCase):
    def test_duplicate_primary_id_fails_clearly(self) -> None:
        with copied_dataset() as dataset:
            rewrite_csv(
                dataset / "financial_profiles.csv",
                lambda rows: rows.append(dict(rows[0])),
            )
            with self.assertRaisesRegex(DuplicateKeyError, "duplicate key 'user_01'"):
                load_dataset(dataset)

    def test_missing_required_join_fails_clearly(self) -> None:
        with copied_dataset() as dataset:
            def break_user(rows: list[dict[str, str]]) -> None:
                rows[0]["user_id"] = "missing_user"

            rewrite_csv(dataset / "requests.csv", break_user)
            with self.assertRaisesRegex(MissingReferenceError, "unresolved key"):
                load_dataset(dataset)

    def test_bad_linked_event_id_fails_clearly(self) -> None:
        with copied_dataset() as dataset:
            def break_link(rows: list[dict[str, str]]) -> None:
                rows[0]["linked_event_id"] = "event_missing"

            rewrite_csv(dataset / "financial_events.csv", break_link)
            with self.assertRaisesRegex(MissingReferenceError, "linked_event_id"):
                load_dataset(dataset)

    def test_forward_lifecycle_link_fails_clearly(self) -> None:
        with copied_dataset() as dataset:
            def link_forward(rows: list[dict[str, str]]) -> None:
                rows[0]["linked_event_id"] = "event_02"

            rewrite_csv(dataset / "financial_events.csv", link_forward)
            with self.assertRaisesRegex(
                InvalidLifecycleLinkError, "not an earlier source row"
            ):
                load_dataset(dataset)

    def test_bad_decimal_date_and_enum_fail_with_field_context(self) -> None:
        cases = (
            (
                "financial_profiles.csv",
                lambda rows: rows[0].__setitem__(
                    "current_available_balance", "not-a-number"
                ),
                "current_available_balance",
            ),
            (
                "requests.csv",
                lambda rows: rows[0].__setitem__("request_date", "2026/01/01"),
                "request_date",
            ),
            (
                "financial_events.csv",
                lambda rows: rows[0].__setitem__("status", "unknown"),
                "status",
            ),
        )
        for filename, mutate, column in cases:
            with self.subTest(column=column), copied_dataset() as dataset:
                rewrite_csv(dataset / filename, mutate)
                with self.assertRaisesRegex(FieldParseError, column):
                    load_dataset(dataset)

    def test_blank_amount_without_image_fails_clearly(self) -> None:
        with copied_dataset() as dataset:
            def remove_image(rows: list[dict[str, str]]) -> None:
                rows[:] = [row for row in rows if row["related_event_id"] != "event_253"]

            rewrite_csv(dataset / "images.csv", remove_image)
            with self.assertRaisesRegex(MissingEvidenceError, "event_253"):
                load_dataset(dataset)

    def test_missing_exact_rate_does_not_invert_or_interpolate(self) -> None:
        repository = load_dataset(DATASET_DIR)
        foreign_event = next(
            event
            for event in repository.events
            if event.direction.value != "non_cash"
            and event.currency
            is not repository.profiles_by_user_id[event.user_id].home_currency
        )
        profile = repository.profiles_by_user_id[foreign_event.user_id]
        key = (
            foreign_event.settlement_date,
            foreign_event.currency.value,
            profile.home_currency.value,
        )
        self.assertIsNotNone(key[0])

        with copied_dataset() as dataset:
            def remove_exact_rate(rows: list[dict[str, str]]) -> None:
                rows[:] = [
                    row
                    for row in rows
                    if (
                        row["rate_date"],
                        row["from_currency"],
                        row["to_currency"],
                    )
                    != (key[0].isoformat(), key[1], key[2])
                ]

            rewrite_csv(dataset / "exchange_rates.csv", remove_exact_rate)
            with self.assertRaises(MissingRateError) as caught:
                load_dataset(dataset)
            self.assertEqual(caught.exception.from_currency.value, key[1])
            self.assertEqual(caught.exception.to_currency.value, key[2])


if __name__ == "__main__":
    unittest.main()
