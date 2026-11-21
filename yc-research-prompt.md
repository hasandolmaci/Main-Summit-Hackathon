I want you to research Y Combinator companies and find which founder and company traits separate successful companies from failed ones. Work in a new folder `yc-research/` in the current directory. Use Python (standard library is enough). Follow this plan exactly.

## 1. Data and sample
- Download all YC companies: `https://yc-oss.github.io/api/companies/all.json` (fields include name, slug, batch, status [Active/Inactive/Acquired/Public], top_company [bool], industry, website, one_liner).
- Groups, all from the SAME batches as the top companies (so company age is comparable):
  - **top** = every company with `top_company = true` (~91)
  - **other** = random Active/Acquired non-top companies
  - **inactive** = random Inactive companies (= dead)
- Sample size: [N, e.g. 300 or 1000] in total. Use a fixed random seed. Shuffle the list.
- **Blind the researchers:** never pass the group label to whoever researches a company.

## 2. Founder data (scripted, reliable)
For every company fetch `https://www.ycombinator.com/companies/<slug>` (User-Agent "Mozilla/5.0", ~1 request/second). The page embeds JSON in `data-page="..."` (HTML-unescape it): `props.company` has `founders` (full_name, title, founder_bio, linkedin_url), `year_founded`, `location`, `long_description`. Save as `yc_pages.json`.

## 3. Evidence packs (scripted, no limits)
Sub-agents share a cap of about 200 web searches per turn, so do NOT rely on them searching. Instead build an evidence pack per company with a script:
- Google News RSS: `https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US&ceid=US:en` for `"<company>" startup`, `"<company>" (raises OR funding OR acquired OR "shut down")`, and per founder `"<founder>" <company>` and `"<founder>" founder` (keep title, link, date).
- Wikipedia: search API `https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=...&format=json`, then the summary at `/api/rest_v1/page/summary/<title>`, for the company and each founder (keep only if the title matches).
- Use 4 threads with retries; save `evidence.json` incrementally so a crash doesn't lose progress.

## 4. Research / classification
Give each researcher 10-20 companies with their evidence pack (YC founders + bios + news + Wikipedia). They may open linked articles (WebFetch) but should use at most a few searches. Run them in parallel. Options:
- Claude Haiku sub-agents (cheap; good when given evidence packs, poor when they must search themselves), and/or
- an OpenAI model via the Responses API with the `web_search` tool (`search_context_size: "low"`), only if I set `OPENAI_API_KEY` myself. Track cost per batch from `usage` and stop at a hard dollar budget that I give you. Handle HTTP 429 by waiting the time given in the error message.

Each researcher returns one JSON line per company with these fields (values yes/no/unknown unless stated; judge as of BEFORE/AT YC; "unknown" only if really not found; never guess):
`slug, name, founders, founder_count, domain_expert (worked in or studied this exact industry before founding), technical_founder, business_founder, repeat_founder, prior_exit, phd_or_researcher, elite_school, big_tech_alum, university_spinoff, regulated_or_hardware (yes/no), b2b (yes/no), deep_tech (yes/no), launched_before_yc, paying_customers_before_yc, funding_before_yc, public_trace_types (list: show_hn, product_hunt, github_repo, research_paper, blog, app_store, award_or_competition, press, none_found), real_pivot, non_us_hq, raised_after_demo_day, outcome (text), failure_reasons (only for dead companies; list: no_market_need, ran_out_of_cash, couldnt_raise_next_round, competition, cofounder_conflict, regulation_legal, unit_economics, pivoted_too_late_or_too_often, team_left_or_acquihire, product_problems, market_timing, unknown), founder_evidence (one sentence per founder), evidence_quality (high/medium/low), sources (URLs)`.

## 5. Quality control (do this before analysing)
- After the first batches, measure the share of "unknown" in the founder and pre-YC fields. If it's above ~50%, the research is too shallow: give the researchers more evidence or smaller batches and redo it.
- Spot-check 10 random companies against their sources yourself.
- If two models were used, have ~50 companies done by both and report how often they agree, per field.

## 6. Analysis
- Groups for the comparison: **Survived = top + other**, **Dead = inactive**. Also show top alone.
- For each yes/no feature: % yes among companies where it's known (exclude "unknown" from the denominator, and report how many were unknown per group), with 95% Wilson intervals.
- Test survived vs dead with Fisher's exact test (two-sided). Because many features are tested, also report Holm-adjusted p-values.
- Also report: average founder count, the distribution of failure reasons among dead companies, and which public_trace_types existed before funding per group.
- Check that the results hold within early vs late batches (batch-age confound).

## 7. Output
Write `yc-results.md` with:
1. A table in exactly this format, sorted by strength of evidence:

| Pattern | Survived | Dead | How strong |
|---|---|---|---|
| A founder already worked in or studied this exact problem | 73% | 43% | Borderline (p = 0.06) |
| Regulated or hardware market | 55% | 25% | Strongest result (p = 0.03) |
| Pivoted | ~63% | 67% | No difference |

   "How strong": "Strong" if Holm-adjusted p < 0.05, "Likely" if raw p < 0.05, "Borderline" if p < 0.10, "Weak, could be chance" otherwise, "No difference" if the gap is under 5 points. Show n per cell where it helps.
2. The failure-reason breakdown for dead companies.
3. What it means for scoring early-stage, unfunded startups (which traits to weight, which to ignore).
4. Caveats: survivorship and selection bias (YC already filtered founders), "unknown" data, US focus, correlation is not causation.
Keep all raw files (sample, yc_pages, evidence, research JSONL, analysis script) so the result can be reproduced.

Tell me the plan, the sample size and the expected cost/time before starting the research step, and give me progress updates.
