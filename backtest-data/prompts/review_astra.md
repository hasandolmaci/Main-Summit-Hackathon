**Useful as an audit and demo dataset; not yet a credible weight-learning dataset.** I loaded the CSVs and inspected the raw inputs and collection/assembly code. No files were modified.

- **(a) Learn weights:** No reliable multifeature weights yet. Fourteen positives cannot support the intended 10–12-feature model, stable time validation or calibrated probabilities.
- **(b) Sanity-check prior weights:** Limited usefulness after fixing the joins and auditing labels. It can suggest hypotheses, but cannot justify reversing grant or GitHub weights.
- **(c) Pitch evidence:** Suitable for demonstrating historical reconstruction and explaining preliminary findings. Insufficient to claim predictive accuracy, investment quality or discovery ahead of databases.
- **(d) Live demo:** Useful for showing evidence-backed company profiles. The 27 discovery leads are contemporary, unlabelled leads—not additional backtest observations.

**Computed results.** Of 205 companies, 93 are labelled: 14 positive and 79 negative. The observed funding rate is **15.1%**, with a **95% Wilson interval of 9.2–23.7%**.

| Signal | Funded: signal present versus rest* | Two-sided Fisher p |
|---|---:|---:|
| Pricing page | 4/9 versus 10/84 | 0.027 |
| Impressum | 10/45 versus 4/48 | 0.083 |
| Grant score | 0/8 versus 14/85 | 0.602 |
| GitHub open source | 1/7 versus 13/86 | 1.000 |

*“Rest” includes missing values, reproducing the brief’s comparisons; missing does not mean absent.*

Pricing’s funded proportion has a wide **18.9–73.3%** interval. Excluding missing pricing values gives 4/9 versus 9/72, p=0.033. Even correcting only these four exploratory comparisons removes conventional significance. Zero funded grant recipients out of eight remains compatible with a funding rate up to **32.4%**. These results do not establish useful predictive weights.

**The main risks are concrete:**

- **Broken point-in-time joins.** [assemble.py](<C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/scripts/assemble.py>) joins by normalised name while retaining a different occurrence’s discovery date. PROSION, NEUREVO and suena have mismatched dates. NEUREVO’s and suena’s website snapshots occur **after** their final as-of dates; suena is positive. PROSION also retains a later discovery date despite an earlier raw occurrence.
- **“First round” is not established.** All 14 positive round dates satisfy their recorded 18-month windows, but nine positives still require review. The Reonic evidence explicitly mentions an earlier €3m investment; its recorded round is therefore not first. ESG-X’s supposed funding evidence describes an acquisition and its acquirer’s funding. Of 79 negatives, 67 mean “nothing found,” and 64 lack a Crunchbase cross-check.
- **Unknowns are selected, not random.** Among 76 UNKNOWN rows, 24 already have equity-round flags and 31 have death flags. Overall, 35/38 death-flagged companies are excluded. Moreover, `y_dead` measures current unreachability/parking or news, not verified closure within 18 months.
- **Cohort and coverage bias.** UnternehmerTUM and SpinLab supply 78/93 labelled observations. Yearly positives range from 1/19 in 2020 to 3/7 in 2025. Lists were collected from current pages, with approximate batch dates or profile-publication dates. Historical completeness and German eligibility need checking; Holloid’s archived page lists Vienna.
- **Leakage extends beyond `info_`.** Current cohort descriptions, HN points and GitHub stars are unsafe. GitHub momentum selects repositories using today’s stars. Shared news sources create correlated feature/label ascertainment even when dates are filtered. `evidence.csv` omits enrichment evidence and label quotations.

**Highest-value next steps:**

1. **Repair identity/date joins and enforce timestamp checks.** Removes demonstrated leakage immediately.
2. **Audit all positives and round-bearing exclusions first:** 23 `CB_ROUND_NO_DATE` plus 24 UNKNOWN equity flags. Confirm first institutional dates; this can recover positives and remove false eligibility.
3. **Audit negatives and failed websites**, recording funding-check coverage and historical German eligibility. Reduces survival and false-negative bias.
4. **Freeze a tiny prior scorecard** using verified historical features. Compare descriptively by cohort/year; defer learned weights and sparse founder/product-live features.
5. **Run 24 months only as sensitivity.** Existing dates would yield 15 positives among 86 mature labelled rows, including four reclassifications—not a substantial power improvement.

**Confidence:** High in the counts and merge defects; low in label completeness and predictive conclusions.