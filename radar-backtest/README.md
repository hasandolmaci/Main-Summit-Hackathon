# radar-backtest (v1)

Backtest for a startup "radar": do research-backed public signals flag startups **before** their first funding round?
This folder is a research prototype for choosing metrics, not the product. Everything is deterministic Python; no LLM
reads any data.

## Quick start
```bash
make setup        # venv + pinned requirements
make test         # unit, leakage and end-to-end tests (must be green before any result is reported)
make labels       # YC directory + HTGF portfolio -> data/labels/companies.csv
make cordis       # CORDIS tables from the bulk zips in data/raw/cordis (download them by hand: robots.txt)
make idmap        # GitHub account-id -> date map (recycled-login guard)
make pipeline     # org search, outcome-blind pool sample, GH Archive features, CORDIS links, validation, gate
make snapshots backtest report
```
No API keys are needed. Sources: yc-oss (YC directory mirror), htgf.de (deterministic parser), CORDIS bulk zips,
GH Archive via the free ClickHouse playground (quota ≈ 300e9 rows read per hour; the pipeline waits it out),
ecosyste.ms owner metadata (5,000 requests/hour anonymous). Everything is cached in `data/cache/` (gitignored), so
every step is resumable. See `HANDOFF.md` for decisions and status.

## Design in one paragraph
Candidates come from an outcome-blind pool of GitHub organisations: org-like GH Archive owners, hash-sampled per
first-activity year, whose website is a company domain, whose login is the domain stem, and that showed team activity
in their first 12 months (≥ 2 pushers, ≥ 10 Push/PR/Issues/Release events). Funded companies must meet the same rule. A seeded background sample **B** of the pool is drawn *before* any label lookup;
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
- [x] scaffold, shared library, tests (≈300), verified papers
- [x] labels (6,607), CORDIS (469k participations), keyless GitHub org discovery, GH Archive backend
- [ ] pipeline run (org search → pool → features → gate)
- [ ] snapshots, backtest, leakage review, report
