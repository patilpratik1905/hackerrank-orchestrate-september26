"""Minimal repository entry point for data-contract validation.

Decision generation is added in later steps. This command currently proves that
all participant-facing inputs normalize into complete typed request bundles.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from buy_or_wait.data import load_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("dataset"))
    parser.add_argument(
        "--check-data",
        action="store_true",
        help="load all inputs and verify every production and sample request bundle",
    )
    args = parser.parse_args()
    if not args.check_data:
        parser.error("Step 4 supports only --check-data; decision generation is not built")

    repository = load_dataset(args.dataset_dir)
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


if __name__ == "__main__":
    raise SystemExit(main())
