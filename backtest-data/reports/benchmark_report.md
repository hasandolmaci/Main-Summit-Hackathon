# Exploratory prior-anchored logistic benchmark

Generated 2026-11-20T22:53:56.303002+00:00. Reference: all 205 backtest rows. Main: 90 labelled, 13 positives, 77 negatives; 115 excluded blank labels.

## Counts by as-of year band

| Year band | All rows | Labelled | Positive | Negative | Rate (Wilson 95% CI) |
|---|---:|---:|---:|---:|---:|
| 2019-21 | 74 | 25 | 1 | 24 | 4.0% (0.7%, 19.5%) |
| 2022-23 | 88 | 45 | 6 | 39 | 13.3% (6.3%, 26.2%) |
| 2024-25 | 43 | 20 | 6 | 14 | 30.0% (14.5%, 51.9%) |

## Repeated cross-validation

| Model | k | lambda | AUC mean +/- SD | Brier mean +/- SD | Log loss mean +/- SD |
|---|---:|---:|---:|---:|---:|
| constant | 0 | 0 | 0.4461 +/- 0.0000 | 0.1240 +/- 0.0000 | 0.4146 +/- 0.0000 |
| year-only | 0 | 0 | 0.5928 +/- 0.0493 | 0.1215 +/- 0.0029 | 0.4052 +/- 0.0103 |
| prior-offset | 4 | 0 | 0.6811 +/- 0.0275 | 0.1169 +/- 0.0030 | 0.3852 +/- 0.0097 |
| full | 4 | 4 | 0.6820 +/- 0.0267 | 0.1167 +/- 0.0029 | 0.3847 +/- 0.0096 |

Selected full k=4, lambda=4. Prior-offset selects its own k=4 on the same folds. Each of 20 seeded repeats has five stratified folds. Metrics are computed on the complete out-of-fold vector per repeat; SD is across repeats, not across folds. Constant baseline uses training-fold prevalence. Year-only and prior-offset use band penalty 0.5 and an unpenalised intercept. Full uses summed logistic loss plus lambda times squared deltas/missing coefficients plus 0.5 times squared year coefficients.

The constant baseline has pooled OOF AUC below 0.5 because fold-specific training prevalences differ slightly and are inversely related to held-out prevalence. It has no within-fold discrimination; each within-fold AUC is 0.5. Its zero repeat SD follows identical stratified fold class counts.

Grid selection and reporting use the same CV runs (not nested); estimates therefore have tuning optimism. Splits stratify labels, not years: only one positive is in the oldest band. No class balancing, interactions, standardisation or feature selection.

## Paired OOF AUC bootstrap (1000 company resamples)

Predictions are averaged over the 20 out-of-fold predictions per company, then the same companies are resampled for both models. These intervals condition on the selected settings and fixed OOF predictions; they do not rerun CV or account for model-selection uncertainty.

- Full: AUC 0.6913, bootstrap 95% CI [0.5489, 0.8262].
- Prior-offset: AUC 0.6893, bootstrap 95% CI [0.5441, 0.8247].
- Paired full minus prior-offset: 0.0020, 95% CI [-0.0298, 0.0356].

## Final weights and stability

| Feature | Prior | Final weight | Delta | Bootstrap median | 95% interval | Share positive |
|---|---:|---:|---:|---:|---:|---:|
| f_pricing_page | 0.1500 | 0.8060 | 0.2060 | 0.7860 | [0.5248, 1.0726] | 100.0% |
| f_impressum | 0.1000 | 0.4803 | 0.0803 | 0.4908 | [0.2337, 0.7286] | 100.0% |
| has_legal_entity | 0.0500 | 0.2784 | 0.0784 | 0.2863 | [0.0675, 0.5226] | 99.1% |
| f_gmbh_ig | 0.0500 | 0.2000 | 0.0000 | 0.2000 | [0.2000, 0.2000] | 100.0% |
| f_waitlist | 0.0500 | 0.2000 | 0.0000 | 0.2000 | [0.2000, 0.2000] | 100.0% |
| f_customer_logos | 0.1000 | 0.2485 | -0.1515 | 0.2517 | [0.0330, 0.4849] | 98.9% |
| f_jobs_page | 0.0500 | 0.0865 | -0.1135 | 0.0994 | [-0.1125, 0.3319] | 82.4% |
| m_grant_score | 0.1000 | 0.2109 | -0.1891 | 0.2259 | [0.0891, 0.3413] | 99.9% |
| m_gh_has_open_source | 0.1000 | 0.4000 | 0.0000 | 0.4000 | [0.4000, 0.4000] | 100.0% |
| m_gh_external_score | 0.0500 | 0.2000 | 0.0000 | 0.2000 | [0.2000, 0.2000] | 100.0% |
| m_patent_verified_before_asof | 0.0500 | 0.2000 | 0.0000 | 0.2000 | [0.2000, 0.2000] | 100.0% |
| m_ph_score_before_asof | 0.0500 | 0.2000 | 0.0000 | 0.2000 | [0.2000, 0.2000] | 100.0% |
| age_le_18 | 0.1000 | 0.4856 | 0.0856 | 0.4881 | [0.2270, 0.7233] | 100.0% |

1000 ordinary company bootstrap refits at the selected fixed k/lambda. Intervals are percentile intervals conditional on settings. Frozen features vary only through the fixed k, so their intervals are degenerate. Learned means a prior adjustment was estimated, even when its delta is near zero.

## Sensitivity: y_label_incl_dead

116 labelled; 13 positives; 103 negatives. Full grid independently retuned.

| Model | k | lambda | AUC mean +/- SD | Brier mean +/- SD | Log loss mean +/- SD |
|---|---:|---:|---:|---:|---:|
| constant | 0 | 0 | 0.4477 +/- 0.0000 | 0.0997 +/- 0.0000 | 0.3520 +/- 0.0000 |
| year-only | 0 | 0 | 0.5735 +/- 0.0519 | 0.0998 +/- 0.0016 | 0.3510 +/- 0.0069 |
| prior-offset | 4 | 0 | 0.6842 +/- 0.0332 | 0.0946 +/- 0.0016 | 0.3271 +/- 0.0070 |
| full | 4 | 16 | 0.6855 +/- 0.0314 | 0.0945 +/- 0.0016 | 0.3267 +/- 0.0070 |

Review-excluded: 67 rows, 1 positives; too few for five-fold CV. Confident-only: 0 rows; unavailable.

## Encoding and display

The prior is reproduced using its original numeric weights, age <=18 transform, legal-evidence-present indicator, and missing-as-zero convention. Alias features are merged so legal entity and age are counted once. Seven deltas are fitted. All other signals receive k times their frozen prior coefficient; serial/founder signals have no supplied prior and are excluded.

snapshot_missing is 1 when any model website signal is blank (including legal entity); age has a separate missing indicator. The source f_snapshot_missing flag is not used as a predictor. Blank legal evidence is unknown, not confirmed absence; this makes snapshot_missing broader than the archive-missing flag. Coverage counts observed unique merit features, excluding year and missingness indicators.

Quality includes customers, grants, GitHub, external contributions, patents and Product Hunt. Opportunity includes pricing, Impressum, legal entity, GmbH i.G., waitlist, jobs and age <=18. Contributions are final_weight * value. Intercept, bands and missingness effects are excluded from displayed merit. New startups use the latest year band internally. Each raw sum is converted independently using 100*(# reference below + 0.5*# reference equal)/205. Reference includes unlabelled and ineligible rows and is frozen in the JSON.

Eligibility uses funding/German/gate fields only for status, never training. Known failures override unknowns. With no gate fields status is ELIGIBLE_UNCHECKED; provided unknowns require REVIEW. Funding status on this historical backtest may describe funding after its as-of date, so scoring status is not historical eligibility and does not filter the frozen reference or training data.

## Caveats

**Only 13 positive examples. This is an exploratory ranking benchmark, not calibrated probabilities or investment advice.** Percentile scores are relative to a small selected cohort and do not establish predictive accuracy, causation, investment returns, discovery advantage, or representativeness for German startups. Earlier label-informed reviews mean this is not untouched confirmation. Quality and Opportunity are proposed constructs, not independently validated measures.

Historical fields are trusted as assembled; source-by-source timestamp verification has not been rerun. info_* and cb_* never enter predictors. Website archive age is not company age and cannot establish a company-age eligibility gate. Sparse patents/GitHub signals and missingness can distort rankings. Review-excluded and confident-only data cannot support meaningful validation. Bootstrap intervals omit tuning and source-label uncertainty. The discovery file has no model inputs: zero coverage and tied percentiles contain no company-specific evidence.

Runtime: 11.4 seconds. Seed: 20261120.

## Scoring system (user format)

This section supersedes the legacy scoring/display and eligibility descriptions above for the new scored CSVs. The learned benchmark itself is unchanged. Outputs: `scored_backtest.csv`, `scored_leads.csv`; frozen peer reference: `models/peer_reference.json`.

Each category is sum(weight * value) / sum(abs(weight)) over every metric in that category, including uncollected metrics in the denominator. Penalties subtract absolute weight. Learned metrics use final benchmark coefficients; prior metrics use the original unscaled weights from `prior_weights.json`; other metrics use their default weights. Coverage uses absolute metric-weight mass pooled across both categories. Collectable coverage includes metrics with mapped columns; full coverage includes every metric. Observed zeros count toward coverage, unknowns do not. Compound metrics count as observed only when their scoring proxy can be evaluated; context alone is insufficient.

Eligibility is evaluated before reference selection. The reference contains 140 non-ineligible backtest rows, including unlabelled and review rows, without a coverage filter. Ineligible rows retain eligibility and coverage but have blank merit scores/signals and no call priority. Percentiles use sector x age-band groups with at least 10 members, then sector with at least 10, then global. Call priority is 1-based, sorting ranked rows by quality percentile decile descending, then opportunity percentile descending; exact ties retain input order. Ranked means status != INELIGIBLE and collectable coverage >=70%.

### Funded rates (y_label)

Only the 90 rows with y_label exactly 0 or 1 enter funded-rate denominators (13 positives). Terciles use the 33 1/3 and 66 2/3 percentile-value quantiles among labelled non-ineligible scored rows, separately for each score. Equal score values stay together (<= lower cut: low; <= upper cut: middle; otherwise high), so group sizes may differ. Ineligible labelled rows have no merit score and are reported separately. These are descriptive in-sample tables, not out-of-fold validation.

#### Quality terciles

| Group | Labelled | Funded | Funded rate |
|---|---:|---:|---:|
| Low tercile | 30 | 4 | 13% |
| Middle tercile | 27 | 1 | 4% |
| High tercile | 28 | 8 | 29% |
| Ineligible (unscored) | 5 | 0 | 0% |

Cut points: 25.0, 65.8.

#### Opportunity terciles

| Group | Labelled | Funded | Funded rate |
|---|---:|---:|---:|
| Low tercile | 31 | 4 | 13% |
| Middle tercile | 28 | 4 | 14% |
| High tercile | 26 | 5 | 19% |
| Ineligible (unscored) | 5 | 0 | 0% |

Cut points: 34.2, 75.0.

#### Ranked (coverage >= 70%)

Only 3 rows are ranked: retoflow (#1, not funded), Breathment (#2, not funded), Emidat (#3, funded).

> Correction (post-P4): the age gate originally failed 54 companies because `f_age_months` (website/domain archive age) exceeded 36 months. Domain age is not company age, so an old domain now gives `unknown` (review) instead of `fail`. Statuses after fix: ELIGIBLE 62, ELIGIBLE_REVIEW 120, INELIGIBLE 23. All 13 positives are now scored. Note the `ineligible_reasons` column lists every gate note, including passing gates; read `status` and the gate results, not that text alone.

### Coverage and eligibility

| Dataset | Rows | Non-ineligible | Ranked | Collectable mean / median / min / max | Full mean / median / min / max |
|---|---:|---:|---:|---|---|
| Backtest | 205 | 182 | 3 | 38.5% / 40.8% / 6.6% / 74.6% | 27.1% / 28.7% / 4.7% / 52.6% |
| Discovery leads | 27 | 27 | 0 | 0.0% / 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% / 0.0% |

Backtest statuses (after age-gate fix): {'ELIGIBLE': 62, 'ELIGIBLE_REVIEW': 120, 'INELIGIBLE': 23}. Reference rows: 182.

### Implementation choices and caveats

- Only 13 positives: small bins, especially the ranked subset, are unstable. Learned weights were estimated on these labels and the peer reference includes these companies; these tables do not establish generalization.
- Scores are peer percentiles, not funding probabilities. The combined user-format system has not been independently validated. Scores from different fallback populations are relative to different peer sets.
- Many metrics are not yet collected, so coverage_full is low. Zero merit from missing evidence is not proof of poor quality. Discovery leads have no mapped scoring inputs: all have zero coverage and none is ranked; their tied percentiles are not company-specific evidence.
- Text rubrics are conservative keyword screens, not completed manual reviews: buyer plus costly-problem evidence, regulated/hardware evidence, explicit university spin-off language, and thesis plus product language. Ambiguous text remains unobserved; matches need manual review and can misread negation, boilerplate, language or context. Historical f_domain is used as a company-site ownership proxy, excluding known hosted-profile domains; ownership still needs verification.
- log1p_capped is fixed at log(1+x)/log(101) for news/HN counts (cap 100), and log(1+days)/log(366) for head-start days (cap 365), bounded at 1. Twelve-month news takes precedence over overlapping total news. Only dates on/before cutoff count for certificate/head-start transforms.
- Patent credit requires verified pre-cutoff patent evidence. Grants use m_grant_score rather than automatically crediting cohort membership. Ordinary GmbH/UG flags do not establish formation-stage evidence; positive raise-soon formation credit requires explicit text. A careers page alone does not prove an open role. Hackathon-to-company conversion remains unknown without dated formation/linkage evidence; an explicit zero project count is observed zero. Text-only pilot offers use default weight 1.0, as specified by the map.
- Existing eligibility.evaluate semantics are used unchanged: as-of mode exempts current portfolio/alive indicators, uses cohort-based German evidence and website age as a venture-age proxy, and leaves activity/authenticity unverified. Its funding and parent-mention rules are simplifications of the prose spec. Review status can still be ranked by the requested status/coverage rule; operational investment eligibility requires checking evidence.

Validation: both requested Python 3.13 scoring commands completed. Focused integration checks covered missingness versus observed zero, penalties, prior versus learned weights, weighted normalization/coverage, future-date rejection, age boundaries, decile/opportunity order, CSV schema, label isolation and reference membership. The earlier test_benchmark.py targets the superseded scorer interface and was not used as validation of this rewrite.
