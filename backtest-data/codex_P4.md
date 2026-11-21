Implemented and ran both commands. Saved the peer reference and verified scoring, coverage, penalties, ranking order, and label isolation.

Funded rates, low → middle → high tercile:

- Quality: **19.0% → 5.0% → 30.0%**
- Opportunity: **20.8% → 11.8% → 20.0%**
- Ranked: **50.0% (1/2)**; not ranked: **13.6% (12/88)**

Only 2 backtest rows met the ranking threshold. No discovery leads qualified.

Outputs:

- [Scored backtest](reports/scored_backtest.csv)
- [Scored leads](reports/scored_leads.csv)
- [Updated report](reports/benchmark_report.md)

The report includes coverage statistics and caveats about the 13 positives, low full coverage, text heuristics, and percentiles versus probabilities.