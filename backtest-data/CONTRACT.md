# Shared contract for all scripts in backtest-data/ (read this first)

Goal: a backtest dataset of German startups. For each company we know WHEN we first saw it (asof_date, from a dated cohort list) and we label whether it raised its FIRST INSTITUTIONAL funding round within 18 months after asof_date. Features must use only evidence that existed BEFORE asof_date.

Rules for every script:
- Python 3.13, standard library only (urllib, json, csv, re, html, concurrent.futures). No pip installs.
- Polite scraping: User-Agent 'Mozilla/5.0 (research script)', >= 1 s between requests to the same host, retries with backoff, timeouts 20-30 s.
- Cache every HTTP response under raw/cache/<script_name>/ (filename = sha1 of URL) so reruns are fast and resumable.
- UTF-8 CSV, comma-separated, header row. Unknown = empty cell (never 0 for unknown).
- Only write the files assigned to you. Never edit other files.
- Print a short summary at the end (row counts, % missing per column).

## Cohort CSV schema (raw/cohort_<source>.csv)
name,website,description,source,cohort,asof_date
- website: the company's own URL if available (not a social profile), else empty
- source: short snake_case id of the list, e.g. hightechxl_portfolio
- cohort: human-readable batch/year/programme label
- asof_date: ISO date YYYY-MM-DD = when the company appeared in that list (batch/cohort/award date; if only a year is known use YYYY-07-01)
- Only include cohorts from 2019 to 2025. Germany-based only if the source is international (keep a country column if available).
- Extra columns are allowed AFTER these six.

Example rows exist in raw/cohort_utum.csv.
