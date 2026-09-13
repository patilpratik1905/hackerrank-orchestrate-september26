"""Extract and validate typed facts from untrusted messages and images."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from buy_or_wait.data import load_dataset  # noqa: E402
from buy_or_wait.evidence import (  # noqa: E402
    EvidenceCache,
    OpenAICompatibleEvidenceClient,
    extract_repository_evidence,
    seed_reviewed_image_cache,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    parser.add_argument(
        "--cache",
        type=Path,
        default=CODE_DIR / "evidence" / "evidence_cache.json",
    )
    parser.add_argument(
        "--reviewed-images",
        type=Path,
        default=CODE_DIR / "evidence" / "reviewed_images.json",
    )
    parser.add_argument(
        "--refresh-images",
        action="store_true",
        help="ignore reviewed/cache image facts and call the configured structured VLM",
    )
    parser.add_argument(
        "--model-ambiguous-messages",
        action="store_true",
        help="use the configured structured multilingual model for messages not covered by deterministic templates",
    )
    parser.add_argument("--timeout-seconds", type=float, default=20)
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()

    repository = load_dataset(args.dataset_dir)
    cache = EvidenceCache(args.cache)
    seeded = 0
    client = None
    if args.refresh_images or args.model_ambiguous_messages:
        client = OpenAICompatibleEvidenceClient()
    else:
        seeded = seed_reviewed_image_cache(repository, cache, args.reviewed_images)
    run = extract_repository_evidence(
        repository,
        cache,
        model_client=client,
        refresh_images=args.refresh_images,
        model_ambiguous_messages=args.model_ambiguous_messages,
        timeout_seconds=args.timeout_seconds,
        max_attempts=args.max_attempts,
    )
    result = {
        "status": "PASS",
        "facts": len(run.facts),
        "fact_types": dict(sorted(Counter(fact.fact_type.value for fact in run.facts).items())),
        "blank_event_amounts_resolved": len(run.resolved_amounts),
        "resolved_event_ids": sorted(run.resolved_amounts),
        "cache_entries": cache.entry_count,
        "cache_hits": run.cache_hits,
        "cache_misses": run.cache_misses,
        "reviewed_image_entries_seeded": seeded,
        "usage": {
            "provider": run.usage.provider,
            "model": run.usage.model,
            "model_calls": run.usage.model_calls,
            "input_tokens": run.usage.input_tokens,
            "output_tokens": run.usage.output_tokens,
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
