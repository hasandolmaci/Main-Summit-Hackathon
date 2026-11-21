# backtest.csv: how to use it (for the person running the analysis)

**One row = one company at its as-of date** (the date it first appeared in a cohort list). Rebuild everything with `python -X utf8 scripts/assemble.py`.

## Labels
| Column | Use |
|---|---|
| `y_label` | **Main label.** 1 = first institutional round within 18 months after the as-of date; 0 = none found; empty = excluded (unknown, funded before, Crunchbase round without date, audit exclusion) |
| `y_label_incl_dead` | Sensitivity: same as `y_label`, but dead "unknown" companies without any Crunchbase equity round count as 0 |
| `y_label_confident` | 1 = the LLM audit kept the label with high confidence; 0 = keep but less certain; empty = not audited |
| `y_label_basis`, `y_funding_status` | Why the label is what it is |
| `needs_review` | 1 = check by hand before relying on it |

## Features: what you may use
- **Use:** `f_*` (website before the as-of date: Impressum, legal entity, GmbH i.G., pricing, waitlist, customer logos, jobs page, age), `m_grant_score`, `m_gh_has_open_source`, `m_gh_external_score`, `m_patent_verified_before_asof`, `m_ph_score_before_asof`, `m_serial_score`, `e_news_mentions_before_12m`, `s_*` (old Impressum, analytics, team page).
- **Never use as features:** everything starting with `info_` (today's values or leaky: employee counts, current GitHub stars and momentum, today's cohort descriptions, HTGF portfolio flag) and `cb_*` (label support).
- **Missing ≠ 0.** Empty means "unknown". Treat it as its own category or report coverage.

## Rules for the analysis
1. Use the frozen weights in `prior_weights.json`. **Don't tune them on these labels.**
2. Always split or control by as-of year band (2019–21 / 22–23 / 24–25). The funding rate rises strongly over time.
3. Report counts, rates and intervals, not an AUC headline. There are only ~13 positives.
4. Sensitivities: `y_label` vs `y_label_incl_dead`; all rows vs `needs_review` removed; `y_label_confident == 1` only.

## Don't claim
Predictive accuracy, calibrated probabilities, causation, "grants or GitHub don't matter", representativeness for all German startups, or finding companies before the databases.
