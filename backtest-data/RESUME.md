# Paused jobs (stopped 2026-11-20 at the user's request). Restart all on "start".

All HTTP responses are cached under raw/cache/, so restarted jobs skip work already done.

| # | Job | How to restart | Output |
|---|---|---|---|
| **0 (FIRST, top priority)** | **GitHub open-source check:** does the company have a GitHub org/user with public repos, repos created before the as-of date, commit weeks in the 8 weeks before the as-of date, stars. Needs GITHUB_TOKEN (user sets it in PowerShell; without it 60 req/h is far too slow) | `python -X utf8 scripts/features.py raw/cohort_short.csv --no-crtsh --workers 6` (GitHub on), or a dedicated GitHub-only pass if quicker | f_gh_* columns in raw/features_cohort_short.csv |
| 1 | Point-in-time features (Wayback + HN) on the short cohort, was at ~170/220 | `python -X utf8 scripts/features.py raw/cohort_short.csv --no-crtsh --no-github --workers 8` | raw/features_cohort_short.csv, raw/evidence_cohort_short.csv |
| 2 | Sol E1: free enrichment (HTGF portfolio, Devpost, news mentions, patents) | `prompts/run_E1.ps1` (Codex GPT-6.1-Sol; reuses scripts/enrich_free.py if it exists) | raw/enrich_free.csv |
| 3 | Sol E2: Wayback Impressum / team / jobs / analytics mining | `prompts/run_E2.ps1` (Codex GPT-6.1-Sol; reuses scripts/enrich_snapshots.py if it exists) | raw/enrich_snapshot.csv |
| 4 | Merge (run after 1–3) | `python -X utf8 scripts/assemble.py` | backtest.csv, evidence.csv, discovery_leads.csv |
| 5 | Optional: crt.sh rerun (certificates) | `python -X utf8 scripts/features.py raw/cohort_short.csv --no-github --workers 4` | refreshes features |
| 6 | Optional: LinkedIn founders via Apify for the ~93 labelled companies (needs founder names from job 3; about $0.75–1.10, the Apify budget left is about €2) | Apify `harvestapi/linkedin-profile-search-by-name` | raw/linkedin_founders_backtest.csv |

Already finished and kept: cohorts (raw/cohort_*.csv), labels (raw/labels_cohort_short.csv), Crunchbase (raw/crunchbase_rounds_*.csv), LinkedIn finds (raw/linkedin_*.csv), relevance (raw/relevance_v2.csv), backtest.csv v1, discovery_leads.csv.
Apify spent so far: about $1.00 (~€0.92).
