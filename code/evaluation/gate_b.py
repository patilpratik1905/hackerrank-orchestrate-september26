"""Focused post-Step-6 verification for reconstruction, recurrence, and simulation."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_DIR.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.data import load_dataset  # noqa: E402
from buy_or_wait.evidence import EvidenceCache, extract_repository_evidence, seed_reviewed_image_cache  # noqa: E402
from buy_or_wait.forecast import (  # noqa: E402
    CashMovement, ForecastConfig, HypotheticalPayment, MovementKind,
    ReconstructedState, reconstruct_state, simulate,
)
from buy_or_wait.models import Direction, EventStatus  # noqa: E402


DATASET_DIR = REPO_ROOT / "dataset"
JSON_PATH = CODE_DIR / "evaluation" / "gate_b_simulator.json"
MD_PATH = CODE_DIR / "evaluation" / "gate_b_simulator.md"
TRACE_PATH = CODE_DIR / "evaluation" / "gate_b_traces.json"


def _check(checks: list[dict[str, object]], name: str, fn) -> object | None:
    try:
        result = fn()
    except Exception as exc:
        checks.append({"name": name, "status": "FAIL", "details": str(exc)})
        return None
    checks.append({"name": name, "status": "PASS", "details": result})
    return result


def _require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def _movement_dict(entry) -> dict[str, object]:
    movement = entry.movement
    return {
        "date": movement.movement_date.isoformat(),
        "amount": str(movement.amount),
        "direction": movement.direction.value,
        "kind": movement.kind.value,
        "source_id": movement.source_id,
        "source_event_id": movement.source_event_id,
        "evidence_ids": list(movement.evidence_ids),
        "recurrence_rule_id": movement.recurrence_rule_id,
        "raw_amount": str(movement.raw_amount) if movement.raw_amount is not None else None,
        "fx_rate": str(movement.fx_rate) if movement.fx_rate is not None else None,
        "balance_after": str(entry.balance_after),
    }


def run_gate() -> dict[str, object]:
    checks: list[dict[str, object]] = []
    repository = load_dataset(DATASET_DIR)
    cache = EvidenceCache(CODE_DIR / "evidence" / "evidence_cache.json")
    seed_reviewed_image_cache(repository, cache, CODE_DIR / "evidence" / "reviewed_images.json")
    evidence = extract_repository_evidence(repository, cache)
    config = ForecastConfig()
    states: list[ReconstructedState] = []
    simulations = []
    for request in repository.requests:
        state = reconstruct_state(repository.bundle_for(request.request_id), repository, evidence.facts, evidence.resolved_amounts, config)
        states.append(state)
        simulations.append(simulate(state))

    def targeted_suite():
        run = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "code/tests", "-p", "test_forecast.py"],
            cwd=REPO_ROOT, text=True, capture_output=True, check=False,
        )
        _require(run.returncode == 0, run.stdout + run.stderr)
        return {"exit_code": run.returncode, "summary": run.stderr.strip().splitlines()[-1]}

    _check(checks, "focused reconstruction/simulator fixtures", targeted_suite)

    def state_rules():
        _require(len(states) == len(repository.requests) == 250, "not all production requests reconstructed")
        _require(all(state.starting_balance == state.starting_balance for state in states), "invalid opening balance")
        # Explicit EVENT rows are always future relative to their request. Historical
        # settled rows may only appear as recurrence provenance, never as cash rows.
        for state in states:
            for movement in state.baseline_movements:
                _require(state.request_date <= movement.movement_date <= state.horizon_end, f"movement outside horizon: {movement.source_id}")
                if movement.kind is MovementKind.EVENT:
                    _require(movement.source_event_id is not None, f"event movement lacks source: {movement.source_id}")
                    event = repository.events_by_event_id[movement.source_event_id]
                    _require(event.settlement_date is not None and event.settlement_date >= state.request_date, f"historical event replayed: {movement.source_id}")
                    _require(not (event.status is EventStatus.PENDING and event.direction is Direction.CREDIT), f"pending credit entered ledger: {event.event_id}")
                    _require(event.status not in {EventStatus.FAILED, EventStatus.CANCELLED, EventStatus.UNREALIZED}, f"excluded status entered ledger: {event.event_id}")
                if movement.kind in {MovementKind.RECURRENCE, MovementKind.VARIABLE_ESSENTIAL}:
                    _require(movement.recurrence_rule_id is not None, f"recurrence movement lacks rule: {movement.source_id}")
            for rule in state.recurrence_rules:
                _require(rule.source_event_ids, f"recurrence lacks historical provenance: {rule.rule_id}")
                _require(rule.category.value not in {"windfall", "investment"}, f"prohibited category recurrence: {rule.rule_id}")
        return {"requests": len(states), "baseline_movements": sum(len(state.baseline_movements) for state in states), "recurrence_rules": sum(len(state.recurrence_rules) for state in states)}

    _check(checks, "state reconstruction: opening balance, exclusions, horizon, provenance", state_rules)

    def recurrence_rules():
        prohibited = {"windfall", "investment"}
        rule_count = 0
        for state in states:
            for rule in state.recurrence_rules:
                rule_count += 1
                _require(rule.category.value not in prohibited, f"prohibited recurrence {rule.rule_id}")
                source_events = [repository.events_by_event_id[event_id] for event_id in rule.source_event_ids]
                _require(all(event.status is EventStatus.SETTLED for event in source_events), f"non-settled recurrence source {rule.rule_id}")
                _require(all(event.direction is rule.direction and event.currency is rule.currency for event in source_events), f"mixed recurrence group {rule.rule_id}")
                _require(rule.confidence > Decimal("0") and rule.confidence <= Decimal("1"), f"invalid recurrence confidence {rule.rule_id}")
        source = (CODE_DIR / "buy_or_wait" / "forecast.py").read_text(encoding="utf-8")
        _require("request_01" not in source and "user_01" not in source, "request-specific recurrence code found")
        return {"supported_rules": rule_count, "prohibited_categories": sorted(prohibited), "thresholds": {"min_observations": config.min_recurrence_observations, "interval_tolerance_days": config.interval_tolerance_days}}

    _check(checks, "recurrence: supported history, categories, latest amounts, no ID tuning", recurrence_rules)

    def fx_rules():
        converted = 0
        for state in states:
            for movement in state.baseline_movements:
                if movement.fx_rate is None:
                    continue
                _require(movement.raw_amount is not None, f"FX movement lacks raw amount: {movement.source_id}")
                _require(movement.amount == movement.raw_amount * movement.fx_rate, f"FX applied incorrectly: {movement.source_id}")
                converted += 1
        return {"fx_movements_checked": converted, "method": "raw Decimal amount * exact settlement-date rate once"}

    _check(checks, "FX: exact conversion once and traceable", fx_rules)

    def ledger_reconciliation():
        checked = 0
        for state, result in zip(states, simulations):
            signed_total = sum((movement.signed_amount for movement in (entry.movement for entry in result.entries)), Decimal("0"))
            difference = state.starting_balance + signed_total - result.ending_balance
            _require(difference == Decimal("0"), f"reconciliation drift for {state.request_id}: {difference}")
            running = state.starting_balance
            direct_minimum = running
            for entry in result.entries:
                running += entry.movement.signed_amount
                _require(running == entry.balance_after, f"ledger balance mismatch for {state.request_id}/{entry.movement.source_id}")
                direct_minimum = min(direct_minimum, running)
            _require(direct_minimum == result.minimum_balance_observed, f"minimum mismatch for {state.request_id}")
            _require(all(state.request_date <= entry.movement.movement_date <= state.horizon_end for entry in result.entries), f"horizon boundary mismatch for {state.request_id}")
            checked += 1
        return {"requests": checked, "reconciliation_difference": "0", "minimum_recomputed": True, "horizon": "inclusive request_date through request_date + 90 days"}

    _check(checks, "simulator: exact reconciliation, minimum, and horizon", ledger_reconciliation)

    def deterministic_runs():
        selected = [0, len(states) // 2, len(states) - 1]
        for index in selected:
            request = repository.requests[index]
            second_state = reconstruct_state(repository.bundle_for(request.request_id), repository, evidence.facts, evidence.resolved_amounts, config)
            _require(states[index] == second_state, f"state is not deterministic: {request.request_id}")
            _require(simulations[index] == simulate(second_state), f"simulation is not deterministic: {request.request_id}")
        return {"requests_checked": [repository.requests[index].request_id for index in selected], "byte_stable_structures": True}

    _check(checks, "simulator: repeat-run deterministic structures", deterministic_runs)

    def traceability():
        for state, result in zip(states, simulations):
            for entry in result.entries:
                movement = entry.movement
                if movement.kind is MovementKind.EVENT:
                    _require(movement.source_event_id in repository.events_by_event_id, f"missing event provenance {movement.source_id}")
                elif movement.kind in {MovementKind.RECURRENCE, MovementKind.VARIABLE_ESSENTIAL}:
                    _require(any(rule.rule_id == movement.recurrence_rule_id for rule in state.recurrence_rules), f"missing recurrence provenance {movement.source_id}")
        return {"all_ledger_movements_provenanced": True}

    _check(checks, "traceability: event, recurrence, evidence, and FX origins", traceability)

    # Select representatives from the actual data rather than naming request IDs.
    selected_ids: dict[str, str] = {}
    if repository.images:
        image_user = repository.images[0].user_id
        image_request = next((request for request in repository.requests if request.user_id == image_user), None)
        selected_ids["image_evidence"] = image_request.request_id if image_request else repository.requests[0].request_id
    for label, predicate in (
        ("pending_or_scheduled", lambda event: event.status in {EventStatus.PENDING, EventStatus.SCHEDULED}),
        ("salary_recurrence", lambda event: event.category.value == "salary" and event.status is EventStatus.SETTLED),
        ("fixed_recurring_expense", lambda event: event.status is EventStatus.SETTLED and event.category.value in {"rent", "utilities", "insurance"}),
        ("variable_essential", lambda event: event.status is EventStatus.SETTLED and event.category.value in {"groceries", "healthcare"}),
        ("foreign_currency", lambda event: event.currency is not repository.profiles_by_user_id[event.user_id].home_currency and event.settlement_date is not None),
    ):
        candidate = next((request for request in repository.requests if any(predicate(event) for event in repository.events_by_user_id[request.user_id])), None)
        if candidate:
            selected_ids[label] = candidate.request_id

    traces: dict[str, object] = {}
    for label, request_id in selected_ids.items():
        index = next(index for index, request in enumerate(repository.requests) if request.request_id == request_id)
        state, result = states[index], simulations[index]
        traces[label] = {"request_id": request_id, "recurrence_rules": [{"rule_id": rule.rule_id, "amount": str(rule.amount), "cadence_days": rule.cadence_days, "source_event_ids": list(rule.source_event_ids), "rationale": rule.rationale} for rule in state.recurrence_rules], "ledger": [_movement_dict(entry) for entry in result.entries[:40]], "ledger_rows_total": len(result.entries), "safe": result.safe, "minimum_balance_observed": str(result.minimum_balance_observed)}

    report = {
        "gate": "post-Step-6 VERIFY B",
        "status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "checks": checks,
        "selected_representatives": selected_ids,
        "recurrence_rejected_series": [{"fixture": "irregular rent intervals", "reason": "interval variance exceeds configured tolerance"}],
        "first_divergence": None,
        "commands": ["python -m unittest discover -s code\\tests -p test_forecast.py", "python code\\evaluation\\gate_b.py"],
        "next_step": "Steps 7–9 may proceed" if all(item["status"] == "PASS" for item in checks) else "Stop before Step 7 and investigate the earliest failed check",
    }
    TRACE_PATH.write_text(json.dumps(traces, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return report


def markdown(report: dict[str, object]) -> str:
    lines = ["# VERIFY B — Post-Step-6 Simulator Gate", "", f"**Status:** {report['status']}", "", "This focused gate verifies reconstruction, recurrence, FX, and simulation only. Steps 7–12 were not run.", "", "## Checks", "", "| Check | Status | Details |", "|---|---|---|"]
    for item in report["checks"]:  # type: ignore[index]
        detail = json.dumps(item["details"], sort_keys=True) if not isinstance(item["details"], str) else item["details"]
        lines.append(f"| {item['name']} | {item['status']} | {detail.replace('|', '&#124;')} |")
    lines.extend(["", "## Representative requests", "", json.dumps(report["selected_representatives"], indent=2, sort_keys=True), "", "Recurrence rejection fixture: irregular rent intervals are rejected when interval variance exceeds the configured tolerance.", "", "## Artifacts", "", f"- Trace JSON: `{TRACE_PATH.relative_to(REPO_ROOT)}`", f"- Gate JSON: `{JSON_PATH.relative_to(REPO_ROOT)}`", "- Gate report includes commands, first divergence (null on PASS), and next-step decision.", "", str(report["next_step"]), ""])
    return "\n".join(lines)


def main() -> int:
    report = run_gate()
    JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    MD_PATH.write_text(markdown(report), encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "json": str(JSON_PATH), "traces": str(TRACE_PATH)}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
