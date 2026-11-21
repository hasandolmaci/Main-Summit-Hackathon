Created [m_patents.py](scripts/m_patents.py) and [m_patents.csv](raw/m_patents.csv). Processed all 220 rows and printed fill rates plus 5 example rows.

| Columns | Fill rate |
|---|---:|
| name, website, asof_date | 100% |
| patent_applications_before, patent_evidence, patent_score | 18.2% |
| patent_first_priority_date | 1.8% |

Results: **4 candidate matches, 36 zeros, 180 unknowns**. Google and Espacenet blocks limited coverage; failed searches remain empty.

Validated score bounds and strict date cutoffs. Evidence includes matched assignees for collision review. Counts use Google’s default grouped search totals.