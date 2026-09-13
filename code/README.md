# Buy or Wait? code

The current implementation includes strict participant-data normalization and the
25-sample evaluation harness. It does not yet forecast cash flow or recommend a
payment plan.

From the repository root, validate and bundle every participant-facing row:

```text
python code/main.py --dataset-dir dataset --check-data
```

Python integration example:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path("code").resolve()))
from buy_or_wait.data import load_dataset

repository = load_dataset(Path("dataset"))
bundle = repository.bundle_for("request_26")
```

`bundle` contains the typed request, profile, all user events, payment options,
source messages, source images, and the exact dated exchange-rate records required
by the user's foreign-currency cash events. Sample requests and their completed
labels are stored separately; production `Request` objects never contain labels.

See `evaluation/README.md` for sample-scoring commands.

## Evidence extraction

Build or reuse the hash-bound evidence cache and verify that all 16 blank event
amounts resolve:

```text
python code/evidence/main.py --dataset-dir dataset
```

The checked-in reviewed-image manifest records the context-selected value, visible
document label, source hash, and confidence for every supplied image. Messages are
parsed deterministically from the supplied multilingual templates. Source text is
always treated as untrusted data.

To deliberately re-extract images with a structured vision model, configure
`EVIDENCE_API_URL`, `EVIDENCE_API_KEY`, and `EVIDENCE_MODEL` in the environment,
then run:

```text
python code/evidence/main.py --dataset-dir dataset --refresh-images
```

Add `--model-ambiguous-messages` to route only messages outside the deterministic
template rules through the configured structured multilingual model. Valid model
results use a separate extractor version and cache key.

The refresh path uses a 20-second timeout and two attempts by default. Credentials
are never stored in the cache. The cache records provider/model token metadata for
later aggregation into `evaluation/usage_report.md`.

## Deterministic state reconstruction and 90-day ledger

`buy_or_wait.forecast` reconstructs future cash movements from the typed source
records and evidence, detects only supported recurring commitments, applies exact
dated FX, and simulates the inclusive range from `request_date` through
`request_date + 90 days`. It starts at `current_available_balance`, never replays
historical settled cash, reserves pending debits, excludes pending credits/refunds,
and processes debits and hypothetical payments before credits on the same date.

The recurrence assumptions are global and configurable through `ForecastConfig`:
three settled observations are required; weekly (6–8 day), monthly (25–35 day), or
otherwise regular 8–60 day intervals must agree within three days. Protected,
non-fixed variable categories use an upper-median settled debit estimate from the
prior 90 days. A recurrence that would require an unavailable exact FX rate is
omitted and explicitly traced rather than converted with an invented rate.

Inspect a baseline ledger without selecting a payment recommendation:

```text
python code/forecast_inspect.py request_26 --dataset-dir dataset
python code/forecast_inspect.py request_01 --sample --dataset-dir dataset
```

Each ledger row carries its event, evidence, recurrence, and FX provenance. The
same `simulate()` function accepts hypothetical payments and spending modifications
for the future capacity and candidate-validation steps.
