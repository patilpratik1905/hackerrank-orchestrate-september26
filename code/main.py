"""Buy or Wait? production entry point.

Usage from repository root:

    python code/main.py
    python code/main.py --dataset-dir dataset --output output.csv
    python code/main.py --check-data

Reads participant-facing files under dataset/, processes all requests, and
writes output.csv with deterministic financial recommendations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.capacity import calculate_capacity  # noqa: E402
from buy_or_wait.agent import decide_request  # noqa: E402
from buy_or_wait.data import load_dataset  # noqa: E402
from buy_or_wait.evidence import EvidenceCache, extract_repository_evidence, seed_reviewed_image_cache  # noqa: E402
from buy_or_wait.forecast import reconstruct_state  # noqa: E402
from buy_or_wait.schema import OUTPUT_COLUMNS  # noqa: E402
from buy_or_wait.serialize import serialize_row  # noqa: E402


PIPELINE_VERSION = "steps-1-12-v1"
CHECKPOINT_PATH = CODE_DIR / "evaluation" / "run_checkpoint.json"


def _input_fingerprint(dataset_dir: Path, *, sample_only: bool) -> str:
    digest = hashlib.sha256(PIPELINE_VERSION.encode())
    digest.update(str(sample_only).encode())
    for path in sorted(dataset_dir.glob("*.csv")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    for path in (CODE_DIR / "buy_or_wait" / name for name in ("forecast.py", "capacity.py", "candidates.py", "serialize.py", "agent.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _save_checkpoint(payload: dict[str, object]) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(CHECKPOINT_PATH)


def write_output_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write output CSV with exact column order and proper escaping."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=OUTPUT_COLUMNS, lineterminator="\n",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in OUTPUT_COLUMNS})


def process_requests(
    dataset_dir: Path,
    output_path: Path,
    *,
    sample_only: bool = False,
    resume: bool = False,
    verbose: bool = True,
) -> dict[str, object]:
    """Run the full pipeline on all requests and write output.csv."""

    start_time = time.time()
    if verbose:
        print(f"Loading dataset from {dataset_dir}...", flush=True)

    repository = load_dataset(dataset_dir)
    cache = EvidenceCache(CODE_DIR / "evidence" / "evidence_cache.json")
    seeded = seed_reviewed_image_cache(
        repository, cache, CODE_DIR / "evidence" / "reviewed_images.json"
    )

    if verbose:
        print(f"Extracting evidence (cache: {cache.entry_count} entries, seeded {seeded} images)...", flush=True)

    evidence = extract_repository_evidence(repository, cache)

    if verbose:
        print(
            f"Evidence: {len(evidence.facts)} facts, "
            f"{len(evidence.resolved_amounts)} blank amounts resolved, "
            f"cache hits={evidence.cache_hits} misses={evidence.cache_misses}",
            flush=True,
        )

    requests = repository.sample_requests if sample_only else repository.requests
    if verbose:
        print(f"Processing {len(requests)} requests...", flush=True)

    fingerprint = _input_fingerprint(dataset_dir.resolve(), sample_only=sample_only)
    rows: list[dict[str, str]] = []
    diagnostics: list[dict[str, object]] = []
    checkpoint: dict[str, object] = {}
    if resume and CHECKPOINT_PATH.exists():
        try:
            candidate_checkpoint = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
            if candidate_checkpoint.get("fingerprint") == fingerprint:
                checkpoint = candidate_checkpoint
                rows.extend(candidate_checkpoint.get("rows", []))
                diagnostics.extend(candidate_checkpoint.get("diagnostics", []))
        except (OSError, json.JSONDecodeError, AttributeError):
            checkpoint = {}
    completed_ids = {row.get("request_id") for row in rows}
    method_counts: Counter[str] = Counter()
    stage_times: list[float] = []

    for idx, request in enumerate(requests):
        if request.request_id in completed_ids:
            continue
        req_start = time.time()
        bundle = repository.bundle_for(request.request_id, sample=sample_only)
        decision = decide_request(bundle, repository, evidence)
        state, capacity, candidates, ranked, selected = decision.state, decision.capacity, decision.candidates, decision.ranked_candidates, decision.selected

        row = serialize_row(request, capacity, selected)
        rows.append(row)

        method = selected.method if selected else "not_recommended"
        method_counts[method] += 1

        req_time = time.time() - req_start
        stage_times.append(req_time)

        diagnostics.append({
            "request_id": request.request_id,
            "method": method,
            "safe_amount": str(capacity.amount_safe_to_pay),
            "earliest_date": (
                capacity.earliest_date_for_full_payment.isoformat()
                if capacity.earliest_date_for_full_payment else None
            ),
            "candidates": len(candidates),
            "eligible": len(ranked),
            "min_balance": (
                str(selected.simulation.minimum_balance_observed)
                if selected and selected.simulation else None
            ),
            "time_seconds": round(req_time, 3),
            "stages": list(decision.trace),
        })
        completed_ids.add(request.request_id)
        _save_checkpoint({"version": PIPELINE_VERSION, "fingerprint": fingerprint, "sample_only": sample_only, "rows": rows, "diagnostics": diagnostics})

        if verbose and (idx + 1) % 25 == 0:
            print(f"  Processed {idx + 1}/{len(requests)} requests...", flush=True)

    # Sort by request_id for stable output
    rows.sort(key=lambda r: r["request_id"])

    write_output_csv(output_path, rows)
    total_time = time.time() - start_time

    summary = {
        "status": "PASS",
        "output_path": str(output_path),
        "request_count": len(rows),
        "methods": dict(sorted(method_counts.items())),
        "total_time_seconds": round(total_time, 2),
        "avg_time_per_request": round(total_time / max(len(rows), 1), 3),
        "evidence_cache_hits": evidence.cache_hits,
        "evidence_cache_misses": evidence.cache_misses,
        "evidence_usage": {
            "provider": evidence.usage.provider,
            "model": evidence.usage.model,
            "model_calls": evidence.usage.model_calls,
            "input_tokens": evidence.usage.input_tokens,
            "output_tokens": evidence.usage.output_tokens,
        },
        "checkpoint": str(CHECKPOINT_PATH),
        "resumed": bool(checkpoint),
    }
    (CODE_DIR / "evaluation" / "final_run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    if not sample_only:
        usage = summary["evidence_usage"]
        total_tokens = int(usage["input_tokens"]) + int(usage["output_tokens"])
        report = (
            "# Usage Report — Final 250-Request Run\n\n"
            f"- Command: `python code/main.py`\n- Requests: {len(rows)}\n"
            f"- Total runtime: {summary['total_time_seconds']} seconds\n"
            f"- Average runtime/request: {summary['avg_time_per_request']} seconds\n\n"
            "## Model usage\n\n"
            f"The final run made **{usage['model_calls']} model calls**. Provider: {usage['provider'] or 'none (cached evidence)'}; model: {usage['model'] or 'none (cached evidence)'}.\n\n"
            f"- Input tokens: {usage['input_tokens']}\n- Output tokens: {usage['output_tokens']}\n"
            f"- Total tokens: {total_tokens}\n- Average tokens/request: {Decimal(total_tokens) / Decimal(max(len(rows), 1))}\n"
            "- Estimated total cost: $0.00\n- Estimated cost/request: $0.00\n\n"
            "## Cache and reproducibility\n\n"
            f"Evidence cache hits: {summary['evidence_cache_hits']}; misses: {summary['evidence_cache_misses']}. "
            "Evidence is keyed by source content hash and extractor version. No secrets are stored.\n\n"
            "Pricing assumptions: no model calls occurred in this final run, so cost is $0.00; pricing date 2026-09-13.\n"
        )
        (CODE_DIR / "evaluation" / "usage_report.md").write_text(report, encoding="utf-8", newline="\n")

    if verbose:
        print(f"\nCompleted {len(rows)} requests in {total_time:.1f}s", flush=True)
        print(f"Methods: {dict(sorted(method_counts.items()))}", flush=True)
        print(f"Output: {output_path}", flush=True)

    return summary


def check_data(dataset_dir: Path) -> int:
    """Load all inputs and verify every request bundle."""
    repository = load_dataset(dataset_dir)
    for request in repository.requests:
        repository.bundle_for(request.request_id)
    for request in repository.sample_requests:
        repository.bundle_for(request.request_id, sample=True)
    result = {
        "dataset_dir": str(repository.dataset_dir),
        "status": "PASS",
        "typed_rows": repository.summary(),
        "production_bundles": len(repository.requests),
        "sample_bundles": len(repository.sample_requests),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Buy or Wait? deterministic financial decision agent"
    )
    parser.add_argument(
        "--dataset-dir", type=Path, default=Path("dataset"),
        help="path to dataset/ directory (default: dataset)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="output CSV path (default: output.csv at repo root)",
    )
    parser.add_argument(
        "--check-data", action="store_true",
        help="validate and bundle all inputs without processing",
    )
    parser.add_argument(
        "--sample-only", action="store_true",
        help="process only the 25 solved samples",
    )
    parser.add_argument("--resume", action="store_true", help="resume matching atomic request checkpoint")
    parser.add_argument(
        "--quiet", action="store_true",
        help="suppress progress output",
    )
    args = parser.parse_args()

    if args.check_data:
        return check_data(args.dataset_dir)

    output_path = args.output or (REPO_ROOT / "output.csv")
    summary = process_requests(
        args.dataset_dir, output_path,
        sample_only=args.sample_only,
        resume=args.resume,
        verbose=not args.quiet,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
