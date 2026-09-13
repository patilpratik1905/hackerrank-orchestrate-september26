"""Print an auditable baseline 90-day ledger for one request; no recommendation logic."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.data import load_dataset  # noqa: E402
from buy_or_wait.evidence import EvidenceCache, extract_repository_evidence, seed_reviewed_image_cache  # noqa: E402
from buy_or_wait.forecast import reconstruct_state, simulate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_id")
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    parser.add_argument("--sample", action="store_true", help="look up a public solved-request input")
    parser.add_argument("--cache", type=Path, default=CODE_DIR / "evidence" / "evidence_cache.json")
    args = parser.parse_args()
    repository = load_dataset(args.dataset_dir)
    cache = EvidenceCache(args.cache)
    seed_reviewed_image_cache(repository, cache, CODE_DIR / "evidence" / "reviewed_images.json")
    evidence = extract_repository_evidence(repository, cache)
    state = reconstruct_state(
        repository.bundle_for(args.request_id, sample=args.sample), repository,
        evidence.facts, evidence.resolved_amounts,
    )
    result = simulate(state)
    document = {
        "request_id": state.request_id,
        "request_date": state.request_date.isoformat(),
        "horizon_end_inclusive": state.horizon_end.isoformat(),
        "starting_balance": str(state.starting_balance),
        "minimum_balance_required": str(state.minimum_balance),
        "recurrence_rules": [
            {"rule_id": rule.rule_id, "cadence_days": rule.cadence_days, "amount": str(rule.amount), "rationale": rule.rationale, "source_event_ids": list(rule.source_event_ids)}
            for rule in state.recurrence_rules
        ],
        "excluded": dict(state.excluded_event_reasons),
        "ledger": [
            {"date": entry.movement.movement_date.isoformat(), "amount": str(entry.movement.amount), "direction": entry.movement.direction.value, "kind": entry.movement.kind.value, "source_id": entry.movement.source_id, "event_id": entry.movement.source_event_id, "evidence_ids": list(entry.movement.evidence_ids), "fx_rate": str(entry.movement.fx_rate) if entry.movement.fx_rate is not None else None, "balance_after": str(entry.balance_after)}
            for entry in result.entries
        ],
        "safe": result.safe,
        "minimum_balance_observed": str(result.minimum_balance_observed),
        "minimum_balance_date": result.minimum_balance_date.isoformat(),
        "first_violation_source": result.first_violation.movement.source_id if result.first_violation else None,
        "first_violation_reason": result.first_violation_reason,
    }
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
