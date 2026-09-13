# VERIFY B — Post-Step-6 Simulator Gate

**Status:** PASS

This focused gate verifies reconstruction, recurrence, FX, and simulation only. Steps 7–12 were not run.

## Checks

| Check | Status | Details |
|---|---|---|
| focused reconstruction/simulator fixtures | PASS | {"exit_code": 0, "summary": "OK"} |
| state reconstruction: opening balance, exclusions, horizon, provenance | PASS | {"baseline_movements": 5026, "recurrence_rules": 2027, "requests": 250} |
| recurrence: supported history, categories, latest amounts, no ID tuning | PASS | {"prohibited_categories": ["investment", "windfall"], "supported_rules": 2027, "thresholds": {"interval_tolerance_days": 3, "min_observations": 3}} |
| FX: exact conversion once and traceable | PASS | {"fx_movements_checked": 10, "method": "raw Decimal amount * exact settlement-date rate once"} |
| simulator: exact reconciliation, minimum, and horizon | PASS | {"horizon": "inclusive request_date through request_date + 90 days", "minimum_recomputed": true, "reconciliation_difference": "0", "requests": 250} |
| simulator: repeat-run deterministic structures | PASS | {"byte_stable_structures": true, "requests_checked": ["request_26", "request_151", "request_275"]} |
| traceability: event, recurrence, evidence, and FX origins | PASS | {"all_ledger_movements_provenanced": true} |

## Representative requests

{
  "fixed_recurring_expense": "request_26",
  "foreign_currency": "request_39",
  "image_evidence": "request_26",
  "pending_or_scheduled": "request_31",
  "salary_recurrence": "request_26",
  "variable_essential": "request_26"
}

Recurrence rejection fixture: irregular rent intervals are rejected when interval variance exceeds the configured tolerance.

## Artifacts

- Trace JSON: `code\evaluation\gate_b_traces.json`
- Gate JSON: `code\evaluation\gate_b_simulator.json`
- Gate report includes commands, first divergence (null on PASS), and next-step decision.

Steps 7–9 may proceed
