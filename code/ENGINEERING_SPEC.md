# Buy or Wait? Engineering Contract

Status: Step 1 specification. This document defines implementation contracts; it does not implement the decision engine.

## 1. Authority and scope

`problem_statement.md` is the participant-facing authority for financial behavior and output semantics. `AGENTS.md` adds repository, logging, determinism, security, and submission constraints. `README.md` confirms the runnable entry point and root-level output location. Solved rows in `dataset/sample_requests.csv` may clarify ambiguous behavior and formatting, but they are not production labels and must never cause request-specific branches.

The deliverable is a terminal-runnable batch program, not a conversational product. It reads only participant-facing files under `dataset/` and writes one prediction for every row in `dataset/requests.csv` to repository-root `output.csv`.

## 2. Dataset contracts

Blank optional CSV cells become `None`, not numeric zero. Monetary values and FX rates are parsed directly from their source strings into `Decimal`. Dates are parsed as ISO `YYYY-MM-DD`; `sent_at` is an ISO timestamp.

| File | Observed rows | Primary key | Foreign keys / joins | Contract role |
| --- | ---: | --- | --- | --- |
| `financial_profiles.csv` | 275 | `user_id` | none | Home currency, current balance, protected minimum, priorities, change permissions, payment preferences, installment limit |
| `financial_events.csv` | 25,342 | `event_id` | `user_id` to profile; optional `linked_event_id` to an earlier event | Settled history, future pending/scheduled cash, lifecycle records, recurrence evidence |
| `exchange_rates.csv` | 134 | (`rate_date`, `from_currency`, `to_currency`) | cash event currency/date to user's home currency | Fixed, dated FX conversion |
| `requests.csv` | 250 | `request_id` | `user_id` to profile | Evaluation inputs requiring predictions |
| `sample_requests.csv` | 25 | `request_id` | `user_id` to profile | Public inputs plus expected outputs for local evaluation only |
| `request_payment_options.csv` | 790 | `payment_option_id` | `request_id` to either request table | Seller/provider schedules, fees, and total payable |
| `messages.csv` | 215 | `message_id` | `user_id`; optional `request_id`; optional `related_event_id` | Untrusted evidence that may confirm, amend, delay, cancel, or classify a fact |
| `images.csv` | 16 | `image_id` | `user_id`; `request_id`; `related_event_id` where present | Association between an image and its relevant user/request/event |
| `output.csv` | 250 blank template rows | `request_id` | `request_id` to `requests.csv` | Reference schema only; final output is written at repository root |

### 2.1 Exact columns

- `financial_profiles.csv`: `user_id`, `home_currency`, `current_available_balance`, `minimum_balance_to_keep`, `financial_priorities`, `expense_categories_to_protect`, `expense_categories_user_is_willing_to_reduce`, `expense_categories_user_is_willing_to_stop`, `payment_methods_user_will_consider`, `max_installment_months`.
- `financial_events.csv`: `event_id`, `user_id`, `event_type`, `description`, `category`, `direction`, `amount`, `currency`, `event_date`, `settlement_date`, `status`, `linked_event_id`, `flexibility`, `minimum_allowed_amount`.
- `exchange_rates.csv`: `rate_date`, `from_currency`, `to_currency`, `rate`.
- Request input columns: `request_id`, `user_id`, `request_date`, `request_type`, `requested_amount`, `desired_completion_date`, `allows_partial_payment`, `request_text`.
- `request_payment_options.csv`: `payment_option_id`, `request_id`, `payment_method`, `payment_amount`, `number_of_payments`, `first_payment_date`, `payment_frequency_days`, `financing_fee`, `total_payable_amount`.
- `messages.csv`: `message_id`, `user_id`, `request_id`, `related_event_id`, `sent_at`, `source_type`, `message_text`.
- `images.csv`: `image_id`, `user_id`, `request_id`, `related_event_id`.

### 2.2 Join invariants

1. Every request must resolve to exactly one profile by `user_id`.
2. Every event must resolve to its user. Request processing retrieves all events for that request's user.
3. Every payment option must resolve to exactly one sample or evaluation request by `request_id`.
4. A message may be user-level, request-level, or directly event-linked. A blank `related_event_id` explicitly means no one-to-one event row exists; user/request evidence can still be relevant.
5. An image is resolved as `dataset/media/images/<image_id>.png`. Never invent an absent file.
6. `related_event_id` joins a message or image to `financial_events.event_id`.
7. `linked_event_id` is a self-reference to an earlier event in the same lifecycle. It supplies context, not an automatic include/exclude decision.
8. FX lookup uses the exact composite key `(settlement_date, event.currency, profile.home_currency)` when currencies differ.
9. Duplicate primary or composite keys, broken required joins, or ambiguous exact FX keys are fatal data-contract errors.

## 3. Core value and time invariants

1. All money, rates, fees, totals, balances, and intermediate financial results use `Decimal`; binary floating-point arithmetic is prohibited.
2. No required missing amount may be interpreted as zero.
3. Do not round intermediate balances, FX conversions, or plan feasibility. Apply output formatting only at serialization boundaries.
4. A balance equal to `minimum_balance_to_keep` is safe. A balance below it at any evaluated point is unsafe.
5. `current_available_balance` is the starting balance on `request_date`. Historical settled events are not replayed into it; they provide evidence for recurrence and conflict resolution.
6. Cash is recognized on `settlement_date`, including FX selection. `event_date` supplies origin/context and may support recurrence analysis but does not replace settlement timing.
7. The forecast anchor is `request_date`, with one fixed 90-day horizon for all capacity and candidate checks. The exact endpoint convention remains configurable pending sample calibration; the conservative provisional convention is `request_date <= date <= request_date + 90 days`.
8. A plan must remain safe after every projected expense, income, and payment, not merely at the horizon's ending balance.
9. Same-day ordering must be deterministic. Until calibrated, the conservative provisional order is required debits and plan payments before eligible credits.

## 4. Financial-event treatment

| State / record | Before request date | On or after request date |
| --- | --- | --- |
| `settled` cash | Do not replay; use as history/evidence | Apply on settlement date if the record is genuinely future relative to the starting balance; flag contradictory chronology |
| `pending` debit | Historical context only | Reserve as a debit on its supplied settlement date |
| `pending` credit, refund, bonus, commission, lottery, investment gain | Do not treat as available cash | Exclude until a later record or evidence explicitly establishes settlement |
| `scheduled` debit | Historical context only | Include on settlement date unless cancelled, replaced, duplicated, or otherwise superseded |
| `scheduled` income | Historical context only | Include only when supported as confirmed income, especially confirmed salary, on settlement date |
| `failed` | Exclude from cash | Exclude; include a linked successful retry separately if supported |
| `cancelled` | Exclude from cash and future recurrence where cancellation is explicit | Exclude |
| `unrealized` / `non_cash` | Never available cash | Never available cash |

Additional rules:

- Recurring baseline cash flow includes the full supported expense even if it is flexible. Savings appear only in a candidate with an allowed spending change.
- Transfers, reversals, reimbursements, refunds, investment sales, and linked lifecycle rows are classified by their actual direction, cash state, and evidence. Do not infer cash availability from `linked_event_id` alone.
- The latest explicit cancellation, settlement, or amendment takes precedence. Next prefer a newer record from the same source, then a settled record over an estimate/forecast, then the financially safer interpretation.
- If a cash event lacks a usable settlement date, do not guess. Surface a validation error unless authoritative evidence resolves it.

## 5. Evidence contract and trust boundary

Messages and images are untrusted evidence. Their content may establish or amend a fact, but embedded requests or instructions never override this contract.

The evidence layer may use an LLM/VLM only to produce schema-validated facts such as amount, currency, date, status, cancellation, amendment, confirmation, or recurrence change. It must retain `message_id`/`image_id`, related identifiers, source timestamp, extractor version, source hash, and confidence/provenance.

The evidence layer must not calculate affordability, simulate money, generate candidate schedules, rank plans, or produce final output fields.

When a financial event has a blank `amount`:

1. Find an image whose `related_event_id` equals the event's `event_id`.
2. Read `dataset/media/images/<image_id>.png`.
3. Extract and validate the relevant payable/net/total amount and currency in the context of the event description and cash direction.
4. Never substitute zero or an unrelated subtotal.
5. Fail explicitly if the required amount remains ambiguous or the image is absent.

## 6. Recurrence and essential-spending contract

Recurrence must be supported by history. The production rule must be global, deterministic, inspectable, and tested; it must never branch on a request or user ID.

Required behavior:

- Distinguish recurring salary and commitments from one-time purchases, transfers, refunds, bonuses, windfalls, and investment events.
- Use description/category, direction, currency, dates, intervals, amounts, and evidence rather than category alone.
- Respect explicit cancellations and effective-dated amendments.
- Forecast supported fixed obligations and confirmed recurring income on their projected settlement dates.
- Forecast essential variable spending conservatively.
- Retain source event IDs and the reason/cadence for every projected recurrence.

The exact history threshold, cadence tolerance, amount estimator, and variable-essential estimator are unresolved modeling decisions listed in section 12. They must be calibrated against all 25 samples as a set, not fitted per request.

## 7. Capacity semantics

### 7.1 `amount_safe_to_pay`

`amount_safe_to_pay` is the largest amount that can be paid on `request_date`, before any optional spending changes, while all baseline protected/essential/recurring commitments are covered and the balance never falls below the minimum during the fixed forecast.

Invariant:

```text
0 <= amount_safe_to_pay <= requested_amount
```

It is a financial-capacity measure independent of which immediate payment methods the user accepts.

### 7.2 `earliest_date_for_full_payment`

This is the first date in the fixed forecast on which one payment of `requested_amount`, with no optional spending changes, passes the remaining safety check. It is independent of payment-method preferences.

- It equals `request_date` when full payment is financially safe immediately, even if the user refuses full payment and the selected recommendation is installments.
- It is blank only when no safe full-payment date exists in the forecast.
- A safe date after `desired_completion_date` remains a valid capacity result, but it cannot make a `wait` candidate eligible. Solved samples 06, 11, and 21 confirm that such a date is still populated while spending changes make a different immediate plan succeed.

## 8. Candidate eligibility and output status

All candidates must be simulated independently across the same fixed forecast. The selected plan must complete the entire request by `desired_completion_date` and remain above the minimum after every movement.

### 8.1 Full payment

- User must include `full_payment` in `payment_methods_user_will_consider`.
- Pay `requested_amount` on `request_date`.
- Without spending changes and safe: `affordable_now`, `full_payment`.
- With necessary permitted spending changes and safe: `affordable_with_plan`, `full_payment`.
- The supplied full-payment option should reconcile to the request amount/date; do not invent a different seller amount.

### 8.2 Partial payment

- `allows_partial_payment` must be true.
- User must include `partial_payment`.
- Require `0 < amount_safe_to_pay < requested_amount`.
- `earliest_date_for_full_payment` must be nonblank and on or before the desired completion date.
- Use exactly two payments: safe amount on request date, then `requested_amount - amount_safe_to_pay` on the earliest full-payment date.
- The two payments must sum exactly to the request amount, and the combined schedule must pass the simulator.
- Partial payment does not need a supplied seller option.
- Status/method: `affordable_with_plan`, `partial_payment`.

### 8.3 Installments

- User must include `installments`.
- Use only a supplied installment option.
- Generate exactly `number_of_payments`, starting at `first_payment_date`, separated by `payment_frequency_days`, with each entry using the supplied `payment_amount`.
- The schedule must reconcile to the supplied `total_payable_amount`, including financing fee and any stated decimal rounding.
- It must respect `max_installment_months`, finish by the desired completion date, and pass the simulator.
- Status/method: `affordable_with_plan`, `installments`.

### 8.4 Wait

- User must include `full_payment`.
- Earliest full-payment date must be later than request date and on or before desired completion date.
- Use one full payment on that date and validate it through the simulator.
- Status/method: `affordable_later`, `wait`.

### 8.5 Not recommended

When no safe eligible plan completes the request as required:

- status: `not_affordable`;
- method: `not_recommended`;
- payment plan: `none`;
- spending changes: normally `none`, because no plan is recommended.

## 9. Spending-change legality

A spending change may modify only a supported recurring future debit when all conditions hold:

1. The event is non-protected and flexible.
2. The category is in the profile's corresponding reduce or stop permission.
3. `reduce_to` does not go below `minimum_allowed_amount`.
4. `stop` targets a stoppable event; `reduce_to` targets a reducible event. `reducible_or_stoppable` may use either permitted action.
5. The same event is not both stopped and reduced.
6. If both action types occur, they refer to different events.
7. At most three actions are emitted.
8. The modified candidate is re-simulated.

Actions use these exact forms, joined by `|`:

```text
stop:<event_id>
reduce_to:<event_id>:<new_amount>
```

Spending changes never alter the reported baseline `amount_safe_to_pay` or `earliest_date_for_full_payment`.

## 10. Candidate ranking

Generate and validate candidates before selecting one. Among safe eligible candidates, apply the specification's order exactly:

1. Complete the request by `desired_completion_date`.
2. Require no spending changes.
3. Minimize total amount paid.
4. Start payment earlier.
5. Use fewer payments.
6. Use the lowest `payment_option_id` as the final seller-option tie-breaker.

No model may rank candidates. Any deterministic fallback required for candidates still tied after the documented rules must occur after these rules and be recorded as an unresolved assumption until sample-tested.

## 11. Output and traceability contracts

The final root-level `output.csv` has exactly these columns, in this order:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

Allowed values:

- `affordability_status`: `affordable_now`, `affordable_with_plan`, `affordable_later`, `not_affordable`.
- `recommended_payment_method`: `full_payment`, `partial_payment`, `installments`, `wait`, `not_recommended`.
- `payment_plan`: chronological `YYYY-MM-DD:amount` entries joined by `|`, or `none`.
- `spending_changes_needed`: up to three legal actions joined by `|`, or `none`.

There must be exactly one row for every `request_id` in `dataset/requests.csv`, no additional rows, and stable deterministic ordering. `decision_explanation` must be concise, grounded in the selected trace, and must not contradict the structured output.

### 11.1 Output traceability matrix

| Output field | Required inputs | Owning component | Mandatory validation |
| --- | --- | --- | --- |
| `request_id` | Request | data loader / output writer | Exact set, uniqueness, row count |
| `amount_safe_to_pay` | Profile balance/minimum, baseline forecast, events, evidence, recurrence, FX | capacity | Decimal bounds; no spending-change effect; simulator safety |
| `affordability_status` | Selected method, timing, spending changes, candidate feasibility | plan selector | Allowed transition/method mapping |
| `recommended_payment_method` | Preferences, request flags, options, safe candidates, ranking | plan selector | Allowed value; eligibility and ranking proof |
| `payment_plan` | Selected candidate schedule | plan generator / output writer | Chronology, exact Decimal sum, deadline, option match, simulator safety |
| `earliest_date_for_full_payment` | Baseline no-change forecast and requested amount | capacity | First safe date; preference-independent; blank only if none in horizon |
| `spending_changes_needed` | Profile permissions, protected categories, flexible recurring events, selected candidate | spending optimizer | Syntax, maximum three, event legality, minimum amount, re-simulation |
| `decision_explanation` | Final structured decision and audit trace | explanation / output writer | Grounded, concise, noncontradictory |

### 11.2 Failure attribution

Every mismatch or rejected run must be assigned to the earliest responsible stage:

```text
evidence -> recurrence -> FX -> simulation -> capacity -> candidate eligibility -> ranking -> formatting
```

Each projected cash row should retain its originating event/evidence/recurrence/FX reference. Each candidate should retain explicit rejection reasons and its minimum simulated balance.

## 12. Confirmed sample interpretations and unresolved assumptions

### 12.1 Behavior confirmed across authoritative text and samples

| Behavior | Evidence |
| --- | --- |
| Full safe payment now produces `affordable_now` | Sample 01 |
| A financially safe full amount does not override refusal of full payment | Sample 12 chooses installments while earliest full date is request date |
| Partial payment uses exactly safe-now amount plus the remainder on earliest full-payment date | Sample 19 |
| A permitted stop/reduction can enable immediate full payment, while capacity fields remain no-change values | Samples 06, 11, and 21 |
| Earliest no-change full-payment date may be after the user's deadline and is still populated | Samples 06, 11, and 21 |
| Wait requires a later safe full date within the deadline | Samples 03, 04, 08, 13, 18, and 23 |
| An installment option can be rejected by method preference or duration even though it exists | Samples 01, 02, 03, and 12 |
| `not_affordable` can still report a positive safe amount today | Samples 05, 10, 14, 15, 20, 24, and 25 |

These samples corroborate general rules; production code must not recognize their IDs.

### 12.2 Assumptions requiring global sample calibration

| ID | Ambiguity | Provisional decision | Verification method |
| --- | --- | --- | --- |
| A-01 | Is “next 90 days” request date through +89 or through +90? | Use conservative inclusive `request_date + 90` until tested | Compare capacity/date results for all 25 samples under both boundaries; retain one global rule |
| A-02 | Ordering of multiple movements on the same date | Required debits and candidate payments before eligible credits | Build boundary fixtures; inspect sample dates with same-day events; use safer rule if labels do not distinguish |
| A-03 | Minimum history and cadence tolerance for recurrence | Require repeated, consistent history; no category-only recurrence | Evaluate one global threshold set against all sample capacity outputs and inspect false positives |
| A-04 | Conservative estimator and timing for essential variable spending | Use a documented robust recent-history estimate without unsupported inflation | Compare global estimators against all sample safe amounts; diagnose by cash-flow trace, not request ID |
| A-05 | Exact interpretation of `max_installment_months` for 28/30/31-day options | Initially require `number_of_payments <= max_installment_months` | Verify every solved installment selection/rejection and add synthetic duration boundaries |
| A-06 | Final deterministic order among non-option candidates tied after all official criteria | Preserve official criteria first; use a stable documented fallback only if needed | Enumerate sample candidate ties and add a synthetic tie fixture |
| A-07 | Canonical decimal text in `payment_plan` | Integral payments have no decimals; fractional payments use two decimal places | Compare all solved plan strings through one global formatter |
| A-08 | Treatment of a future record marked `settled` relative to the request-date balance | Apply on settlement date but flag contradictory chronology | No such observed sample pattern was relied upon; test a conservative synthetic fixture |

The status of each assumption must be recorded with evaluation evidence before the final run. Do not silently tune an assumption against evaluation requests.

## 13. Minimal implementation architecture

Keep the Python implementation small and dependency-light. Reuse files if equivalent modules already exist.

```text
code/
  main.py                    # terminal entry point and orchestration only
  buy_or_wait/
    models.py                # dataclasses, enums, Decimal/date types
    data.py                  # CSV parsing, indexes, joins, exact FX lookup
    evidence.py              # typed message/image facts and cache boundary
    forecast.py              # state reconstruction, recurrence, 90-day simulator, capacity
    plans.py                 # candidate generation, spending changes, validation, ranking
    output.py                # explanation templates and exact CSV serialization
  evaluation/
    main.py                  # 25-sample metrics and deterministic validators
    usage_report.md          # exact final-run model/token/cost report
  tests/                     # focused unit, integration, regression, and adversarial tests
```

No UI, server, database, vector store, RAG system, workflow framework, or multi-agent architecture is required. Direct indexed joins replace retrieval infrastructure.

## 14. Deterministic validation and submission constraints

Before writing the final file, independently validate:

- exact columns and order;
- exactly 250 unique evaluation request IDs;
- Decimal parsing and amount bounds;
- allowed enum values;
- chronological and complete plans;
- exact partial-payment rules;
- exact installment-option match;
- deadline and 90-day safety;
- legal flexible-only spending changes;
- grounded explanations.

The final code must run from the terminal, read secrets only from environment variables, avoid organizer-only files and hardcoded labels, and document setup/run steps. `code.zip` must contain `evaluation/usage_report.md` for the exact full-dataset run. Root `log.txt` remains append-only, UTF-8, shared, and gitignored; it is submitted separately as the chat transcript.

