from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buy_or_wait.forecast import HypotheticalPayment
from buy_or_wait.serialize import format_money, format_payment_plan, format_plan_amount


class SerializeTests(unittest.TestCase):
    def test_decimal_formatting_has_no_scientific_notation(self):
        self.assertEqual(format_money(Decimal("1E+3")), "1000")
        self.assertEqual(format_plan_amount(Decimal("620.40")), "620.40")
        self.assertEqual(format_plan_amount(Decimal("620")), "620")

    def test_plan_round_trip_is_chronological_and_lossless_at_cents(self):
        candidate = SimpleNamespace(payments=(
            HypotheticalPayment(date(2026, 9, 1), Decimal("100")),
            HypotheticalPayment(date(2026, 10, 1), Decimal("20.50")),
        ))
        self.assertEqual(format_payment_plan(candidate), "2026-09-01:100|2026-10-01:20.50")

    def test_csv_escaping_for_commas_quotes_and_pipes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("entry", Path(__file__).resolve().parents[1] / "main.py")
        assert spec and spec.loader
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        row = {column: "x,y|\"quoted\"" for column in entry.OUTPUT_COLUMNS}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            entry.write_output_csv(path, [row])
            with path.open(newline="", encoding="utf-8") as handle:
                parsed = list(csv.DictReader(handle))
            self.assertEqual(parsed[0], row)


if __name__ == "__main__":
    unittest.main()
