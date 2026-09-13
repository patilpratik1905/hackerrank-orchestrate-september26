"""Deterministic audit for the participant-facing Buy or Wait? dataset.

This module intentionally contains no forecasting or recommendation logic. Run it
from the repository root:

    python code/evaluation/data_audit.py --dataset-dir dataset \
        --report code/evaluation/data_profile.md
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping, Sequence

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.schema import EXPECTED_SCHEMAS  # noqa: E402

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "financial_profiles.csv": ("user_id",),
    "financial_events.csv": ("event_id",),
    "exchange_rates.csv": ("rate_date", "from_currency", "to_currency"),
    "requests.csv": ("request_id",),
    "sample_requests.csv": ("request_id",),
    "request_payment_options.csv": ("payment_option_id",),
    "messages.csv": ("message_id",),
    "images.csv": ("image_id",),
    "output.csv": ("request_id",),
}

DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "financial_events.csv": ("event_date", "settlement_date"),
    "exchange_rates.csv": ("rate_date",),
    "requests.csv": ("request_date", "desired_completion_date"),
    "sample_requests.csv": (
        "request_date",
        "desired_completion_date",
        "earliest_date_for_full_payment",
    ),
    "request_payment_options.csv": ("first_payment_date",),
}

DECIMAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "financial_profiles.csv": (
        "current_available_balance",
        "minimum_balance_to_keep",
    ),
    "financial_events.csv": ("amount", "minimum_allowed_amount"),
    "exchange_rates.csv": ("rate",),
    "requests.csv": ("requested_amount",),
    "sample_requests.csv": ("requested_amount", "amount_safe_to_pay"),
    "request_payment_options.csv": (
        "payment_amount",
        "financing_fee",
        "total_payable_amount",
    ),
}

INTEGER_COLUMNS: dict[str, tuple[str, ...]] = {
    "financial_profiles.csv": ("max_installment_months",),
    "request_payment_options.csv": ("number_of_payments", "payment_frequency_days"),
}

# Columns whose blanks are not explicitly meaningful in the challenge contract.
# Conditional blanks (event amount/settlement date, optional preferences/links,
# sample earliest date, full-payment frequency, and output predictions) are
# validated by their dedicated checks rather than being silently coerced.
REQUIRED_NONEMPTY_COLUMNS: dict[str, tuple[str, ...]] = {
    "financial_profiles.csv": (
        "user_id",
        "home_currency",
        "current_available_balance",
        "minimum_balance_to_keep",
        "financial_priorities",
        "expense_categories_to_protect",
        "payment_methods_user_will_consider",
    ),
    "financial_events.csv": (
        "event_id",
        "user_id",
        "event_type",
        "description",
        "category",
        "direction",
        "currency",
        "event_date",
        "status",
        "flexibility",
    ),
    "exchange_rates.csv": EXPECTED_SCHEMAS["exchange_rates.csv"],
    "requests.csv": EXPECTED_SCHEMAS["requests.csv"],
    "sample_requests.csv": tuple(
        column
        for column in EXPECTED_SCHEMAS["sample_requests.csv"]
        if column != "earliest_date_for_full_payment"
    ),
    "request_payment_options.csv": tuple(
        column
        for column in EXPECTED_SCHEMAS["request_payment_options.csv"]
        if column != "payment_frequency_days"
    ),
    "messages.csv": (
        "message_id",
        "user_id",
        "sent_at",
        "source_type",
        "message_text",
    ),
    "images.csv": EXPECTED_SCHEMAS["images.csv"],
    "output.csv": ("request_id",),
}

ALLOWED_VALUES: dict[tuple[str, str], set[str]] = {
    ("financial_events.csv", "direction"): {"credit", "debit", "non_cash"},
    ("financial_events.csv", "status"): {
        "cancelled",
        "failed",
        "pending",
        "scheduled",
        "settled",
        "unrealized",
    },
    ("financial_events.csv", "flexibility"): {
        "fixed",
        "reducible",
        "reducible_or_stoppable",
        "stoppable",
    },
    ("requests.csv", "allows_partial_payment"): {"false", "true"},
    ("sample_requests.csv", "allows_partial_payment"): {"false", "true"},
    ("request_payment_options.csv", "payment_method"): {
        "full_payment",
        "installments",
    },
}


@dataclass(frozen=True)
class AuditIssue:
    severity: str
    code: str
    message: str


def read_csv_table(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = tuple(reader.fieldnames or ())
        return headers, [dict(row) for row in reader]


def key_for(row: Mapping[str, str], columns: Sequence[str]) -> tuple[str, ...]:
    return tuple(row.get(column, "") for column in columns)


def duplicate_keys(
    rows: Iterable[Mapping[str, str]], columns: Sequence[str]
) -> list[tuple[str, ...]]:
    counts = Counter(key_for(row, columns) for row in rows)
    return sorted(key for key, count in counts.items() if count > 1)


def missing_foreign_keys(
    rows: Iterable[Mapping[str, str]],
    columns: Sequence[str],
    valid_keys: set[tuple[str, ...]],
    *,
    optional: bool = False,
) -> list[tuple[str, ...]]:
    missing: list[tuple[str, ...]] = []
    for row in rows:
        key = key_for(row, columns)
        if optional and all(not value for value in key):
            continue
        if key not in valid_keys:
            missing.append(key)
    return sorted(set(missing))


def parse_decimal(value: str, *, allow_blank: bool) -> Decimal | None:
    if value == "":
        if allow_blank:
            return None
        raise ValueError("required decimal value is blank")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"non-finite decimal value: {value!r}")
    return parsed


def decimal_places(value: str) -> int:
    parsed = parse_decimal(value, allow_blank=False)
    assert parsed is not None
    return max(0, -parsed.as_tuple().exponent)


def parse_iso_date(value: str, *, allow_blank: bool) -> date | None:
    if value == "":
        if allow_blank:
            return None
        raise ValueError("required date value is blank")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO date: {value!r}") from exc


def parse_iso_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value!r}") from exc


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a valid PNG file")
    return struct.unpack(">II", header[16:24])


def _distribution(rows: Iterable[Mapping[str, str]], column: str) -> dict[str, int]:
    return dict(sorted(Counter(row.get(column, "") for row in rows).items()))


def _format_distribution(values: Mapping[str, int]) -> str:
    return ", ".join(f"{key or '<blank>'}={count}" for key, count in values.items())


def run_audit(dataset_dir: Path) -> dict[str, object]:
    tables: dict[str, list[dict[str, str]]] = {}
    headers: dict[str, tuple[str, ...]] = {}
    issues: list[AuditIssue] = []

    for filename, expected in EXPECTED_SCHEMAS.items():
        path = dataset_dir / filename
        if not path.is_file():
            issues.append(AuditIssue("error", "missing_file", filename))
            tables[filename] = []
            headers[filename] = ()
            continue
        actual_headers, rows = read_csv_table(path)
        tables[filename] = rows
        headers[filename] = actual_headers
        if actual_headers != expected:
            issues.append(
                AuditIssue(
                    "error",
                    "schema_mismatch",
                    f"{filename}: expected {expected}, found {actual_headers}",
                )
            )

    for filename, columns in PRIMARY_KEYS.items():
        duplicates = duplicate_keys(tables[filename], columns)
        if duplicates:
            issues.append(
                AuditIssue(
                    "error",
                    "duplicate_primary_key",
                    f"{filename}: {duplicates[:5]}",
                )
            )

    missing_counts: dict[str, dict[str, int]] = {}
    for filename, rows in tables.items():
        missing_counts[filename] = {
            column: sum(1 for row in rows if row.get(column, "") == "")
            for column in headers[filename]
        }

    for filename, columns in REQUIRED_NONEMPTY_COLUMNS.items():
        for column in columns:
            count = missing_counts[filename].get(column, 0)
            if count:
                issues.append(
                    AuditIssue(
                        "error",
                        "unexpected_blank",
                        f"{filename}.{column}: {count} blank values",
                    )
                )

    invalid_domain_values: dict[str, list[str]] = {}
    for (filename, column), allowed in ALLOWED_VALUES.items():
        observed = {row[column] for row in tables[filename] if row[column]}
        unexpected = sorted(observed - allowed)
        if unexpected:
            key = f"{filename}.{column}"
            invalid_domain_values[key] = unexpected
            issues.append(
                AuditIssue(
                    "error",
                    "invalid_domain_value",
                    f"{key}: {unexpected}",
                )
            )

    date_ranges: dict[str, dict[str, dict[str, str | int | None]]] = {}
    for filename, columns in DATE_COLUMNS.items():
        date_ranges[filename] = {}
        for column in columns:
            parsed_values: list[date] = []
            invalid = 0
            for row in tables[filename]:
                value = row[column]
                try:
                    parsed = parse_iso_date(value, allow_blank=True)
                except ValueError:
                    invalid += 1
                    continue
                if parsed is not None:
                    parsed_values.append(parsed)
            date_ranges[filename][column] = {
                "min": min(parsed_values).isoformat() if parsed_values else None,
                "max": max(parsed_values).isoformat() if parsed_values else None,
                "invalid": invalid,
            }
            if invalid:
                issues.append(
                    AuditIssue(
                        "error",
                        "invalid_date",
                        f"{filename}.{column}: {invalid} invalid values",
                    )
                )

    invalid_timestamps = 0
    for row in tables["messages.csv"]:
        try:
            parse_iso_timestamp(row["sent_at"])
        except ValueError:
            invalid_timestamps += 1
    if invalid_timestamps:
        issues.append(
            AuditIssue(
                "error",
                "invalid_timestamp",
                f"messages.csv.sent_at: {invalid_timestamps} invalid values",
            )
        )

    numeric_profile: dict[str, dict[str, dict[str, object]]] = {}
    for filename, columns in DECIMAL_COLUMNS.items():
        numeric_profile[filename] = {}
        for column in columns:
            invalid = negative = parsed_count = max_scale = 0
            parsed_values: list[Decimal] = []
            for row in tables[filename]:
                value = row[column]
                if value == "":
                    continue
                try:
                    parsed = parse_decimal(value, allow_blank=False)
                    assert parsed is not None
                    parsed_values.append(parsed)
                    parsed_count += 1
                    negative += int(parsed < 0)
                    max_scale = max(max_scale, decimal_places(value))
                except ValueError:
                    invalid += 1
            numeric_profile[filename][column] = {
                "parsed": parsed_count,
                "invalid": invalid,
                "negative": negative,
                "max_decimal_places": max_scale,
                "min": str(min(parsed_values)) if parsed_values else None,
                "max": str(max(parsed_values)) if parsed_values else None,
            }
            if invalid:
                issues.append(
                    AuditIssue(
                        "error",
                        "invalid_decimal",
                        f"{filename}.{column}: {invalid} invalid values",
                    )
                )
            if negative:
                issues.append(
                    AuditIssue(
                        "error",
                        "negative_monetary_value",
                        f"{filename}.{column}: {negative} negative values",
                    )
                )

    for filename, columns in INTEGER_COLUMNS.items():
        for column in columns:
            for row_number, row in enumerate(tables[filename], start=2):
                value = row[column]
                if value == "":
                    continue
                try:
                    int(value)
                except ValueError:
                    issues.append(
                        AuditIssue(
                            "error",
                            "invalid_integer",
                            f"{filename}:{row_number}.{column}={value!r}",
                        )
                    )

    profiles = tables["financial_profiles.csv"]
    events = tables["financial_events.csv"]
    requests = tables["requests.csv"]
    samples = tables["sample_requests.csv"]
    options = tables["request_payment_options.csv"]
    messages = tables["messages.csv"]
    images = tables["images.csv"]
    output_rows = tables["output.csv"]
    rates = tables["exchange_rates.csv"]
    all_requests = samples + requests

    profile_ids = {(row["user_id"],) for row in profiles}
    request_ids = {(row["request_id"],) for row in all_requests}
    evaluation_request_ids = {(row["request_id"],) for row in requests}
    event_ids = {(row["event_id"],) for row in events}
    rate_keys = {
        (row["rate_date"], row["from_currency"], row["to_currency"])
        for row in rates
    }
    request_by_id = {row["request_id"]: row for row in all_requests}
    event_by_id = {row["event_id"]: row for row in events}

    def linked_user_mismatches(
        rows: Iterable[Mapping[str, str]], foreign_column: str, targets: Mapping[str, Mapping[str, str]]
    ) -> list[tuple[str, str]]:
        return sorted(
            (row.get("user_id", ""), row[foreign_column])
            for row in rows
            if row.get(foreign_column)
            and row[foreign_column] in targets
            and row.get("user_id") != targets[row[foreign_column]].get("user_id")
        )

    relationship_checks = {
        "sample_evaluation_request_id_overlap": sorted(
            {(row["request_id"],) for row in samples}
            & {(row["request_id"],) for row in requests}
        ),
        "requests_missing_profile": missing_foreign_keys(
            all_requests, ("user_id",), profile_ids
        ),
        "events_missing_profile": missing_foreign_keys(events, ("user_id",), profile_ids),
        "options_missing_request": missing_foreign_keys(
            options, ("request_id",), request_ids
        ),
        "messages_missing_profile": missing_foreign_keys(
            messages, ("user_id",), profile_ids
        ),
        "messages_bad_request": missing_foreign_keys(
            messages, ("request_id",), request_ids, optional=True
        ),
        "messages_bad_related_event": missing_foreign_keys(
            messages, ("related_event_id",), event_ids, optional=True
        ),
        "messages_request_user_mismatch": linked_user_mismatches(
            messages, "request_id", request_by_id
        ),
        "messages_event_user_mismatch": linked_user_mismatches(
            messages, "related_event_id", event_by_id
        ),
        "images_missing_profile": missing_foreign_keys(images, ("user_id",), profile_ids),
        "images_bad_request": missing_foreign_keys(
            images, ("request_id",), request_ids, optional=True
        ),
        "images_bad_related_event": missing_foreign_keys(
            images, ("related_event_id",), event_ids, optional=True
        ),
        "images_request_user_mismatch": linked_user_mismatches(
            images, "request_id", request_by_id
        ),
        "images_event_user_mismatch": linked_user_mismatches(
            images, "related_event_id", event_by_id
        ),
        "linked_event_missing_target": sorted(
            (row["event_id"], row["linked_event_id"])
            for row in events
            if row["linked_event_id"]
            and (row["linked_event_id"],) not in event_ids
        ),
        "output_request_mismatch": sorted(
            evaluation_request_ids.symmetric_difference(
                {(row["request_id"],) for row in output_rows}
            )
        ),
    }
    for check, missing in relationship_checks.items():
        if missing:
            issues.append(AuditIssue("error", check, str(missing[:5])))

    event_position = {row["event_id"]: index for index, row in enumerate(events)}
    linked_count = linked_wrong_user = linked_not_earlier = 0
    for event in events:
        linked_id = event["linked_event_id"]
        if not linked_id:
            continue
        linked_count += 1
        target = event_by_id.get(linked_id)
        if target is None:
            continue
        linked_wrong_user += int(target["user_id"] != event["user_id"])
        linked_not_earlier += int(event_position[linked_id] >= event_position[event["event_id"]])
    if linked_wrong_user or linked_not_earlier:
        issues.append(
            AuditIssue(
                "error",
                "invalid_lifecycle_link",
                f"wrong_user={linked_wrong_user}, not_earlier={linked_not_earlier}",
            )
        )

    profile_by_user = {row["user_id"]: row for row in profiles}
    requests_by_user: dict[str, list[dict[str, str]]] = defaultdict(list)
    for request in all_requests:
        requests_by_user[request["user_id"]].append(request)
    request_count_per_user = Counter(len(value) for value in requests_by_user.values())

    relative_events = Counter()
    for event in events:
        user_requests = requests_by_user.get(event["user_id"], [])
        if len(user_requests) != 1:
            relative_events["ambiguous_request_anchor"] += 1
            continue
        request_date = date.fromisoformat(user_requests[0]["request_date"])
        if not event["settlement_date"]:
            relative_events["no_settlement_date"] += 1
            continue
        settlement_date = date.fromisoformat(event["settlement_date"])
        if settlement_date < request_date:
            relative_events["before_request"] += 1
        elif settlement_date == request_date:
            relative_events["on_request"] += 1
        else:
            relative_events["after_request"] += 1

    foreign_events = []
    missing_rate_events = []
    for event in events:
        profile = profile_by_user.get(event["user_id"])
        if profile is None or event["direction"] == "non_cash":
            continue
        if event["currency"] == profile["home_currency"]:
            continue
        foreign_events.append(event["event_id"])
        rate_key = (
            event["settlement_date"],
            event["currency"],
            profile["home_currency"],
        )
        if not event["settlement_date"] or rate_key not in rate_keys:
            missing_rate_events.append(event["event_id"])
    if missing_rate_events:
        issues.append(
            AuditIssue(
                "error",
                "missing_fx_rate",
                f"events={missing_rate_events[:10]}",
            )
        )

    images_by_event: dict[str, list[dict[str, str]]] = defaultdict(list)
    png_inventory: list[dict[str, object]] = []
    for image in images:
        if image["related_event_id"]:
            images_by_event[image["related_event_id"]].append(image)
        path = dataset_dir / "media" / "images" / f"{image['image_id']}.png"
        try:
            width, height = png_dimensions(path)
            png_inventory.append(
                {
                    "image_id": image["image_id"],
                    "related_event_id": image["related_event_id"],
                    "exists": True,
                    "width": width,
                    "height": height,
                    "bytes": path.stat().st_size,
                }
            )
        except (FileNotFoundError, ValueError) as exc:
            png_inventory.append(
                {
                    "image_id": image["image_id"],
                    "related_event_id": image["related_event_id"],
                    "exists": False,
                    "error": str(exc),
                }
            )
            issues.append(
                AuditIssue("error", "invalid_image_file", f"{image['image_id']}: {exc}")
            )

    blank_amount_events = [event for event in events if event["amount"] == ""]
    invalid_blank_settlement_events = [
        event["event_id"]
        for event in events
        if event["settlement_date"] == ""
        and not (
            event["status"] == "unrealized" and event["direction"] == "non_cash"
        )
    ]
    if invalid_blank_settlement_events:
        issues.append(
            AuditIssue(
                "error",
                "unexpected_blank_settlement_date",
                str(invalid_blank_settlement_events[:10]),
            )
        )
    blank_amount_without_image = [
        event["event_id"]
        for event in blank_amount_events
        if not images_by_event.get(event["event_id"])
    ]
    blank_amount_ambiguous_images = [
        event["event_id"]
        for event in blank_amount_events
        if len(images_by_event.get(event["event_id"], [])) != 1
    ]
    if blank_amount_without_image or blank_amount_ambiguous_images:
        issues.append(
            AuditIssue(
                "error",
                "blank_amount_image_coverage",
                "missing="
                f"{blank_amount_without_image}, non_unique={blank_amount_ambiguous_images}",
            )
        )

    message_scope = Counter()
    for message in messages:
        if message["related_event_id"]:
            message_scope["event-linked"] += 1
        elif message["request_id"]:
            message_scope["request-linked"] += 1
        else:
            message_scope["user-level"] += 1

    options_per_request = Counter(
        len(group)
        for group in _group_rows(options, "request_id").values()
    )
    payment_option_issues: list[str] = []
    option_groups = _group_rows(options, "request_id")
    for request in all_requests:
        option_count = len(option_groups.get(request["request_id"], []))
        if not 2 <= option_count <= 4:
            payment_option_issues.append(
                f"{request['request_id']}: expected 2-4 options, found {option_count}"
            )
    for option in options:
        request = request_by_id.get(option["request_id"])
        if request is None:
            continue
        try:
            payment = parse_decimal(option["payment_amount"], allow_blank=False)
            fee = parse_decimal(option["financing_fee"], allow_blank=False)
            total = parse_decimal(option["total_payable_amount"], allow_blank=False)
            requested = parse_decimal(request["requested_amount"], allow_blank=False)
            number = int(option["number_of_payments"])
            assert None not in (payment, fee, total, requested)
            if payment * number != total:
                payment_option_issues.append(f"{option['payment_option_id']}: payment*count")
            if requested + fee != total:
                payment_option_issues.append(f"{option['payment_option_id']}: request+fee")
            if option["payment_method"] == "full_payment":
                if number != 1 or option["payment_frequency_days"]:
                    payment_option_issues.append(
                        f"{option['payment_option_id']}: full-payment shape"
                    )
                if option["first_payment_date"] != request["request_date"]:
                    payment_option_issues.append(
                        f"{option['payment_option_id']}: full-payment date"
                    )
            elif option["payment_method"] == "installments":
                if number <= 1 or not option["payment_frequency_days"]:
                    payment_option_issues.append(
                        f"{option['payment_option_id']}: installment shape"
                    )
            else:
                payment_option_issues.append(
                    f"{option['payment_option_id']}: unknown method"
                )
        except (ValueError, AssertionError) as exc:
            payment_option_issues.append(f"{option['payment_option_id']}: {exc}")
    if payment_option_issues:
        issues.append(
            AuditIssue(
                "error",
                "payment_option_integrity",
                str(payment_option_issues[:10]),
            )
        )

    populated_output_fields = sum(
        1
        for row in output_rows
        for column in EXPECTED_SCHEMAS["output.csv"][1:]
        if row[column]
    )
    if populated_output_fields:
        issues.append(
            AuditIssue(
                "warning",
                "output_template_populated",
                f"{populated_output_fields} prediction cells are populated",
            )
        )

    edge_case_inventory = build_edge_case_inventory(samples, events, images, profiles)

    distributions = {
        "profile_home_currency": _distribution(profiles, "home_currency"),
        "profile_payment_methods": _distribution(
            profiles, "payment_methods_user_will_consider"
        ),
        "request_type": _distribution(requests, "request_type"),
        "request_allows_partial": _distribution(requests, "allows_partial_payment"),
        "event_status": _distribution(events, "status"),
        "event_direction": _distribution(events, "direction"),
        "event_type": _distribution(events, "event_type"),
        "event_flexibility": _distribution(events, "flexibility"),
        "event_currency": _distribution(events, "currency"),
        "event_category": _distribution(events, "category"),
        "payment_method": _distribution(options, "payment_method"),
        "payment_count": _distribution(options, "number_of_payments"),
        "payment_frequency_days": _distribution(options, "payment_frequency_days"),
        "message_source_type": _distribution(messages, "source_type"),
        "sample_affordability_status": _distribution(samples, "affordability_status"),
        "sample_payment_method": _distribution(samples, "recommended_payment_method"),
        "sample_spending_changes": {
            "none": sum(row["spending_changes_needed"] == "none" for row in samples),
            "present": sum(row["spending_changes_needed"] != "none" for row in samples),
        },
    }

    return {
        "dataset_dir": str(dataset_dir.resolve()),
        "row_counts": {filename: len(rows) for filename, rows in tables.items()},
        "headers": {filename: list(value) for filename, value in headers.items()},
        "missing_counts": missing_counts,
        "invalid_domain_values": invalid_domain_values,
        "date_ranges": date_ranges,
        "numeric_profile": numeric_profile,
        "invalid_message_timestamps": invalid_timestamps,
        "relationship_checks": {
            name: len(value) for name, value in relationship_checks.items()
        },
        "request_count_per_user": dict(sorted(request_count_per_user.items())),
        "relative_events": dict(sorted(relative_events.items())),
        "linked_events": {
            "count": linked_count,
            "wrong_user": linked_wrong_user,
            "not_earlier_in_source": linked_not_earlier,
        },
        "foreign_cash_events": len(foreign_events),
        "foreign_cash_events_missing_rate": len(missing_rate_events),
        "blank_amount_events": len(blank_amount_events),
        "blank_amount_events_without_image": len(blank_amount_without_image),
        "blank_amount_events_with_non_unique_image": len(
            blank_amount_ambiguous_images
        ),
        "invalid_blank_settlement_dates": len(invalid_blank_settlement_events),
        "png_inventory": png_inventory,
        "message_scope": dict(sorted(message_scope.items())),
        "options_per_request": dict(sorted(options_per_request.items())),
        "payment_option_issues": payment_option_issues,
        "output_template_populated_prediction_cells": populated_output_fields,
        "distributions": distributions,
        "edge_case_inventory": edge_case_inventory,
        "issues": [asdict(issue) for issue in issues],
        "error_count": sum(issue.severity == "error" for issue in issues),
        "warning_count": sum(issue.severity == "warning" for issue in issues),
    }


def _group_rows(
    rows: Iterable[Mapping[str, str]], column: str
) -> dict[str, list[Mapping[str, str]]]:
    groups: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row[column]].append(row)
    return dict(groups)


def build_edge_case_inventory(
    samples: list[dict[str, str]],
    events: list[dict[str, str]],
    images: list[dict[str, str]],
    profiles: list[dict[str, str]],
) -> dict[str, list[str]]:
    inventory: dict[str, list[str]] = {}

    def sample_ids(predicate) -> list[str]:
        return [row["request_id"] for row in samples if predicate(row)]

    inventory["affordable_full_payment"] = sample_ids(
        lambda row: row["affordability_status"] == "affordable_now"
        and row["recommended_payment_method"] == "full_payment"
    )
    inventory["safe_installments"] = sample_ids(
        lambda row: row["recommended_payment_method"] == "installments"
    )
    inventory["wait_for_full_payment"] = sample_ids(
        lambda row: row["recommended_payment_method"] == "wait"
    )
    inventory["not_affordable"] = sample_ids(
        lambda row: row["recommended_payment_method"] == "not_recommended"
    )
    inventory["valid_partial_payment"] = sample_ids(
        lambda row: row["recommended_payment_method"] == "partial_payment"
    )
    inventory["spending_change"] = sample_ids(
        lambda row: row["spending_changes_needed"] != "none"
    )
    image_request_ids = {row["request_id"] for row in images}
    inventory["sample_with_image"] = [
        row["request_id"] for row in samples if row["request_id"] in image_request_ids
    ]
    profile_by_user = {row["user_id"]: row for row in profiles}
    inventory["refuses_financially_safe_full_payment"] = [
        row["request_id"]
        for row in samples
        if row["amount_safe_to_pay"] == row["requested_amount"]
        and row["recommended_payment_method"] != "full_payment"
        and "full_payment"
        not in profile_by_user[row["user_id"]]["payment_methods_user_will_consider"].split(
            "|"
        )
    ]

    event_cases = {
        "pending_debit": lambda row: row["status"] == "pending"
        and row["direction"] == "debit",
        "pending_credit_or_refund": lambda row: row["status"] == "pending"
        and row["direction"] == "credit",
        "cancelled_or_failed": lambda row: row["status"] in {"cancelled", "failed"},
        "unrealized_investment": lambda row: row["status"] == "unrealized",
        "linked_lifecycle": lambda row: bool(row["linked_event_id"]),
        "blank_amount_with_image": lambda row: row["amount"] == "",
    }
    for name, predicate in event_cases.items():
        inventory[name] = [row["event_id"] for row in events if predicate(row)][:5]

    profile_currency = {row["user_id"]: row["home_currency"] for row in profiles}
    inventory["foreign_currency_event"] = [
        row["event_id"]
        for row in events
        if row["direction"] != "non_cash"
        and row["currency"] != profile_currency[row["user_id"]]
    ][:5]
    return inventory


def render_markdown(result: Mapping[str, object]) -> str:
    row_counts = result["row_counts"]
    assert isinstance(row_counts, dict)
    distributions = result["distributions"]
    assert isinstance(distributions, dict)
    missing_counts = result["missing_counts"]
    assert isinstance(missing_counts, dict)
    edge_cases = result["edge_case_inventory"]
    assert isinstance(edge_cases, dict)
    issues = result["issues"]
    assert isinstance(issues, list)

    lines = [
        "# Buy or Wait? Data Profile",
        "",
        "Generated by `python code/evaluation/data_audit.py --dataset-dir dataset "
        "--report code/evaluation/data_profile.md`.",
        "",
        "This report audits participant-facing data only. It contains no prediction logic "
        "and does not modify source files.",
        "",
        "## Result",
        "",
        f"- Errors: {result['error_count']}",
        f"- Warnings: {result['warning_count']}",
        "- Overall: " + ("PASS" if result["error_count"] == 0 else "FAIL"),
        "",
        "## Table inventory",
        "",
        "| File | Rows | Columns |",
        "| --- | ---: | ---: |",
    ]
    headers = result["headers"]
    assert isinstance(headers, dict)
    for filename in EXPECTED_SCHEMAS:
        lines.append(
            f"| `{filename}` | {row_counts[filename]} | {len(headers[filename])} |"
        )

    lines.extend(
        [
            "",
            "## Integrity controls",
            "",
            f"- Foreign-currency cash events: {result['foreign_cash_events']}",
            "- Foreign-currency events without an exact dated rate: "
            f"{result['foreign_cash_events_missing_rate']}",
            f"- Blank event amounts: {result['blank_amount_events']}",
            "- Blank amounts without exactly one linked image: "
            f"{result['blank_amount_events_without_image']} missing; "
            f"{result['blank_amount_events_with_non_unique_image']} non-unique",
            "- Unexpected blank settlement dates: "
            f"{result['invalid_blank_settlement_dates']}",
            f"- Linked lifecycle events: {result['linked_events']}",
            f"- Requests per user distribution: {result['request_count_per_user']}",
            f"- Events relative to request: {result['relative_events']}",
            f"- Payment options per request: {result['options_per_request']}",
            f"- Message scope: {result['message_scope']}",
            "- Populated prediction cells in blank output template: "
            f"{result['output_template_populated_prediction_cells']}",
            "",
            "### Relationship failures",
            "",
        ]
    )
    relationship_checks = result["relationship_checks"]
    assert isinstance(relationship_checks, dict)
    for name, count in relationship_checks.items():
        lines.append(f"- `{name}`: {count}")

    lines.extend(["", "## Missing values", ""])
    for filename in EXPECTED_SCHEMAS:
        values = {
            column: count
            for column, count in missing_counts[filename].items()
            if count
        }
        lines.append(
            f"- `{filename}`: "
            + (_format_distribution(values) if values else "none")
        )

    lines.extend(["", "## Date ranges", ""])
    date_ranges = result["date_ranges"]
    assert isinstance(date_ranges, dict)
    for filename, columns in date_ranges.items():
        assert isinstance(columns, dict)
        for column, profile in columns.items():
            assert isinstance(profile, dict)
            lines.append(
                f"- `{filename}.{column}`: {profile['min']} to {profile['max']} "
                f"(invalid={profile['invalid']})"
            )

    lines.extend(["", "## Monetary fields", ""])
    numeric_profile = result["numeric_profile"]
    assert isinstance(numeric_profile, dict)
    for filename, columns in numeric_profile.items():
        assert isinstance(columns, dict)
        for column, profile in columns.items():
            assert isinstance(profile, dict)
            lines.append(
                f"- `{filename}.{column}`: parsed={profile['parsed']}, "
                f"invalid={profile['invalid']}, negative={profile['negative']}, "
                f"range={profile['min']} to {profile['max']}, "
                f"max_decimal_places={profile['max_decimal_places']}"
            )

    lines.extend(["", "## Important distributions", ""])
    for name, values in distributions.items():
        assert isinstance(values, dict)
        lines.append(f"- `{name}`: {_format_distribution(values)}")

    lines.extend(["", "## Image inventory", ""])
    png_inventory = result["png_inventory"]
    assert isinstance(png_inventory, list)
    lines.extend(
        [
            "All listed sources must be valid PNGs; image totals remain unresolved until "
            "the evidence-extraction step.",
            "",
            "| Image | Related event | Dimensions | Bytes |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for item in png_inventory:
        assert isinstance(item, dict)
        dimensions = (
            f"{item['width']}x{item['height']}" if item.get("exists") else "invalid"
        )
        lines.append(
            f"| `{item['image_id']}` | `{item['related_event_id']}` | "
            f"{dimensions} | {item.get('bytes', 0)} |"
        )

    lines.extend(["", "## Representative edge-case inventory", ""])
    for name, identifiers in edge_cases.items():
        assert isinstance(identifiers, list)
        lines.append(f"- `{name}`: {', '.join(identifiers) if identifiers else 'none found'}")

    lines.extend(
        [
            "",
            "## Expected anomalies",
            "",
            "- `dataset/output.csv` is intentionally a blank prediction template.",
            "- Sixteen financial-event amounts are intentionally blank and require linked "
            "image extraction; this audit verifies coverage but does not extract them.",
            "- Optional `linked_event_id`, `minimum_allowed_amount`, request/message links, "
            "and installment limits may be blank by contract.",
            "- Pending credits and unrealized values are present as decision traps; their "
            "presence is not a data-quality failure.",
            "",
            "## Audit issues",
            "",
        ]
    )
    if not issues:
        lines.append("No audit errors or warnings.")
    else:
        for issue in issues:
            assert isinstance(issue, dict)
            lines.append(
                f"- **{issue['severity'].upper()} `{issue['code']}`:** {issue['message']}"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    args = parser.parse_args()

    result = run_audit(args.dataset_dir)
    rendered = (
        json.dumps(result, indent=2, sort_keys=True)
        if args.json
        else render_markdown(result)
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_markdown(result), encoding="utf-8", newline="\n")
    print(rendered)
    return 1 if result["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
