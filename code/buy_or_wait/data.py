"""Strict CSV loading, normalization, indexes, bundles, and directed FX lookup."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Sequence, TypeVar

from buy_or_wait.models import (
    AffordabilityStatus,
    Category,
    Currency,
    Direction,
    EventStatus,
    EventType,
    ExchangeRate,
    FinancialEvent,
    FinancialPriority,
    FinancialProfile,
    Flexibility,
    ImageEvidence,
    MessageEvidence,
    MessageSourceType,
    OutputTemplateRow,
    PaymentOption,
    PaymentOptionMethod,
    PaymentPreference,
    Prediction,
    RecommendedPaymentMethod,
    Request,
    RequestBundle,
    RequestType,
    SourceRef,
)
from buy_or_wait.schema import EXPECTED_SCHEMAS, OUTPUT_COLUMNS


class DatasetError(Exception):
    """Base class for deterministic participant-data failures."""


class MissingDatasetFileError(DatasetError):
    pass


class SchemaError(DatasetError):
    pass


class FieldParseError(DatasetError):
    def __init__(
        self, source: SourceRef, column: str, value: str, detail: str
    ) -> None:
        self.source = source
        self.column = column
        self.value = value
        self.detail = detail
        super().__init__(
            f"{source.filename}:{source.row_number} column {column!r} "
            f"value {value!r}: {detail}"
        )


class DuplicateKeyError(DatasetError):
    pass


class MissingReferenceError(DatasetError):
    pass


class InvalidLifecycleLinkError(DatasetError):
    pass


class MissingRateError(DatasetError):
    def __init__(
        self, rate_date: date, from_currency: Currency, to_currency: Currency
    ) -> None:
        self.rate_date = rate_date
        self.from_currency = from_currency
        self.to_currency = to_currency
        super().__init__(
            "missing exact exchange rate for "
            f"({rate_date.isoformat()}, {from_currency.value}, {to_currency.value})"
        )


class MissingEvidenceError(DatasetError):
    pass


@dataclass(frozen=True, slots=True)
class RawRow:
    values: Mapping[str, str]
    source: SourceRef


T = TypeVar("T")
E = TypeVar("E", bound=Enum)


def _read_table(dataset_dir: Path, filename: str) -> tuple[RawRow, ...]:
    path = dataset_dir / filename
    if not path.is_file():
        raise MissingDatasetFileError(f"participant file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = tuple(reader.fieldnames or ())
        expected = EXPECTED_SCHEMAS[filename]
        if headers != expected:
            raise SchemaError(
                f"{filename}: expected columns {expected}, found {headers}"
            )
        rows: list[RawRow] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise SchemaError(
                    f"{filename}:{row_number}: row contains extra unnamed values"
                )
            rows.append(
                RawRow(
                    MappingProxyType({key: value or "" for key, value in row.items()}),
                    SourceRef(filename, row_number),
                )
            )
        return tuple(rows)


def _required(row: RawRow, column: str) -> str:
    value = row.values[column]
    if value == "":
        raise FieldParseError(row.source, column, value, "required value is blank")
    return value


def _optional(row: RawRow, column: str) -> str | None:
    value = row.values[column]
    return value if value != "" else None


def _decimal(
    row: RawRow,
    column: str,
    *,
    optional: bool = False,
    strictly_positive: bool = False,
) -> Decimal | None:
    value = row.values[column]
    if value == "":
        if optional:
            return None
        raise FieldParseError(row.source, column, value, "required number is blank")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise FieldParseError(row.source, column, value, "invalid decimal") from exc
    if not parsed.is_finite():
        raise FieldParseError(row.source, column, value, "decimal must be finite")
    if strictly_positive and parsed <= 0:
        raise FieldParseError(row.source, column, value, "must be greater than zero")
    if not strictly_positive and parsed < 0:
        raise FieldParseError(row.source, column, value, "must not be negative")
    return parsed


def _integer(
    row: RawRow,
    column: str,
    *,
    optional: bool = False,
    strictly_positive: bool = False,
) -> int | None:
    value = row.values[column]
    if value == "":
        if optional:
            return None
        raise FieldParseError(row.source, column, value, "required integer is blank")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise FieldParseError(row.source, column, value, "invalid integer") from exc
    if str(parsed) != value:
        raise FieldParseError(row.source, column, value, "integer is not canonical")
    if strictly_positive and parsed <= 0:
        raise FieldParseError(row.source, column, value, "must be greater than zero")
    if not strictly_positive and parsed < 0:
        raise FieldParseError(row.source, column, value, "must not be negative")
    return parsed


def _date(row: RawRow, column: str, *, optional: bool = False) -> date | None:
    value = row.values[column]
    if value == "":
        if optional:
            return None
        raise FieldParseError(row.source, column, value, "required date is blank")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise FieldParseError(row.source, column, value, "invalid ISO date") from exc
    if parsed.isoformat() != value:
        raise FieldParseError(row.source, column, value, "date is not canonical YYYY-MM-DD")
    return parsed


def _timestamp(row: RawRow, column: str) -> datetime:
    value = _required(row, column)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FieldParseError(row.source, column, value, "invalid ISO timestamp") from exc
    return parsed


def _enum(row: RawRow, column: str, enum_type: type[E]) -> E:
    value = _required(row, column)
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise FieldParseError(
            row.source, column, value, f"unknown value; allowed: {allowed}"
        ) from exc


def _pipe_enum_set(
    row: RawRow,
    column: str,
    enum_type: type[E],
    *,
    optional: bool = False,
) -> frozenset[E]:
    value = row.values[column]
    if value == "":
        if optional:
            return frozenset()
        raise FieldParseError(row.source, column, value, "required list is blank")
    tokens = value.split("|")
    if any(not token for token in tokens) or len(tokens) != len(set(tokens)):
        raise FieldParseError(
            row.source, column, value, "pipe list has blank or duplicate values"
        )
    parsed: list[E] = []
    for token in tokens:
        try:
            parsed.append(enum_type(token))
        except ValueError as exc:
            raise FieldParseError(
                row.source, column, token, f"unknown {enum_type.__name__} value"
            ) from exc
    return frozenset(parsed)


def _boolean(row: RawRow, column: str) -> bool:
    value = _required(row, column)
    if value not in {"true", "false"}:
        raise FieldParseError(row.source, column, value, "expected true or false")
    return value == "true"


def _parse_profile(row: RawRow) -> FinancialProfile:
    current = _decimal(row, "current_available_balance")
    minimum = _decimal(row, "minimum_balance_to_keep")
    assert current is not None and minimum is not None
    return FinancialProfile(
        user_id=_required(row, "user_id"),
        home_currency=_enum(row, "home_currency", Currency),
        current_available_balance=current,
        minimum_balance_to_keep=minimum,
        financial_priorities=_pipe_enum_set(
            row, "financial_priorities", FinancialPriority
        ),
        protected_categories=_pipe_enum_set(
            row, "expense_categories_to_protect", Category
        ),
        reducible_categories=_pipe_enum_set(
            row,
            "expense_categories_user_is_willing_to_reduce",
            Category,
            optional=True,
        ),
        stoppable_categories=_pipe_enum_set(
            row,
            "expense_categories_user_is_willing_to_stop",
            Category,
            optional=True,
        ),
        payment_preferences=_pipe_enum_set(
            row, "payment_methods_user_will_consider", PaymentPreference
        ),
        max_installment_months=_integer(
            row, "max_installment_months", optional=True, strictly_positive=True
        ),
        source=row.source,
    )


def _parse_request(row: RawRow) -> Request:
    request_date = _date(row, "request_date")
    deadline = _date(row, "desired_completion_date")
    amount = _decimal(row, "requested_amount", strictly_positive=True)
    assert request_date is not None and deadline is not None and amount is not None
    if deadline < request_date:
        raise FieldParseError(
            row.source,
            "desired_completion_date",
            deadline.isoformat(),
            "cannot precede request_date",
        )
    return Request(
        request_id=_required(row, "request_id"),
        user_id=_required(row, "user_id"),
        request_date=request_date,
        request_type=_enum(row, "request_type", RequestType),
        requested_amount=amount,
        desired_completion_date=deadline,
        allows_partial_payment=_boolean(row, "allows_partial_payment"),
        request_text=_required(row, "request_text"),
        source=row.source,
    )


def _parse_event(row: RawRow) -> FinancialEvent:
    event_date = _date(row, "event_date")
    settlement_date = _date(row, "settlement_date", optional=True)
    amount = _decimal(row, "amount", optional=True)
    minimum = _decimal(row, "minimum_allowed_amount", optional=True)
    assert event_date is not None
    direction = _enum(row, "direction", Direction)
    status = _enum(row, "status", EventStatus)
    if settlement_date is None and not (
        direction is Direction.NON_CASH and status is EventStatus.UNREALIZED
    ):
        raise FieldParseError(
            row.source,
            "settlement_date",
            "",
            "may be blank only for unrealized non-cash events",
        )
    return FinancialEvent(
        event_id=_required(row, "event_id"),
        user_id=_required(row, "user_id"),
        event_type=_enum(row, "event_type", EventType),
        description=_required(row, "description"),
        category=_enum(row, "category", Category),
        direction=direction,
        amount=amount,
        currency=_enum(row, "currency", Currency),
        event_date=event_date,
        settlement_date=settlement_date,
        status=status,
        linked_event_id=_optional(row, "linked_event_id"),
        flexibility=_enum(row, "flexibility", Flexibility),
        minimum_allowed_amount=minimum,
        source=row.source,
    )


def _parse_option(row: RawRow) -> PaymentOption:
    payment_amount = _decimal(row, "payment_amount", strictly_positive=True)
    count = _integer(row, "number_of_payments", strictly_positive=True)
    first_date = _date(row, "first_payment_date")
    frequency = _integer(
        row, "payment_frequency_days", optional=True, strictly_positive=True
    )
    fee = _decimal(row, "financing_fee")
    total = _decimal(row, "total_payable_amount", strictly_positive=True)
    assert None not in (payment_amount, count, first_date, fee, total)
    method = _enum(row, "payment_method", PaymentOptionMethod)
    if method is PaymentOptionMethod.FULL_PAYMENT and (count != 1 or frequency is not None):
        raise FieldParseError(
            row.source,
            "payment_frequency_days",
            row.values["payment_frequency_days"],
            "full payment requires one payment and blank frequency",
        )
    if method is PaymentOptionMethod.INSTALLMENTS and (count <= 1 or frequency is None):
        raise FieldParseError(
            row.source,
            "payment_frequency_days",
            row.values["payment_frequency_days"],
            "installments require multiple payments and a positive frequency",
        )
    return PaymentOption(
        payment_option_id=_required(row, "payment_option_id"),
        request_id=_required(row, "request_id"),
        payment_method=method,
        payment_amount=payment_amount,
        number_of_payments=count,
        first_payment_date=first_date,
        payment_frequency_days=frequency,
        financing_fee=fee,
        total_payable_amount=total,
        source=row.source,
    )


def _parse_rate(row: RawRow) -> ExchangeRate:
    rate_date = _date(row, "rate_date")
    rate = _decimal(row, "rate", strictly_positive=True)
    assert rate_date is not None and rate is not None
    return ExchangeRate(
        rate_date=rate_date,
        from_currency=_enum(row, "from_currency", Currency),
        to_currency=_enum(row, "to_currency", Currency),
        rate=rate,
        source=row.source,
    )


def _parse_message(row: RawRow) -> MessageEvidence:
    return MessageEvidence(
        message_id=_required(row, "message_id"),
        user_id=_required(row, "user_id"),
        request_id=_optional(row, "request_id"),
        related_event_id=_optional(row, "related_event_id"),
        sent_at=_timestamp(row, "sent_at"),
        source_type=_enum(row, "source_type", MessageSourceType),
        message_text=_required(row, "message_text"),
        source=row.source,
    )


def _parse_image(row: RawRow, dataset_dir: Path) -> ImageEvidence:
    image_id = _required(row, "image_id")
    path = dataset_dir / "media" / "images" / f"{image_id}.png"
    if not path.is_file():
        raise MissingDatasetFileError(
            f"{row.source.filename}:{row.source.row_number}: image file not found: {path}"
        )
    return ImageEvidence(
        image_id=image_id,
        user_id=_required(row, "user_id"),
        request_id=_optional(row, "request_id"),
        related_event_id=_optional(row, "related_event_id"),
        path=path.resolve(),
        source=row.source,
    )


def _parse_prediction(row: RawRow) -> Prediction:
    safe_amount = _decimal(row, "amount_safe_to_pay")
    earliest = _date(row, "earliest_date_for_full_payment", optional=True)
    assert safe_amount is not None
    return Prediction(
        request_id=_required(row, "request_id"),
        amount_safe_to_pay=safe_amount,
        affordability_status=_enum(
            row, "affordability_status", AffordabilityStatus
        ),
        recommended_payment_method=_enum(
            row, "recommended_payment_method", RecommendedPaymentMethod
        ),
        payment_plan=_required(row, "payment_plan"),
        earliest_date_for_full_payment=earliest,
        spending_changes_needed=_required(row, "spending_changes_needed"),
        decision_explanation=_required(row, "decision_explanation"),
        source=row.source,
    )


def _unique_index(
    values: Sequence[T], key: Callable[[T], object], label: str
) -> Mapping[object, T]:
    result: dict[object, T] = {}
    for value in values:
        item_key = key(value)
        if item_key in result:
            raise DuplicateKeyError(f"{label}: duplicate key {item_key!r}")
        result[item_key] = value
    return MappingProxyType(result)


def _group_index(
    values: Iterable[T], key: Callable[[T], object]
) -> Mapping[object, tuple[T, ...]]:
    groups: dict[object, list[T]] = defaultdict(list)
    for value in values:
        groups[key(value)].append(value)
    return MappingProxyType({item_key: tuple(group) for item_key, group in groups.items()})


@dataclass(frozen=True, slots=True)
class DatasetRepository:
    dataset_dir: Path
    profiles: tuple[FinancialProfile, ...]
    events: tuple[FinancialEvent, ...]
    exchange_rates: tuple[ExchangeRate, ...]
    requests: tuple[Request, ...]
    sample_requests: tuple[Request, ...]
    payment_options: tuple[PaymentOption, ...]
    messages: tuple[MessageEvidence, ...]
    images: tuple[ImageEvidence, ...]
    sample_labels: tuple[Prediction, ...]
    output_template_rows: tuple[OutputTemplateRow, ...]
    profiles_by_user_id: Mapping[str, FinancialProfile]
    events_by_event_id: Mapping[str, FinancialEvent]
    events_by_user_id: Mapping[str, tuple[FinancialEvent, ...]]
    requests_by_request_id: Mapping[str, Request]
    sample_requests_by_request_id: Mapping[str, Request]
    all_requests_by_request_id: Mapping[str, Request]
    requests_by_user_id: Mapping[str, tuple[Request, ...]]
    sample_requests_by_user_id: Mapping[str, tuple[Request, ...]]
    payment_options_by_payment_option_id: Mapping[str, PaymentOption]
    payment_options_by_request_id: Mapping[str, tuple[PaymentOption, ...]]
    messages_by_user_id: Mapping[str, tuple[MessageEvidence, ...]]
    messages_by_message_id: Mapping[str, MessageEvidence]
    messages_by_request_id: Mapping[str, tuple[MessageEvidence, ...]]
    messages_by_related_event_id: Mapping[str, tuple[MessageEvidence, ...]]
    images_by_user_id: Mapping[str, tuple[ImageEvidence, ...]]
    images_by_image_id: Mapping[str, ImageEvidence]
    images_by_request_id: Mapping[str, tuple[ImageEvidence, ...]]
    images_by_related_event_id: Mapping[str, tuple[ImageEvidence, ...]]
    rates_by_key: Mapping[tuple[date, Currency, Currency], ExchangeRate]
    sample_labels_by_request_id: Mapping[str, Prediction]
    output_template_by_request_id: Mapping[str, OutputTemplateRow]

    @property
    def output_template_request_ids(self) -> tuple[str, ...]:
        return tuple(row.request_id for row in self.output_template_rows)

    def get_rate(
        self, rate_date: date, from_currency: Currency, to_currency: Currency
    ) -> ExchangeRate:
        try:
            return self.rates_by_key[(rate_date, from_currency, to_currency)]
        except KeyError as exc:
            raise MissingRateError(rate_date, from_currency, to_currency) from exc

    def convert(
        self,
        amount: Decimal,
        rate_date: date,
        from_currency: Currency,
        to_currency: Currency,
    ) -> Decimal:
        if not isinstance(amount, Decimal) or not amount.is_finite():
            raise TypeError("amount must be a finite Decimal")
        if from_currency is to_currency:
            return amount
        return self.get_rate(rate_date, from_currency, to_currency).convert(amount)

    def bundle_for(self, request_id: str, *, sample: bool = False) -> RequestBundle:
        request_index = (
            self.sample_requests_by_request_id if sample else self.requests_by_request_id
        )
        try:
            request = request_index[request_id]
        except KeyError as exc:
            source = "sample" if sample else "production"
            raise MissingReferenceError(
                f"unknown {source} request_id {request_id!r}"
            ) from exc
        profile = self.profiles_by_user_id[request.user_id]
        events = self.events_by_user_id.get(request.user_id, ())
        rate_keys = {
            (event.settlement_date, event.currency, profile.home_currency)
            for event in events
            if event.direction is not Direction.NON_CASH
            and event.currency is not profile.home_currency
            and event.settlement_date is not None
        }
        rates = tuple(
            self.rates_by_key[key]
            for key in sorted(
                rate_keys,
                key=lambda item: (item[0], item[1].value, item[2].value),
            )
        )
        return RequestBundle(
            request=request,
            profile=profile,
            events=events,
            payment_options=self.payment_options_by_request_id.get(request_id, ()),
            messages=self.messages_by_user_id.get(request.user_id, ()),
            images=self.images_by_user_id.get(request.user_id, ()),
            required_exchange_rates=rates,
        )

    def summary(self) -> dict[str, int]:
        return {
            "profiles": len(self.profiles),
            "events": len(self.events),
            "exchange_rates": len(self.exchange_rates),
            "requests": len(self.requests),
            "sample_requests": len(self.sample_requests),
            "payment_options": len(self.payment_options),
            "messages": len(self.messages),
            "images": len(self.images),
            "sample_labels": len(self.sample_labels),
            "output_template_rows": len(self.output_template_rows),
        }


def _require_reference(
    source: SourceRef, column: str, value: str | None, valid: Mapping[object, object]
) -> None:
    if value is not None and value not in valid:
        raise MissingReferenceError(
            f"{source.filename}:{source.row_number} column {column!r}: "
            f"unresolved key {value!r}"
        )


def load_dataset(dataset_dir: str | Path = "dataset") -> DatasetRepository:
    """Load and validate all participant-facing tables into immutable indexes."""

    root = Path(dataset_dir).resolve()
    if not root.is_dir():
        raise MissingDatasetFileError(f"dataset directory not found: {root}")

    raw = {
        filename: _read_table(root, filename)
        for filename in EXPECTED_SCHEMAS
    }
    profiles = tuple(_parse_profile(row) for row in raw["financial_profiles.csv"])
    events = tuple(_parse_event(row) for row in raw["financial_events.csv"])
    rates = tuple(_parse_rate(row) for row in raw["exchange_rates.csv"])
    requests = tuple(_parse_request(row) for row in raw["requests.csv"])
    sample_requests = tuple(
        _parse_request(row) for row in raw["sample_requests.csv"]
    )
    options = tuple(
        _parse_option(row) for row in raw["request_payment_options.csv"]
    )
    messages = tuple(_parse_message(row) for row in raw["messages.csv"])
    images = tuple(_parse_image(row, root) for row in raw["images.csv"])
    sample_labels = tuple(
        _parse_prediction(row) for row in raw["sample_requests.csv"]
    )

    profiles_by_user = _unique_index(
        profiles, lambda item: item.user_id, "financial_profiles.user_id"
    )
    events_by_id = _unique_index(
        events, lambda item: item.event_id, "financial_events.event_id"
    )
    requests_by_id = _unique_index(
        requests, lambda item: item.request_id, "requests.request_id"
    )
    sample_requests_by_id = _unique_index(
        sample_requests,
        lambda item: item.request_id,
        "sample_requests.request_id",
    )
    overlapping_requests = set(requests_by_id) & set(sample_requests_by_id)
    if overlapping_requests:
        raise DuplicateKeyError(
            f"request IDs overlap sample and production: {sorted(overlapping_requests)}"
        )
    all_requests = MappingProxyType(
        {**sample_requests_by_id, **requests_by_id}
    )
    options_by_id = _unique_index(
        options,
        lambda item: item.payment_option_id,
        "request_payment_options.payment_option_id",
    )
    messages_by_id = _unique_index(
        messages, lambda item: item.message_id, "messages.message_id"
    )
    images_by_id = _unique_index(
        images, lambda item: item.image_id, "images.image_id"
    )
    rates_by_key = _unique_index(
        rates,
        lambda item: (item.rate_date, item.from_currency, item.to_currency),
        "exchange_rates(rate_date,from_currency,to_currency)",
    )
    sample_labels_by_id = _unique_index(
        sample_labels,
        lambda item: item.request_id,
        "sample labels request_id",
    )

    for request in (*sample_requests, *requests):
        _require_reference(
            request.source, "user_id", request.user_id, profiles_by_user
        )
    for event in events:
        _require_reference(event.source, "user_id", event.user_id, profiles_by_user)
        _require_reference(
            event.source, "linked_event_id", event.linked_event_id, events_by_id
        )
        if event.linked_event_id:
            target = events_by_id[event.linked_event_id]
            if target.user_id != event.user_id:
                raise InvalidLifecycleLinkError(
                    f"{event.source.filename}:{event.source.row_number}: linked event "
                    f"{target.event_id!r} belongs to another user"
                )
            if target.source.row_number >= event.source.row_number:
                raise InvalidLifecycleLinkError(
                    f"{event.source.filename}:{event.source.row_number}: linked event "
                    f"{target.event_id!r} is not an earlier source row"
                )
    for option in options:
        _require_reference(option.source, "request_id", option.request_id, all_requests)
        request = all_requests[option.request_id]
        if option.payment_amount * option.number_of_payments != option.total_payable_amount:
            raise FieldParseError(
                option.source,
                "total_payable_amount",
                str(option.total_payable_amount),
                "must equal payment_amount multiplied by number_of_payments",
            )
        if request.requested_amount + option.financing_fee != option.total_payable_amount:
            raise FieldParseError(
                option.source,
                "financing_fee",
                str(option.financing_fee),
                "requested_amount plus financing_fee must equal total_payable_amount",
            )
        if (
            option.payment_method is PaymentOptionMethod.FULL_PAYMENT
            and option.first_payment_date != request.request_date
        ):
            raise FieldParseError(
                option.source,
                "first_payment_date",
                option.first_payment_date.isoformat(),
                "full-payment option must begin on request_date",
            )
    for message in messages:
        _require_reference(message.source, "user_id", message.user_id, profiles_by_user)
        _require_reference(message.source, "request_id", message.request_id, all_requests)
        _require_reference(
            message.source, "related_event_id", message.related_event_id, events_by_id
        )
        if message.request_id and all_requests[message.request_id].user_id != message.user_id:
            raise MissingReferenceError(
                f"{message.source.filename}:{message.source.row_number}: request/user mismatch"
            )
        if message.related_event_id and events_by_id[message.related_event_id].user_id != message.user_id:
            raise MissingReferenceError(
                f"{message.source.filename}:{message.source.row_number}: event/user mismatch"
            )
    for image in images:
        _require_reference(image.source, "user_id", image.user_id, profiles_by_user)
        _require_reference(image.source, "request_id", image.request_id, all_requests)
        _require_reference(
            image.source, "related_event_id", image.related_event_id, events_by_id
        )
        if image.request_id and all_requests[image.request_id].user_id != image.user_id:
            raise MissingReferenceError(
                f"{image.source.filename}:{image.source.row_number}: request/user mismatch"
            )
        if image.related_event_id and events_by_id[image.related_event_id].user_id != image.user_id:
            raise MissingReferenceError(
                f"{image.source.filename}:{image.source.row_number}: event/user mismatch"
            )

    options_by_request = _group_index(options, lambda item: item.request_id)
    for request_id in all_requests:
        option_count = len(options_by_request.get(request_id, ()))
        if not 2 <= option_count <= 4:
            raise MissingReferenceError(
                f"request {request_id!r} must have 2-4 payment options; found {option_count}"
            )

    images_by_event = _group_index(
        (image for image in images if image.related_event_id is not None),
        lambda item: item.related_event_id,
    )
    for event in events:
        if event.amount is None:
            evidence_count = len(images_by_event.get(event.event_id, ()))
            if evidence_count != 1:
                raise MissingEvidenceError(
                    f"event {event.event_id!r} has blank amount and requires exactly one "
                    f"linked image; found {evidence_count}"
                )

    for event in events:
        profile = profiles_by_user[event.user_id]
        if event.direction is Direction.NON_CASH or event.currency is profile.home_currency:
            continue
        if event.settlement_date is None:
            raise MissingRateError(
                event.event_date, event.currency, profile.home_currency
            )
        key = (event.settlement_date, event.currency, profile.home_currency)
        if key not in rates_by_key:
            raise MissingRateError(*key)

    output_rows = raw["output.csv"]
    output_template_rows: list[OutputTemplateRow] = []
    for row in output_rows:
        request_id = _required(row, "request_id")
        output_template_rows.append(OutputTemplateRow(request_id, row.source))
        populated = [column for column in OUTPUT_COLUMNS[1:] if row.values[column]]
        if populated:
            raise SchemaError(
                f"{row.source.filename}:{row.source.row_number}: output template has "
                f"populated prediction columns {populated}"
            )
    output_template = tuple(output_template_rows)
    output_template_by_id = _unique_index(
        output_template,
        lambda item: item.request_id,
        "output.request_id",
    )
    if set(output_template_by_id) != set(requests_by_id):
        raise MissingReferenceError(
            "output.csv request IDs do not exactly match production requests.csv"
        )

    return DatasetRepository(
        dataset_dir=root,
        profiles=profiles,
        events=events,
        exchange_rates=rates,
        requests=requests,
        sample_requests=sample_requests,
        payment_options=options,
        messages=messages,
        images=images,
        sample_labels=sample_labels,
        output_template_rows=output_template,
        profiles_by_user_id=profiles_by_user,
        events_by_event_id=events_by_id,
        events_by_user_id=_group_index(events, lambda item: item.user_id),
        requests_by_request_id=requests_by_id,
        sample_requests_by_request_id=sample_requests_by_id,
        all_requests_by_request_id=all_requests,
        requests_by_user_id=_group_index(requests, lambda item: item.user_id),
        sample_requests_by_user_id=_group_index(
            sample_requests, lambda item: item.user_id
        ),
        payment_options_by_payment_option_id=options_by_id,
        payment_options_by_request_id=options_by_request,
        messages_by_user_id=_group_index(messages, lambda item: item.user_id),
        messages_by_message_id=messages_by_id,
        messages_by_request_id=_group_index(
            (item for item in messages if item.request_id is not None),
            lambda item: item.request_id,
        ),
        messages_by_related_event_id=_group_index(
            (item for item in messages if item.related_event_id is not None),
            lambda item: item.related_event_id,
        ),
        images_by_user_id=_group_index(images, lambda item: item.user_id),
        images_by_image_id=images_by_id,
        images_by_request_id=_group_index(
            (item for item in images if item.request_id is not None),
            lambda item: item.request_id,
        ),
        images_by_related_event_id=images_by_event,
        rates_by_key=rates_by_key,
        sample_labels_by_request_id=sample_labels_by_id,
        output_template_by_request_id=output_template_by_id,
    )
