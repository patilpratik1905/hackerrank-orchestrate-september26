"""Run the lightweight foundation verification gate after evidence extraction.

This gate deliberately checks only Steps 1--5.  It does not construct financial
state, forecast cash flow, or make recommendations.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import fields
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable


CODE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_DIR.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.data import MissingRateError, load_dataset  # noqa: E402
from buy_or_wait.evidence import (  # noqa: E402
    DECISION_FIELDS,
    IMAGE_EXTRACTOR_VERSION,
    EvidenceCache,
    EvidenceFact,
    EvidenceSourceType,
    EvidenceValidationError,
    extract_message_deterministically,
    extract_repository_evidence,
    parse_structured_model_output,
    seed_reviewed_image_cache,
    sha256_file,
)
from buy_or_wait.models import Currency  # noqa: E402
from buy_or_wait.schema import EXPECTED_SCHEMAS  # noqa: E402
from evaluation.data_audit import read_csv_table, run_audit  # noqa: E402
from evaluation.main import evaluate_file, project_expected_output  # noqa: E402
from evaluation.validators import (  # noqa: E402
    load_prediction_file,
    load_validation_context,
    validate_prediction_row,
    write_prediction_rows,
)


DATASET_DIR = REPO_ROOT / "dataset"
DEFAULT_JSON = CODE_DIR / "evaluation" / "gate_a_foundation.json"
DEFAULT_MD = CODE_DIR / "evaluation" / "gate_a_foundation.md"


def _issue_codes(row: dict[str, str], context: object) -> set[str]:
    return {issue.code for issue in validate_prediction_row(row, context).issues}


def _raise_if_not(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check(results: list[dict[str, object]], name: str, action: Callable[[], object]) -> object | None:
    try:
        details = action()
    except Exception as exc:  # Report every independent foundation check.
        results.append({"name": name, "status": "FAIL", "details": str(exc)})
        return None
    results.append({"name": name, "status": "PASS", "details": details})
    return details


def _expected_counts() -> dict[str, int]:
    return {
        "financial_profiles.csv": 275,
        "financial_events.csv": 25342,
        "exchange_rates.csv": 134,
        "requests.csv": 250,
        "sample_requests.csv": 25,
        "request_payment_options.csv": 790,
        "messages.csv": 215,
        "images.csv": 16,
        "output.csv": 250,
    }


def _render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# VERIFY A — Foundation Gate",
        "",
        f"**Status:** {report['status']}",
        "",
        "This report verifies Steps 1–5 only; no state reconstruction or simulation was run.",
        "",
        "## Checks",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
    ]
    for item in report["checks"]:  # type: ignore[index]
        detail = json.dumps(item["details"], sort_keys=True) if not isinstance(item["details"], str) else item["details"]
        lines.append(f"| {item['name']} | {item['status']} | {detail.replace('|', '&#124;')} |")
    lines.extend(["", "## Commands", ""])
    for command in report["commands"]:  # type: ignore[index]
        lines.append(f"- `{command}`")
    traces = report.get("resolved_image_evidence", [])
    lines.extend(["", "## Required blank-amount evidence", "", "| Image | Event | Amount | Currency | Version | Hash | Provenance |", "|---|---|---:|---|---|---|---|"])
    for trace in traces:  # type: ignore[union-attr]
        lines.append(
            "| {image_id} | {event_id} | {amount} | {currency} | {extractor_version} | "
            "{source_hash} | {provenance} |".format(**trace)
        )
    lines.extend(["", "## Gate decision", ""])
    if report["status"] == "PASS":
        lines.append("All mandatory foundation checks passed. Step 6 may begin.")
    else:
        lines.append("Do not begin Step 6. Fix the earliest owning Step 1–5 component, add a regression test, and rerun this gate.")
    return "\n".join(lines) + "\n"


def run_gate() -> dict[str, object]:
    checks: list[dict[str, object]] = []
    commands = [
        "python -m unittest discover -s code\\tests -v",
        "python code\\evaluation\\gate_a.py",
    ]
    audit = run_audit(DATASET_DIR)
    production_repository = load_dataset(DATASET_DIR)
    reviewed_seed = CODE_DIR / "evidence" / "reviewed_images.json"

    def evaluator_check() -> dict[str, object]:
        _, samples = read_csv_table(DATASET_DIR / "sample_requests.csv")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "solved_predictions.csv"
            write_prediction_rows(path, [project_expected_output(row) for row in samples])
            report = evaluate_file(DATASET_DIR, path)
        metrics = report["metrics"]
        _raise_if_not(not report["mismatches"], "solved predictions have field mismatches")
        _raise_if_not(bool(report["summary"]["structurally_valid"]), "solved predictions are not structurally valid")
        for metric in metrics.values():
            if isinstance(metric, dict) and "accuracy" in metric:
                _raise_if_not(metric["accuracy"] == 1.0, f"non-perfect sample metric: {metric}")
        return {"rows": len(samples), "all_metrics": "100%"}

    _check(checks, "evaluator: solved outputs score 100% field-by-field", evaluator_check)

    def evaluator_mutations() -> dict[str, object]:
        _, samples = read_csv_table(DATASET_DIR / "sample_requests.csv")
        by_id = {row["request_id"]: row for row in samples}
        context = load_validation_context(DATASET_DIR, samples)
        cases: dict[str, tuple[dict[str, str], str]] = {}
        amount = project_expected_output(by_id["request_01"])
        amount["amount_safe_to_pay"] = "99999999.99"
        cases["amount_bound"] = (amount, "safe_amount_out_of_bounds")
        enum = project_expected_output(by_id["request_01"])
        enum["affordability_status"] = "maybe"
        cases["enum"] = (enum, "invalid_affordability_status")
        bad_date = project_expected_output(by_id["request_01"])
        bad_date["earliest_date_for_full_payment"] = "03-03-2024"
        cases["date"] = (bad_date, "invalid_earliest_date")
        order = project_expected_output(by_id["request_02"])
        order["payment_plan"] = "|".join(reversed(order["payment_plan"].split("|")))
        cases["payment_order"] = (order, "nonchronological_payment_plan")
        partial = project_expected_output(by_id["request_19"])
        partial["payment_plan"] = "2024-09-04:28820|2024-09-15:10839.99"
        cases["partial_sum"] = (partial, "partial_payment_sum_or_schedule")
        installments = project_expected_output(by_id["request_02"])
        installments["payment_plan"] = installments["payment_plan"].replace("15952906.67", "15952906.66", 1)
        cases["installment_option"] = (installments, "installment_option_mismatch")
        spending = project_expected_output(by_id["request_06"])
        spending["spending_changes_needed"] = "delete:event_476"
        cases["spending_action"] = (spending, "invalid_spending_change_grammar")
        detected: dict[str, str] = {}
        for name, (row, expected) in cases.items():
            codes = _issue_codes(row, context)
            _raise_if_not(expected in codes, f"{name} mutation missed; received {sorted(codes)}")
            detected[name] = expected
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.csv"
            rows = [project_expected_output(row) for row in samples]
            rows.append(dict(rows[0]))
            write_prediction_rows(path, rows)
            _, issues, _ = load_prediction_file(path, set(by_id))
        _raise_if_not("duplicate_request_id" in {issue.code for issue in issues}, "duplicate ID mutation missed")
        detected["duplicate_id"] = "duplicate_request_id"
        return detected

    _check(checks, "evaluator: required corruptions are rejected", evaluator_mutations)

    def integrity_check() -> dict[str, object]:
        _raise_if_not(audit["row_counts"] == _expected_counts(), f"unexpected rows: {audit['row_counts']}")
        _raise_if_not(audit["error_count"] == 0 and audit["warning_count"] == 0, "audit contains errors or warnings")
        _raise_if_not(all(value == 0 for value in audit["relationship_checks"].values()), "broken foreign-key relationship")
        _raise_if_not(audit["linked_events"]["wrong_user"] == 0, "linked event crosses users")
        _raise_if_not(audit["linked_events"]["not_earlier_in_source"] == 0, "linked event is not earlier")
        for filename, expected_header in EXPECTED_SCHEMAS.items():
            headers, _ = read_csv_table(DATASET_DIR / filename)
            _raise_if_not(tuple(headers) == expected_header, f"schema mismatch: {filename}")
        for request in production_repository.requests:
            production_repository.bundle_for(request.request_id)
        for request in production_repository.sample_requests:
            production_repository.bundle_for(request.request_id, sample=True)
        _raise_if_not(not production_repository.sample_labels, "production loader exposed solved labels")
        _raise_if_not(not production_repository.sample_labels_by_request_id, "production label index is populated")
        _raise_if_not(
            all(event.amount is None or isinstance(event.amount, Decimal) for event in production_repository.events),
            "blank amount was converted to a numeric value",
        )
        return {"rows": audit["row_counts"], "bundles": 275, "blank_event_amounts": audit["blank_amount_events"]}

    _check(checks, "data layer: schemas, joins, bundles, None handling, label isolation", integrity_check)

    def money_check() -> dict[str, object]:
        values = [
            *(profile.current_available_balance for profile in production_repository.profiles),
            *(profile.minimum_balance_to_keep for profile in production_repository.profiles),
            *(event.amount for event in production_repository.events if event.amount is not None),
            *(event.minimum_allowed_amount for event in production_repository.events if event.minimum_allowed_amount is not None),
            *(option.payment_amount for option in production_repository.payment_options),
            *(option.financing_fee for option in production_repository.payment_options),
            *(option.total_payable_amount for option in production_repository.payment_options),
            *(rate.rate for rate in production_repository.exchange_rates),
        ]
        _raise_if_not(all(type(value) is Decimal for value in values), "non-Decimal monetary runtime value")
        _raise_if_not(Decimal("0.1") + Decimal("0.2") == Decimal("0.3"), "Decimal precision fixture failed")
        _raise_if_not(all("e" not in str(value).lower() for value in values), "scientific notation in Decimal serialization")
        sources = [*CODE_DIR.joinpath("buy_or_wait").glob("*.py"), CODE_DIR / "main.py"]
        float_hits = []
        for source in sources:
            text = source.read_text(encoding="utf-8")
            if re.search(r"\bfloat\s*\(|Decimal\s*\(\s*float", text):
                float_hits.append(str(source.relative_to(REPO_ROOT)))
        _raise_if_not(not float_hits, f"float conversion in financial production code: {float_hits}")
        return {"runtime_decimal_values": len(values), "float_conversion_hits": 0}

    _check(checks, "money: Decimal runtime precision and serialization", money_check)

    def fx_check() -> dict[str, object]:
        converted = production_repository.convert(
            Decimal("10.25"), date(2023, 10, 15), Currency.EUR, Currency.ZAR
        )
        _raise_if_not(converted == Decimal("205.00"), f"unexpected EUR/ZAR conversion: {converted}")
        for rate_date, source, target in (
            (date(1900, 1, 1), Currency.ZAR, Currency.EUR),
            (date(2023, 10, 15), Currency.ZAR, Currency.EUR),
        ):
            try:
                production_repository.convert(Decimal("10"), rate_date, source, target)
            except MissingRateError:
                continue
            raise AssertionError(f"missing/reversed rate did not fail: {rate_date} {source}->{target}")
        _raise_if_not(audit["foreign_cash_events_missing_rate"] == 0, "foreign cash event lacks exact rate")
        return {"foreign_cash_events": audit["foreign_cash_events"], "spot_check": "10.25 EUR -> 205.00 ZAR"}

    _check(checks, "FX: exact dated directional conversion only", fx_check)

    trace_rows: list[dict[str, str]] = []

    def evidence_check() -> dict[str, object]:
        nonlocal trace_rows
        with tempfile.TemporaryDirectory() as temporary:
            cache_path = Path(temporary) / "cache.json"
            cache = EvidenceCache(cache_path)
            seeded = seed_reviewed_image_cache(production_repository, cache, reviewed_seed)
            first = extract_repository_evidence(production_repository, cache)
            cache.save()
            second = extract_repository_evidence(production_repository, EvidenceCache(cache_path))
            _raise_if_not(first.cache_misses == 215 and first.cache_hits == 16, f"unexpected first cache stats: {first.cache_hits}/{first.cache_misses}")
            _raise_if_not(second.cache_misses == 0 and second.cache_hits == 231, f"cache did not fully reuse: {second.cache_hits}/{second.cache_misses}")
            blank_events = [event for event in production_repository.events if event.amount is None]
            _raise_if_not(len(blank_events) == 16 and len(first.resolved_amounts) == 16, "not every blank amount resolved")
            for image in production_repository.images:
                event_id = image.related_event_id
                _raise_if_not(event_id is not None, f"image lacks related event: {image.image_id}")
                event = production_repository.events_by_event_id[event_id]
                facts = first.facts_by_source_id.get(image.image_id, ())
                _raise_if_not(len(facts) == 1, f"image fact count for {image.image_id}: {len(facts)}")
                fact = facts[0]
                _raise_if_not(event.amount is None and fact.amount == first.resolved_amounts[event_id], f"unresolved/mismatched {event_id}")
                _raise_if_not(fact.currency is event.currency, f"currency mismatch for {image.image_id}")
                _raise_if_not(fact.source_hash == sha256_file(image.path), f"hash mismatch for {image.image_id}")
                _raise_if_not(fact.extractor_version == IMAGE_EXTRACTOR_VERSION, f"version mismatch for {image.image_id}")
                _raise_if_not(fact.provenance == image.source, f"provenance mismatch for {image.image_id}")
                trace_rows.append({
                    "image_id": image.image_id,
                    "event_id": event_id,
                    "amount": str(fact.amount),
                    "currency": fact.currency.value if fact.currency else "",
                    "extractor_version": fact.extractor_version,
                    "source_hash": fact.source_hash[:12],
                    "provenance": f"{fact.provenance.filename}:{fact.provenance.row_number}",
                })
            _raise_if_not(first.resolved_amounts["event_253"] == Decimal("4365000"), "net-pay slip spot check failed")
            _raise_if_not(first.resolved_amounts["event_1442"] == Decimal("100000.00"), "rent-balance receipt spot check failed")
            image = production_repository.images[0]
            missed = EvidenceCache(cache_path).get(
                image.image_id, IMAGE_EXTRACTOR_VERSION, "0" * 64,
                source_type=EvidenceSourceType.IMAGE, user_id=image.user_id,
                request_id=image.request_id, related_event_id=image.related_event_id,
                provenance=image.source,
            )
            _raise_if_not(missed is None, "changed content hash returned stale cache entry")
        kwargs = {
            "source_type": EvidenceSourceType.MESSAGE,
            "source_id": "fixture",
            "user_id": "user_fixture",
            "request_id": None,
            "related_event_id": None,
            "source_timestamp": None,
            "source_hash": "a" * 64,
            "extractor_version": "fixture-v1",
            "provenance": production_repository.messages[0].source,
        }
        for payload in ("not json", '{"facts": [], "recommended_payment_method": "full_payment"}'):
            try:
                parse_structured_model_output(payload, **kwargs)
            except EvidenceValidationError:
                continue
            raise AssertionError("malformed or decision-bearing model output was accepted")
        injection = extract_message_deterministically(production_repository.messages_by_message_id["message_142"])
        _raise_if_not(bool(injection), "prompt-injection fixture produced no evidence fact")
        _raise_if_not(not (set(field.name for field in fields(EvidenceFact)) & DECISION_FIELDS), "evidence schema exposes decision field")
        return {"seeded_images": seeded, "facts": len(first.facts), "resolved_blank_amounts": len(first.resolved_amounts), "cache_second_run": {"hits": second.cache_hits, "misses": second.cache_misses}}

    _check(checks, "evidence: all blank amounts traceable, cached, and decision-isolated", evidence_check)

    def anti_overfit_check() -> dict[str, object]:
        paths = [*CODE_DIR.joinpath("buy_or_wait").glob("*.py"), CODE_DIR / "main.py", CODE_DIR / "evidence" / "main.py"]
        hits: list[str] = []
        for path in paths:
            found = re.findall(r"\b(?:request|user)_\d+\b", path.read_text(encoding="utf-8"))
            if found:
                hits.append(f"{path.relative_to(REPO_ROOT)}: {sorted(set(found))}")
        _raise_if_not(not hits, f"request/user-specific production branch evidence: {hits}")
        return {"production_id_specific_hits": 0, "sample_labels_default": 0}

    _check(checks, "anti-overfitting: no request/user-specific production logic", anti_overfit_check)

    test_run = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "code/tests", "-v"],
        cwd=REPO_ROOT, text=True, capture_output=True, check=False,
    )
    _check(
        checks,
        "foundation regression suite",
        lambda: (
            _raise_if_not(test_run.returncode == 0, test_run.stdout + test_run.stderr),
            {"exit_code": test_run.returncode, "summary": test_run.stderr.strip().splitlines()[-1]},
        )[1],
    )
    return {
        "gate": "VERIFY A",
        "status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "checks": checks,
        "counts": {"audit": audit["row_counts"], "resolved_evidence": len(trace_rows)},
        "unresolved_evidence": [],
        "failing_fixtures": [],
        "resolved_image_evidence": trace_rows,
        "commands": commands,
        "files_changed_by_gate": [
            "code/buy_or_wait/data.py",
            "code/tests/test_data_loader.py",
            "code/evaluation/gate_a.py",
            "code/evaluation/gate_a_foundation.json",
            "code/evaluation/gate_a_foundation.md",
        ],
    }


def main() -> int:
    report = run_gate()
    DEFAULT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    DEFAULT_MD.write_text(_render_markdown(report), encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "report": str(DEFAULT_JSON)}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
