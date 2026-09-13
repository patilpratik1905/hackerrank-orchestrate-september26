"""Strict normalized data contracts for participant-facing records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Currency(StringEnum):
    EUR = "EUR"
    IDR = "IDR"
    INR = "INR"
    USD = "USD"
    ZAR = "ZAR"


class FinancialPriority(StringEnum):
    DEBT_REPAYMENT = "debt_repayment"
    EDUCATION = "education"
    EMERGENCY_SAVINGS = "emergency_savings"
    FAMILY_SUPPORT = "family_support"
    HEALTHCARE = "healthcare"
    HOUSING = "housing"
    RETIREMENT_INVESTMENT = "retirement_investment"
    TRAVEL = "travel"


class Category(StringEnum):
    CLOUD_STORAGE = "cloud_storage"
    DEBT_REPAYMENT = "debt_repayment"
    DELIVERY_MEMBERSHIP = "delivery_membership"
    DINING = "dining"
    EDUCATION = "education"
    ENTERTAINMENT = "entertainment"
    FAMILY_SUPPORT = "family_support"
    GROCERIES = "groceries"
    GYM = "gym"
    HEALTHCARE = "healthcare"
    HOUSING = "housing"
    INSURANCE = "insurance"
    INVESTMENT = "investment"
    MUSIC_SUBSCRIPTION = "music_subscription"
    RENT = "rent"
    SALARY = "salary"
    SHOPPING = "shopping"
    STREAMING = "streaming"
    TRANSPORT = "transport"
    UTILITIES = "utilities"
    WINDFALL = "windfall"
    WORK_EXPENSE = "work_expense"


class PaymentPreference(StringEnum):
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"


class RequestType(StringEnum):
    DEBT_REPAYMENT = "debt_repayment"
    EDUCATION = "education"
    EMERGENCY_EXPENSE = "emergency_expense"
    FAMILY_TRANSFER = "family_transfer"
    HOUSING = "housing"
    INVESTMENT = "investment"
    OTHER = "other"
    PURCHASE = "purchase"
    TRAVEL = "travel"


class EventType(StringEnum):
    DEBT_PAYMENT = "debt_payment"
    EXPENSE = "expense"
    INCOME = "income"
    INVESTMENT_PURCHASE = "investment_purchase"
    INVESTMENT_SALE = "investment_sale"
    INVESTMENT_VALUATION = "investment_valuation"
    REFUND = "refund"
    SUBSCRIPTION = "subscription"


class Direction(StringEnum):
    CREDIT = "credit"
    DEBIT = "debit"
    NON_CASH = "non_cash"


class EventStatus(StringEnum):
    CANCELLED = "cancelled"
    FAILED = "failed"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    SETTLED = "settled"
    UNREALIZED = "unrealized"


class Flexibility(StringEnum):
    FIXED = "fixed"
    REDUCIBLE = "reducible"
    REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"
    STOPPABLE = "stoppable"


class PaymentOptionMethod(StringEnum):
    FULL_PAYMENT = "full_payment"
    INSTALLMENTS = "installments"


class MessageSourceType(StringEnum):
    BANK = "bank"
    EMPLOYER = "employer"
    FINANCIAL_SERVICE = "financial_service"
    MERCHANT = "merchant"
    SERVICE_PROVIDER = "service_provider"


class AffordabilityStatus(StringEnum):
    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class RecommendedPaymentMethod(StringEnum):
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


@dataclass(frozen=True, slots=True)
class SourceRef:
    filename: str
    row_number: int


@dataclass(frozen=True, slots=True)
class FinancialProfile:
    user_id: str
    home_currency: Currency
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: frozenset[FinancialPriority]
    protected_categories: frozenset[Category]
    reducible_categories: frozenset[Category]
    stoppable_categories: frozenset[Category]
    payment_preferences: frozenset[PaymentPreference]
    max_installment_months: int | None
    source: SourceRef


@dataclass(frozen=True, slots=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: RequestType
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: EventType
    description: str
    category: Category
    direction: Direction
    amount: Decimal | None
    currency: Currency
    event_date: date
    settlement_date: date | None
    status: EventStatus
    linked_event_id: str | None
    flexibility: Flexibility
    minimum_allowed_amount: Decimal | None
    source: SourceRef


@dataclass(frozen=True, slots=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: PaymentOptionMethod
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal
    source: SourceRef


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    rate_date: date
    from_currency: Currency
    to_currency: Currency
    rate: Decimal
    source: SourceRef

    def convert(self, amount: Decimal) -> Decimal:
        return amount * self.rate


@dataclass(frozen=True, slots=True)
class MessageEvidence:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime
    source_type: MessageSourceType
    message_text: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class ImageEvidence:
    image_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    path: Path
    source: SourceRef


@dataclass(frozen=True, slots=True)
class Prediction:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: AffordabilityStatus
    recommended_payment_method: RecommendedPaymentMethod
    payment_plan: str
    earliest_date_for_full_payment: date | None
    spending_changes_needed: str
    decision_explanation: str
    source: SourceRef | None = None


@dataclass(frozen=True, slots=True)
class OutputTemplateRow:
    request_id: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class RequestBundle:
    request: Request
    profile: FinancialProfile
    events: tuple[FinancialEvent, ...]
    payment_options: tuple[PaymentOption, ...]
    messages: tuple[MessageEvidence, ...]
    images: tuple[ImageEvidence, ...]
    required_exchange_rates: tuple[ExchangeRate, ...]
