# VERIFY A — Foundation Gate

**Status:** PASS

This report verifies Steps 1–5 only; no state reconstruction or simulation was run.

## Checks

| Check | Status | Details |
|---|---|---|
| evaluator: solved outputs score 100% field-by-field | PASS | {"all_metrics": "100%", "rows": 25} |
| evaluator: required corruptions are rejected | PASS | {"amount_bound": "safe_amount_out_of_bounds", "date": "invalid_earliest_date", "duplicate_id": "duplicate_request_id", "enum": "invalid_affordability_status", "installment_option": "installment_option_mismatch", "partial_sum": "partial_payment_sum_or_schedule", "payment_order": "nonchronological_payment_plan", "spending_action": "invalid_spending_change_grammar"} |
| data layer: schemas, joins, bundles, None handling, label isolation | PASS | {"blank_event_amounts": 16, "bundles": 275, "rows": {"exchange_rates.csv": 134, "financial_events.csv": 25342, "financial_profiles.csv": 275, "images.csv": 16, "messages.csv": 215, "output.csv": 250, "request_payment_options.csv": 790, "requests.csv": 250, "sample_requests.csv": 25}} |
| money: Decimal runtime precision and serialization | PASS | {"float_conversion_hits": 0, "runtime_decimal_values": 31287} |
| FX: exact dated directional conversion only | PASS | {"foreign_cash_events": 140, "spot_check": "10.25 EUR -> 205.00 ZAR"} |
| evidence: all blank amounts traceable, cached, and decision-isolated | PASS | {"cache_second_run": {"hits": 231, "misses": 0}, "facts": 239, "resolved_blank_amounts": 16, "seeded_images": 16} |
| anti-overfitting: no request/user-specific production logic | PASS | {"production_id_specific_hits": 0, "sample_labels_default": 0} |
| foundation regression suite | PASS | {"exit_code": 0, "summary": "OK"} |

## Commands

- `python -m unittest discover -s code\tests -v`
- `python code\evaluation\gate_a.py`

## Required blank-amount evidence

| Image | Event | Amount | Currency | Version | Hash | Provenance |
|---|---|---:|---|---|---|---|
| image_01 | event_253 | 4365000 | IDR | image-reviewed-v1 | f37b40e6af42 | images.csv:2 |
| image_02 | event_1442 | 100000.00 | INR | image-reviewed-v1 | ccd779e5382b | images.csv:3 |
| image_03 | event_1545 | 41272.00 | INR | image-reviewed-v1 | e5fb0bbcda6c | images.csv:4 |
| image_04 | event_1700 | 2854.00 | INR | image-reviewed-v1 | 281e7f1e7bd1 | images.csv:5 |
| image_05 | event_1786 | 822.05 | INR | image-reviewed-v1 | 9abcda5647af | images.csv:6 |
| image_06 | event_3051 | 1995.00 | INR | image-reviewed-v1 | 9055551fbe59 | images.csv:7 |
| image_07 | event_3231 | 8528.00 | INR | image-reviewed-v1 | f6d30a743552 | images.csv:8 |
| image_08 | event_4535 | 15339.00 | INR | image-reviewed-v1 | e28592ad8b4d | images.csv:9 |
| image_09 | event_5170 | 723.00 | INR | image-reviewed-v1 | e0e74e14425d | images.csv:10 |
| image_10 | event_6033 | 79679.26 | INR | image-reviewed-v1 | c90f98caf087 | images.csv:11 |
| image_11 | event_6859 | 3650.00 | INR | image-reviewed-v1 | 795e000d4842 | images.csv:12 |
| image_12 | event_7307 | 33.50 | USD | image-reviewed-v1 | e10b0123e66d | images.csv:13 |
| image_13 | event_7941 | 2298 | INR | image-reviewed-v1 | 1ae54b378a95 | images.csv:14 |
| image_14 | event_9421 | 4593.00 | INR | image-reviewed-v1 | bf88e4aa35e6 | images.csv:15 |
| image_15 | event_9806 | 9968.00 | INR | image-reviewed-v1 | 0c0fe3d79e67 | images.csv:16 |
| image_16 | event_10521 | 393.22 | INR | image-reviewed-v1 | 2665cf731a86 | images.csv:17 |

## Gate decision

All mandatory foundation checks passed. Step 6 may begin.
