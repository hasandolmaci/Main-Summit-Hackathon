# Cleaning report — DACH cohort backtest

Rules were fixed before the loop; the cleaning code receives the data **without** any `y_*` column (`clean.split_outcomes`). Labels below are counted only for this report.

## 0. Re-export (no Excel)

- Input `dach/data/raw/backtest_upload.csv`: 205 rows × 98 columns.
- All dates were `dd/mm/yyyy` → re-exported as ISO `yyyy-mm-dd` (`dach/data/clean/backtest_iso.csv`).
- `s_homepage_snapshot_ts` / `s_impressum_snapshot_ts` had been turned into scientific notation by Excel (157 / 89 values, e.g. `2.02202E+13`). Only 6 significant digits survive, so they are recovered to **month precision** (flag `<col>_precision`); an as-of check passes only if the whole snapshot month lies before as_of. The generating pipeline is not in this repository: an exact (second-precision) re-export has to be produced there; `read_iso` refuses any file that contains scientific notation again.

## Rules

| rule | what | rows affected |
|---|---|---|
| a | missing ≠ 0: blanks stay NaN end to end; nothing is filled with 0; missing indicators are added in-fold | all |
| b | `domain_invalid`: platform / university domain or snapshot of another site → all domain- and snapshot-derived fields blanked (GitHub match blanked too when it was made through that domain); row kept | 3 |
| c | `f_snapshot_invalid`: parked / for-sale / unrelated page → page-derived fields blanked; row kept | 4 |
| d | not a startup: legal form e.V. or cohort description says student group/club → row dropped | 7 |

## Rows affected

| rule | company | reason |
|---|---|---|
| b_domain_invalid | beworm | id_domain is linkedin.com (platform) |
| c_snapshot_invalid | cedardb | snapshot is an unrelated Chinese-language site |
| c_snapshot_invalid | esgx | parked / unrelated page |
| b_domain_invalid | mbee | snapshot is another site (stabl.com, 2005 page) |
| b_domain_invalid | opsyon | id_domain is a uni-muenchen.de subdomain (university) |
| c_snapshot_invalid | medicsen | parked / unrelated page |
| c_snapshot_invalid | reonic | parked / unrelated page |
| d_not_a_startup | neurotum | student group |
| d_not_a_startup | enhands | student group |
| d_not_a_startup | impetussailingteam | student sailing team |
| d_not_a_startup | warrspacelabs | student group (WARR) |
| d_not_a_startup | tufastmotoelectric | student racing team |
| d_not_a_startup | robotum | student robotics club |
| d_not_a_startup | deepen | legal form e.V. |

## Labelled rows before / after

| | positives | negatives |
|---|---|---|
| before cleaning | 13 | 77 |
| after cleaning | 13 | 72 |
