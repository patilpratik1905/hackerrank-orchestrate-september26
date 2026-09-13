"""Run Steps 7--9 on the 25 public samples only."""

from __future__ import annotations

import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_DIR.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.capacity import calculate_capacity  # noqa: E402
from buy_or_wait.candidates import Candidate, generate_candidates, rank_candidates  # noqa: E402
from buy_or_wait.data import load_dataset  # noqa: E402
from buy_or_wait.evidence import EvidenceCache, extract_repository_evidence, seed_reviewed_image_cache  # noqa: E402
from buy_or_wait.forecast import reconstruct_state  # noqa: E402
from evaluation.data_audit import read_csv_table  # noqa: E402
from evaluation.main import evaluate_file, project_expected_output  # noqa: E402
from evaluation.validators import write_prediction_rows  # noqa: E402


def _money(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _actions(candidate: Candidate) -> str:
    values = []
    for action in candidate.spending_changes:
        if action.action.value == "stop":
            values.append(f"stop:{action.event_id}")
        else:
            values.append(f"reduce_to:{action.event_id}:{_money(action.new_amount or Decimal('0'))}")
    return "|".join(values) if values else "none"


def _row(request, capacity, selected: Candidate | None) -> dict[str, str]:
    if selected is None:
        return {
            "request_id": request.request_id,
            "amount_safe_to_pay": _money(capacity.amount_safe_to_pay),
            "affordability_status": "not_affordable",
            "recommended_payment_method": "not_recommended",
            "payment_plan": "none",
            "earliest_date_for_full_payment": capacity.earliest_date_for_full_payment.isoformat() if capacity.earliest_date_for_full_payment else "",
            "spending_changes_needed": "none",
            "decision_explanation": "No safe option completes the request within the required deadline.",
        }
    if selected.method == "full_payment":
        status = "affordable_now" if not selected.spending_changes else "affordable_with_plan"
    elif selected.method in {"partial_payment", "installments"}:
        status = "affordable_with_plan"
    else:
        status = "affordable_later"
    explanation = {
        "full_payment": "Full payment is safe while preserving the required minimum balance.",
        "partial_payment": "A partial payment is safe now and the remainder is scheduled when full payment is safe.",
        "installments": "Installments complete the request safely within the user’s permitted duration.",
        "wait": "Wait until the projected full-payment date to preserve the required minimum balance.",
    }[selected.method]
    return {
        "request_id": request.request_id,
        "amount_safe_to_pay": _money(capacity.amount_safe_to_pay),
        "affordability_status": status,
        "recommended_payment_method": selected.method,
        "payment_plan": "|".join(f"{payment.payment_date.isoformat()}:{_money(payment.amount)}" for payment in selected.payments),
        "earliest_date_for_full_payment": capacity.earliest_date_for_full_payment.isoformat() if capacity.earliest_date_for_full_payment else "",
        "spending_changes_needed": _actions(selected),
        "decision_explanation": explanation,
    }


def run() -> dict[str, object]:
    repository = load_dataset(REPO_ROOT / "dataset")
    cache = EvidenceCache(CODE_DIR / "evidence" / "evidence_cache.json")
    seed_reviewed_image_cache(repository, cache, CODE_DIR / "evidence" / "reviewed_images.json")
    evidence = extract_repository_evidence(repository, cache)
    _, expected = read_csv_table(REPO_ROOT / "dataset" / "sample_requests.csv")
    rows: list[dict[str, str]] = []
    diagnostics: list[dict[str, object]] = []
    trace_rows: list[dict[str, object]] = []
    for raw, request in zip(expected, repository.sample_requests):
        bundle = repository.bundle_for(request.request_id, sample=True)
        state = reconstruct_state(bundle, repository, evidence.facts, evidence.resolved_amounts)
        capacity = calculate_capacity(state, request.requested_amount)
        candidates = generate_candidates(state, bundle, capacity)
        ranked = rank_candidates(candidates)
        selected = ranked[0] if ranked else None
        rows.append(_row(request, capacity, selected))
        diagnostics.append({
            "request_id": request.request_id,
            "amount_safe_to_pay": str(capacity.amount_safe_to_pay),
            "earliest_date_for_full_payment": capacity.earliest_date_for_full_payment.isoformat() if capacity.earliest_date_for_full_payment else None,
            "candidate_count": len(candidates),
            "eligible_count": len(ranked),
            "selected_method": selected.method if selected else "not_recommended",
            "selected_option_id": selected.option_id if selected else None,
            "selected_changes": _actions(selected) if selected else "none",
            "rejections": Counter(reason for candidate in candidates for reason in candidate.rejection_reasons),
            "minimum_balance": str(selected.simulation.minimum_balance_observed) if selected and selected.simulation else None,
        })
        if selected and selected.simulation:
            trace_rows.append({
                "request_id": request.request_id,
                "selected_method": selected.method,
                "minimum_balance_observed": str(selected.simulation.minimum_balance_observed),
                "minimum_balance_date": selected.simulation.minimum_balance_date.isoformat(),
                "safe": selected.simulation.safe,
                "ledger": [
                    {"date": entry.movement.movement_date.isoformat(), "source": entry.movement.source_id,
                     "kind": entry.movement.kind.value, "direction": entry.movement.direction.value,
                     "amount": str(entry.movement.amount), "balance_after": str(entry.balance_after)}
                    for entry in selected.simulation.entries
                ],
            })
    output_path = CODE_DIR / "evaluation" / "sample_baseline_predictions.csv"
    write_prediction_rows(output_path, rows)
    report = evaluate_file(REPO_ROOT / "dataset", output_path)
    stage_by_field = {
        "amount_safe_to_pay": "safe amount",
        "earliest_date_for_full_payment": "earliest date",
        "payment_plan": "candidate eligibility/ranking",
        "recommended_payment_method": "candidate eligibility/ranking",
        "affordability_status": "candidate eligibility/ranking",
        "spending_changes_needed": "spending changes",
        "decision_explanation": "formatting",
    }
    roots = [
        {
            "request_id": item["request_id"], "field": item["field"],
            "expected": item["expected"], "actual": item["actual"],
            "first_divergent_stage": stage_by_field.get(item["field"], "formatting"),
            "root_cause": "systemic forecast/capacity calibration or candidate selection; inspect ledger trace",
            "regression_action": "retain as solved-sample regression; never patch request ID",
        }
        for item in report["mismatches"]
    ]
    result = {
        "steps": [7, 8, 9],
        "sample_rows": len(rows),
        "metrics": report["metrics"],
        "summary": report["summary"],
        "mismatches": report["mismatches"],
        "root_causes": roots,
        "diagnostics": diagnostics,
        "production_label_access": False,
        "deterministic": True,
    }
    (CODE_DIR / "evaluation" / "sample_baseline_metrics.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    (CODE_DIR / "evaluation" / "capacity_metrics.json").write_text(json.dumps({
        "sample_rows": len(rows),
        "amount_safe_to_pay": report["metrics"]["amount_safe_to_pay"],
        "earliest_date_for_full_payment": report["metrics"]["earliest_date_for_full_payment"],
        "mismatches": [item for item in roots if item["field"] in {"amount_safe_to_pay", "earliest_date_for_full_payment"}],
    }, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    table = "# 25-sample baseline root-cause table\n\n| Request | Field | Expected | Actual | First stage |\n|---|---|---:|---:|---|\n"
    table += "\n".join(f"| {item['request_id']} | {item['field']} | {item['expected']} | {item['actual']} | {item['first_divergent_stage']} |" for item in roots)
    table += "\n\nDifferences remain regression cases; production never reads solved labels and fixes must be general.\n"
    (CODE_DIR / "evaluation" / "sample_baseline_root_causes.md").write_text(table, encoding="utf-8", newline="\n")
    (CODE_DIR / "evaluation" / "sample_baseline_traces.json").write_text(json.dumps(trace_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps({"summary": result["summary"], "mismatches": len(result["mismatches"])}, sort_keys=True))
