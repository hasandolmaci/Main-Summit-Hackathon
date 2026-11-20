# Radar backtest v1 — plan (scope v2, user-directed)

## Context
This is a backtesting system for a startup radar (Main Summit Hackathon; HTGF is the jury). It tests whether metrics backed by
research and computed from public timestamped records flag startups before their first funding. The user fixed the base metrics
(research done by the user). This session builds the data pipeline, point-in-time snapshots, walk-forward backtest, papers/index.md
with quotes and page numbers, and an honest report.

**Unchanged from the previous plan**
- folder layout and `scripts/lib`: HTTP cache, backoff, logging, meta.json sidecars
- leakage tests, the locked-years guard, determinism
- the improvement loop is known but NOT started
- the honest report
- pushed only to branch `claude/lucid-darwin-qmoswq`; no PR

**Removed from v1:** Show HN pool, HN features, Wayback and all website features, SEC Form D, OpenAlex, launch_attention.
- No website scraping, apart from parsing the HTGF portfolio label list.
- No LLM reads any data; all processing is deterministic Python.

## Base metrics (fixed by the user; not re-selected)
| metric | evidence (quote + page in papers/index.md) | source | ts | transform |
|---|---|---|---|---|
| has_patent_application | Haeussler/Harhoff/Mueller 2014; Farre-Mensa et al. 2020 | patents-public-data (BigQuery) | publication_date | binary |
| has_trademark | Block et al. 2014; Catalini/Guzman/Stern | EUIPO + USPTO bulk | filing date | binary |
| has_public_rd_grant | Howell 2017; Santoleri et al. 2024 | CORDIS bulk H2020 + Horizon Europe (+ FP7, same format, so pre-2014 grants aren't missed) | ecSignatureDate (else startDate) | binary |
| oss_external_engagement | Conti/Peukert/Roche 2025 | GH Archive | created_at | 12m count → log1p → robust z vs same-year B controls → sigmoid (IQR=0 → percentile) |
| github_activity_acceleration | Conti et al. 2025; Wright/Nagle/Greenstein | GH Archive | created_at | log((W1+1)/(W0+1)), 90d windows → clip ±3 → sigmoid |
| serial_founder | Hsu 2007; Zhang 2011 | Companies House officers + SH01 | appointment/filing | 0 / .5 / 1, UK only |
| producthunt_rank | Cao 2019 (medium) | Product Hunt API v2 | launch date | 0 / .33 / .66 / 1 |

**Definitions**
- **Team(as_of):** actors with a PushEvent to repos owned by the org (repo_name owner == org login) before as_of.
- **oss_external_engagement:** the team's PullRequestEvent(opened) + IssuesEvent(opened) + PushEvent to repos not owned by the org, in [as_of−12m, as_of).
- **Activity (for acceleration):** Push, PR, Issues and Release events on org-owned repos.
- If the paper's definition differs, index.md says so; the user's definition stays the implementation.

**Missing vs 0:** if a source does not cover an entity, the value is MISSING (NaN), not 0.
- **GitHub:** missing when there is no matched org.
- **CORDIS:** missing when the entity's country is not Horizon-eligible (ccTLD or registry); unknown-country .com/.io is also missing.
- **Companies House:** missing for non-UK companies.
- **kNN:** distance over the dimensions both entities observe, × sqrt(D/d); d=0 means no neighbour.
- **Logistic regression:** fill 0 and add a has_<source> flag.

**Scaling** uses only the same-year, outcome-blind B sample. Functions get no labels (enforced by their signatures).

## Data sources (first pass = GitHub + CORDIS)
- **GH Archive.** Primary: the ClickHouse public playground table `github_events` (play.clickhouse.com, user `play`).
  - It is a free, keyless mirror of GH Archive: 11.2B events from 2011-02-12 to today.
  - A full-history query took ~2 s.
  - It has author_association at event time.
  - Mirror fidelity test: event counts for 3 sample hours (2013, 2016, 2021) are compared with the raw files from data.gharchive.org.
  - BigQuery `githubarchive.month.*` is implemented as an alternative backend with a dry-run byte printout and a 200 GB guard. It needs a GCP key, so it isn't used now.
  - Pre-2015 (Timeline API era): check event-type and field coverage per year, map it explicitly, and test it.
- **GitHub GraphQL:** batched `organization(login){websiteUrl createdAt name}`, 100 aliases per query. Same field as GET /orgs/{login}.blog, but ~100× fewer calls. One token, no rotation.
- **GitHub REST** `/organizations?since=<id>`: an outcome-blind sample of existing orgs, using random ID offsets per creation period.
- **CORDIS bulk CSVs:** organization.csv and project.csv for FP7, H2020 and Horizon Europe.
- **Labels:**
  - YC: the yc-oss JSON; date = acceptance proxy (batch start − 2.5 months); batch start as a sensitivity run.
  - HTGF: portfolio parser. Only name, website and announcement date are extracted, deterministically. HTML structure is inspected through tag/class statistics only, never page text.
- **Later, one at a time after the first pass:**
  - patents: BigQuery needs a GCP key; the free fallback is PatentsView bulk (US)
  - trademarks: EUIPO / USPTO bulk
  - Companies House: key via the secure form
  - Product Hunt: token via the secure form

## Entities, pool, labels
- **Entity key:** registrable domain (+ UK company number later).
- **Login rule (the same for every entity):**
  - The org counts only if its websiteUrl's registrable domain == the entity's domain AND its login ∈ candidates(domain stem, GitHub display name).
  - Candidates: stem plus fixed affixes such as hq, app, io, labs, inc, dev, ai, team, get-, use-, try-, join-.
  - No fuzzy matching.
  - For positives, YC/HTGF names only generate extra lookups; acceptance uses the same rule.
- **Pool P (outcome-blind):**
  - (a) Sampled GitHub orgs that satisfy the login rule with a non-platform domain, have org createdAt in [as_of−5y, as_of), and have ≥1 GH Archive event before as_of.
  - (b) CORDIS private companies (activityType PRC) with organizationURL; entry = first signature date.
- **B sample:** seeded hash per channel × entry year, drawn BEFORE any label lookup. Inclusion probability stored.
- **Controls:** B minus any entity matching a label source. Ambiguous entities are dropped from both groups.
- **Positives:** YC/HTGF companies that satisfy the same P criteria at as_of (GitHub org via the login rule, or a CORDIS domain match).
- **Later sources:** patents and trademarks match by exact normalized name + country; UK SH01 is a sensitivity analysis only.
- **Europe subset** (EU/UK/CH/NO ccTLD, or registry/CORDIS country) is a headline result alongside "all".
- **Known caveats (go in the report):**
  - websiteUrl is the current value.
  - Deleted orgs are missing for both groups, but survival correlates with funding.
  - Channel (b) entities always have grant = 1 at visibility, so the grant AUC partly measures the channel. The report includes a channel audit.
  - Label coverage outside the US and Germany is thin, so European controls are more contaminated.

## Backtest
- **as_of and labels:** as_of = Jan 1. Label = first funding in [as_of, as_of+24m). Already-funded entities are excluded.
- **kNN references:** funded in [as_of−5y, as_of) with funding < as_of; never the candidate itself; profile at funding − 90 days, scaled against B at that date.
- **Methods, in order:**
  1. random
  2. single-metric baselines
  3. L2 logistic regression (balanced)
  4. kNN Euclidean, k = 1, 3, 5
  5. LR-weighted kNN (coefficients clipped at 0)
- **Training embargo:** train on as_of ≤ Y−2. Coefficients are re-learned in every fold.
- **Measures:**
  - AUC with a 95% bootstrap CI, POOLED across selection years (entity-clustered, label-stratified)
  - precision@50 vs random
  - coefficient table with bootstrap stability
- **Thresholds:** ≥100 covered positives is the target; fewer than 30 → AUC reported as exploratory only.
- **Year split** comes from the gate's coverage counts. Locked years stay untouched.

## Execution
0. **Scaffold:** `radar-backtest/` tree, venv + requirements, `scripts/lib` (config, http, domains, schema, meta, scaling, metrics), Makefile, pytest skeleton. Commit and push to branch.
1. **Collectors:** yc + htgf labels, CORDIS bulk, GitHub org lookup (GraphQL) + org sampling (REST), GH Archive (ClickHouse backend + BigQuery stub), with fixture tests.
2. **GATE:** positives matched to a GitHub org or CORDIS per year, and coverage of each metric among positives and B controls. Report before full collection.
3. Full first pass: events, then snapshots, then the backtest in method order, then the report. All tests green. Review by an adversarial agent.
4. Add patents, trademarks, Companies House and Product Hunt one at a time. Keys are requested when each is reached; BigQuery dry-run and ask above 200 GB.

## Verification
- `make test` (leakage, labels, embargo, references, scaling without labels, determinism, shuffled AUC ≈ 0.5, login rule, mirror fidelity, pre-2015 mapping)
- `make validate` (schema, duplicates, dates, coverage per source and year)
- `make gate`
- `make backtest report`, run twice → identical hashes
