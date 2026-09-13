# Final submission report

## Validation

| Requirement | Result |
|---|---|
| Participant-facing dataset load | PASS (250 production bundles, 25 sample bundles) |
| Evidence resolution | PASS (16/16 blank amounts; cache hits 231, misses 0) |
| Unit/integration tests | PASS (87 tests, 119 subtests) |
| 25-sample structural validation | PASS (25/25) |
| Frozen 250-request run | PASS (250 rows, no crashes) |
| Output schema/IDs/bounds/plans/simulation | PASS on `output.final.csv` |
| Deterministic repeat | PASS (byte-identical output hash) |
| Checkpoint/resume | PASS (resume output hash identical) |
| Usage report | PASS (written from final run summary) |
| Package archive | PASS (`code.zip`, caches/secrets/alternative CSVs excluded) |

The final run was executed with `python code/main.py --quiet --output output.final.csv`
because an existing Excel process held the repository-root `output.csv` open. The
validated generated file is therefore `output.final.csv`; copy it to root `output.csv`
after releasing that external lock. No prediction rows were manually edited.

## Reproduction commands

```text
python code/main.py
python code/evaluation/final_validation.py
python -m pytest code/tests -q
python code/main.py --resume
```

## Unresolved risks

- The supplied solved samples still contain 61 field-level prediction differences;
  they are documented and routed in `sample_baseline_root_causes.md` rather than
  overfit with request-specific rules.
- Root `output.csv` must be replaced after the external Excel file lock is closed.

See `architecture_compliance.md` for the AI-agent compliance conclusion.
