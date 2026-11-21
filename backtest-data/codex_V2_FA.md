Created [v2_founders.py](scripts/v2_founders.py) and [v2_founders_A.csv](raw/v2_founders_A.csv): **110 rows written**.

| Column | Filled |
|---|---:|
| cid | 110 |
| name | 110 |
| founder_names_asof | 8 |
| founder_count_asof | 9 |
| source_asof | 9 |
| snapshot_date | 9 |
| founder_names_now | 80 |
| founder_count_now | 94 |
| source_now | 94 |
| legal_form | 61 |
| evidence_url | 95 |

Validation passed for counts, evidence URLs, and strict historical cutoffs. Director/CEO proxies are labeled; counts may be incomplete.

Current sites and manual web searches worked. Wayback stopped after three timeouts; Bing stopped after three unrelated responses. Full source results and failed domains are in [run_summary.json](raw/cache/v2_founders/run_summary.json).