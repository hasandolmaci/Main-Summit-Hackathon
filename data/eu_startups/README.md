# European early-stage startups, as-of 2021–2023: backtest dataset

## What each row is
One startup frozen at its **as-of date** (`asof_date`). Each row meets these conditions:
- Based in Europe (EU, UK, CH, NO, IS, TR and the Balkans). `country = EU_UNKNOWN` means Crunchbase tags it as EU but the country could not be worked out.
- Had raised **≤ €800k before `asof_date`** (see `eligible` and `pre_asof_funding_eur`).
- Young: founded 2019–2022 (Crunchbase cohort) or at most 5 years old at the grant (Innovate UK).

**`label = 1`** means the startup raised its first equity round (pre-seed, seed, angel, Series A or later, convertible note, equity crowdfunding) in the **24 months after `asof_date`**. Grants and debt do not count.

Use **`eu_startups_2021_2023_train.csv`** for training. It keeps only rows where `train_ready = 1`: the funding filter is met, the label is known, the 24-month window has been fully observed, and the company is in a tech category.
`eu_startups_2021_2023_all.csv` keeps everything else too, such as censored 2023 rows and rows with unknown pre-as-of funding.

**Size:** 33,195 rows in total. 19,711 are train-ready, with 2,137 positives (10.8%), across 41 countries.
The train set is 17,358 rows from the Crunchbase cohort and 2,353 from the grant and pitch programs (almost all Innovate UK); 13,216 have a 2021 as-of date, 5,678 a 2022 date and 817 a 2023 date.
EIC Accelerator, Women TechEU and Falling Walls rows are mostly in the "all" file only. They either lack a Crunchbase match or have a 24-month window that ends after the snapshot.

## Where each cohort comes from
| cohort_source | as-of date | comp_placement | source |
|---|---|---|---|
| Crunchbase young-startup cohort (founded 2019/2020/2021/2022) | 30 Jun 2021 / 30 Jun 2021 / 30 Jun 2022 / 30 Jun 2023 | empty (not a competition) | Crunchbase snapshot, Aug 2024 (Hugging Face `opensporks/crunchbase`) |
| EIC Accelerator (ACC, ACC-BF) | grant signature date 2021–2023 | 2 (winner) | CORDIS Horizon Europe open data |
| Women TechEU 2021/2022 | grant signature date | 2 | CORDIS |
| Innovate UK: <competition> | project start 2021–2023; micro/small lead applicant, ≤ 5 years old | 2 | UKRI "Innovate UK funded projects" + Companies House |
| Falling Walls Venture 2021–2023 | 7 Nov of each year | 1 finalist / 2 winner | falling-walls.com finalists page |

## How the labels were made (`label_basis`)
- `cb_*`: from the Crunchbase snapshot (round count, last round type and date). `cb_last_round_in_window(first_date_unknown)` means the company raised in the window but the exact first-round date is unknown.
- `round_data` / `news_round_data`: news-based round records from the Fliar Radar samples on Hugging Face (CC BY-NC).
- `companies_house_SH01+articles`: a UK share allotment (SH01) filed within 60 days of a resolution or new articles, inside the window. This is a priced-round signal taken from public filing history, which is current to Nov 2026. `companies_house_no_round_in_window` means no such filing in the window.
- `label_complete = 0`: the window runs past the Aug 2024 snapshot, so a 0 can't be trusted yet. Every 2023 Crunchbase row is in this state.

## Checks
- Spot-check against the web: DabbaDrop, ManholeMetrics and Aerit are all labelled 1, and all three rounds are confirmed on public funding pages with matching dates.
- Companies House signal vs Crunchbase label, on 193 UK companies that have both: they agree 87% of the time. The Companies House signal catches 71% of the rounds Crunchbase records. When it flags a round, Crunchbase agrees 88% of the time.

## Columns from your schema
| column | status |
|---|---|
| id, name, domain, country, cohort_source, asof_date | filled |
| label, first_round_date, label_evidence_url | filled. The date is empty when only "raised in window" is known |
| comp_placement | filled for program rows. Empty for the Crunchbase cohort |
| cc_rank_pct | filled. Common Crawl domain harmonic-centrality percentile from the last web-graph release **before** `asof_date`. 0 = domain not in the graph |
| pat_any, pat_count | **empty**. Run `s05_epo_ops_patents.py` with a free EPO OPS key. Google Patents blocked bulk queries from this environment |
| tm_any, tm_eu | **empty**. Run `s06_trademarks_apify.py` with an Apify token. EUIPO/TMview block scripted search |
| ip_bundle | computed by `s05` once patents and/or trademarks are in |
| gh_ext_contrib, gh_commit_accel | **empty**. Run `s04_github.py` with `GITHUB_TOKEN`. It matches GitHub orgs only when the org's website equals the company domain |

## Known biases
1. **Crunchbase coverage bias.** Startups that later raise are more likely to have a Crunchbase profile. That makes negatives under-represented in the Crunchbase cohort (base rate about 10–13%). Real early-stage base rates are lower. The grant cohorts don't have this problem, because their membership comes from the grant lists.
2. **Pre-as-of funding can be undercounted.** Crunchbase sometimes misses small early rounds. Example: Aerit had a $549k seed in Jan 2022 that Crunchbase doesn't list. It is still under the cap.
3. **"Open to funding"** is proxied by being young, tech, and ≤ €800k raised, or by having applied to a competitive program. `open_source_hint = 1` marks companies tagged "Open Source". Filter them out if you want closed-source only.
4. **2023 Crunchbase rows are censored.** Labelling them needs funding data after Aug 2024, for example tech.eu or Dealroom.
