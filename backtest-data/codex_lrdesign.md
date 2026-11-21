Recommend a **strongly regularised logistic benchmark anchored to the frozen prior**, presented as an experimental ranking tool.

The current CSV contains **205 rows, 98 columns, and 13 positives/77 negatives**. Year-band positive counts are **1/25, 6/45, and 6/20**. Removing `needs_review` leaves only **one positive**; `y_label_confident == 1` selects no rows. Earlier reviews’ 14-positive results are not current validation results.

**Model.** Learn adjustments for six signals:

| Feature | Observed among 90 labelled rows |
|---|---:|
| `f_pricing_page` | 78 |
| `f_impressum` | 78 |
| `f_customer_logos` | 78 |
| `f_jobs_page` | 78 |
| `age_months_le_18`, derived from `f_age_months` | 83 |
| `m_grant_score` | 90 |

Keep other prior coefficients frozen: GitHub/founder coverage is sparse, patents have no observed positives, and `f_gmbh_ig` is constant.

Use
\[
\operatorname{logit}(p)=a+\gamma_{\text{year}}+4S_{\text{prior}}+X\delta+M\eta.
\]
Here, \(S_{\text{prior}}\) reproduces the existing weights and missing-as-zero policy exactly; legal-entity text becomes an evidence-present indicator. The factor four is an explicitly chosen scale assumption, **not learned evidence**. Preserve the prior file unchanged.

Minimise summed logistic loss plus \(8\|\delta\|^2+8\|\eta\|^2+2\|\gamma\|^2\), leaving the intercept unpenalised. This gives strongly constrained coefficient adjustments. Use SciPy optimisation with sklearn validation utilities; ordinary sklearn L2 alone shrinks toward zero, not the prior.

Encode unknowns with missingness indicators, sharing one indicator where website missingness patterns coincide. Unknown age stays unknown. Use two year-band indicators; no interactions, class balancing, or feature selection. Parse mixed date formats and verify source timestamps before modelling. Treat unverifiable evidence as missing. Exclude `info_*`, `cb_*`, and label-derived predictors.

**Validation.** Use 20 seeded repetitions of stratified five-fold CV, balancing years where feasible. Fit every learned transformation within training folds. Compare against unchanged prior rankings, a prior-offset model with intercept/year controls but no learned adjustments, training-fold prevalence, and year-only logistic regression.

Report AUC, Brier, log loss, coverage, counts and Wilson funding-rate intervals. Report within-band discrimination and pooled within-band concordance; the oldest band has only one positive. Use 1,000 company-level bootstrap replicates rerunning validation for approximate 95% metric and paired-difference intervals; keep duplicate company copies together. Bootstrap fitted coefficients for intervals and sign consistency of both final coefficients and learned adjustments.

Repeat with `y_label_incl_dead`; report the review-excluded analysis as extremely underpowered and confident-only analysis as unavailable. These remain exploratory estimates: earlier label-informed reviews prevent treating this as untouched confirmation.

**Benchmark mapping.** Apply every specified eligibility gate first: fail excludes; unknown requires review. Website archive age is **not company age** and cannot establish the ≤3-year gate.

Split additive signal contributions into Quality—customers, grants, OSS, external contributions, patents, Product Hunt—and Opportunity—the remaining prior signals. Assign each learned adjustment to its corresponding component. Exclude intercept, year and missingness effects from displayed merit scores. Convert each component and their sum into tie-aware percentiles against a frozen, documented 205-company reference. Total is the percentile of the sum, not the average of component percentiles. Display coverage alongside all scores; these are neither probabilities nor independently validated constructs.

Founder and current employee signals require separately documented, frozen expert priors: the supplied file contains none. Show these as provisional supplementary scores, outside the historically validated benchmark.

**Deliverable specification; no files created:**

- `scripts/train_benchmark.py`: `validate_inputs`, `build_features`, `fit_prior_lr`, `cross_validate`, `bootstrap_report`, `export_model`.
- `models/benchmark_weights.json`: schema, transforms, coefficients, penalties, prior hash, reference distributions and provenance.
- `reports/benchmark_report.md`: validation numbers, baselines, coefficient intervals/stability and sensitivities.
- `scripts/score_startups.py`: eligibility, Quality/Opportunity/total, coverage, model version and top signed signal contributions.

Pitch: “A prior-guided funding-association benchmark with 13 positive examples.” No calibrated probabilities, investment-return prediction, causation or proven discovery advantage. **Confidence: high in implementation feasibility; low in generalisation and learned weights.**