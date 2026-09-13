from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.evidence import (  # noqa: E402
    EvidenceAction, EvidenceFact, EvidenceFactType, EvidenceRecurrence,
    EvidenceSourceType, EvidenceStatus,
)
from buy_or_wait.forecast import (  # noqa: E402
    ForecastConfig, HypotheticalPayment, MovementKind, SpendingAction,
    SpendingModification, detect_recurrence, reconstruct_state, simulate,
)
from buy_or_wait.models import (  # noqa: E402
    Category, Currency, Direction, EventStatus, EventType, FinancialEvent,
    FinancialPriority, FinancialProfile, Flexibility, Request, RequestBundle,
    RequestType, SourceRef,
)


SOURCE = SourceRef("fixture.csv", 2)
REQUEST_DAY = date(2026, 9, 1)


class FakeRepository:
    def __init__(self, events: tuple[FinancialEvent, ...], rates: dict[tuple[date, Currency, Currency], Decimal] | None = None) -> None:
        self.events_by_event_id = {event.event_id: event for event in events}
        self.rates = rates or {}

    def get_rate(self, rate_date: date, source: Currency, target: Currency) -> SimpleNamespace:
        try:
            rate = self.rates[(rate_date, source, target)]
        except KeyError as exc:
            raise AssertionError(f"missing fixture rate {rate_date} {source}->{target}") from exc
        return SimpleNamespace(rate=rate, convert=lambda amount: amount * rate)


def profile(*, balance: str = "500.00", minimum: str = "200.00", home: Currency = Currency.USD) -> FinancialProfile:
    return FinancialProfile(
        "fixture_user", home, Decimal(balance), Decimal(minimum),
        frozenset({FinancialPriority.HOUSING}), frozenset({Category.GROCERIES, Category.RENT}),
        frozenset({Category.GROCERIES}), frozenset({Category.STREAMING}), frozenset(), None, SOURCE,
    )


def event(
    event_id: str, *, amount: str = "100.00", settlement: date | None = REQUEST_DAY,
    event_date: date | None = None, direction: Direction = Direction.DEBIT,
    status: EventStatus = EventStatus.SCHEDULED, category: Category = Category.GROCERIES,
    description: str = "Fixture expense", currency: Currency = Currency.USD,
    event_type: EventType = EventType.EXPENSE, linked: str | None = None,
) -> FinancialEvent:
    return FinancialEvent(
        event_id, "fixture_user", event_type, description, category, direction,
        Decimal(amount), currency, event_date or settlement or REQUEST_DAY, settlement,
        status, linked, Flexibility.REDUCIBLE_OR_STOPPABLE, Decimal("10.00"), SOURCE,
    )


def bundle(events: tuple[FinancialEvent, ...], *, p: FinancialProfile | None = None) -> RequestBundle:
    request = Request("fixture_request", "fixture_user", REQUEST_DAY, RequestType.PURCHASE, Decimal("50"), date(2026, 10, 1), True, "fixture", SOURCE)
    return RequestBundle(request, p or profile(), events, (), (), (), ())


def fact(
    event_id: str | None, *, kind: EvidenceFactType = EvidenceFactType.OTHER_AMENDMENT,
    action: EvidenceAction = EvidenceAction.AMENDMENT, status: EvidenceStatus | None = None,
    amount: str | None = None, timestamp: datetime | None = None,
) -> EvidenceFact:
    amount_value = Decimal(amount) if amount is not None else None
    return EvidenceFact(
        f"fact:{event_id or 'user'}", EvidenceSourceType.MESSAGE, "message_fixture", "fixture_user", None,
        event_id, kind, amount_value, Currency.USD if amount is not None else None, None, None, None,
        status, action, EvidenceRecurrence.UNKNOWN, None,
        timestamp or datetime(2026, 9, 2, tzinfo=timezone.utc), Decimal("0.99"), "a" * 64,
        "fixture-v1", "fixture", SOURCE,
    )


class ForecastTests(unittest.TestCase):
    def state(self, events: tuple[FinancialEvent, ...], *, p: FinancialProfile | None = None, facts: tuple[EvidenceFact, ...] = (), rates: dict[tuple[date, Currency, Currency], Decimal] | None = None):
        repo = FakeRepository(events, rates)
        return reconstruct_state(bundle(events, p=p), repo, facts), repo

    def test_minimum_balance_equality_is_safe(self) -> None:
        state, _ = self.state((event("debit", amount="300"),), p=profile(balance="500", minimum="200"))
        result = simulate(state)
        self.assertTrue(result.safe)
        self.assertEqual(result.minimum_balance_observed, Decimal("200"))

    def test_mid_period_violation_is_unsafe_despite_positive_ending_balance(self) -> None:
        events = (
            event("debit", amount="150", settlement=date(2026, 9, 2)),
            event("salary", amount="300", settlement=date(2026, 9, 3), direction=Direction.CREDIT, category=Category.SALARY, event_type=EventType.INCOME),
        )
        state, _ = self.state(events, p=profile(balance="300", minimum="200"))
        result = simulate(state)
        self.assertFalse(result.safe)
        self.assertEqual(result.ending_balance, Decimal("450"))
        self.assertEqual(result.first_violation.movement.source_id, "debit")  # type: ignore[union-attr]

    def test_pending_debit_reserved_and_pending_credit_ignored(self) -> None:
        events = (
            event("pending_debit", amount="120", settlement=date(2026, 9, 2), status=EventStatus.PENDING),
            event("pending_credit", amount="500", settlement=date(2026, 9, 2), direction=Direction.CREDIT, status=EventStatus.PENDING, category=Category.SALARY, event_type=EventType.INCOME),
        )
        state, _ = self.state(events)
        result = simulate(state)
        self.assertEqual([entry.movement.source_id for entry in result.entries], ["pending_debit"])
        self.assertEqual(result.ending_balance, Decimal("380"))

    def test_failed_cancelled_and_unrealized_do_not_move_cash(self) -> None:
        events = (
            event("failed", status=EventStatus.FAILED), event("cancelled", status=EventStatus.CANCELLED),
            event("unrealized", amount="9000", settlement=None, direction=Direction.NON_CASH, status=EventStatus.UNREALIZED, category=Category.INVESTMENT, event_type=EventType.INVESTMENT_VALUATION),
        )
        state, _ = self.state(events)
        self.assertEqual(state.baseline_movements, ())
        self.assertEqual(simulate(state).ending_balance, Decimal("500"))

    def test_confirmed_salary_uses_settlement_date(self) -> None:
        salary = event("salary", amount="500", settlement=date(2026, 9, 15), direction=Direction.CREDIT, category=Category.SALARY, event_type=EventType.INCOME)
        state, _ = self.state((salary,))
        result = simulate(state)
        self.assertEqual(result.entries[0].movement.movement_date, date(2026, 9, 15))
        self.assertEqual(result.ending_balance, Decimal("1000"))

    def test_linked_retry_suppresses_prior_future_debit(self) -> None:
        original = event("original", amount="75", settlement=date(2026, 9, 3))
        retry = event("retry", amount="75", settlement=date(2026, 9, 4), linked="original")
        state, _ = self.state((original, retry))
        self.assertEqual([movement.source_id for movement in state.baseline_movements], ["retry"])

    def test_bank_evidence_neutralizes_matched_internal_transfer(self) -> None:
        debit = event("transfer_out", amount="80", settlement=date(2026, 9, 3))
        credit = event("transfer_in", amount="80", settlement=date(2026, 9, 3), direction=Direction.CREDIT, category=Category.SALARY, event_type=EventType.INCOME)
        transfer_fact = fact(None, kind=EvidenceFactType.INTERNAL_TRANSFER, action=EvidenceAction.CONFIRMATION, timestamp=datetime(2026, 9, 3, tzinfo=timezone.utc))
        state, _ = self.state((debit, credit), facts=(transfer_fact,))
        self.assertEqual(state.baseline_movements, ())

    def test_exact_dated_fx_is_traced(self) -> None:
        foreign = event("foreign", amount="100", settlement=date(2026, 9, 4), currency=Currency.USD)
        p = profile(home=Currency.INR)
        state, _ = self.state((foreign,), p=p, rates={(date(2026, 9, 4), Currency.USD, Currency.INR): Decimal("83.25")})
        movement = state.baseline_movements[0]
        self.assertEqual((movement.amount, movement.fx_rate), (Decimal("8325.00"), Decimal("83.25")))

    def test_fixed_recurrence_and_false_recurrence(self) -> None:
        recurring = tuple(event(f"rent_{index}", amount="100", settlement=day, event_date=day, status=EventStatus.SETTLED, category=Category.RENT, description="Monthly rent") for index, day in enumerate((date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1))))
        state, repo = self.state(recurring)
        rules = detect_recurrence(bundle(recurring), repo)
        self.assertEqual(len(rules), 1)
        self.assertTrue(any(m.kind is MovementKind.RECURRENCE and m.movement_date == date(2026, 9, 30) for m in state.baseline_movements))
        noisy = tuple(event(f"noise_{index}", settlement=day, event_date=day, status=EventStatus.SETTLED, category=Category.RENT, description="Irregular rent") for index, day in enumerate((date(2026, 5, 1), date(2026, 6, 2), date(2026, 8, 20))))
        self.assertEqual(detect_recurrence(bundle(noisy), FakeRepository(noisy)), ())

    def test_variable_essential_is_conservative_and_traceable(self) -> None:
        descriptions = ("Alpha market", "Beta market", "Gamma market")
        events = tuple(event(f"groceries_{index}", amount=amount, settlement=day, event_date=day, status=EventStatus.SETTLED, category=Category.GROCERIES, description=descriptions[index]) for index, (day, amount) in enumerate(((date(2026, 6, 5), "40"), (date(2026, 7, 5), "80"), (date(2026, 8, 5), "60"))))
        state, _ = self.state(events)
        variable = [m for m in state.baseline_movements if m.kind is MovementKind.VARIABLE_ESSENTIAL]
        self.assertTrue(variable)
        self.assertEqual(variable[0].raw_amount, Decimal("60"))

    def test_evidence_amendment_and_cancellation_take_precedence(self) -> None:
        amended = event("amended", amount="100", settlement=date(2026, 9, 3))
        cancelled = event("cancelled_by_message", amount="100", settlement=date(2026, 9, 4))
        state, _ = self.state((amended, cancelled), facts=(fact("amended", amount="150"), fact("cancelled_by_message", action=EvidenceAction.CANCELLATION, status=EvidenceStatus.CANCELLED)))
        movement = next(item for item in state.baseline_movements if item.source_id == "amended")
        self.assertEqual(movement.amount, Decimal("150"))
        self.assertIn("cancelled_by_message", state.excluded_event_reasons)

    def test_same_day_debits_and_payment_precede_credit(self) -> None:
        debit = event("debit", amount="150", settlement=REQUEST_DAY)
        credit = event("credit", amount="100", settlement=REQUEST_DAY, direction=Direction.CREDIT, category=Category.SALARY, event_type=EventType.INCOME)
        state, _ = self.state((debit, credit), p=profile(balance="400", minimum="200"))
        result = simulate(state, (HypotheticalPayment(REQUEST_DAY, Decimal("60"), "pay"),))
        self.assertEqual([entry.movement.source_id for entry in result.entries], ["debit", "pay", "credit"])
        self.assertFalse(result.safe)

    def test_inclusive_horizon_and_spending_modification(self) -> None:
        last_day = REQUEST_DAY + __import__("datetime").timedelta(days=90)
        events = (event("inside", amount="100", settlement=last_day), event("outside", amount="100", settlement=last_day + __import__("datetime").timedelta(days=1)))
        state, _ = self.state(events, p=profile(balance="300", minimum="250"))
        self.assertEqual([movement.source_id for movement in state.baseline_movements], ["inside"])
        self.assertFalse(simulate(state).safe)
        changed = simulate(state, modifications=(SpendingModification("inside", SpendingAction.STOP),))
        self.assertTrue(changed.safe)


if __name__ == "__main__":
    unittest.main()
