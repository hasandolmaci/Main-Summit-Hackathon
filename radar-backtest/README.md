# radar-backtest (v1)

Backtest for a startup "radar": do research-backed public signals flag startups **before** their first funding round?
This folder is a research prototype for choosing metrics, not the product. Everything is deterministic Python; no LLM
reads any data.

## Quick start
```bash
make setup        # venv + pinned requirements
make test         # unit + leakage tests (must be green before any result is reported)
make labels       # YC directory + HTGF portfolio -> data/labels/companies.csv
make cordis       # CORDIS bulk (FP7, H2020, Horizon Europe)
make orgs         # GitHub org sample (pool) + org lookups for labelled companies
make gate         # count covered positives per year + metric coverage (stop and review here)
make gharchive    # GH Archive events (free ClickHouse public mirror by default)
make validate snapshots backtest report
```
Secrets come only from environment variables (`GITHUB_TOKEN`; later `COMPANIES_HOUSE_API_KEY`, `PRODUCTHUNT_TOKEN`).
Nothing secret is written to disk. Collection is cached in `data/cache/` (gitignored), so every step is resumable.

## Design in one paragraph
Candidates come from an outcome-blind pool: (a) GitHub organisations whose website is a company domain, and (b) private
companies in EU CORDIS grant data. A seeded background sample **B** of the pool is drawn *before* any label lookup;
B minus funded companies are the controls, and B is also the scaling reference. Positives are YC / HTGF companies that
meet the same pool criteria. For each as_of date (1 January), only events with `ts < as_of` are used; label = first
funding in `[as_of, as_of + 24 months)`; already-funded companies are excluded. Models are trained only on as_of years
whose labels were fully known at prediction time (embargo `≤ Y−2`). Locked test years are never scored until the user
says so (`RADAR_UNLOCK_TEST=1` + explicit flag).

## Base metrics (fixed; evidence in `papers/index.md`)
| metric | source | timestamp | transform |
|---|---|---|---|
| has_public_rd_grant | CORDIS bulk | signature date | binary |
| oss_external_engagement | GH Archive | event time | 12m count → log1p → robust z vs same-year B → sigmoid |
| github_activity_acceleration | GH Archive | event time | log((W1+1)/(W0+1)) → clip ±3 → sigmoid |
| has_patent_application, has_trademark, serial_founder, producthunt_rank | added one at a time after the first pass | | |

Missing source = MISSING (not 0). kNN uses dimensions observed for both entities (× sqrt(D/d)); logistic regression
fills 0 and adds `has_<source>`.

## Layout
`data/labels` labels · `data/raw/<source>` raw responses (never hand-edited; bulk zips referenced by manifest + sha256) ·
`data/processed` entities / events / snapshots (each with `.meta.json` provenance) · `scripts/lib` shared code ·
`scripts/sources` one collector per source · `tests` pytest · `results` report + figures · `logs` collection log and
(later) improvement-loop log.

## Status
- [x] scaffold + shared library + tests
- [ ] labels, CORDIS, GitHub orgs, GH Archive collectors
- [ ] gate check
- [ ] snapshots, backtest, report
