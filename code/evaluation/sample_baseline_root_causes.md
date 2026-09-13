# 25-sample baseline root-cause table

| Request | Field | Expected | Actual | First stage |
|---|---|---:|---:|---|
| request_01 | amount_safe_to_pay | 25256 | 22744.81 | safe amount |
| request_01 | affordability_status | affordable_now | not_affordable | candidate eligibility/ranking |
| request_01 | recommended_payment_method | full_payment | not_recommended | candidate eligibility/ranking |
| request_01 | payment_plan | 2024-03-03:25256 | none | candidate eligibility/ranking |
| request_01 | earliest_date_for_full_payment | 2024-03-03 |  | earliest date |
| request_02 | amount_safe_to_pay | 17229139.2 | 5142579.66 | safe amount |
| request_02 | affordability_status | affordable_with_plan | not_affordable | candidate eligibility/ranking |
| request_02 | recommended_payment_method | installments | not_recommended | candidate eligibility/ranking |
| request_02 | payment_plan | 2025-08-08:15952906.67|2025-09-07:15952906.67|2025-10-07:15952906.67 | none | candidate eligibility/ranking |
| request_02 | earliest_date_for_full_payment | 2025-09-15 | 2025-10-15 | earliest date |
| request_03 | amount_safe_to_pay | 873000 | 1537587.02 | safe amount |
| request_03 | payment_plan | 2019-11-15:5491000 | 2019-10-15:5491000 | candidate eligibility/ranking |
| request_03 | earliest_date_for_full_payment | 2019-11-15 | 2019-10-15 | earliest date |
| request_04 | amount_safe_to_pay | 8401800 | 11876727.39 | safe amount |
| request_05 | amount_safe_to_pay | 737 | 1521.91 | safe amount |
| request_06 | amount_safe_to_pay | 603.3 | 620.4 | safe amount |
| request_06 | affordability_status | affordable_with_plan | affordable_now | candidate eligibility/ranking |
| request_06 | payment_plan | 2026-01-03:620.40 | 2026-01-03:620.4 | candidate eligibility/ranking |
| request_06 | earliest_date_for_full_payment | 2026-01-15 | 2026-01-03 | earliest date |
| request_06 | spending_changes_needed | stop:event_476 | none | spending changes |
| request_07 | amount_safe_to_pay | 87170.56 | 0 | safe amount |
| request_07 | affordability_status | affordable_with_plan | not_affordable | candidate eligibility/ranking |
| request_07 | recommended_payment_method | installments | not_recommended | candidate eligibility/ranking |
| request_07 | payment_plan | 2024-09-12:68432|2024-10-10:68432|2024-11-07:68432 | none | candidate eligibility/ranking |
| request_07 | earliest_date_for_full_payment | 2024-10-23 |  | earliest date |
| request_08 | amount_safe_to_pay | 284.57 | 0 | safe amount |
| request_08 | affordability_status | affordable_later | not_affordable | candidate eligibility/ranking |
| request_08 | recommended_payment_method | wait | not_recommended | candidate eligibility/ranking |
| request_08 | payment_plan | 2025-04-15:996.60 | none | candidate eligibility/ranking |
| request_08 | earliest_date_for_full_payment | 2025-04-15 |  | earliest date |
| request_10 | amount_safe_to_pay | 12700 | 266700 | safe amount |
| request_10 | earliest_date_for_full_payment |  | 2024-12-06 | earliest date |
| request_11 | amount_safe_to_pay | 12510645 | 13110000 | safe amount |
| request_11 | affordability_status | affordable_with_plan | affordable_now | candidate eligibility/ranking |
| request_11 | earliest_date_for_full_payment | 2025-07-15 | 2025-05-03 | earliest date |
| request_11 | spending_changes_needed | reduce_to:event_989:665950 | none | spending changes |
| request_13 | amount_safe_to_pay | 433.4 | 941.6 | safe amount |
| request_13 | affordability_status | affordable_later | affordable_now | candidate eligibility/ranking |
| request_13 | recommended_payment_method | wait | full_payment | candidate eligibility/ranking |
| request_13 | payment_plan | 2024-05-15:941.60 | 2024-03-07:941.6 | candidate eligibility/ranking |
| request_13 | earliest_date_for_full_payment | 2024-05-15 | 2024-03-07 | earliest date |
| request_14 | amount_safe_to_pay | 597.74 | 0 | safe amount |
| request_15 | amount_safe_to_pay | 83.05 | 0 | safe amount |
| request_17 | amount_safe_to_pay | 243849.58 | 274600 | safe amount |
| request_17 | earliest_date_for_full_payment | 2026-03-15 | 2026-03-01 | earliest date |
| request_18 | amount_safe_to_pay | 462 | 694.61 | safe amount |
| request_18 | payment_plan | 2026-09-15:3246.10 | 2026-08-15:3246.1 | candidate eligibility/ranking |
| request_18 | earliest_date_for_full_payment | 2026-09-15 | 2026-08-15 | earliest date |
| request_19 | amount_safe_to_pay | 28820 | 26231.32 | safe amount |
| request_19 | payment_plan | 2024-09-04:28820|2024-09-15:10840 | 2024-09-04:26231.32|2024-09-15:13428.68 | candidate eligibility/ranking |
| request_20 | amount_safe_to_pay | 5400 | 21614.85 | safe amount |
| request_21 | amount_safe_to_pay | 1543.35 | 1574.4 | safe amount |
| request_21 | affordability_status | affordable_with_plan | affordable_now | candidate eligibility/ranking |
| request_21 | payment_plan | 2026-04-03:1574.40 | 2026-04-03:1574.4 | candidate eligibility/ranking |
| request_21 | earliest_date_for_full_payment | 2026-04-15 | 2026-04-03 | earliest date |
| request_21 | spending_changes_needed | stop:event_1815|reduce_to:event_1816:23.50 | none | spending changes |
| request_22 | amount_safe_to_pay | 475.46 | 500.99 | safe amount |
| request_23 | amount_safe_to_pay | 9152 | 7221.71 | safe amount |
| request_24 | amount_safe_to_pay | 13420 | 23118.25 | safe amount |
| request_24 | earliest_date_for_full_payment |  | 2026-03-15 | earliest date |
| request_25 | amount_safe_to_pay | 1425000 | 4268528.45 | safe amount |

Differences remain regression cases; production never reads solved labels and fixes must be general.
