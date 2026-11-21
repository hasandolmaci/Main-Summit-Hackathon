# Research task: histories of Y Combinator companies

You get a JSON file (named in the prompt) with 10 YC companies: name, YC batch, current status, website, one-liner, founders, YC page URL.
For EACH company, use web search to research its history. Good sources: the YC company page, TechCrunch / news articles, founder blog posts and post-mortems, Hacker News threads (hn.algolia.com search), Wikipedia, Crunchbase public pages, Wayback Machine, GitHub, Product Hunt, LinkedIn snippets in search results. Spend roughly equal effort on each company (about 3-6 searches). If you cannot find something, write "unknown" — never guess or invent. Every non-trivial fact needs a source URL.

For each company return ONE JSON object per line (JSONL) with exactly these keys:
- "slug", "name", "group" (copy from input)
- "founding_story": 1-2 sentences, how/why it started
- "founders_background": short, per founder if known (prior jobs, prior startups, education, technical or not)
- "pre_funding_traces": list of public traces that existed BEFORE their first outside money (YC counts as first outside money unless they had earlier angels). Use these tags where possible: "show_hn", "product_hunt", "github_repo", "research_paper", "blog_or_newsletter", "paying_customers", "waitlist_or_beta", "competition_or_award", "university_incubator", "side_project_traction", "prior_company_pivot", "app_store", "none_found". Add a short note + source for each.
- "how_they_got_funded": short narrative of the funding path (YC application story, demo day, seed round, notable investors, what impressed investors), with amounts/dates if known
- "first_round_after_yc": e.g. "Seed $2M, 2013, led by X" or "unknown"
- "traction_at_yc": users/revenue/growth around YC if known, else "unknown"
- "pivots": "none" / short description
- "outcome": what happened (still operating, acquired by X for $Y, IPO, shut down in YEAR)
- "failure_reasons": ONLY for shut-down or struggling companies: list using these tags where possible: "no_market_need", "ran_out_of_cash", "couldnt_raise_next_round", "competition", "cofounder_conflict", "regulation_legal", "unit_economics", "pivoted_too_late_or_too_often", "team_left_or_acquihire", "product_problems", "market_timing", "unknown". Add a short explanation + source. For successful companies use [].
- "key_lesson": one sentence
- "evidence_quality": "high" (multiple solid sources) / "medium" / "low" (barely anything found)
- "sources": list of URLs used

Output: ONLY the 10 JSONL lines as your final answer, nothing else (no markdown fences, no commentary).
