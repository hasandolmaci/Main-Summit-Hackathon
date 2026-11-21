# Startup Radar engine: first backtest on the EU 2021–2023 dataset

Two CSVs in (`metrics.csv` + companies), one ranked CSV out. No LLM at scoring time.

```bash
pip install -r engine/requirements.txt
python engine/prepare_eu.py      # data/eu_startups/... -> engine/build/companies_eu.csv
cd engine && python backtest.py  # -> engine/out/backtest_results.{csv,md}, shortlist_<year>.csv
```

| File | What it is |
|---|---|
| `metrics.csv` | Metric registry: paper, source column, transform, weight, active flag |
| `category_map_tech_depth.csv` | Frozen LLM encoding of Crunchbase categories (see `encoder_system_prompt.md`) |
| `prepare_eu.py` | Maps the dataset to the engine's company format, point-in-time columns only |
| `radar.py` | Transforms → profile vectors → weighted distance → score + explanation |
| `backtest.py` | Freeze-date backtest, AUC with bootstrap CI, precision@k, shuffled-label check |

## Data decisions

- **Exclusions:** 36 open-source-only companies and 1,493 companies that had already raised equity before the as-of date. The label means "first equity round", so the second group is 0 by definition and would only add noise. 18,186 rows are left.
- **Point in time:** the Common Crawl rank is dropped when the graph release came after the as-of date (237 rows). Labels, `cb_status_2024` and Companies House status are never used as features.
- **Grant amounts:** Innovate UK awards are in GBP and EIC grants in EUR. GBP is converted at 1.17.
- **Empty = unknown.** Patents, trademarks and GitHub are empty in this dataset, so those metrics are in `metrics.csv` but switched off (`active=0`).
- **Is an LLM needed?** Only for `categories`. There are 763 Crunchbase tags, too many to map by hand. Every other metric is binary, numeric or a ratio.

Active metrics and their coverage: `web_visible` 87%, `web_centrality` 57%, `grant_won` 13%, `grant_amount` 13%, `sector_tech_depth` 88%.

## Backtest design

For each test year Y:
1. Freeze the data at 1 Jan Y.
2. **Reference winners** are companies with an earlier snapshot whose first round is dated before the freeze. **Reference others** are all other earlier-snapshot companies.
3. The **test set** is companies with a snapshot in Y.
4. The **label** is whether they raised within 24 months.

| Test year | Test companies | Later raised | Reference winners | Reference others |
|---|---|---|---|---|
| 2022 | 5,136 (4,159 Crunchbase, 977 grant programs) | 724 | 299 | 11,934 |
| 2023 | 817 (all Innovate UK) | 86 | 1,043 | 16,326 |

## Results (full table: `out/backtest_results.md`)

| Model | 2022 all | 2022 grant programs | 2023 (Innovate UK) |
|---|---|---|---|
| `spec_knn`: 1 − mean distance to 10 nearest winners (as designed) | 0.517 (0.49–0.54) | 0.461 | 0.504 (0.45–0.57) |
| same, with **shuffled reference labels** | 0.512 | – | 0.502 |
| `contrast_knn`: distance to others − distance to winners | 0.523 | 0.469 | 0.504 |
| `share_knn`: share of winners among 100 nearest | 0.525 | 0.575 | 0.589 |
| `prior_weighted_sum`: weighted mean of metric values, no labels | 0.490 | **0.596** | **0.594 (0.53–0.67)**, top-10% lift 2.3× |
| logistic regression (diagnostic only) | 0.486 | 0.586 | 0.632 |

AUC with 95% bootstrap CI. Base rate is 14.1% in 2022 and 10.5% in 2023.

Single-metric AUCs, as a diagnostic (not used for tuning):

| Metric | Result |
|---|---|
| `grant_amount` | 0.60–0.66 in every program year |
| `sector_tech_depth` | 0.53–0.55 in the Crunchbase cohort |
| `web_centrality` | 0.56 in 2021 but 0.50 in 2022 |
| `web_visible` | ≤ 0.5 |

## What this shows

1. **The distance-to-winners score, as designed, finds no signal.** Its AUC is the same as with shuffled reference labels. With only 3–5 weak, low-dimensional metrics and thousands of reference companies, every company has near-identical neighbours. The distance then measures how common a profile is, not whether it looks like a winner. `contrast_knn` falls into the same trap, because the nearest "others" are always at distance ≈ 0.
2. **A fixed weighted sum without labels works where there is a real signal.** In grant cohorts it reaches AUC ≈ 0.59–0.60, with precision@50 of 26% against a 10.5% base rate. The signal is almost all `grant_amount`.
3. **No model works in the Crunchbase cohort** (AUC ≤ 0.53). Web rank and sector are all it has. The research-backed signals (patents, trademarks, outside open-source work) are exactly the empty columns.
4. **Keep "nearest past winner" as the explanation, not as the score.** The shortlists rank by `prior_weighted_sum` and show the nearest winner plus the shared strong metrics. Shared zeros are never listed as a reason.

## Caveats

- At a freeze date, the known winners are **fast raisers**. In 2022 they are 2021-snapshot companies that raised within about 6 months. The test label covers 24 months.
- The 2023 test set is UK grant recipients only. Crunchbase-based negatives are under-represented (see `data/eu_startups/README.md`).
- Categories come from the Aug 2024 Crunchbase snapshot, not the as-of date. Only 9% of program rows have categories.
- HTGF lead time can't be measured here: the dataset has no HTGF portfolio flag.

## Next steps, in order

1. Fill `pat_any`, `tm_any` and `gh_ext_contrib` with `data/eu_startups/pipeline/s04–s06`, then set `active=1` and rerun.
2. Add the grant status for Crunchbase-cohort companies (a CORDIS / Innovate UK match by name or domain). Grants are the one signal that works.
3. Decide between the weighted sum and `share_knn` only after step 1. With today's metrics, neither distance variant is worth its complexity.
