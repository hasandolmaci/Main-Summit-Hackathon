| year | segment | model | n | positives | base_rate | auc (95% CI) | p_at_50 | p_at_top10pct | lift@top10% |
|---|---|---|---|---|---|---|---|---|---|
| 2022 | all | spec_knn (as in README, min overlap 1) | 5136 | 724 | 0.141 | 0.517 (0.494-0.538) | 0.16 | 0.15 | 1.06 |
| 2022 | all | contrast_knn (winners vs others, min overlap 2) | 5136 | 724 | 0.141 | 0.523 (0.500-0.545) | 0.12 | 0.154 | 1.09 |
| 2022 | all | share_knn (k=100, min overlap 2) | 5136 | 724 | 0.141 | 0.525 (0.503-0.548) | 0.28 | 0.14 | 0.99 |
| 2022 | all | prior_weighted_sum | 5136 | 724 | 0.141 | 0.490 (0.468-0.514) | 0.18 | 0.163 | 1.16 |
| 2022 | all | logreg (diagnostic) | 5136 | 724 | 0.141 | 0.486 (0.464-0.510) | 0.18 | 0.138 | 0.98 |
| 2022 | all | spec_knn (as in README, min overlap 1) [shuffled reference labels] | 5136 | 724 | 0.141 | 0.512 (0.488-0.534) | 0.08 | 0.119 | 0.84 |
| 2022 | all | contrast_knn (winners vs others, min overlap 2) [shuffled reference labels] | 5136 | 724 | 0.141 | 0.518 (0.496-0.539) | 0.12 | 0.138 | 0.98 |
| 2022 | all | share_knn (k=100, min overlap 2) [shuffled reference labels] | 5136 | 724 | 0.141 | 0.486 (0.464-0.507) | 0.14 | 0.16 | 1.13 |
| 2022 | crunchbase | spec_knn (as in README, min overlap 1) | 4159 | 610 | 0.147 | 0.515 (0.488-0.539) | 0.16 | 0.147 | 1.0 |
| 2022 | program | spec_knn (as in README, min overlap 1) | 977 | 114 | 0.117 | 0.461 (0.411-0.513) | 0.04 | 0.071 | 0.61 |
| 2022 | crunchbase | contrast_knn (winners vs others, min overlap 2) | 4159 | 610 | 0.147 | 0.522 (0.497-0.548) | 0.14 | 0.147 | 1.0 |
| 2022 | program | contrast_knn (winners vs others, min overlap 2) | 977 | 114 | 0.117 | 0.469 (0.417-0.523) | 0.1 | 0.092 | 0.79 |
| 2022 | crunchbase | share_knn (k=100, min overlap 2) | 4159 | 610 | 0.147 | 0.529 (0.507-0.553) | 0.06 | 0.12 | 0.82 |
| 2022 | program | share_knn (k=100, min overlap 2) | 977 | 114 | 0.117 | 0.575 (0.517-0.626) | 0.28 | 0.235 | 2.01 |
| 2022 | crunchbase | prior_weighted_sum | 4159 | 610 | 0.147 | 0.499 (0.476-0.523) | 0.12 | 0.173 | 1.18 |
| 2022 | program | prior_weighted_sum | 977 | 114 | 0.117 | 0.596 (0.536-0.649) | 0.18 | 0.173 | 1.49 |
| 2022 | crunchbase | logreg (diagnostic) | 4159 | 610 | 0.147 | 0.479 (0.456-0.505) | 0.02 | 0.12 | 0.82 |
| 2022 | program | logreg (diagnostic) | 977 | 114 | 0.117 | 0.586 (0.529-0.641) | 0.18 | 0.194 | 1.66 |
| 2023 | all | spec_knn (as in README, min overlap 1) | 817 | 86 | 0.105 | 0.504 (0.446-0.567) | 0.16 | 0.171 | 1.62 |
| 2023 | all | contrast_knn (winners vs others, min overlap 2) | 817 | 86 | 0.105 | 0.504 (0.443-0.564) | 0.16 | 0.122 | 1.16 |
| 2023 | all | share_knn (k=100, min overlap 2) | 817 | 86 | 0.105 | 0.589 (0.529-0.648) | 0.06 | 0.085 | 0.81 |
| 2023 | all | prior_weighted_sum | 817 | 86 | 0.105 | 0.594 (0.528-0.667) | 0.26 | 0.244 | 2.32 |
| 2023 | all | logreg (diagnostic) | 817 | 86 | 0.105 | 0.632 (0.568-0.700) | 0.18 | 0.22 | 2.09 |
| 2023 | all | spec_knn (as in README, min overlap 1) [shuffled reference labels] | 817 | 86 | 0.105 | 0.502 (0.440-0.573) | 0.2 | 0.134 | 1.27 |
| 2023 | all | contrast_knn (winners vs others, min overlap 2) [shuffled reference labels] | 817 | 86 | 0.105 | 0.512 (0.453-0.573) | 0.2 | 0.134 | 1.27 |
| 2023 | all | share_knn (k=100, min overlap 2) [shuffled reference labels] | 817 | 86 | 0.105 | 0.411 (0.347-0.477) | 0.16 | 0.134 | 1.27 |
| 2023 | program | spec_knn (as in README, min overlap 1) | 817 | 86 | 0.105 | 0.504 (0.445-0.562) | 0.16 | 0.171 | 1.62 |
| 2023 | program | contrast_knn (winners vs others, min overlap 2) | 817 | 86 | 0.105 | 0.504 (0.442-0.570) | 0.16 | 0.122 | 1.16 |
| 2023 | program | share_knn (k=100, min overlap 2) | 817 | 86 | 0.105 | 0.589 (0.528-0.652) | 0.06 | 0.085 | 0.81 |
| 2023 | program | prior_weighted_sum | 817 | 86 | 0.105 | 0.594 (0.517-0.668) | 0.26 | 0.244 | 2.32 |
| 2023 | program | logreg (diagnostic) | 817 | 86 | 0.105 | 0.632 (0.570-0.694) | 0.18 | 0.22 | 2.09 |
