# DACH cohort backtest — improvement loop (exploratory)

**Verdict: no metric distinguishable from noise.** Real-label OOF AUC **0.620** beats **55%** of the 20 shuffled-label runs (needs ≥ 95%; shuffled OOF AUC median 0.616, 95th pct 0.692; shuffled runs kept on average 0.1 metrics).

- Labelled companies: 85 (13 positives) after cleaning; label definition frozen.
- Baseline floor (cohort source + cohort year): OOF AUC **0.620**.
- Starting set (GitHub, as-of gated): OOF AUC 0.565.
- Final set: **baseline only** → OOF AUC 0.620.

AUC = within-fold AUC pooled over grouped folds (groups = programme × cohort), 5 folds × 20 repeats; Δ CIs from a 1,000× paired bootstrap on out-of-fold predictions.

## Every attempt (real labels)

| pass     | metric                |   auc_before |   auc_after |   delta |   ci_lo |   ci_hi |   share_improved | decision         | reason                                                                                                   |
|:---------|:----------------------|-------------:|------------:|--------:|--------:|--------:|-----------------:|:-----------------|:---------------------------------------------------------------------------------------------------------|
| forward  | legal_form_level      |        0.565 |       0.6   |   0.035 |  -0.109 |   0.181 |             0.8  | drop             |                                                                                                          |
| forward  | registered_hrb        |        0.565 |       0.669 |   0.104 |  -0.09  |   0.293 |             0.9  | drop             |                                                                                                          |
| forward  | corporate_spinoff     |        0.565 |       0.539 |  -0.026 |  -0.12  |   0.039 |             0.25 | drop             |                                                                                                          |
| forward  | pricing_page          |        0.565 |       0.673 |   0.108 |  -0.054 |   0.27  |             1    | drop             |                                                                                                          |
| forward  | customer_logos        |        0.565 |       0.524 |  -0.041 |  -0.177 |   0.08  |             0.2  | drop             |                                                                                                          |
| forward  | waitlist              |        0.565 |       0.584 |   0.019 |  -0.087 |   0.112 |             0.7  | drop             |                                                                                                          |
| forward  | product_live          |        0.565 |       0.53  |  -0.035 |  -0.131 |   0.02  |             0.1  | drop             |                                                                                                          |
| forward  | team_size             |        0.565 |       0.572 |   0.007 |  -0.078 |   0.107 |             0.45 | drop             |                                                                                                          |
| forward  | eng_roles             |        0.565 |       0.588 |   0.023 |  -0.072 |   0.122 |             0.7  | drop             |                                                                                                          |
| forward  | sales_roles           |        0.565 |       0.591 |   0.026 |  -0.069 |   0.113 |             0.75 | drop             |                                                                                                          |
| forward  | growth_tooling        |        0.565 |       0.506 |  -0.059 |  -0.188 |   0.044 |             0.05 | drop             |                                                                                                          |
| forward  | infra_activity        |        0.565 |       0.544 |  -0.021 |  -0.105 |   0.038 |             0.05 | drop             |                                                                                                          |
| forward  | domain_age            |        0.565 |       0.561 |  -0.004 |  -0.122 |   0.112 |             0.5  | drop             |                                                                                                          |
| forward  | exist_programme_grant |        0.565 |       0.561 |  -0.004 |  -0.047 |   0.023 |             0.35 | drop             |                                                                                                          |
| forward  | federal_rd_grant      |        0.565 |       0.599 |   0.034 |  -0.027 |   0.107 |             0.9  | drop             |                                                                                                          |
| forward  | news_visibility       |        0.565 |       0.551 |  -0.014 |  -0.074 |   0.035 |             0.2  | drop             |                                                                                                          |
| forward  | verified_patent       |        0.565 |       0.5   |  -0.065 |  -0.246 |   0.049 |             0.15 | drop             |                                                                                                          |
| forward  | serial_founder        |              |             |         |         |         |                  | excluded_pre_run | provenance cannot be shown to be as-of (founder history likely from present-day LinkedIn); rule: exclude |
| forward  | product_stage         |              |             |         |         |         |                  | skipped          | no variance / no as-of data                                                                              |
| forward  | research_spinoff      |              |             |         |         |         |                  | skipped          | no variance / no as-of data                                                                              |
| forward  | ip_claim              |              |             |         |         |         |                  | skipped          | no variance / no as-of data                                                                              |
| forward  | academic_founders     |        0.565 |       0.573 |   0.008 |  -0.115 |   0.143 |             0.55 | drop             |                                                                                                          |
| forward  | hardware              |              |             |         |         |         |                  | skipped          | no variance / no as-of data                                                                              |
| forward  | b2b                   |              |             |         |         |         |                  | skipped          | no variance / no as-of data                                                                              |
| backward | gh_public_repos       |        0.567 |       0.565 |  -0.002 |  -0.043 |   0.031 |             0.5  | REMOVED          |                                                                                                          |
| backward | gh_open_source        |        0.586 |       0.567 |  -0.019 |  -0.126 |   0.077 |             0.3  | REMOVED          |                                                                                                          |
| backward | gh_external_contribs  |        0.62  |       0.586 |  -0.035 |  -0.149 |   0.047 |             0.05 | REMOVED          |                                                                                                          |

## Leakage guards

- Tripwire check: `needs_review` scores univariate AUC **0.89** on the labelled rows and is caught (test `test_tripwire_catches_needs_review`). Any candidate with univariate AUC > 0.80 or derived from a timestamp is halted and logged as a suspected leak.
- GitHub metrics NaN when the account was created after as_of; snapshot-derived fields NaN unless the snapshot lies before as_of; patents only via `m_patent_verified_before_asof` (raw assignee matches collide).
- `m_serial_score` excluded before the run (provenance not shown to be as-of).
- Text candidates 19–21, 23, 24 (step 4c) were not computed: no Claude API key in this environment. They remain pre-registered; running them later is a separate, clearly labelled run.

## Shuffled-label control

| run | kept | metrics kept | OOF AUC |
|---|---|---|---|
| 0 | 0 | – | 0.627 |
| 1 | 0 | – | 0.616 |
| 2 | 0 | – | 0.629 |
| 3 | 0 | – | 0.613 |
| 4 | 0 | – | 0.607 |
| 5 | 0 | – | 0.602 |
| 6 | 0 | – | 0.625 |
| 7 | 0 | – | 0.623 |
| 8 | 0 | – | 0.597 |
| 9 | 0 | – | 0.607 |
| 10 | 0 | – | 0.623 |
| 11 | 1 | legal_form_level | 0.755 |
| 12 | 0 | – | 0.614 |
| 13 | 0 | – | 0.621 |
| 14 | 0 | – | 0.613 |
| 15 | 0 | – | 0.616 |
| 16 | 0 | – | 0.632 |
| 17 | 0 | – | 0.601 |
| 18 | 0 | – | 0.602 |
| 19 | 1 | team_size | 0.688 |

With shuffled labels the keep rule still kept at least one metric in 2 of 20 runs (an empirical false-keep rate of ~10% per full loop at n = 85).
