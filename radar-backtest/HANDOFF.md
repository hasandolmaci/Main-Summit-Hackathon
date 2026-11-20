# Handoff — radar-backtest v1

Read this first if you are a new session continuing the work. The approved plan is `docs/plan.md`
(scope v2: the user fixed the base metrics). The original brief is the user's master prompt; the key rules are repeated here.

## Non-negotiable rules
- Point-in-time: only events with `ts < as_of`; label = first funding in `[as_of, as_of+24m)`; already-funded excluded;
  training embargo `as_of ≤ Y−2`; kNN references funded before as_of, never the candidate itself.
- Pool/B sample is drawn **before any label lookup** (`scripts/select_controls.py` must never read labels).
- Same matching rule for every entity (positives and controls); only high-confidence links.
- Missing source = MISSING (NaN), not 0. LR fills 0 + `has_<source>`; kNN uses shared dimensions × sqrt(D/d).
- **Locked test years (2020–2024) are never scored** until the user explicitly says so (`guard_years`, test enforced).
- **Improvement loop (spec §9) is NOT started** until the user says "start the loop". `logs/loop_log.csv` has the header only.
- No LLM reads or classifies data; deterministic Python only. No LinkedIn/Crunchbase/etc. scraping. Respect robots.txt.
  Store no personal emails/phones (also not in caches). Ask the user before anything that costs money or > ~1 h API time.
- Commit after each working step; push to branch `claude/lucid-darwin-qmoswq` only. **No PR, never merge to main.**

## User decisions (chronological, all binding)
1. Free/official data only for v1 (no Apollo/Apify/paid exports). Keys via secure forms only, never pasted in chat.
2. Base metrics fixed by the user (see `README.md` table / `docs/plan.md`). First pass = GitHub (2 metrics) + CORDIS grant;
   then patents, trademarks, Companies House (serial_founder), Product Hunt, one at a time.
3. Removed from v1: Show HN pool, HN features, Wayback/website features, SEC Form D, OpenAlex, launch_attention.
4. YC label date = acceptance proxy (batch start − 76 days); batch start = sensitivity (`companies_batchstart.csv`).
5. HTGF via deterministic parser only (site sets `ai-input=no`).
6. GH Archive via the free ClickHouse public mirror (BigQuery backend coded, needs GCP creds, 200 GB guard).
7. Europe subset (EU/UK/CH/NO) is a headline result next to "all". Pooled AUC across selection years, no per-year headlines.
8. LinkedIn: rejected (ToS + not backtestable: no history, survivorship). Founder signal = Companies House `serial_founder` later.
9. CORDIS zips were downloaded by the user in a browser (robots.txt blocks `/data/` for scripts) and imported with
   `--zip-dir`; they live on branch `cordis-raw-zips` (not in the working branch). Finding: CORDIS has a website URL only
   for FP7-era companies (H2020/Horizon newcomers: 0.1% / 0.0% have one).
11. Pool v2 (2026-11-20): pool = org-like GH Archive owners hash-sampled per first-activity year that meet the
    **entry rule** (first 12 months: ≥ 2 distinct pushers and ≥ 10 Push/PR/Issues/Release events) and are findable
    by the **stem-only tier-1 search** (login = domain stem, website = domain); same rule for positives. Recycled
    logins (GitHub id created > 90 days after the first GH event) are dropped for everyone.
12. CORDIS grants matched by domain OR unique normalised legal name + same country (ccTLD); SPARQL confirmed 0.0%
    websites for post-FP7 companies. Keys never use label names.
13. ClickHouse playground quota (~25 full scans/hour): `scripts/pipeline.py` waits for the reset and resumes.
10. CORDIS **pool channel dropped** (`config.yaml pool.channels: [github]`): candidates come from GitHub orgs only; CORDIS
    stays the `has_public_rd_grant` feature. Grant **matching rule pending**: user waits for the SPARQL check; the
    recommended extension is exact normalised legal name + same country (country from ccTLD/registry), unique match only.

## Status (see `git log`)
| piece | status |
|---|---|
| scaffold, `scripts/lib` (config, cached http, domains, schema, meta, scaling, cohort, metrics) | done, tested |
| papers: `papers/index.md` (16 papers, quotes + pages, independently rechecked) | done |
| labels: `data/labels/companies.csv` 6,600 rows (YC 6,078 + HTGF 522) | done (`make labels`) |
| GH Archive collector `scripts/sources/gharchive.py` (span/team/activity/external, schema-check, fidelity) | done, smoke-tested on 5 orgs |
| GitHub org discovery, **GitHub-API route** `scripts/sources/github.py` | done + reviewed, but **blocked in cloud sessions** (proxy allows repo-scoped REST only). Works on a normal machine with `GITHUB_TOKEN` (public read-only). |
| GitHub org discovery, **keyless route** `scripts/sources/github_owners.py` (GH Archive + ecosyste.ms, 5,000 req/h anonymous) | being built (may be missing/partial — check the file and its tests) |
| CORDIS bulk `scripts/sources/cordis.py` | done: 469k participations, 12.5k PRC company domains imported from the user's zips (`git show origin/cordis-raw-zips:cordis_zips/<file>` to get them) |
| CORDIS SPARQL `scripts/sources/cordis_sparql.py` (allowed endpoint `/datalab/sparql`) | being built (may be missing/partial) |
| pool/B `select_controls.py`, entities/events `resolve_entities.py`, `gate.py`, `validate_data.py` | done, tested on synthetic data |
| snapshots `build_snapshots.py` (+ leakage mutation tests), `backtest.py` (+ embargo/reference/determinism tests) | done, tested on synthetic data |
| `report.py` (results/report.md + figures) | **not written yet** |

## Next steps (exact order)
On a normal machine (e.g. the user's Mac) the GitHub-API route is the fastest and gives GitHub's own org creation date.
```bash
cd radar-backtest && make setup && make test
export GITHUB_TOKEN=...                     # public read-only token; never commit it
# 1) CORDIS: either the SPARQL collector (if finished) or the manual zips:
.venv/bin/python -m scripts.sources.cordis --zip-dir ~/Downloads/cordis      # FP7, H2020, HORIZON csv zips
# 2) outcome-blind GitHub org sample (pool channel a) — pick ONE route:
.venv/bin/python -m scripts.sources.github sample                            # GitHub API route
#    or: .venv/bin/python -m scripts.sources.github_owners sample           # keyless route
# 3) universe + B, then org search for everyone (same rule for all entities):
.venv/bin/python -m scripts.select_controls
.venv/bin/python -m scripts.resolve_entities targets
.venv/bin/python -m scripts.sources.github find --input data/processed/org_search_input.csv
# 4) GH Archive for all matched orgs (orgs file = one login per line from entity_github_org.parquet)
.venv/bin/python -c "import pandas as pd; pd.read_parquet('data/processed/entity_github_org.parquet').org_login.drop_duplicates().to_csv('data/processed/orgs.txt', index=False, header=False)"
.venv/bin/python -m scripts.sources.gharchive all --orgs data/processed/orgs.txt
# 5) entities + events, validation, GATE (stop here and show the user the counts)
.venv/bin/python -m scripts.resolve_entities build
make validate gate
# 6) after the user's OK: snapshots -> backtest (selection years only) -> report
make snapshots backtest
```
Then write `scripts/report.py` (headline "We flagged X of Y funded startups a median of N months before their first round",
dataset/coverage, methods vs baselines with CIs, LR coefficients vs paper signs, R=5 vs R=3, failure cases, limitations)
and send the user the gate counts and results.

## Known caveats to carry into the report
- Org website (`websiteUrl` / ecosyste.ms `website`) is current state; renamed orgs lose GH Archive history (mirror has no ids).
- Survivorship: deleted orgs/companies missing for both groups, but survival correlates with funding.
- Channel (b) CORDIS entities always have grant = 1 at visibility → grant AUC partly measures the channel (do a channel audit).
- Label coverage outside the US/Germany is thin → European controls more contaminated.
- `github_activity_acceleration` has weak evidence (no paper measures acceleration); `oss_external_engagement` differs from
  Conti et al. (org-level, fork-dominated) — user's definition kept; see `papers/index.md`.
- ClickHouse mirror: 1.3% fewer PushEvents than raw GH Archive in a 2021 sample hour; PR/Issues/Release exact.
