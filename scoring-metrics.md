# Scorecard: how we rate unfunded startup leads (HTGF track)

Put together on 2026-11-20 from two independent proposals (GPT-6-Astra and Opus). The final decisions are noted inline. All weights are first guesses until the back-test is run.

## Step 1: Hard gates (a lead must pass these before it gets a score)

### A. Not-yet-funded check

Run the cheapest checks first. Save every check with its source URL, date and query, and note when a source couldn't be reached.

1. **Website, Impressum and team page (Luna):** look for "backed by", "Investoren", investor logos, "pre-seed / seed round", and business-angel or EIC Fund mentions.
2. **Investor portfolio blacklist:** scrape the portfolios of HTGF, First Momentum, LUMO, Antler, Merantix, UnternehmerTUM Funding and the YC directory. A match on name or domain means **funded**.
3. **News search:** search Tech.eu, deutsche-startups.de and EU-Startups for the company name plus *raises / Finanzierungsrunde / seed / Series / "sammelt … ein"*.
4. **Registries:**
   - DE: Bundesanzeiger / Unternehmensregister for a *Kapitalerhöhung*, a new *Gesellschafterliste*, or a fund or corporate shareholder.
   - BE: Belgian Gazette deeds for a capital increase.
5. **Crunchbase / Dealroom (by hand, top 20 only):** any entry that lists a round.

| Status | Meaning | Result |
|---|---|---|
| **FUNDED** | Priced equity (angels included), a VC investment, or a portfolio match | ❌ Excluded |
| **BACKED** | A SAFE or convertible note, or an accelerator that takes equity (YC, Antler, Startupbootcamp) | ❌ Excluded from the ranking; shown in an "already backed" tab |
| **UNKNOWN** | Unclear logo, unspecified round, capital increase, conflicting evidence, or fewer than 3 sources checked | ⏸ Held for human review |
| **GRANT-ONLY** | Only non-dilutive money (EXIST, SPRIND, NWO Take-off, UnternehmerTUM prototyping grant, RVO, EIC grant) | ✅ Passes |
| **CLEAN** | 3 or more independent sources checked, no funding found | ✅ Passes |

> Decision: the two models disagreed on SAFEs and equity-taking accelerators (Opus: show them with a penalty; Astra: exclude them). **We exclude them**, because the brief is "never funded before". "CLEAN" means *no funding found*, not proven unfunded, so the best find also needs a manual Crunchbase / Gesellschafterliste check and, ideally, confirmation from the founders.

### B. Noise gates (Luna classifies; every rejection goes into a visible "thrown out" log with its reason)

- **Hobby:** a personal account, a single contributor, no own domain.
- **Agency or consultancy:** "for clients" wording, a client portfolio, no repeatable product.
- **Student assignment:** "assignment", a course code or "lecture" in the README; a university-owned org.
- **Corporate project:** the Impressum names a large AG or GmbH, or the members use corporate email domains.
- **Open source with no company:** no Impressum, no pricing, no waitlist, no legal entity.
- **Too old:** the first venture trace is more than 3 years ago.
- **Dead:** no substantive activity in 90 days (180 days for a documented deep-tech milestone).
- **Region:** DE / NL / BE / LU only. **The "best find" must be based in Germany** (HTGF's mandate). Benelux leads are shown with a "needs a German entity" flag.

### C. Anti-gaming

- **GitHub:**
  - Flag it if more than 40% of all stars arrived within 48h (use the API's `starred_at`).
  - Sample about 100 stargazers and flag it if more than 30% are accounts under 30 days old with no repos and no followers.
  - Flag it if stars/forks is above 50.
- **Show HN:** flag a very high points-to-comments ratio, or a brand-new submitter account.
- **Hugging Face:** flag download counts that are outliers compared with likes and activity.
- **Effect:** a flag sets the momentum metric to zero and puts a badge on the card. Going viral alone is not a flag.

## Step 2: Score (0–100)

Each metric is scored from 0 to 1, and Score = Σ(weight × metric). **Missing data scores 0 and is labelled "missing"**; the weight is not shared out to the other metrics. Each lead also gets a **coverage %**: the share of the total weight that has usable evidence. Leads below 70% coverage stay in research instead of the ranking.

| Group | Metric | How to compute | Source | Who | Weight |
|---|---|---|---|---|---:|
| **Team (25)** | Relevant capability | Founder has done relevant technical or domain work (papers, repos, prior products, PhD in the field). Prior exits add to it. Prestige or h-index alone counts for nothing | OpenAlex/arXiv, GitHub, website | Luna + human | 15 |
| | Execution continuity | Distinct weeks with substantive commits or model updates in the last 8 weeks ÷ 8; bots, forks and cosmetic commits excluded. Core team of 2–8 is ideal | GitHub, HF | Auto | 10 |
| **Product / tech (20)** | Technical substance | 0 = claim only; 0.5 = working artifact you can inspect; 1 = artifact plus evidence it beats an alternative (benchmark, paper, patent) | Repo, model, arXiv, EPO | Luna + human | 15 |
| | Product readiness | Live app, docs, first TLS certificate, waitlist (additive) | crt.sh, website | Auto | 5 |
| **Commercial intent (10)** | Company signals | Own domain + Impressum with named founders + legal entity + pricing or pilot offer + open roles (additive) | Registry, website, job APIs | Auto + Luna | 10 |
| **Market (10)** | Buyer and problem clarity | A specific paying buyer, plus a concrete, costly problem. No invented market-size numbers | Website, HN discussion | Luna | 10 |
| **Traction (15)** | Momentum | Star velocity (from snapshots at least 14 days apart), Show HN points + 2×comments, and HF download growth. Log scale, percentile within the same age cohort | GitHub, HN, HF | Auto | 10 |
| | Customers or pilots | min(number of verified customer or pilot organisations, 2) ÷ 2; logos alone don't count | Website, case studies | Luna + human | 5 |
| **HTGF fit (10)** | Thesis match | AI infrastructure, deep tech or industrial tech, with a repeatable product rather than custom work | Website, artifacts, registry purpose | Luna | 5 |
| | Programme validation | Incubator, grant or competition selection, weighted by how selective it is (EXIST/SPRIND > open accelerators) | Programme lists | Auto | 5 |
| **Earliness (10)** | Venture age | Age from the earliest *verified venture-specific* date. Peaks at 3–18 months and falls to 0 at 36 months. Only counts with recent activity | Registry, cohort year, dated site | Auto | 10 |
| | | | | | **100** |

> Decisions where the models disagreed:
> - **Momentum weight.** Opus wanted 20, Astra 5. **We use 10.** Stars are easy to fake and favour developer tools over hardware, but they are still one of our few automatic signals.
> - **Prestige and h-index.** Opus wanted to score them; Astra said to ignore them. **We score relevant work, not titles**, and show h-index on the card as information only.
> - **Bonus for not being in startup databases.** Opus wanted a bonus; Astra said no. **No points**, because being absent can also just mean obscure or noise. We show it as a badge.
> - **Added from Astra:** the coverage %, a customers/pilots metric, and missing data scoring 0.

## Step 3: Lead card (what the jury sees for each lead)

- Name, domain, city/region, sector tag, first-seen date and the source that found it.
- Total score, a bar per group, and the coverage %.
- **Funding status badge** with the checklist of sources checked, each with a link and timestamp.
- Top 3 signals, each with an evidence link (star chart, HN thread, registry entry, paper, HF model).
- Team: founders, roles, relevant background, contributor count.
- Noise-classifier verdict and any anti-gaming flags.
- 3 lines on "why HTGF, why now", written by Sol **from the cited evidence only**.
- What is still unknown, and the next thing to verify.

## Step 4: Check the scorecard works (about 3–4h of the 21)

1. **Funding-check recall:** run the funding check on 20 companies known to be funded (from the HTGF and First Momentum portfolios). **Every one must come out FUNDED or BACKED, with zero leaks.**
2. **Back-test:** for about 10–30 portfolio companies with a known first-round date, rebuild their signals as of 3 months *before* that round (`starred_at`, Wayback, crt.sh and registry dates). Mix them with about 100 random candidates from the same sources. Check that they rank in the top quartile, and compare against a stars-only baseline.
3. **Hand-label:** two teammates each label 40–100 random candidates as venture or noise. Report noise-filter precision and recall, and precision@10 of the ranking (target ≥ 80%).
4. **Sensitivity:** change each weight by ±30%. The top 10 should mostly stay the same.

Show these numbers on the pitch slide: precision@10, funded leakage (0), unknown rate, and cost per lead.

## Top risks

1. **False CLEAN.** Paywalls, limits on scraping Handelsregister and unannounced angel money can hide a round. Mitigation: manual checks plus founder confirmation for the best find.
2. **Source bias.** Hardware and deep-tech teams leave few GitHub traces. Mitigation: lean on grant, incubator, paper and patent sources for them.
3. **Wrong merges and LLM mistakes.** The engine could tie the wrong repo or person to a company, or hallucinate a founder's background. Mitigation: evidence links are mandatory and anything unverified is labelled.
