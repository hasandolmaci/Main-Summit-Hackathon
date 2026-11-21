Created [label_funding.py](</C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/scripts/label_funding.py>) and ran it on all **132 rows**.

Output: [labels_cohort_utum.csv](</C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/raw/labels_cohort_utum.csv>)

| y_label | Count |
|---|---:|
| 1 | **7** |
| 0 | **37** |
| Empty | **88** |

Only seven positives were found, so there aren’t ten to report:

| Company | Round date | Evidence |
|---|---|---|
| Qualiwise | 2024-08-30 | [123 ventures investment](https://www.deutsche-startups.de/2024/08/30/dealmonitor-swiss-mile-thryve-wellabe/) |
| Radical Dot | 2025-03-03 | [UVC Partners and others invested 2.7 million](https://www.deutsche-startups.de/2025/03/03/dealmonitor-radical-dot-quantum-industries/) |
| Emidat | 2024-08-16 | [General Catalyst and institutional co-investors](https://www.deutsche-startups.de/2024/08/16/dealmonitor-caresyntax-emidat/) |
| Hula Earth | 2024-11-13 | [€1.6 million, including Point Nine Capital](https://www.deutsche-startups.de/2024/11/14/dealmonitor-tourlane-biolux-hula/) |
| Seal Robotics | 2026-01-29 | StartingUp: $2.1 million pre-seed |
| Qoro Quantum | 2026-04-09 | Quantum Computing Report: $750,000 pre-seed |
| Valuemize | 2026-07-16 | Startbase: seven-figure pre-seed |

Full URLs and quotes are stored in the CSV. Four positives have `needs_review=1`; dates use publication dates as proxies. Boundary, grant-exclusion, source-failure, currency-parsing, and dataset checks passed.