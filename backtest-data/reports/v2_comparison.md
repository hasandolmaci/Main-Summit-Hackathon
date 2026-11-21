# V2 in-sample comparison

These are **in-sample** descriptive results, not held-out validation. Existing weights and peer references use this cohort; the new metric weights are **research priors, not fitted**. Label auditing changes the evaluation population, so changes cannot be attributed solely to better features.

AUC uses Mann-Whitney pair comparisons with half credit for ties. Only labels exactly 0 or 1 count. Each score AUC uses labelled rows where that score is present; INELIGIBLE rows have blank merit scores and are excluded from those AUCs. Coverage AUC includes every labelled row with coverage. No blank score is imputed.

| Measure | V1 | V2 |
|---|---:|---:|
| Labelled / positives | 90 / 13 | 89 / 12 |
| AUC: quality_0_100 | 0.6303 (n=85, positives=13) | 0.7095 (n=84, positives=12) |
| AUC: opportunity_0_100 | 0.5668 (n=85, positives=13) | 0.5503 (n=84, positives=12) |
| AUC: quality_observed_0_100 | N/A (n=0, positives=0) | 0.6088 (n=84, positives=12) |
| AUC: coverage_collectable_pct | 0.6653 (n=90, positives=13) | 0.7760 (n=89, positives=12) |
| Coverage mean / median (labelled) | 40.83% / 41.07% | 43.49% / 44.07% |
| Coverage mean / median (all 205) | 38.48% / 40.81% | 41.26% / 42.12% |
| Ranked (all rows; coverage >=70%, non-INELIGIBLE) | 3 | 4 |
| Ranked (labelled only) | 3 | 2 |

V1 has no stored quality_observed_0_100 column; its AUC is unavailable rather than reconstructed under a different model.

## Funded rate by quality tercile

Terciles are equal-count groups of labelled rows with a quality score, ascending by quality; stable input order breaks boundary ties. Funded means y_label=1. Tied scores can span groups.

| Version | Tercile | Labelled | Positives | Funded rate | Quality range |
|---|---|---:|---:|---:|---:|
| V1 | Low | 28 | 4 | 14.29% | 6.67–25.00 |
| V1 | Middle | 28 | 1 | 3.57% | 25.00–65.79 |
| V1 | High | 29 | 8 | 27.59% | 65.79–99.12 |
| V2 | Low | 28 | 2 | 7.14% | 3.33–36.90 |
| V2 | Middle | 28 | 2 | 7.14% | 36.90–66.67 |
| V2 | High | 28 | 8 | 28.57% | 67.54–99.12 |

## Top 15 v2 backtest calls

Official calls come first, using the unchanged call policy: descending quality decile, then opportunity. Remaining places are provisional research candidates in the same score order; they do not meet the coverage rule and have no official priority. Labels are shown for evaluation and never used for prioritization.

| List order | Official priority | Ranked | name | status | quality_0_100 | quality_observed_0_100 | opportunity_0_100 | coverage_collectable_pct | y_label |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | True | Q.ANT | ELIGIBLE_REVIEW | 98.81 | 96.43 | 98.81 | 76.50 |  |
| 2 | 2 | True | ARQUE Systems | ELIGIBLE | 99.12 | 76.32 | 82.46 | 76.11 | 0 |
| 3 | 3 | True | Breathment | ELIGIBLE_REVIEW | 95.83 | 87.50 | 79.17 | 71.26 | 0 |
| 4 | 4 | True | Emidat | ELIGIBLE_REVIEW | 96.88 | 96.88 | 78.12 | 73.85 |  |
| 5 |  | False | Valuemize | ELIGIBLE | 93.86 | 79.82 | 93.86 | 65.17 | 1 |
| 6 |  | False | zentur.io | ELIGIBLE_REVIEW | 96.43 | 84.52 | 91.67 | 69.25 | 1 |
| 7 |  | False | Divizend | ELIGIBLE_REVIEW | 90.62 | 71.88 | 90.62 | 67.41 |  |
| 8 |  | False | PROSION | ELIGIBLE | 96.67 | 96.67 | 90.00 | 66.99 |  |
| 9 |  | False | GEMESYS | ELIGIBLE | 97.37 | 95.61 | 88.60 | 67.05 | 1 |
| 10 |  | False | retoflow | ELIGIBLE | 92.11 | 85.09 | 86.84 | 68.86 | 0 |
| 11 |  | False | Olive Robotics | ELIGIBLE_REVIEW | 95.61 | 92.11 | 85.09 | 65.14 |  |
| 12 |  | False | MotorSkins | ELIGIBLE_REVIEW | 91.67 | 98.81 | 83.33 | 48.60 | 0 |
| 13 |  | False | STROMDAO | ELIGIBLE_REVIEW | 96.43 | 82.14 | 82.14 | 65.01 | 0 |
| 14 |  | False | IO-DYNAMICS | ELIGIBLE | 95.83 | 70.83 | 75.00 | 69.25 | 1 |
| 15 |  | False | AICU | ELIGIBLE_REVIEW | 90.00 | 90.00 | 53.33 | 40.92 |  |

Only 4 companies meet the ranking rule; the other 11 listed companies require further evidence before calling.

## Live leads ranked by v2

0 of 20 leads meet the ranking rule. Live scoring uses the frozen backtest v2 reference, without refitting and without --asof-view. Sector and German-source patches match score_live.py; new live intelligence is contextual and does not change y_ columns or eligibility logic.

The table also orders non-INELIGIBLE provisional live leads by the same quality-decile/opportunity policy. Provisional rows retain Ranked=False and blank official priority.

| List order | Official priority | Ranked | name | status | quality_0_100 | quality_observed_0_100 | opportunity_0_100 | coverage_collectable_pct |
|---|---|---|---|---|---|---|---|---|
| 1 |  | False | Synagen AI | ELIGIBLE_REVIEW | 91.18 | 82.35 | 67.65 | 58.21 |
| 2 |  | False | AI Data Defense | ELIGIBLE_REVIEW | 94.44 | 91.67 | 25.00 | 47.65 |
| 3 |  | False | BatteryIncluded | ELIGIBLE_REVIEW | 86.11 | 77.78 | 80.56 | 44.42 |
| 4 |  | False | Tabularis.AI | ELIGIBLE_REVIEW | 86.11 | 52.78 | 25.00 | 48.30 |
| 5 |  | False | Zen AI | ELIGIBLE_REVIEW | 71.43 | 64.29 | 28.57 | 41.80 |
| 6 |  | False | Narratic AI | ELIGIBLE_REVIEW | 64.29 | 42.86 | 78.57 | 41.95 |
| 7 |  | False | mitte.ai | ELIGIBLE_REVIEW | 63.89 | 36.11 | 72.22 | 43.17 |
| 8 |  | False | Intra AI | ELIGIBLE_REVIEW | 63.89 | 36.11 | 58.33 | 44.87 |
| 9 |  | False | Rhesis AI | ELIGIBLE_REVIEW | 63.89 | 36.11 | 25.00 | 46.79 |
| 10 |  | False | AI Recruiter | ELIGIBLE_REVIEW | 42.86 | 42.86 | 28.57 | 43.11 |
| 11 |  | False | turnus.ai | ELIGIBLE_REVIEW | 36.11 | 44.44 | 33.33 | 25.90 |
| 12 |  | False | ImmoFynn AI | ELIGIBLE_REVIEW | 35.71 | 42.86 | 28.57 | 17.04 |
| 13 |  | False | VPATH AI | ELIGIBLE_REVIEW | 11.11 | 11.11 | 62.50 | 39.88 |
| 14 |  | False | Airgonomics.ai | ELIGIBLE_REVIEW | 11.11 | 13.89 | 56.94 | 32.83 |
| 15 |  | False | Practible AI | ELIGIBLE_REVIEW | 3.12 | 3.12 | 37.50 | 29.52 |
| 16 |  | False | AI-VORTEX | ELIGIBLE_REVIEW | 3.12 | 3.12 | 37.50 | 29.52 |
| 17 |  | False | AI SLEIPNIR | ELIGIBLE_REVIEW | 4.17 | 4.17 | 25.00 | 34.05 |

### Ineligible live leads

| Name | Status | Quality | Observed quality | Opportunity | Coverage |
|---|---|---:|---:|---:|---:|
| AI VisionGuard | INELIGIBLE |  |  |  | 38.93 |
| POMA AI | INELIGIBLE |  |  |  | 53.68 |
| phablo.ai | INELIGIBLE |  |  |  | 44.07 |

## Evidence and limitations

Label audit: 1 excluded labels; 5 UNSURE flags (their existing labels remain per instruction).
Requested current-founder fallback: 155 backtest name rows and 167 count rows. These are current/undated proxies, not historical proof; v2 is therefore not fully point-in-time clean. Counts can be partial founder/director/CEO observations; zero checked names does not establish a zero-person founder team. Team-size scoring treats counts below one as unknown.
Only medium/high-confidence background rows are used. Post-as-of fields are ignored, and positive historical background facts require their own evidence date strictly before cutoff. Year/month-only dates use the last day of that period. Unverified/undated facts remain separate. Star-academic=0 follows the requested researched-company rule and means no certified star found, not exhaustive proof of absence. PhD stays blank when no explicit positive/negative evidence exists.
Observed quality uses the same numerator as quality, divided by absolute weights of non-missing quality metrics only. Its separate empirical distribution is stored at peer-group, sector, and global levels for exactly the same non-INELIGIBLE reference rows. A row with no observed quality metrics has raw zero, matching the existing reference missing-score convention. Percentile peer fallbacks and eligibility blanking are unchanged.
All supplied local sources loaded successfully; no web scraping was needed. Name features were not read or used (no variance). Patent input is sparse: DPMA name-search zeros are scoped checks, not worldwide absence. Its notes report Google Patents 503, Espacenet/OPS 403, and Lens 403 circuit breakers after three failures. Trademark data are observed DPMA DE/EM/IR matches, sometimes lower bounds or incomplete searches; current-owner matches without historical applicant verification are not promoted. Founder-background notes record OpenAlex checks stopped after source failures for some rows. No new external source availability was tested.

Evidence URLs are preserved from the supplied research. Some founder fallbacks cite only the local companies_all.csv known-founder list; where live intelligence lacks an external URL, provenance points to the supplied v2_live_intel.csv record. These are local input citations, not independently verified public evidence. No external URLs or dates were guessed.

Validation passed: original schema/order and company identity checks; original values unchanged outside authorized columns; live y_ fields preserved; minimal scoring-map changes; v1-equivalent legacy scores when the v1 map is used; strict background date checks; identical observed/reference member counts; team-size transform; tie-aware AUC; independently recomputed observed-quality denominators.

## Fill counts

Filled means nonblank, including checked zeros; changed compares values with the original input. Every new or potentially changed column is listed.

| Column | Backtest filled / 205 | Backtest changed | Live filled / 20 | Live changed |
|---|---:|---:|---:|---:|
| cid | 205 | 205 | 20 | 20 |
| m_founder_count | 190 | 190 | 20 | 20 |
| m_founder_phd | 15 | 15 | 5 | 5 |
| m_star_academic | 46 | 46 | 16 | 16 |
| m_trademark_before_asof | 78 | 78 | 14 | 14 |
| v2_founders_from_now | 155 | 155 | 19 | 19 |
| v2_founder_count_from_now | 167 | 167 | 19 | 19 |
| y_label_v2_unsure | 5 | 5 | 0 | 0 |
| live_funding_status | 0 | 0 | 20 | 20 |
| live_red_flags | 0 | 0 | 18 | 18 |
| live_customers | 0 | 0 | 8 | 8 |
| v2_evidence | 73 | 73 | 13 | 13 |
| v2_undated_facts | 172 | 172 | 20 | 20 |
| v2_post_asof_facts | 5 | 5 | 0 | 0 |
| s_founder_names | 175 | 169 | 20 | 9 |
| m_founder_technical | 35 | 27 | 11 | 5 |
| m_founder_industry_exp | 11 | 3 | 2 | 1 |
| m_serial_score | 9 | 2 | 4 | 3 |
| m_patent_verified_before_asof | 18 | 14 | 0 | 0 |
| y_label | 89 | 1 | 0 | 0 |
| y_label_basis | 205 | 1 | 0 | 0 |
| needs_review | 205 | 0 | 0 | 0 |

Rows written: backtest_v2.csv 205; live_leads_v2.csv 20; scored_backtest_v2.csv 205; scored_live_v2.csv 20.
Sources worked: founders A/B, founder background, trademarks, patents, label audit, live intel, original data/model files, relevance_v2 sector lookup and live_metrics. Sources failed during this merge: none; upstream source failures are described above.
