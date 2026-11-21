# How existing tools rate startups, and what we can borrow

Researched 2026-11-20 from each vendor's own help pages and blog posts. None of them publishes full formulas or weights; the vendors' accuracy claims are their own and weren't independently checked.

## 1. How the big tools score

| Tool | Score | What goes into it | Notable idea |
|---|---|---|---|
| **CB Insights, Mosaic** | 0–1000 | **4 Ms:** Momentum (traction), Market (health of the industry), Money (financial health), Management (separate founder/team score). A weighted average of the four | Vendor claim: the top 10% by score catch 80% of future unicorns. 510+ counts as "high potential" |
| **Dealroom Signal** | 1–100 | Average of **Founding Team + Growth + Completeness**. **Timing** (when the next round is likely) is shown separately. Growth = headcount change **compared with same-size companies in the same sector** | **Completeness is part of the score**; the Timing curve; comparing against peers |
| **Crunchbase** | Heat 0–100, Growth 0–100, Growth Prediction | Heat = **change** in attention (engagement, news, financial and organisational signals), not raw volume. Growth = funding, milestones, hiring, **compared with the company's own past, with time decay** | Momentum = change against its own baseline, fading over time |
| **Harmonic** | Growth score, Heat score | 100+ headcount metrics **by department** (engineering vs sales); **stealth founders** (e.g. a senior AI-lab engineer leaves with no announced next job) | Where a company hires matters more than how many it hires; departures as a signal |
| **Specter** | Growth ranking; **Talent signal score 1–10** | Talent score = **Funding, Building, Exit, Background, Fit**. "Interest signals" = **who** is engaging (seniority, relevance) | Founder score split into 5 parts; investor attention as a signal |
| **Tracxn** | Tracxn Score | 4 sub-scores, each a **percentile**: **Size, Execution, Growth, Team** | Every sub-score is a simple percentile |
| **PitchBook** | Opportunity Score 0–100 | Probability of each exit type × expected IRR, turned into a percentile | Outputs a probability, then ranks by percentile |
| **EQT Motherbrain** (internal) | n/a | 50+ sources: funding, hiring, web traffic, product reviews; **company-description embeddings** to measure similarity; **human feedback retrains the model** | "Find companies like this one"; a learning feedback loop |

**What they have in common:** a few sub-scores (team, growth, market, completeness) → each normalised as a percentile against similar companies → combined as a weighted average. Momentum is measured as *change*, not totals. People signals carry the most weight early on.

---

## 2. What we can implement in 21 hours

| # | Idea | Borrowed from | How we do it | Effort |
|---|---|---|---|---|
| 1 | **Percentile scores against similar companies** | Tracxn, Dealroom | Each metric becomes a percentile within the same sector and age group, not compared with all companies | Low |
| 2 | **Show completeness / confidence next to the score** | Dealroom | We already have the coverage %. Show it as a separate "confidence" badge | Low |
| 3 | **"Raise-soon" indicator** ⭐ | Dealroom Timing | For unfunded teams: signals that they're about to raise (GmbH i.G. registered, pricing page live, first "Founding Engineer" job, pitching at events, a "we're raising" post). **HTGF wants to meet teams just before their first round**, so this is a strong pitch point | Medium |
| 4 | **Momentum = change vs own baseline, with time decay** | Crunchbase Heat | Star, download and HN growth compared with the project's own previous snapshot. Old spikes fade | Low |
| 5 | **Stealth-founder proxy without LinkedIn** ⭐ | Harmonic, Specter | Watch GitHub profiles between snapshots: the company field changes to "Stealth", "Building…" or "Founder @ X"; a new org created by people from top labs or universities | Medium |
| 6 | **Hiring mix** | Harmonic | Job ads: a first *sales* hire = commercial traction; *engineering* hires = product build | Low |
| 7 | **Investor-attention signal** ⭐ | Specter interest signals | Who stars or forks the repo: accounts of known VC/angel employees, or founders of funded startups. Creative and cheap | Medium |
| 8 | **Founder score in 5 parts** | Specter | Background, Building (shipping now), Exit (past success), Fit (domain match), plus our research-backed domain expertise | Low (fits our Team group) |
| 9 | **"More like this" search** | EQT (embeddings) | Embed the company descriptions; from the best find, show similar unfunded companies | Low–medium |
| 10 | **Thumbs up/down feedback on lead cards** | EQT feedback loop | Store votes; show that the weights can learn (demo only) | Low |
| 11 | **Validation in Mosaic style** ⭐ | CB Insights | Run our score on the **1,000 YC companies** we're researching: "the top 10% by our score contain X% of YC top companies". A strong number for the jury | Medium (we already have the data) |

**Suggested picks for the hackathon:** 1, 2, 3, 4 and 11 make the core credible; 5 and 7 are the "creative signals" slide.

---

## 3. The 15 sites you suggested (tested from a script, 2026-11-20)

| Site | Script access | How to collect | Value for us |
|---|---|---|---|
| **BetaList** | ✅ 200, plain HTML | Scrape; has region pages (`/regions/germany`) | ⭐ Pre-launch, can be filtered to Germany |
| **Devpost** | ❌ `/software/newest` 403, but ✅ **JSON API** `devpost.com/software/search?query=&page=1` | API | ⭐ Hackathon projects with team members. A creative early signal: *hackathon winners who keep building* |
| **Show HN, new** | ✅ 200 | HN Algolia API (already in our list) | ⭐ Already planned |
| **Dealroom, unfunded companies** | ❌ 403, login + paid | By hand only | Useful as a **check**, not a source |
| **Indie Hackers** | ✅ 200, HTML | Scrape | Mostly bootstrapped solo SaaS; lots of noise for a VC |
| **Peerlist Launchpad** | ✅ 200, data in embedded JSON | Parse `__NEXT_DATA__` | Good, early, global |
| **Launching Next** | ✅ 200, embedded JSON | Scrape | Good, but noisy |
| **OpenVC** | ✅ 200, but the page is a **founder application form**, not a public list | n/a | ❌ No public list of startups raising |
| **Uneed** | ✅ 200, embedded JSON | Scrape | Daily SaaS/AI launches; global and noisy |
| **MicroLaunch** | ✅ 200, embedded JSON | Scrape | Early products; global and noisy |
| **Product Hunt** | ❌ homepage 403, but ✅ **RSS feed** `producthunt.com/feed` (50 items) | RSS (or the API with a token) | Good; use the feed |
| **GitHub Trending** | ✅ 200 | Scrape | Already planned |
| **Reddit r/SideProject** | ❌ JSON 403, RSS 404, page is empty for scripts | OAuth API only | Skip in 21h |
| **Crunchbase** | ❌ 403, paid | By hand | **Funding check only** |
| **Wellfound** | ✅ 200 homepage (deeper pages are anti-bot) | By hand / light scraping | Useful as a check (team size, "raising" tag) |

**Takeaways:**
- **Most launch sites are global and full of solo side projects.** They need the Germany filter (`.de` domain, Impressum, German location) and the noise gates.
- **New useful sources:** the Devpost JSON API, Peerlist, MicroLaunch and the Product Hunt RSS feed.
- **Dealroom, Crunchbase and Wellfound** are for *verifying* funding, not discovering startups.

---

## Sources
- CB Insights: [Mosaic score](https://cbinsights.com/mosaic-score), [Mosaic whitepaper](https://www.cbinsights.com/mosaic-whitepaper), [Mosaic: Moneyball for startups](https://www.cbinsights.com/research/team-blog/mosaic-moneyball-for-startups)
- Dealroom: [Dealroom Signal (knowledge base)](https://knowledge.dealroom.co/knowledge/dealroom-signal-1), [Introducing Dealroom Signal](https://dealroom.co/blog/introducing-dealroom-signal)
- Crunchbase: [Technical overview: Heat Score](https://support.crunchbase.com/hc/en-us/articles/53439100839955), [Technical overview: Growth Score](https://support.crunchbase.com/hc/en-us/articles/53295907409555), [Growth Prediction model](https://support.crunchbase.com/hc/en-us/articles/53539611962003)
- Harmonic: [Tracking startup growth signals](https://harmonic.ai/blog/best-tools-for-tracking-startup-growth-signals), [Finding stealth founders](https://harmonic.ai/blog/how-to-find-track-stealth-startup-founders)
- Specter: [Talent signal score](https://intercom.help/specter-labs/en/articles/14430493-talent-signals-signal-score), [Interest signals](https://intercom.help/specter-labs/en/articles/8504989-what-is-interest-signals), [Company signals](https://tryspecter.com/company-signals)
- Tracxn: [What is Tracxn Score](https://intercom.help/tracxn-technologies-limited/en/articles/6655284-what-is-tracxn-score)
- PitchBook: [Understanding the VC Exit Predictor](https://pitchbook.com/help/understanding-vc-exit-predictor), [Technical documentation (PDF)](https://pitchbook.brightspotcdn.com/19/ec/ad5c586645caae342cc85a49ed22/vc-exit-predictor-technical-documentation-1.pdf)
- EQT Motherbrain: [EQT Motherbrain](https://eqtgroup.com/motherbrain/), [VentureBeat: how Motherbrain finds startups](https://venturebeat.com/business/how-eqt-ventures-motherbrain-uses-ai-to-find-promising-startups)
