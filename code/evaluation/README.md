# Sample evaluator

The evaluator scores a prediction CSV against the 25 completed rows in
`dataset/sample_requests.csv`. It never treats requests 26–275 as labels and does
not calculate affordability.

From the repository root, evaluate a prediction file:

```text
python code/evaluation/main.py --dataset-dir dataset --predictions path/to/sample_predictions.csv --json-output code/evaluation/latest_evaluation.json
```

The prediction file must use the exact final-output columns and contain one row
for each solved request. The command prints a human-readable report and writes the
same results as JSON when `--json-output` is supplied. Use `--json` to print JSON
instead.

Validate the organizer-provided solved rows against every currently applicable
structural rule without copying them into a second CSV:

```text
python code/evaluation/main.py --dataset-dir dataset --reference-check --json-output code/evaluation/sample_evaluation.json
```

Exit code `0` means the prediction file is structurally valid. A lower model score
does not make the file invalid; malformed schemas, IDs, rows, or plans do. Exit
code `1` means at least one structural rule failed.

Financial feasibility is intentionally reported as `not_run`. A later simulator
can be passed to `validate_prediction_row` through its `feasibility_validator`
callback. Until that callback exists, this harness never claims a plan is safe.
