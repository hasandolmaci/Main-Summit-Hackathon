**1. Verdict: yes-with-caveats.** Pitch it as an evidence-backed analyst triage prototype. **Do not pitch the current ranking as validated.** Meaningful improvements remain within 10–15 hours, principally eligibility, evidence quality, and demonstrating useful live leads. More model fitting is unlikely to help. I inspected the code and recomputed coverage and label counts without modifying files.

**2. Top weaknesses, in priority order**

- **The funding target is insufficiently verified.** In [backtest.csv](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/backtest.csv>), **12/13 positives require review**, and no rows have `y_label_confident=1`. Audit notes repeatedly question whether funding was the *first institutional* round. The sample also cannot establish discovery advantage over databases.
- **The operational pipeline produces almost no shortlist.** Only 3/205 historical companies qualify for ranking; all 27 live leads have zero coverage. Founder-background inputs cover only eight companies. Actual observation counts show six Quality and **three** Opportunity metrics entirely empty, including hackathon-to-company.
- **Eligibility is materially weaker than advertised.** [eligibility.py](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/scripts/eligibility.py>) passes the sector gate for **196/205**, rather than leaving it mostly unknown. MEINDOC passes. All 62 `ELIGIBLE` companies still have four unknown gates. Undated funding can pass; cohort membership establishes German eligibility. Q.ANT’s archived description explicitly identifies a wholly owned TRUMPF subsidiary—an independence failure missed by the scorer.
- **Validation does not validate the displayed ranking.** [benchmark_report.md](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/reports/benchmark_report.md>) evaluates logistic predictions, while Quality/Opportunity use different transformations, weights and percentiles. CV tuning and reporting share folds; displayed terciles are in-sample. Learned adjustments add essentially nothing: paired AUC difference CI **−.030 to +.036**.
- **Weighting and historical comparability are fragile.** In [scoring_map.json](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/models/scoring_map.json>), analytics tooling weighs 1.0 versus customer logos at .248; founder evidence is reused across metrics. Pricing coefficients are transferred to different constructs. Peer sectors come from explicitly **current** website research in `classify_relevance_v2.py`, compromising historical eligibility and percentile claims.

**3. Improvements ranked by expected value per hour**

**Do before pitch — approximately 13 hours total:**

| Rank | Action and files | Hours | Expected effect |
|---|---|---:|---|
| 1 | Correct claims and reconcile stale report sections; `reports/benchmark_report.md`, `DATA_README.md`. Clearly distinguish benchmark from displayed scores. | 0.5 | Very high credibility |
| 2 | Repair eligibility semantics; `scripts/eligibility.py`, `models/scoring_map.json`. Require dated geography/ownership/funding evidence; unresolved critical gates mean review. Fix misleading reasons column. | 1.5–2 | Fewer embarrassing false recommendations |
| 3 | Audit all 13 positives and selected top-ranked negatives using existing evidence; `raw/label_audit.csv`, `scripts/assemble.py`. Verify round order, dates and investor type. | 2–3 | Protects the central evaluation claim |
| 4 | Enrich 5–10 live leads uniformly using company sites, founder evidence and existing GitHub collectors; `discovery_leads.csv`, `scripts/m_founders.py`, `scripts/m_github.py`. Record URLs/dates and unresolved checks. | 3–4 | Converts infrastructure into a demonstrable sourcing workflow |
| 5 | Establish a frozen-prior baseline; remove duplicated evidence from any revised score. Evaluate the exact proposed ranking with year/source breakdowns, precision@k and uncertainty; `score_startups.py`, `peer_scoring.py`, `train_benchmark.py`. | 3 | Tests whether complexity earns its place |

**Later:** broader independently labelled cohorts, prospective discovery tracking, founder enrichment at scale, and validated timing signals.

**4. What NOT to do**

Avoid larger models, embeddings, mass scraping, tuning to anecdotes, or lowering coverage merely to populate rankings. Do not count retoflow’s later round as an 18-month success, showcase Q.ANT as eligible, or optimize around non-German Synthesia.

**5. Honest pitch**

“We prioritize public startup evidence for analyst review. A small retrospective benchmark suggests signal, but learned adjustments add no demonstrated benefit; eligibility and missing evidence remain explicit review steps.”

**6. Confidence:** High in the implementation findings; moderate in the improvement ordering; low in predictive generalization.