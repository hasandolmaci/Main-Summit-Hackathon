# Startup radar backtest — results (v1, selection years only)

> **EXPLORATORY RESULT** — only 28 funded companies are visible to the radar before their first round (target ≥ 100; below 30 the plan reports AUC as exploratory only). Treat every number below as indicative, with wide confidence intervals.

**Headline:** we flagged **2 of 28** funded startups in our candidate set a median of **6 months** before their first round (in the top 5% of the estimated candidate pool on a 1 January before funding; method: L2 logistic regression, as_of years 2015–2019).

Ranking quality: pooled ROC AUC **0.527 [0.436, 0.619]** (0.5 = random). In the top 5% of the pool the funded share is estimated at **0.0005** vs **0.0004** overall (lift **1.0 [0.0, 2.8]×**).

> Read this as: among companies visible to the radar on 1 January of each year, how well the score ranks the ones that raised a first round (YC / HTGF) in the next 24 months above the ones that did not. Controls are an outcome-blind random sample of GitHub organisations and are *assumed* unfunded.

## 1. Dataset

Candidates per as_of year (1 January). Positives = first funding in the next 24 months; controls = outcome-blind background sample B (assumed unfunded, NOT verified). The weighted base rate re-weights controls by 1/inclusion-probability, i.e. estimates the rate in the full GitHub pool.

|   as_of |   positives |   controls |   europe_pos |   weighted_base_rate |
|--------:|------------:|-----------:|-------------:|---------------------:|
|    2015 |           9 |        194 |            0 |               0.0007 |
|    2016 |           7 |        231 |            0 |               0.0004 |
|    2017 |           8 |        221 |            0 |               0.0004 |
|    2018 |           6 |        200 |            0 |               0.0002 |
|    2019 |           8 |        149 |            1 |               0.0003 |

Distinct funded companies in the evaluated cohorts: **28** (label sources: yc 24, htgf 4). Sector mix of positives (sector is known only for labelled companies, so it is descriptive only):

| sector         |   positives |
|:---------------|------------:|
| B2B            |          11 |
| Education      |           5 |
| Consumer       |           3 |
| Digital Tech   |           2 |
| Fintech        |           2 |
| Digital Health |           1 |
| Healthcare     |           1 |
| Government     |           1 |
| unknown        |           1 |
| Unspecified    |           1 |

Metric coverage (share of cohort rows where the source covers the company; missing ≠ 0):

|                              |   positives |   controls |
|:-----------------------------|------------:|-----------:|
| oss_external_engagement      |       1     |      1     |
| github_activity_acceleration |       1     |      1     |
| has_public_rd_grant          |       0.026 |      0.128 |

Per-source event coverage by year: `data/processed/coverage_by_source_year.csv`.

Entity matching: GitHub org = website equals company domain AND login is the domain stem (stem-only, identical for every company); EU grants = website domain, else unique legal name + same country. Precision proxy for the 785 matched labelled companies: the org's login/display name equals the company name in **93.4%** of links (99.7% if one name contains the other). Hand-check samples: `data/processed/match_sample_github.csv`, `data/processed/match_sample_cordis.csv` (**not yet verified by a human**).

## 2. Methods vs baselines

Pooled AUC compares positives and controls only within the same as_of cohort (case-cohort weights); 95% CIs from an entity-clustered, label-stratified bootstrap. **P@20 / P@50 / R@50 are computed inside the case-cohort sample** (all funded companies + a small random sample of controls), so they overstate real-world precision; the **top 5% recall and lift are pool-weighted** (controls weighted by 1/inclusion probability) and estimate what a radar user would see. Lead time = months from the first 1 January on which the company was in the pool's top 5% to its first funding.

**Subset: all**

| method                                |   n_pos | AUC [95% CI]         | P@20 [95% CI]        | P@50 [95% CI]        | R@50 [95% CI]        |   sample base rate | top 5% recall [95% CI]   | top 5% lift [95% CI]   | top 1% recall / lift   | top 10% recall / lift   |   median lead (months) | exploratory   |
|:--------------------------------------|--------:|:---------------------|:---------------------|:---------------------|:---------------------|-------------------:|:-------------------------|:-----------------------|:-----------------------|:------------------------|-----------------------:|:--------------|
| baseline_github_activity_acceleration |      28 | 0.630 [0.542, 0.711] | 0.080 [0.030, 0.140] | 0.068 [0.040, 0.096] | 0.470 [0.291, 0.613] |              0.038 | 0.083 [0.000, 0.222]     | 1.8 [0.0, 5.4]         | 0.00 / 0.0×            | 0.22 / 2.4×             |                   12   | True          |
| baseline_has_public_rd_grant          |      28 | 0.454 [0.422, 0.498] | 0.010 [0.000, 0.040] | 0.004 [0.004, 0.036] | 0.025 [0.050, 0.285] |              0.038 | 0.000 [0.000, 0.050]     | 0.0 [0.0, 1.3]         | 0.00 / 0.0×            | 0.03 / 0.3×             |                  nan   | True          |
| baseline_oss_external_engagement      |      28 | 0.464 [0.353, 0.575] | 0.010 [0.000, 0.030] | 0.028 [0.012, 0.048] | 0.170 [0.073, 0.338] |              0.038 | 0.000 [0.000, 0.033]     | 0.0 [0.0, 0.7]         | 0.00 / 0.0×            | 0.02 / 0.2×             |                  nan   | True          |
| knn_k1                                |      28 | 0.523 [0.411, 0.634] | 0.040 [0.010, 0.080] | 0.036 [0.016, 0.064] | 0.240 [0.085, 0.427] |              0.038 | 0.022 [0.000, 0.100]     | 0.4 [0.0, 2.0]         | 0.00 / 0.0×            | 0.07 / 0.7×             |                    2.9 | True          |
| knn_k3                                |      28 | 0.500 [0.383, 0.620] | 0.040 [0.010, 0.090] | 0.040 [0.024, 0.072] | 0.265 [0.132, 0.446] |              0.038 | 0.025 [0.000, 0.103]     | 0.6 [0.0, 2.3]         | 0.03 / 2.6×            | 0.13 / 1.3×             |                   22   | True          |
| knn_k5                                |      28 | 0.492 [0.377, 0.611] | 0.030 [0.010, 0.080] | 0.040 [0.016, 0.064] | 0.271 [0.103, 0.405] |              0.038 | 0.025 [0.000, 0.100]     | 0.6 [0.0, 2.5]         | 0.03 / 3.2×            | 0.10 / 1.0×             |                   22   | True          |
| logreg                                |      28 | 0.527 [0.436, 0.619] | 0.040 [0.010, 0.070] | 0.048 [0.024, 0.072] | 0.309 [0.172, 0.446] |              0.038 | 0.047 [0.000, 0.127]     | 1.0 [0.0, 2.8]         | 0.03 / 4.7×            | 0.07 / 0.8×             |                    6.4 | True          |
| lr_knn_k1                             |      28 | 0.483 [0.441, 0.520] | 0.000 [0.000, 0.060] | 0.012 [0.012, 0.048] | 0.079 [0.098, 0.367] |              0.038 | 0.000 [0.000, 0.137]     | 0.0 [0.0, 2.7]         | 0.00 / 0.0×            | 0.00 / 0.0×             |                   12.4 | True          |
| lr_knn_k3                             |      28 | 0.476 [0.431, 0.519] | 0.000 [0.000, 0.060] | 0.016 [0.012, 0.052] | 0.104 [0.098, 0.367] |              0.038 | 0.000 [0.000, 0.137]     | 0.0 [0.0, 2.7]         | 0.00 / 0.0×            | 0.00 / 0.0×             |                  nan   | True          |
| lr_knn_k5                             |      28 | 0.478 [0.426, 0.523] | 0.000 [0.000, 0.060] | 0.016 [0.012, 0.052] | 0.104 [0.100, 0.376] |              0.038 | 0.000 [0.000, 0.137]     | 0.0 [0.0, 2.7]         | 0.00 / 0.0×            | 0.00 / 0.0×             |                   14.9 | True          |
| random                                |      28 | 0.428 [0.330, 0.529] | 0.030 [0.000, 0.060] | 0.024 [0.008, 0.048] | 0.148 [0.040, 0.329] |              0.038 | 0.000 [0.000, 0.067]     | 0.0 [0.0, 1.2]         | 0.00 / 0.0×            | 0.08 / 0.8×             |                  nan   | True          |

**Subset: europe**

| method                                |   n_pos |   AUC [95% CI] |   P@20 [95% CI] |   P@50 [95% CI] |   R@50 [95% CI] |   sample base rate |   top 5% recall [95% CI] |   top 5% lift [95% CI] | top 1% recall / lift   | top 10% recall / lift   |   median lead (months) | exploratory   |
|:--------------------------------------|--------:|---------------:|----------------:|----------------:|----------------:|-------------------:|-------------------------:|-----------------------:|:-----------------------|:------------------------|-----------------------:|:--------------|
| baseline_github_activity_acceleration |       1 |          0.868 |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| baseline_has_public_rd_grant          |       1 |          0.463 |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| baseline_oss_external_engagement      |       1 |          0     |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| knn_k1                                |       1 |          0.04  |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| knn_k3                                |       1 |          0.04  |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| knn_k5                                |       1 |          0.04  |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| logreg                                |       1 |          1     |           0.011 |           0.011 |               1 |              0.011 |                        1 |                     25 | 1.00 / 3006.9×         | 1.00 / 10.7×            |                    nan | True          |
| lr_knn_k1                             |       1 |          0.535 |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| lr_knn_k3                             |       1 |          0.171 |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| lr_knn_k5                             |       1 |          0.171 |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| random                                |       1 |          0.068 |           0.011 |           0.011 |               1 |              0.011 |                        0 |                      0 | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |

**Subset: dach**

| method                                |   n_pos |   AUC [95% CI] |   P@20 [95% CI] |   P@50 [95% CI] |   R@50 [95% CI] |   sample base rate |   top 5% recall [95% CI] |   top 5% lift [95% CI] | top 1% recall / lift   | top 10% recall / lift   |   median lead (months) | exploratory   |
|:--------------------------------------|--------:|---------------:|----------------:|----------------:|----------------:|-------------------:|-------------------------:|-----------------------:|:-----------------------|:------------------------|-----------------------:|:--------------|
| baseline_github_activity_acceleration |       1 |          1     |           0.033 |           0.033 |               1 |              0.033 |                        1 |                  796.8 | 1.00 / 796.8×          | 1.00 / 796.8×           |                    nan | True          |
| baseline_has_public_rd_grant          |       1 |          0.5   |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| baseline_oss_external_engagement      |       1 |          0     |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| knn_k1                                |       1 |          0.15  |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| knn_k3                                |       1 |          0.15  |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| knn_k5                                |       1 |          0.15  |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| logreg                                |       1 |          1     |           0.033 |           0.033 |               1 |              0.033 |                        1 |                  796.8 | 1.00 / 796.8×          | 1.00 / 796.8×           |                    nan | True          |
| lr_knn_k1                             |       1 |          0.797 |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| lr_knn_k3                             |       1 |          0.648 |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| lr_knn_k5                             |       1 |          0.15  |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| random                                |       1 |          0     |           0.033 |           0.033 |               1 |              0.033 |                        0 |                    0   | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |

**Subset: rest_not_dach**

| method                                |   n_pos | AUC [95% CI]         | P@20 [95% CI]        | P@50 [95% CI]        | R@50 [95% CI]        |   sample base rate | top 5% recall [95% CI]   | top 5% lift [95% CI]   | top 1% recall / lift   | top 10% recall / lift   |   median lead (months) | exploratory   |
|:--------------------------------------|--------:|:---------------------|:---------------------|:---------------------|:---------------------|-------------------:|:-------------------------|:-----------------------|:-----------------------|:------------------------|-----------------------:|:--------------|
| baseline_github_activity_acceleration |      27 | 0.622 [0.530, 0.706] | 0.080 [0.050, 0.130] | 0.064 [0.044, 0.088] | 0.459 [0.309, 0.613] |              0.038 | 0.087 [0.000, 0.259]     | 1.8 [0.0, 5.4]         | 0.00 / 0.0×            | 0.24 / 2.5×             |                    nan | True          |
| baseline_has_public_rd_grant          |      27 | 0.453 [0.436, 0.471] | 0.000 [0.000, 0.020] | 0.020 [0.004, 0.040] | 0.139 [0.040, 0.283] |              0.038 | 0.000 [0.000, 0.000]     | 0.0 [0.0, 0.0]         | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| baseline_oss_external_engagement      |      27 | 0.475 [0.374, 0.585] | 0.010 [0.000, 0.040] | 0.032 [0.012, 0.056] | 0.206 [0.067, 0.354] |              0.038 | 0.000 [0.000, 0.033]     | 0.0 [0.0, 0.6]         | 0.00 / 0.0×            | 0.02 / 0.2×             |                    nan | True          |
| knn_k1                                |      27 | 0.535 [0.437, 0.642] | 0.040 [0.000, 0.080] | 0.036 [0.016, 0.064] | 0.247 [0.098, 0.450] |              0.038 | 0.022 [0.000, 0.080]     | 0.5 [0.0, 1.8]         | 0.00 / 0.0×            | 0.08 / 0.8×             |                    nan | True          |
| knn_k3                                |      27 | 0.513 [0.397, 0.636] | 0.040 [0.010, 0.080] | 0.044 [0.024, 0.068] | 0.304 [0.144, 0.486] |              0.038 | 0.051 [0.000, 0.129]     | 1.1 [0.0, 3.0]         | 0.03 / 2.9×            | 0.11 / 1.1×             |                    nan | True          |
| knn_k5                                |      27 | 0.504 [0.398, 0.615] | 0.030 [0.000, 0.070] | 0.040 [0.016, 0.060] | 0.285 [0.105, 0.440] |              0.038 | 0.029 [0.000, 0.120]     | 0.6 [0.0, 2.9]         | 0.03 / 3.6×            | 0.11 / 1.1×             |                    nan | True          |
| logreg                                |      27 | 0.525 [0.432, 0.612] | 0.040 [0.010, 0.070] | 0.048 [0.024, 0.068] | 0.320 [0.180, 0.528] |              0.038 | 0.047 [0.000, 0.136]     | 1.0 [0.0, 2.3]         | 0.03 / 4.5×            | 0.08 / 0.8×             |                    nan | True          |
| lr_knn_k1                             |      27 | 0.482 [0.442, 0.523] | 0.020 [0.000, 0.050] | 0.040 [0.008, 0.044] | 0.265 [0.076, 0.314] |              0.038 | 0.000 [0.000, 0.123]     | 0.0 [0.0, 3.2]         | 0.00 / 0.0×            | 0.07 / 0.7×             |                    nan | True          |
| lr_knn_k3                             |      27 | 0.482 [0.439, 0.525] | 0.020 [0.000, 0.050] | 0.044 [0.008, 0.048] | 0.294 [0.077, 0.320] |              0.038 | 0.000 [0.000, 0.129]     | 0.0 [0.0, 3.2]         | 0.00 / 0.0×            | 0.07 / 0.7×             |                    nan | True          |
| lr_knn_k5                             |      27 | 0.486 [0.441, 0.529] | 0.020 [0.000, 0.050] | 0.044 [0.008, 0.048] | 0.294 [0.077, 0.337] |              0.038 | 0.000 [0.000, 0.129]     | 0.0 [0.0, 3.2]         | 0.00 / 0.0×            | 0.07 / 0.7×             |                    nan | True          |
| random                                |      27 | 0.445 [0.348, 0.543] | 0.030 [0.000, 0.060] | 0.028 [0.008, 0.048] | 0.185 [0.040, 0.329] |              0.038 | 0.000 [0.000, 0.045]     | 0.0 [0.0, 1.3]         | 0.00 / 0.0×            | 0.08 / 0.8×             |                    nan | True          |

**Subset: with_github**

| method                                |   n_pos | AUC [95% CI]         | P@20 [95% CI]        | P@50 [95% CI]        | R@50 [95% CI]        |   sample base rate | top 5% recall [95% CI]   | top 5% lift [95% CI]   | top 1% recall / lift   | top 10% recall / lift   |   median lead (months) | exploratory   |
|:--------------------------------------|--------:|:---------------------|:---------------------|:---------------------|:---------------------|-------------------:|:-------------------------|:-----------------------|:-----------------------|:------------------------|-----------------------:|:--------------|
| baseline_github_activity_acceleration |      28 | 0.630 [0.542, 0.711] | 0.080 [0.030, 0.140] | 0.068 [0.040, 0.096] | 0.470 [0.291, 0.613] |              0.038 | 0.083 [0.000, 0.222]     | 1.8 [0.0, 5.4]         | 0.00 / 0.0×            | 0.22 / 2.4×             |                    nan | True          |
| baseline_has_public_rd_grant          |      28 | 0.454 [0.422, 0.498] | 0.010 [0.000, 0.040] | 0.004 [0.004, 0.036] | 0.025 [0.050, 0.285] |              0.038 | 0.000 [0.000, 0.050]     | 0.0 [0.0, 1.3]         | 0.00 / 0.0×            | 0.03 / 0.3×             |                    nan | True          |
| baseline_oss_external_engagement      |      28 | 0.464 [0.353, 0.575] | 0.010 [0.000, 0.030] | 0.028 [0.012, 0.048] | 0.170 [0.073, 0.338] |              0.038 | 0.000 [0.000, 0.033]     | 0.0 [0.0, 0.7]         | 0.00 / 0.0×            | 0.02 / 0.2×             |                    nan | True          |
| knn_k1                                |      28 | 0.523 [0.411, 0.634] | 0.040 [0.010, 0.080] | 0.036 [0.016, 0.064] | 0.240 [0.085, 0.427] |              0.038 | 0.022 [0.000, 0.100]     | 0.4 [0.0, 2.0]         | 0.00 / 0.0×            | 0.07 / 0.7×             |                    nan | True          |
| knn_k3                                |      28 | 0.500 [0.383, 0.620] | 0.040 [0.010, 0.090] | 0.040 [0.024, 0.072] | 0.265 [0.132, 0.446] |              0.038 | 0.025 [0.000, 0.103]     | 0.6 [0.0, 2.3]         | 0.03 / 2.6×            | 0.13 / 1.3×             |                    nan | True          |
| knn_k5                                |      28 | 0.492 [0.377, 0.611] | 0.030 [0.010, 0.080] | 0.040 [0.016, 0.064] | 0.271 [0.103, 0.405] |              0.038 | 0.025 [0.000, 0.100]     | 0.6 [0.0, 2.5]         | 0.03 / 3.2×            | 0.10 / 1.0×             |                    nan | True          |
| logreg                                |      28 | 0.527 [0.436, 0.619] | 0.040 [0.010, 0.070] | 0.048 [0.024, 0.072] | 0.309 [0.172, 0.446] |              0.038 | 0.047 [0.000, 0.127]     | 1.0 [0.0, 2.8]         | 0.03 / 4.7×            | 0.07 / 0.8×             |                    nan | True          |
| lr_knn_k1                             |      28 | 0.483 [0.441, 0.520] | 0.000 [0.000, 0.060] | 0.012 [0.012, 0.048] | 0.079 [0.098, 0.367] |              0.038 | 0.000 [0.000, 0.137]     | 0.0 [0.0, 2.7]         | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| lr_knn_k3                             |      28 | 0.476 [0.431, 0.519] | 0.000 [0.000, 0.060] | 0.016 [0.012, 0.052] | 0.104 [0.098, 0.367] |              0.038 | 0.000 [0.000, 0.137]     | 0.0 [0.0, 2.7]         | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| lr_knn_k5                             |      28 | 0.478 [0.426, 0.523] | 0.000 [0.000, 0.060] | 0.016 [0.012, 0.052] | 0.104 [0.100, 0.376] |              0.038 | 0.000 [0.000, 0.137]     | 0.0 [0.0, 2.7]         | 0.00 / 0.0×            | 0.00 / 0.0×             |                    nan | True          |
| random                                |      28 | 0.428 [0.330, 0.529] | 0.030 [0.000, 0.060] | 0.024 [0.008, 0.048] | 0.148 [0.040, 0.329] |              0.038 | 0.000 [0.000, 0.067]     | 0.0 [0.0, 1.2]         | 0.00 / 0.0×            | 0.08 / 0.8×             |                    nan | True          |

**Subset: without_github** — no funded companies in this subset.

*Without GitHub* is empty by design: v1 candidates come from GitHub organisations only (the CORDIS pool channel was dropped). *By sector* is not reported: sector is known only for labelled companies, not for the controls, so a per-sector AUC would be meaningless. Gradient boosting was skipped: the spec allows it only with several hundred positives per training fold, which v1 does not have.

![AUC by method](figures/auc_by_method.png)

## Why the distance method uses Euclidean distance, not raw cosine

On 0–1 metric vectors, uncentred cosine similarity ignores magnitude. The weakest complete profile in the cohorts [0.15, 0.05, 0.0] and the strongest [0.69, 0.49, 1.0] have uncentred cosine **0.62** (near-identical direction), centred cosine **-0.18**, Euclidean distance **1.22**. Raw cosine would call a dormant company 'similar' to a strong one; we therefore use Euclidean distance (and would only use cosine on centred vectors).

## 3. Logistic regression: coefficients

Re-learned from scratch for every as_of year on training years ≤ Y−2 (labels complete at Y). Features are on a 0–1 scale, so coefficients are comparable.

|   as_of |   n_train |   n_train_pos |   oss_external_engagement |   github_activity_acceleration |   has_public_rd_grant |   has_github |   has_cordis |
|--------:|----------:|--------------:|--------------------------:|-------------------------------:|----------------------:|-------------:|-------------:|
|    2015 |       108 |             3 |                    -1.207 |                         -0.979 |                -0.175 |       -0.002 |       -1.745 |
|    2016 |       247 |             9 |                    -0.827 |                         -1.151 |                -0.166 |        0     |       -2.206 |
|    2017 |       450 |            18 |                    -0.666 |                         -1.438 |                -0.139 |        0.001 |       -2.603 |
|    2018 |       688 |            25 |                    -0.621 |                         -0.109 |                -0.095 |       -0.003 |       -2.913 |
|    2019 |       917 |            33 |                    -0.713 |                          0.124 |                -0.073 |       -0.002 |       -3.089 |

Bootstrap (entity resample) of the last fold's fit: sign stability = share of resamples with the same sign.

| feature                      |   coef_full |   ci_lo |   ci_hi |   sign_stability | expected (papers)                              | sign agrees   |
|:-----------------------------|------------:|--------:|--------:|-----------------:|:-----------------------------------------------|:--------------|
| oss_external_engagement      |      -0.713 |  -2.614 |   1.03  |            0.76  | + (Conti et al. 2025; association)             | no            |
| github_activity_acceleration |       0.124 |  -1.093 |   1.467 |            0.53  | + (weak: no paper measures acceleration)       | yes           |
| has_public_rd_grant          |      -0.073 |  -0.22  |   0     |            0.61  | + (Howell 2017; Santoleri et al. 2024; causal) | no            |
| has_github                   |      -0.002 |  -0.1   |   0.155 |            0.525 | — (missing-data flag)                          |               |
| has_cordis                   |      -3.089 |  -3.359 |  -2.777 |            1     | — (missing-data flag)                          |               |

Variation across folds:

|                              |   mean |   std |    min |    max |
|:-----------------------------|-------:|------:|-------:|-------:|
| oss_external_engagement      | -0.807 | 0.236 | -1.207 | -0.621 |
| github_activity_acceleration | -0.71  | 0.681 | -1.438 |  0.124 |
| has_public_rd_grant          | -0.13  | 0.044 | -0.175 | -0.073 |

## 4. Missing-data audit

If a 'has data' flag alone predicts funding, part of the signal is survivorship or discovery bias (e.g. dead companies lose repos). Values far from 0.5 are a warning.

| flag       |   AUC vs label |
|:-----------|---------------:|
| has_github |          0.5   |
| has_cordis |          0.451 |

## 5. Sensitivity runs

**kNN reference window R=3 (main R=5)**

| method                                |   AUC main |   AUC R3 |   auc_lo |   auc_hi |
|:--------------------------------------|-----------:|---------:|---------:|---------:|
| baseline_github_activity_acceleration |      0.63  |    0.63  |    0.543 |    0.723 |
| baseline_has_public_rd_grant          |      0.454 |    0.454 |    0.425 |    0.496 |
| baseline_oss_external_engagement      |      0.464 |    0.464 |    0.344 |    0.572 |
| knn_k1                                |      0.523 |    0.546 |    0.442 |    0.651 |
| knn_k3                                |      0.5   |    0.512 |    0.39  |    0.626 |
| knn_k5                                |      0.492 |    0.483 |    0.367 |    0.604 |
| logreg                                |      0.527 |    0.527 |    0.445 |    0.609 |
| lr_knn_k1                             |      0.483 |    0.528 |    0.479 |    0.584 |
| lr_knn_k3                             |      0.476 |    0.498 |    0.431 |    0.561 |
| lr_knn_k5                             |      0.478 |    0.487 |    0.417 |    0.557 |
| random                                |      0.428 |    0.428 |    0.332 |    0.53  |

**YC date = batch start (main = acceptance)**

| method                                |   AUC main |   AUC batchstart |   auc_lo |   auc_hi |
|:--------------------------------------|-----------:|-----------------:|---------:|---------:|
| baseline_github_activity_acceleration |      0.63  |            0.623 |    0.547 |    0.705 |
| baseline_has_public_rd_grant          |      0.454 |            0.451 |    0.418 |    0.484 |
| baseline_oss_external_engagement      |      0.464 |            0.436 |    0.339 |    0.532 |
| knn_k1                                |      0.523 |            0.505 |    0.407 |    0.593 |
| knn_k3                                |      0.5   |            0.483 |    0.39  |    0.574 |
| knn_k5                                |      0.492 |            0.459 |    0.367 |    0.543 |
| logreg                                |      0.527 |            0.627 |    0.555 |    0.721 |
| lr_knn_k1                             |      0.483 |            0.432 |    0.373 |    0.474 |
| lr_knn_k3                             |      0.476 |            0.512 |    0.45  |    0.58  |
| lr_knn_k5                             |      0.478 |            0.53  |    0.466 |    0.598 |
| random                                |      0.428 |            0.403 |    0.314 |    0.483 |

**engagement scaled by percentile (main = robust z)**

| method                                |   AUC main |   AUC scaling_pct |   auc_lo |   auc_hi |
|:--------------------------------------|-----------:|------------------:|---------:|---------:|
| baseline_github_activity_acceleration |      0.63  |             0.63  |    0.543 |    0.723 |
| baseline_has_public_rd_grant          |      0.454 |             0.454 |    0.425 |    0.496 |
| baseline_oss_external_engagement      |      0.464 |             0.464 |    0.344 |    0.572 |
| knn_k1                                |      0.523 |             0.529 |    0.423 |    0.63  |
| knn_k3                                |      0.5   |             0.489 |    0.374 |    0.597 |
| knn_k5                                |      0.492 |             0.47  |    0.356 |    0.584 |
| logreg                                |      0.527 |             0.524 |    0.437 |    0.607 |
| lr_knn_k1                             |      0.483 |             0.483 |    0.442 |    0.519 |
| lr_knn_k3                             |      0.476 |             0.476 |    0.425 |    0.517 |
| lr_knn_k5                             |      0.478 |             0.478 |    0.426 |    0.52  |
| random                                |      0.428 |             0.428 |    0.332 |    0.53  |

## 6. Would we have found HTGF's deals first?

HTGF portfolio companies in the evaluated cohorts: **4**; flagged in the pool's top 5% before HTGF's investment year: **0**. HTGF dates are year-precise ('Invested since'), so lead time is reported in whole years.

| entity_id      |   htgf_invest_year | flagged   | flagged_on   |   best_rank |   cohort_size |
|:---------------|-------------------:|:----------|:-------------|------------:|--------------:|
| airgateway.com |               2018 | False     | NaT          |          29 |           229 |
| tangany.com    |               2020 | False     | NaT          |          42 |           157 |
| valispace.com  |               2018 | False     | NaT          |          72 |           206 |
| mediaire.de    |               2019 | False     | NaT          |         139 |           157 |

**Honest miss:** `mediaire.de` (HTGF 2019) — best rank 139 of 157. What the radar saw on 2019-01-01: outside-OSS contributions (12 m) = 30, activity last quarter vs previous = 40 vs 2, EU grant = no. The public traces we measure were weak at that date.

## 7. Failure cases and reasons

**Highest-ranked companies that did not raise (per our labels) within 24 months** — some may be unlabelled funded companies (controls are not verified):

| company (domain)     | as_of      |   score | why it ranked high                                                   |
|:---------------------|:-----------|--------:|:---------------------------------------------------------------------|
| rivetlogic.com       | 2015-01-01 |   0.724 | no single strong signal (ranked by combination / missing-data flags) |
| fabule.com           | 2015-01-01 |   0.722 | no single strong signal (ranked by combination / missing-data flags) |
| soabridge.org        | 2017-01-01 |   0.717 | no single strong signal (ranked by combination / missing-data flags) |
| fireflymigration.com | 2017-01-01 |   0.716 | no single strong signal (ranked by combination / missing-data flags) |
| wireclub.com         | 2015-01-01 |   0.711 | no single strong signal (ranked by combination / missing-data flags) |
| mywot.com            | 2015-01-01 |   0.704 | no single strong signal (ranked by combination / missing-data flags) |
| persado.com          | 2016-01-01 |   0.702 | no single strong signal (ranked by combination / missing-data flags) |
| minitheory.com       | 2016-01-01 |   0.7   | no single strong signal (ranked by combination / missing-data flags) |
| tmforum.org          | 2017-01-01 |   0.699 | no single strong signal (ranked by combination / missing-data flags) |
| wireclub.com         | 2016-01-01 |   0.698 | no single strong signal (ranked by combination / missing-data flags) |

**Funded companies the radar ranked lowest:**

| entity_id        | funding_date              |   best_rank |   cohort_size |
|:-----------------|:--------------------------|------------:|--------------:|
| replit.com       | 2017-10-31 00:00:00+00:00 |         181 |           238 |
| sqreen.com       | 2017-10-31 00:00:00+00:00 |         175 |           229 |
| tpaga.co         | 2017-03-31 00:00:00+00:00 |         150 |           238 |
| womenwhocode.com | 2016-03-31 00:00:00+00:00 |         141 |           203 |
| mediaire.de      | 2019-01-01 00:00:00+00:00 |         139 |           157 |
| skydropx.com     | 2018-03-31 00:00:00+00:00 |         128 |           206 |
| goodybag.com     | 2015-10-31 00:00:00+00:00 |         124 |           203 |
| upchieve.org     | 2020-10-31 00:00:00+00:00 |         115 |           157 |
| coderhouse.com   | 2020-10-31 00:00:00+00:00 |         113 |           157 |
| teamleada.com    | 2015-03-31 00:00:00+00:00 |         103 |           203 |

**Top-ranked funded companies with plain-English reasons:**

| company        | as_of      | funded     | why it ranked high                                                   |
|:---------------|:-----------|:-----------|:---------------------------------------------------------------------|
| audm.com       | 2017-01-01 | 2017-03-31 | no single strong signal (ranked by combination / missing-data flags) |
| opentrons.com  | 2015-01-01 | 2015-10-31 | no single strong signal (ranked by combination / missing-data flags) |
| airgateway.com | 2017-01-01 | 2018-01-01 | no single strong signal (ranked by combination / missing-data flags) |
| zeplin.io      | 2015-01-01 | 2015-03-31 | no single strong signal (ranked by combination / missing-data flags) |
| suiteness.com  | 2016-01-01 | 2016-03-31 | no single strong signal (ranked by combination / missing-data flags) |
| podium.com     | 2015-01-01 | 2015-10-31 | no single strong signal (ranked by combination / missing-data flags) |
| sunsama.com    | 2017-01-01 | 2018-10-31 | no single strong signal (ranked by combination / missing-data flags) |
| coub.com       | 2015-01-01 | 2016-03-31 | no single strong signal (ranked by combination / missing-data flags) |
| animaapp.com   | 2018-01-01 | 2018-03-31 | no single strong signal (ranked by combination / missing-data flags) |
| starkbank.com  | 2019-01-01 | 2019-10-31 | no single strong signal (ranked by combination / missing-data flags) |

## 8. Limitations (stated plainly)

- **Selection bias in labels:** positives are YC and HTGF companies only; companies funded by other investors sit among the controls and make the radar look worse than it is (and differently so by region).
- **Unverified negatives:** controls are assumed unfunded; nobody checked.
- **Coverage:** a company is only visible if it has a GitHub organisation whose website is its domain and whose login is the domain name, and that showed team activity in its first year. Most deep-tech, hardware and biotech startups (HTGF's core) leave no such trace; university spin-offs with only a university web page are invisible.
- **Current-state fields:** org websites and names are today's values; renamed orgs lose history; logins reused by a new owner are detected with an account-id→date map and dropped.
- **Survivorship:** deleted orgs are missing for funded and unfunded companies alike, but survival correlates with funding.
- **EU grants:** CORDIS stores websites only for FP7-era companies; newer grantees are matched by exact legal name + country, which needs a country-code domain (.de, .fr …); .com companies are 'not covered'.
- **Stealth companies** leave few public traces and are invisible by construction.
- **Non-UK registries** (Handelsregister) have no free bulk API; registry-based signals (serial founder) are roadmap.
- **HTGF dates are year-precise**; YC dates are acceptance proxies (batch start − 76 days; batch start as sensitivity).
- **Weak evidence for acceleration:** no paper measures GitHub *acceleration*; the engagement definition differs from Conti et al. (team-level PRs/issues/pushes vs org-level forks). See papers/index.md.
- **Locked test years were not evaluated**; all numbers are from selection years used during development.
