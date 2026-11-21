# Research task, PASS 2: founder backgrounds and early history of YC companies

Each company below already includes founder names, titles, YC bios and LinkedIn URLs from the YC page. Use them.

Budget rule: use at most 3 web searches per company. Spend them mainly on founder backgrounds (prior jobs, prior companies, education), then on outcome/funding if still unclear.

## Required research steps (do ALL of them for EVERY company)
1. Founder background search(es): e.g. `"<founder name>" <company> founder previously`.
2. If needed, one company search: e.g. `"<company>" Y Combinator raises OR "shut down"`.
3. Only after this, fill the fields. Use "unknown" ONLY if the searches really found nothing. Never guess or invent.

## Output: one JSON object per company, one per line (JSONL), with exactly these keys
- "slug", "name"
- "founders": list of names
- "founder_count": integer
- "domain_expert": yes/no/unknown — any founder worked in or studied the specific industry/problem BEFORE founding
- "technical_founder": yes/no/unknown — engineer, developer, scientist, CTO, CS/engineering degree
- "business_founder": yes/no/unknown — sales, marketing, finance, MBA, consulting, operations, business role
- "repeat_founder": yes/no/unknown — any founder founded a company before
- "prior_exit": yes/no/unknown — any founder's earlier company was acquired or IPO'd
- "phd_or_researcher": yes/no/unknown
- "elite_school": yes/no/unknown — Stanford, MIT, Harvard, Berkeley, CMU, Princeton, Yale, Caltech, Oxford, Cambridge, IIT, Waterloo
- "big_tech_alum": yes/no/unknown — Google, Facebook/Meta, Microsoft, Amazon, Apple, Twitter, Uber, Airbnb, Stripe, Palantir, LinkedIn
- "university_spinoff": yes/no/unknown — built on university/lab research or founded while students/researchers there
- "regulated_or_hardware": yes/no — regulated sector (finance, health, insurance, energy, transport, legal) or physical hardware
- "b2b": yes/no
- "deep_tech": yes/no — AI research, biotech, robotics, space, energy, quantum, semiconductors, nuclear
- "launched_before_yc": yes/no/unknown — product live/public before or during YC application
- "paying_customers_before_yc": yes/no/unknown
- "funding_before_yc": yes/no/unknown — angels, grants, friends & family before YC
- "public_trace_types": list from ["show_hn","product_hunt","github_repo","research_paper","blog","app_store","award_or_competition","press","none_found"] that existed BEFORE their first outside money
- "real_pivot": yes/no/unknown — materially changed product or business model at any time
- "non_us_hq": yes/no/unknown — company based outside the US at YC time
- "raised_after_demo_day": yes/no/unknown — raised a seed/next round within ~12 months after YC
- "outcome": short text (operating / acquired by X (year) / IPO / shut down (year))
- "failure_reasons": only if shut down or clearly failed, list from ["no_market_need","ran_out_of_cash","couldnt_raise_next_round","competition","cofounder_conflict","regulation_legal","unit_economics","pivoted_too_late_or_too_often","team_left_or_acquihire","product_problems","market_timing","unknown"]; else []
- "founder_evidence": one short sentence per founder summarizing what you found (with the key facts)
- "evidence_quality": high / medium / low
- "sources": list of URLs used

Output ONLY the JSONL lines, nothing else.
