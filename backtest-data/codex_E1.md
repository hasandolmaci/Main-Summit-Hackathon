Created and ran [enrich_free.py](scripts/enrich_free.py), producing [enrich_free.csv](raw/enrich_free.csv) with 220 verified rows. Printed fill rates and five examples.

- HTGF: 5 dated positives, 202 directory nonmatches, 13 unknown.
- News counts: 99.1% filled.
- Devpost and patents: blocked; left empty.
- Sector growth: skipped.

Devpost counts are explicitly all-time when available. HTGF zeros reflect current-directory nonmatches, not proven historical absence.