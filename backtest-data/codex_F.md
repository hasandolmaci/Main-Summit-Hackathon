Created [m_founders.py](scripts/m_founders.py) and [m_founders.csv](raw/m_founders.csv). Processed and validated all 220 rows; printed fill rates and five example rows.

| Column | Fill rate |
|---|---:|
| name | 100% |
| website | 100% |
| asof_date | 100% |
| founder_names | 5.9% |
| managing_directors | 14.1% |
| source_url | 34.1% |
| snapshot_ts | 34.1% |

Responses are cached under `raw/cache/m_founders/`. To preserve the historical cutoff, 14 live fallbacks are marked `live` with name cells empty. Archive selection uses the latest eligible capture returned by the requested collapsed CDX query.