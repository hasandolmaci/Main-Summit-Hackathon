Created and ran all four collectors; CSV validation passed. Only the eight requested files were written, using in-memory caching for these runs.

HighTechXL includes Netherlands entries and a country column. KIT has no usable cohort dates, so `asof_date` is empty. SpinLab dates are profile-publication dates.

| Source | Rows | Rows with website | Rows with date |
|---|---:|---:|---:|
| HighTechXL | 17 | 15 | 17 |
| SpinLab Leipzig | 149 | 119 | 149 |
| KIT-Gruenderschmiede | 768 | 613 | 0 |
| CyberLab Karlsruhe | 301 | 215 | 301 |