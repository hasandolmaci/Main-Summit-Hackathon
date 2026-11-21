# Backtest dataset spec: learning the metric weights

**Who does what:** Person A (you) collects the data · Person B (your friend) fits and tests the weights.
Written 2026-11-20 from two independent proposals (GPT-6-Astra via Codex, and Opus). The final decisions are marked ✅.

---

## 1. What we're predicting (labels)

✅ **Target: "raises a first institutional round within 18 months of being discovered."** This measures how likely a startup is to get funded, not how good an investment it is. Say so in the pitch.

| Label | Definition |
|---|---|
| **Positive (y=1)** | German startup that passed the gates at its as-of date and got its **first institutional investment** within 18 months after it: a VC, CVC or angel fund, **or an accelerator that takes equity** (YC, Antler…), whether as a priced round, SAFE or convertible note. Angels totalling ≤ €150k before that are allowed |
| **Negative (y=0)** | Same cohort list (same incubator, grant, competition or Show HN year, or same GitHub-org quarter), **with no institutional round within 18 months**. Every funding-check step must have run (CLEAN or GRANT-ONLY) |
| **Excluded** | Funding status UNKNOWN, unclear identity or unclear round date. Keep these rows but leave them out of training |
| **Extra flag** | `y_dead` = closed within 18 months. It still counts as negative, but B can check whether the model ranks quiet companies or dead ones |

### ✅ As-of date (the most important rule)
- **as-of date = the date the company first appears in one of our discovery sources** (cohort list, grant list, Show HN post, GitHub org creation…). The same rule applies to positives and negatives.
- ❌ **Don't** use "6 months before the round". That picks the date using knowledge of the outcome, which is leakage. (Opus suggested it; Astra warned against it. Astra's rule wins.)
- **Every feature must be built from evidence that existed before the as-of date:**
  - Wayback snapshots from before that date
  - GitHub `starred_at` and commits from before it
  - HN `created_at`, register entries, cohort lists and patents from before it
- **Never use a page as it looks today.**
- **Maturity cutoff:** as-of date **≤ 2025-05-20**, so every row has a full 18 months of follow-up by today. Run 24 months as a sensitivity check.
- **Survivorship:** rebuild the cohort lists from **Wayback copies of the incubator and grant pages from that year**, so companies that later died are still included.
- **Investor portfolios are used only to create labels, never to find candidates.** If you add extra positives found through funding news, flag them `meta_sampling=news_boost` so B can test without them.

---

## 2. How much data

| Tier | Positives | Negatives | Ratio | Features it supports |
|---|---|---|---|---|
| **Minimum (realistic target)** ✅ | **120–150** | **400–500** | ~1:3 | ~10–12, with strong pull toward the prior weights |
| Good | 250 | 750–1,000 | 1:3–1:4 | 15–20 |
| Ideal | 500+ | 2,000 | ~1:4 | 25, stable across folds |

- **Why:** penalised logistic regression needs roughly **10 positive examples per feature**.
  - Each time fold needs **at least 30–40 positives in its test window**, or the AUC uncertainty is about ±0.08 or worse.
  - precision@20 has a standard error of about ±0.11, so report it averaged over folds plus a bootstrap.
- **The two models disagreed on the minimum:** Astra said 300+/600−, Opus 100+/300−. ✅ **We target 120–150 / 400–500**, because manual label checks take about 3–5 minutes per company. **Collect before the hackathon.** During the 21 hours, only the automated feature extraction should run.
- **Present the result honestly:** "learned **corrections** to the research-based weights", reported as signs plus confidence intervals, not exact numbers.

---

## 3. What to collect (schema)

**One row = one company at one as-of date.** Column prefixes: `id_`, `meta_`, `f_` (features), `y_` (labels). **Unknown = empty (NaN), never 0**, and each feature group gets a `_missing` flag.

### Identity and provenance
| Column | Meaning | Source |
|---|---|---|
| `id_company`, `id_domain`, `id_legal_name`, `id_hrb`, `id_city` | Identity; match on domain first | Impressum, Handelsregister |
| `meta_cohort_source`, `meta_asof_date` | Where and when we first found it | Cohort, grant or HN lists (Wayback) |
| `meta_sampling` | `cohort` or `news_boost` | |

### ✅ Core features (the minimum tier: ~12, all reconstructable as of the past)
| Column | Meaning | Source | Expected missing |
|---|---|---|---|
| `f_age_months` | Founding to as-of date | Register date / first Wayback capture / crt.sh | ~10% |
| `f_impressum`, `f_legal_entity`, `f_gmbh_ig` | Commercial intent | Wayback snapshot before the as-of date | ~25% |
| `f_pricing_page`, `f_product_live` | Product readiness (pricing; `app.`/`api.` certificate) | Wayback, crt.sh | ~40% |
| `f_programme_count`, `f_grant_exist` | Programme validation | Dated cohort and grant lists | ~0% |
| `f_uni_spinoff` | University origin | Incubator lists, university commit emails | low |
| `f_deep_tech`, `f_b2b`, `f_regulated_hw` | Company type (supported by the YC data) | LLM reading the website text from **before** the as-of date | ~10% |
| `f_gh_star_growth_90d`, `f_gh_commit_weeks_active` | Momentum vs own baseline; execution continuity | GitHub `starred_at`, commits | ~50% (no repo) |
| `f_hn_launch` | Show HN post before the as-of date | HN Algolia | ~60% |
| `f_description_text` | Website text before the as-of date (for similarity) | Wayback | ~20% |

### Optional features (good tier, if time allows)
`f_fake_star_share` · `f_launch_platforms` · `f_customer_logos` · `f_patents` (EPO, priority date before the as-of date) · `f_hf_models` · `f_n_founders` · `f_founder_industry_exp` · `f_founder_prior_exit` · `f_founder_technical`

### Labels
`y_label` (1/0/NaN) · `y_round_date` · `y_round_type` (vc / angel_fund / accelerator_equity / safe…) · `y_amount_eur` · `y_dead` · `y_database_first_seen` (for measuring head start)

### Evidence (separate file)
`evidence.csv`: `id_company, meta_asof_date, field, value, url, quote, snapshot_ts, retrieved_at, missing_reason`. Every value has to be traceable.

---

## 4. ❌ Leave these out of the backtest (can't be reconstructed, or would leak)
- **Only exists as of today:** headcount, HQ, HN votes, Hugging Face downloads, open roles / hiring mix, investor attention, online attention, the stealth-founder signal, experimentation tools (BuiltWith history is paid), market health.
- **Leaky or circular:** raise-soon (that *is* the label), head start vs databases (an evaluation metric, not an input).
- **Too sparse for now:** complementary team, founder commitment, founder skills. Defer founder features to the optional tier; our YC study showed 40–90% unknown and no clear signal.
- **Not a weight:** thesis match belongs in eligibility and segmentation.
- **Keep only with care:** similarity to past winners. B must compute it **inside each training fold only**.

---

## 5. LinkedIn via Apify: optional, small and careful
- ✅ **Recommendation:** don't build the core dataset on it. At most, a **small optional enrichment run**.
- **Who and what:** **founders only** (at most 3 per company). Only their employer, title and dates *before the as-of date*, plus degree subject and end year. Turn that into 4 yes/no values (industry experience, prior founder/exit, technical degree, years of experience), then **delete the raw JSON**.
- **Never:** employees, photos, emails, contacting anyone, or "prestige" fields. **Never use actors that need your LinkedIn login cookie.**
- **Cost:** about $1.40–4 per 1,000 profiles, so about 1,500 founders plus 700 company pages comes to **under $20**.
- **Risks:**
  - **Scraping breaks LinkedIn's terms**, even when done through Apify without a login.
  - **Founder data is personal data under GDPR:** you need a written purpose, minimal fields, local storage, pseudonymised founder IDs and **deletion within 30 days**.
  - Ask the organisers whether that's acceptable.
- **LinkedIn's current headcount** reflects today, so it **leaks**. Use it only to confirm identity.
- **Fallback without LinkedIn:** Wayback team and about pages, GitHub profiles (bio, account age, commits), Handelsregister managing directors, press quotes.

---

## 6. Handoff to B
- **Files:**
  - `backtest.csv` (UTF-8; Parquet optional), one row per (`id_company`, `meta_asof_date`)
  - `evidence.csv`
  - `data_dictionary.csv` (column, type, unit, source, reconstructable, how it's computed as of the date, missing %)
- **Split:**
  - **Rolling by as-of year** (e.g. train ≤2022, test 2023; train ≤2023, test 2024; then 2025-H1).
  - A company never appears in both train and test.
  - Tune hyperparameters on an inner time split.
  - Do imputation, scaling and kNN similarity **inside each training fold**.
- **Model:** **regularised logistic regression**, with ridge pulled toward the prior weights, or w = λ·learned + (1−λ)·prior with λ picked on validation. Force positive weights where it makes sense.
- **Baselines to beat:** random · stars only · programme validation only · **prior-weights scorecard**.
- **Metrics:**
  - ROC-AUC and PR-AUC, with bootstrap confidence intervals
  - precision@20 and @50, and funded recall in the top 10%, measured on a test cohort at the **natural** rate (no resampling)
  - Brier score / calibration
  - **median head start** = `y_database_first_seen − meta_asof_date`
- **Report:** weight signs and confidence intervals, how stable the weights are across folds, and coverage. Use the learned weights only if they beat the prior scorecard.

---

## 7. Top risks
1. **Noisy negatives:** CLEAN only means "nothing found", so some negatives were quietly funded. This pushes the weights toward zero.
2. **Leakage and survivorship:** using today's pages or today's lists. The as-of rule and the Wayback cohorts prevent it.
3. **Too few rows:** weights will be unstable. Report signs and confidence intervals, and blend with the priors.

## Where the two models disagreed, and what was chosen
| Topic | Astra | Opus | ✅ Decision |
|---|---|---|---|
| As-of date | First appearance, the same for everyone | Round date − 6 months | **First appearance** (avoids leakage) |
| Accelerator equity | Positive | Half-weight label (0.5) | **Positive**, with the round type recorded |
| Follow-up window | 18 months | 24 months | **18 months**, with 24 as a sensitivity check |
| Minimum sample | 300 / 600 | 100 / 300 | **120–150 / 400–500** (realistic for 2 people) |
| Founder features | Drop or defer | Keep, with missing flags | **Optional tier only** |

Also check before collecting: GitHub may restrict listing stargazers for some repos, and the `starred_at` history doesn't include stars that were later removed. Test it on a few repos first.
