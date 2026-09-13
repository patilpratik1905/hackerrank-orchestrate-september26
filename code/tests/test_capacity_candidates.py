from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from buy_or_wait.capacity import amount_safe_to_pay, earliest_date_for_full_payment, calculate_capacity
from buy_or_wait.candidates import Candidate, generate_candidates, rank_candidates, validate_candidate
from buy_or_wait.forecast import CashMovement, HypotheticalPayment, MovementKind, ReconstructedState, simulate
from buy_or_wait.models import (
    Category, Currency, Direction, EventStatus, EventType, FinancialEvent,
    FinancialPriority, FinancialProfile, Flexibility, PaymentOption,
    PaymentOptionMethod, PaymentPreference, Request, RequestBundle, RequestType,
    SourceRef,
)


SRC = SourceRef("fixture.csv", 2)
DAY = date(2026, 9, 1)


def profile(*, preferences=frozenset({PaymentPreference.FULL_PAYMENT}), reducible=frozenset(), stoppable=frozenset()):
    return FinancialProfile("u", Currency.USD, Decimal("1000"), Decimal("200"),
        frozenset({FinancialPriority.HOUSING}), frozenset({Category.RENT}),
        frozenset(reducible), frozenset(stoppable), frozenset(preferences), 6, SRC)


def state(movements=(), *, balance="1000", minimum="200"):
    return ReconstructedState("r", "u", DAY, DAY + timedelta(days=90), Decimal(balance), Decimal(minimum), tuple(movements), (), {}, {})


def bundle(p, *, amount="300", deadline=DAY + timedelta(days=30), options=(), allows_partial=True, events=()):
    req = Request("r", "u", DAY, RequestType.PURCHASE, Decimal(amount), deadline, allows_partial, "fixture", SRC)
    return RequestBundle(req, p, tuple(events), tuple(options), (), (), ())


class CapacityCandidateTests(unittest.TestCase):
    def test_capacity_is_exact_linear_cushion(self):
        movement = CashMovement(DAY + timedelta(days=2), Decimal("100"), Direction.DEBIT, MovementKind.EVENT, "e", "e")
        s = state((movement,))
        self.assertEqual(amount_safe_to_pay(s, Decimal("900")), Decimal("700"))
        self.assertEqual(amount_safe_to_pay(s, Decimal("500")), Decimal("500"))

    def test_earliest_date_waits_for_confirmed_credit(self):
        credit = CashMovement(DAY + timedelta(days=5), Decimal("500"), Direction.CREDIT, MovementKind.EVENT, "salary", "salary")
        s = state((credit,), balance="300", minimum="200")
        earliest, result = earliest_date_for_full_payment(s, Decimal("500"))
        self.assertEqual(earliest, DAY + timedelta(days=5))
        self.assertTrue(result and result.safe)

    def test_installment_schedule_uses_supplied_option_and_preference(self):
        option = PaymentOption("opt", "r", PaymentOptionMethod.INSTALLMENTS, Decimal("100"), 3, DAY + timedelta(days=2), 10, Decimal("0"), Decimal("300"), SRC)
        p = profile(preferences={PaymentPreference.INSTALLMENTS})
        s = state()
        candidates = generate_candidates(s, bundle(p, options=(option,)), calculate_capacity(s, Decimal("300")))
        ranked = rank_candidates(candidates)
        self.assertEqual(ranked[0].method, "installments")
        self.assertEqual([x.payment_date for x in ranked[0].payments], [DAY + timedelta(days=2), DAY + timedelta(days=12), DAY + timedelta(days=22)])

    def test_unsafe_between_installments_is_rejected(self):
        option = PaymentOption("opt", "r", PaymentOptionMethod.INSTALLMENTS, Decimal("500"), 2, DAY + timedelta(days=2), 10, Decimal("0"), Decimal("1000"), SRC)
        debit = CashMovement(DAY + timedelta(days=6), Decimal("400"), Direction.DEBIT, MovementKind.EVENT, "rent", "rent")
        s = state((debit,), balance="700", minimum="200")
        p = profile(preferences={PaymentPreference.INSTALLMENTS})
        candidates = generate_candidates(s, bundle(p, amount="1000", options=(option,)), calculate_capacity(s, Decimal("1000")))
        self.assertFalse(any(c.eligible for c in candidates if c.method == "installments"))

    def test_permitted_reduction_is_a_simulator_valid_variant(self):
        event = FinancialEvent("flex", "u", EventType.EXPENSE, "Flexible shopping", Category.SHOPPING, Direction.DEBIT, Decimal("100"), Currency.USD, DAY, DAY + timedelta(days=4), EventStatus.SCHEDULED, None, Flexibility.REDUCIBLE, Decimal("0"), SRC)
        movement = CashMovement(DAY + timedelta(days=4), Decimal("100"), Direction.DEBIT, MovementKind.RECURRENCE, "recurrence:flex", "flex", recurrence_rule_id="recurrence:flex", raw_amount=Decimal("100"))
        s = state((movement,), balance="500", minimum="200")
        p = profile(reducible={Category.SHOPPING})
        candidates = generate_candidates(s, bundle(p, amount="300", events=(event,)), calculate_capacity(s, Decimal("300")))
        self.assertTrue(any(c.eligible and c.spending_changes for c in candidates))
        self.assertTrue(all(c.simulation is None or c.simulation.safe for c in candidates if c.eligible))

    def test_structural_validator_rejects_wrong_partial_sum(self):
        p = profile(preferences={PaymentPreference.PARTIAL_PAYMENT})
        s = state()
        bad = Candidate("partial_payment", True, (),
            (HypotheticalPayment(DAY, Decimal("100")), HypotheticalPayment(DAY + timedelta(days=1), Decimal("199"))),
            None, Decimal("299"), (), simulate(s), ())
        self.assertIn("partial payment must contain two payments summing to requested amount", validate_candidate(bad, s, bundle(p, amount="300")))


if __name__ == "__main__":
    unittest.main()
