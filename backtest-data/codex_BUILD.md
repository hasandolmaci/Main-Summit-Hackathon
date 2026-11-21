Implemented and ran training plus both scoring commands. Selected **k=4, λ=4**. All 10 tests pass; `backtest.csv` is unchanged.

Created [model weights](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/models/benchmark_weights.json>), [full report](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/reports/benchmark_report.md>), [205 scored backtest rows](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/reports/scored_backtest.csv>) and [27 scored leads](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/reports/scored_leads.csv>).

CV results, mean ± SD across 20 repeats:

| Model | AUC | Brier | Log loss |
|---|---:|---:|---:|
| Constant | .4461 ± .0000 | .1240 ± .0000 | .4146 ± .0000 |
| Year-only | .5928 ± .0493 | .1215 ± .0029 | .4052 ± .0103 |
| Prior-offset | .6811 ± .0275 | .1169 ± .0030 | .3852 ± .0097 |
| Full | .6820 ± .0267 | .1167 ± .0029 | .3847 ± .0096 |

Final weights:

| Feature | Weight |
|---|---:|
| f_pricing_page | .8060 |
| f_impressum | .4803 |
| has_legal_entity | .2784 |
| f_gmbh_ig | .2000 |
| f_waitlist | .2000 |
| f_customer_logos | .2485 |
| f_jobs_page | .0865 |
| m_grant_score | .2109 |
| m_gh_has_open_source | .4000 |
| m_gh_external_score | .2000 |
| m_patent_verified_before_asof | .2000 |
| m_ph_score_before_asof | .2000 |
| age_le_18 | .4856 |

Paired AUC-difference 95% CI: **[−.0298, .0356]**. With 13 positives, results remain exploratory. Scores are percentiles, not probabilities. Discovery leads have **0% coverage**, tied scores and `ELIGIBLE_UNCHECKED` status.