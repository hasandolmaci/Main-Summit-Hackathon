Created [script](</C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/scripts/v2_founder_bg.py>) and [CSV](</C:/Users/hasan/OneDrive/Desktop/main hackathon/backtest-data/raw/v2_founder_bg.csv>): **409 rows**, including both supplemental files.

Coverage is partial: **123 rows have dated evidence; 164 have reviewed evidence**. Unknowns remain blank. Date, duplicate, evidence-link, and privacy checks passed.

Fill counts:

```text
cid/company/founder/confidence: 409 each
role: 99
has_phd: 28
technical_background: 61
industry_years_before_asof: 4
prior_company/prior_evidence_url: 27 each
prior_company_vc_backed_or_exit: 8
is_professor: 11
star_academic: 7
openalex_id/citations_note: 14 each
works_before_asof: 12
top_employer: 10
evidence_url/evidence_dates: 123 each
asof_date/check_notes: 409 each
undated_facts: 76
post_asof_facts: 22
```

Company/university pages, press, registers, and public professional snippets worked. OpenAlex returned **102 successful cached responses**, then stopped after **three HTTP 429 errors**.