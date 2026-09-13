# Buy or Wait?

AI-powered financial decision agent for the HackerRank Orchestrate challenge.

For every purchase/payment request, the agent determines whether to pay in full,
pay partially, use installments, wait, or not proceed — based on the user's
financial position, recurring commitments, evidence from messages/images, and
available payment options.

## Quick Start

```bash
# Install dependencies (standard library only — no pip install needed)
# Python 3.12+ required

# Run the full pipeline (250 requests → output.csv)
python code/main.py

# Run only the 25 solved samples
python code/main.py --sample-only --output sample_output.csv

# Validate dataset integrity
python code/main.py --check-data
```

## Setup

**Python 3.12+** is required. The solution uses only the Python standard library
(no external packages needed).

Optional: Copy `.env.example` to `.env` and set credentials if you need to
re-extract evidence from images using a vision model:

```bash
cp .env.example .env
# Edit .env with your API credentials
```

## Execution

From the repository root:

```bash
python code/main.py
```

This reads `dataset/` and writes `output.csv` at the repository root.

Options:
- `--dataset-dir PATH` — dataset directory (default: `dataset`)
- `--output PATH` — output CSV path (default: `output.csv`)
- `--sample-only` — process only the 25 solved samples
- `--check-data` — validate inputs without processing
- `--quiet` — suppress progress output

## Evaluation

Evaluate predictions against the 25 solved samples:

```bash
python code/evaluation/main.py --predictions code/evaluation/sample_baseline_predictions.csv --dataset-dir dataset
```

Run the full test suite:

```bash
python -m pytest code/tests/ -v
```

Run the strict final validation on output.csv:

```bash
python code/evaluation/final_validation.py
```

## Evidence Cache

The evidence cache (`code/evidence/evidence_cache.json`) stores extracted facts
from messages and images, keyed by content hash. The reviewed-image manifest
(`code/evidence/reviewed_images.json`) contains human-verified amounts for all
16 blank event amounts.

To refresh evidence extraction (requires API credentials):

```bash
python code/evidence/main.py --dataset-dir dataset --refresh-images
```

To re-extract only ambiguous messages through a language model:

```bash
python code/evidence/main.py --dataset-dir dataset --model-ambiguous-messages
```

## Architecture

```
code/
├── main.py                          # Production entry point
├── buy_or_wait/
│   ├── data.py                      # Dataset loading and typed bundles
│   ├── models.py                    # Data contracts (Request, Profile, Event)
│   ├── schema.py                    # CSV column schemas
│   ├── evidence.py                  # Evidence extraction and caching
│   ├── forecast.py                  # State reconstruction and 90-day simulator
│   ├── capacity.py                  # Safe-amount and earliest-date calculation
│   ├── candidates.py                # Candidate generation, validation, ranking
│   └── serialize.py                 # Output serialization and explanations
├── evidence/
│   ├── main.py                      # Evidence extraction CLI
│   ├── evidence_cache.json          # Hash-bound evidence cache
│   └── reviewed_images.json         # Human-verified image amounts
├── evaluation/
│   ├── main.py                      # Sample evaluation scorer
│   ├── validators.py                # Structural validation contracts
│   ├── final_validation.py          # Strict 16-check output validation
│   ├── gate_c.py                    # Steps 7-9 verification gate
│   ├── run_sample_baseline.py       # 25-sample baseline runner
│   └── usage_report.md              # Model usage and cost report
└── tests/
    ├── test_capacity_candidates.py  # Capacity and candidate unit tests
    ├── test_data_audit.py           # Data integrity tests
    ├── test_data_loader.py          # Data loading tests
    ├── test_evaluator.py            # Evaluator tests
    ├── test_evidence.py             # Evidence extraction tests
    └── test_forecast.py             # Forecast and simulation tests
```

## Decision Pipeline

1. **Evidence extraction** — Parse messages (deterministic templates) and images
   (human-reviewed manifest) into typed facts
2. **State reconstruction** — Build cash movements from events, evidence,
   recurrence detection, and FX conversion
3. **Simulation** — Run 90-day inclusive ledger with debit-before-credit ordering
4. **Capacity** — Calculate safe-to-pay amount and earliest full-payment date
5. **Candidate generation** — Enumerate full, partial, installment, and wait options
6. **Validation** — Simulate each candidate, check preferences/deadlines/limits
7. **Ranking** — Select best candidate using 6-rule priority key
8. **Serialization** — Format output with trace-driven explanations

## Tests

84 tests covering:
- Data loading and integrity (14 tests)
- Evidence extraction and caching (8 tests)
- Forecast and simulation (10 tests)
- Capacity and candidates (5 tests)
- Evaluator contracts (24 tests)
- Data audit (7 tests)
- Structural validation (16 tests)
