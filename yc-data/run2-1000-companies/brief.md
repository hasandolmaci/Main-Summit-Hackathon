# Research task: early history of Y Combinator companies

You get a JSON file with YC companies (name, YC batch, website, one-liner, YC page URL).
For EACH company, research its history with web search / web fetch. Good sources: the YC company page (lists founders + bios), TechCrunch and other news, founder blog posts and post-mortems, Hacker News (hn.algolia.com), Wikipedia, Crunchbase public pages, LinkedIn snippets in search results, GitHub, Product Hunt.
Spend roughly equal effort per company (3-6 searches). If you cannot find something, use "unknown". NEVER guess or invent. Judge each field about the time BEFORE or AT YC (not today), unless the field says otherwise.

Return ONE JSON object per company, one per line (JSONL), with exactly these keys:

- "slug", "name" (copy from input)
- "founders": list of founder names found
- "founder_count": integer or "unknown"
- "domain_expert": "yes" if any founder worked in or studied the specific industry/problem BEFORE founding (e.g. nurse founding a nursing-staffing startup, ex-banker founding fintech), "no" if backgrounds are known and none did, else "unknown"
- "technical_founder": yes/no/unknown (engineer, developer, scientist, CTO, CS/engineering degree)
- "business_founder": yes/no/unknown (sales, marketing, finance, MBA, operations, business role)
- "repeat_founder": yes/no/unknown (any founder founded a company before)
- "prior_exit": yes/no/unknown (any founder's earlier company was acquired or IPO'd)
- "phd_or_researcher": yes/no/unknown
- "elite_school": yes/no/unknown (Stanford, MIT, Harvard, Berkeley, CMU, Princeton, Yale, Caltech, Oxford, Cambridge, IIT, Waterloo)
- "big_tech_alum": yes/no/unknown (Google, Facebook/Meta, Microsoft, Amazon, Apple, Twitter, Uber, Airbnb, Stripe, Palantir, LinkedIn)
- "university_spinoff": yes/no/unknown (based on research/IP from a university or lab, or founded while students/researchers there)
- "regulated_or_hardware": yes/no (regulated sector like finance, health, insurance, energy, transport, legal, education-with-licensing, OR builds physical hardware)
- "b2b": yes/no (main customers are businesses)
- "deep_tech": yes/no (core is hard science/engineering: AI research, biotech, robotics, space, energy, quantum, semiconductors, nuclear)
- "launched_before_yc": yes/no/unknown (product was live/public before or during YC application)
- "paying_customers_before_yc": yes/no/unknown
- "funding_before_yc": yes/no/unknown (angels, grants, friends&family before YC)
- "public_trace_types": list from ["show_hn","product_hunt","github_repo","research_paper","blog","app_store","award_or_competition","press","none_found"] that existed BEFORE their first outside money
- "real_pivot": yes/no/unknown (materially changed product or business model at any time)
- "non_us_hq": yes/no/unknown (company based outside the US at YC time)
- "raised_after_demo_day": yes/no/unknown (raised a seed/next round within ~12 months after YC)
- "outcome": short text (operating / acquired by X (year) / IPO / shut down (year))
- "failure_reasons": ONLY if shut down or clearly failed, list from ["no_market_need","ran_out_of_cash","couldnt_raise_next_round","competition","cofounder_conflict","regulation_legal","unit_economics","pivoted_too_late_or_too_often","team_left_or_acquihire","product_problems","market_timing","unknown"]; otherwise []
- "evidence_quality": "high" / "medium" / "low"
- "sources": list of URLs used

Write all lines to the output file named in your prompt (valid JSONL, UTF-8, one object per line, no markdown fences).
