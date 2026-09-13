"""Small, in-memory scenarios representing important dataset patterns.

These are synthetic records for unit tests. They are not production labels and are
never imported by the application entry point.
"""

from __future__ import annotations

from copy import deepcopy


BASE_PROFILE = {
    "user_id": "fixture_user",
    "home_currency": "USD",
    "current_available_balance": "1000.00",
    "minimum_balance_to_keep": "200.00",
    "financial_priorities": "education|debt_repayment",
    "expense_categories_to_protect": "rent|groceries|healthcare",
    "expense_categories_user_is_willing_to_reduce": "dining|streaming",
    "expense_categories_user_is_willing_to_stop": "streaming|cloud_storage",
    "payment_methods_user_will_consider": "full_payment|partial_payment|installments",
    "max_installment_months": "6",
}

BASE_REQUEST = {
    "request_id": "fixture_request",
    "user_id": "fixture_user",
    "request_date": "2026-09-01",
    "request_type": "purchase",
    "requested_amount": "300.00",
    "desired_completion_date": "2026-10-31",
    "allows_partial_payment": "true",
    "request_text": "Can I safely make this purchase?",
}


def event(
    event_id: str,
    *,
    event_type: str = "expense",
    description: str = "Fixture expense",
    category: str = "groceries",
    direction: str = "debit",
    amount: str = "50.00",
    currency: str = "USD",
    event_date: str = "2026-08-01",
    settlement_date: str = "2026-08-01",
    status: str = "settled",
    linked_event_id: str = "",
    flexibility: str = "fixed",
    minimum_allowed_amount: str = "",
) -> dict[str, str]:
    return {
        "event_id": event_id,
        "user_id": "fixture_user",
        "event_type": event_type,
        "description": description,
        "category": category,
        "direction": direction,
        "amount": amount,
        "currency": currency,
        "event_date": event_date,
        "settlement_date": settlement_date,
        "status": status,
        "linked_event_id": linked_event_id,
        "flexibility": flexibility,
        "minimum_allowed_amount": minimum_allowed_amount,
    }


def scenario_catalog() -> dict[str, dict[str, object]]:
    """Return independent copies so tests cannot mutate shared fixture state."""

    catalog: dict[str, dict[str, object]] = {
        "affordable_full_payment": {
            "profile": deepcopy(BASE_PROFILE),
            "request": deepcopy(BASE_REQUEST),
            "events": [],
            "expected_pattern": "full payment can preserve the minimum",
        },
        "safe_installments": {
            "profile": {**BASE_PROFILE, "payment_methods_user_will_consider": "installments"},
            "request": deepcopy(BASE_REQUEST),
            "events": [],
            "payment_option": {
                "payment_option_id": "fixture_option_1",
                "request_id": "fixture_request",
                "payment_method": "installments",
                "payment_amount": "105.00",
                "number_of_payments": "3",
                "first_payment_date": "2026-09-05",
                "payment_frequency_days": "30",
                "financing_fee": "15.00",
                "total_payable_amount": "315.00",
            },
            "expected_pattern": "supplied three-payment schedule",
        },
        "wait_for_salary": {
            "profile": {**BASE_PROFILE, "current_available_balance": "450.00"},
            "request": deepcopy(BASE_REQUEST),
            "events": [
                event(
                    "fixture_salary",
                    event_type="income",
                    description="Confirmed salary",
                    category="salary",
                    direction="credit",
                    amount="500.00",
                    event_date="2026-09-01",
                    settlement_date="2026-09-15",
                    status="scheduled",
                )
            ],
            "expected_pattern": "full payment becomes safe after confirmed salary",
        },
        "not_affordable": {
            "profile": {**BASE_PROFILE, "current_available_balance": "250.00"},
            "request": deepcopy(BASE_REQUEST),
            "events": [],
            "expected_pattern": "no safe plan without unsupported income",
        },
        "valid_partial_payment": {
            "profile": {**BASE_PROFILE, "current_available_balance": "350.00"},
            "request": deepcopy(BASE_REQUEST),
            "events": [
                event(
                    "fixture_partial_salary",
                    event_type="income",
                    category="salary",
                    direction="credit",
                    amount="250.00",
                    settlement_date="2026-09-20",
                    status="scheduled",
                )
            ],
            "expected_pattern": "positive safe amount now and remainder later",
        },
        "flexible_spending": {
            "profile": deepcopy(BASE_PROFILE),
            "request": deepcopy(BASE_REQUEST),
            "events": [
                event(
                    "fixture_streaming",
                    event_type="subscription",
                    description="Streaming plan",
                    category="streaming",
                    amount="40.00",
                    flexibility="reducible_or_stoppable",
                    minimum_allowed_amount="20.00",
                )
            ],
            "expected_pattern": "legal stop or reduce action",
        },
        "blank_amount_image": {
            "profile": deepcopy(BASE_PROFILE),
            "request": deepcopy(BASE_REQUEST),
            "events": [event("fixture_image_event", amount="")],
            "images": [
                {
                    "image_id": "fixture_image",
                    "user_id": "fixture_user",
                    "request_id": "fixture_request",
                    "related_event_id": "fixture_image_event",
                }
            ],
            "expected_pattern": "blank is unresolved and never zero",
        },
        "pending_debit": {
            "profile": deepcopy(BASE_PROFILE),
            "request": deepcopy(BASE_REQUEST),
            "events": [
                event(
                    "fixture_pending_debit",
                    amount="175.00",
                    event_date="2026-09-02",
                    settlement_date="2026-09-04",
                    status="pending",
                )
            ],
            "expected_pattern": "future debit is reserved",
        },
        "pending_credit_refund": {
            "profile": deepcopy(BASE_PROFILE),
            "request": deepcopy(BASE_REQUEST),
            "events": [
                event(
                    "fixture_pending_refund",
                    event_type="refund",
                    category="shopping",
                    direction="credit",
                    amount="175.00",
                    event_date="2026-09-02",
                    settlement_date="2026-09-04",
                    status="pending",
                )
            ],
            "expected_pattern": "unsettled credit is excluded",
        },
        "cancelled_and_failed": {
            "profile": deepcopy(BASE_PROFILE),
            "request": deepcopy(BASE_REQUEST),
            "events": [
                event("fixture_cancelled", status="cancelled"),
                event("fixture_failed", status="failed"),
            ],
            "expected_pattern": "neither record affects cash",
        },
        "unrealized_investment": {
            "profile": deepcopy(BASE_PROFILE),
            "request": deepcopy(BASE_REQUEST),
            "events": [
                event(
                    "fixture_unrealized",
                    event_type="investment_valuation",
                    category="investment",
                    direction="non_cash",
                    amount="5000.00",
                    settlement_date="",
                    status="unrealized",
                )
            ],
            "expected_pattern": "valuation is never cash",
        },
        "linked_lifecycle": {
            "profile": deepcopy(BASE_PROFILE),
            "request": deepcopy(BASE_REQUEST),
            "events": [
                event("fixture_failed_original", status="failed"),
                event(
                    "fixture_retry",
                    status="scheduled",
                    linked_event_id="fixture_failed_original",
                    event_date="2026-09-02",
                    settlement_date="2026-09-05",
                ),
            ],
            "expected_pattern": "link supplies lifecycle context, not cash treatment",
        },
        "foreign_currency": {
            "profile": {**BASE_PROFILE, "home_currency": "INR"},
            "request": {**BASE_REQUEST, "requested_amount": "25000.00"},
            "events": [
                event(
                    "fixture_usd_expense",
                    amount="100.00",
                    currency="USD",
                    settlement_date="2026-09-04",
                    status="pending",
                )
            ],
            "rates": [
                {
                    "rate_date": "2026-09-04",
                    "from_currency": "USD",
                    "to_currency": "INR",
                    "rate": "83.25",
                }
            ],
            "expected_pattern": "exact dated USD to INR conversion",
        },
        "refuses_full_payment": {
            "profile": {
                **BASE_PROFILE,
                "payment_methods_user_will_consider": "installments",
            },
            "request": deepcopy(BASE_REQUEST),
            "events": [],
            "expected_pattern": "capacity does not override method preference",
        },
    }
    return deepcopy(catalog)

