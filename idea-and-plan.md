# Idea & build plan: HTGF "Find the Founders Before Everyone Else"

## What HTGF asks for (from the track PDF)

**Build a sourcing engine that spots startups from the traces they leave long before their first funding round.**

| Judged on | What it means for us |
|---|---|
| 1. Lead quality | Would HTGF actually want to talk to them? → strict noise filter, team focus |
| 2. **Earliness** | How early compared with common databases? → **measure it in days** |
| 3. **Creativity of signals** | Signals VCs would not think of → our differentiator |
| 4. Technical execution | Does it work in a demo? |
| 5. Scalability | Can it run continuously and add more sources? |

**Deliverables:** a working demo that finds real leads, plus **"our best find"** (one startup, its signals, and why HTGF should look closer).
**Their tips:** filter the noise · look at the team · **focus on Germany (70% of their deals; rest of Europe secondary)** · start small with a few sources.

---

## The idea: **"Vorsprung"** (German for "head start")

> **An engine that watches for the moment a project turns into a company, and tells HTGF about it before anyone else does.**

Most VC tools wait for funding news. We look for the *transition signals*: a side project gets an Impressum, a repo gets a company domain, a research team pitches at a demo day, a GmbH "in Gründung" appears. A lead fires when **several independent weak signals point to the same team** (we call it *signal convergence*).

### Why it should win each criterion

| Criterion | Our answer |
|---|---|
| Lead quality | Hard gates first (not yet funded, not noise, based in Germany), then the scorecard (`scoring-metrics.md`), with team weighted highest |
| Earliness | For every lead we show **"first seen by Vorsprung" vs "first seen in databases or press"** = lead time in days. Back-tested on real portfolio companies |
| Creativity | German-specific "company is being born" signals (below) |
| Technical execution | Live dashboard plus a "Refresh" button that pulls new traces |
| Scalability | Every source is a plugin with the same interface, run on a schedule; adding a source is about 50 lines of code |

### Signals VCs would not think of (our "creativity" slide)

1. **Impressum appears.** German law requires an Impressum on commercial sites. The day a project site gets one (Wayback diff), a hobby has become a business. *Germany-only, and very hard to copy.*
2. **"UG (haftungsbeschränkt)" or "GmbH i.G." in the Impressum.** "i.G." means *in Gründung*: the company is literally being founded right now.
3. **Pitch-event agendas** (Luma, Eventbrite, university demo days). Teams pitching at "Startup Pitch Night" are asking for investors. *Tested: Eventbrite Berlin lists pitch nights as JSON.*
4. **University email domains in GitHub commits** (tum.de, kit.edu, rwth-aachen.de). A research spin-off is forming.
5. **First TLS certificate for `app.` or `api.` subdomains** (crt.sh). The product is going live.
6. **"Founding Engineer" or first job posts from companies nobody has heard of** (Arbeitnow / Bundesagentur APIs).
7. **EXIST grant and incubator cohort lists.** Public money, no equity. The perfect "not funded yet" pool.
8. **Pricing page or waitlist appears** (Wayback diff). Commercial intent switched on.

---

## Scope (their tip: start small, prove it works)

**Focus area:** AI and deep-tech teams coming out of **German universities and tech hubs** (Munich, Berlin, Karlsruhe, Aachen, Darmstadt).

| Layer | Sources (MVP) | Later (scalability slide) |
|---|---|---|
| Discovery | GitHub orgs (location DE, created in the last 18 months) · Show HN · **incubator, EXIST and grant lists** (UnternehmerTUM grants, TUM Venture Labs, KIT, Startup Incubator Berlin) · **Luma / Eventbrite pitch events** | Hugging Face, arXiv/OpenAlex, Handelsregister, Product Hunt |
| Enrichment | Company website (Impressum, team, pricing, investor logos) · Wayback first capture · crt.sh · GitHub contributors | Patents, job APIs, LinkedIn by hand |
| Funding check | Website text · investor portfolios (HTGF, First Momentum, Antler, YC…) · news RSS (deutsche-startups, Tech.eu) · Crunchbase by hand for the top 20 | Registry capital increases |

---

## How it works (pipeline)

```
[Collectors] → raw_items (SQLite, raw JSON kept)
   → [Entity resolution: website domain first, then cross-links, then shared people]
   → [Website crawler: Impressum, team, pricing, investor logos]
   → [Gates: funding check · noise filter (Luna) · Germany · anti-gaming]
   → [Scorecard 0–100 + coverage %]
   → [Earliness: first-seen vs database/press date]
   → [Dashboard: ranked lead cards with evidence links]
```

**Stack (fast to build):** Python · httpx · SQLite · Streamlit dashboard · OpenAI **Luna** (bulk classification) / **Sol** (lead summaries) · cron or n8n for scheduled runs.
**Data strategy:** collect overnight, **freeze the snapshot at 08:00**; the demo runs on the snapshot, and "Refresh" is optional live data that falls back to the snapshot.

---

## Team roles (4 people)

| Role | Owns |
|---|---|
| **Lead / integrator** | Repo, schema, entity resolution, the end-to-end run, submission |
| **Collectors** | GitHub, Show HN, incubator, EXIST and event scrapers, website crawler |
| **Scoring & AI** | Gates, Luna noise filter, funding check, scorecard, earliness calculation, cost tracking |
| **Dashboard & pitch** | Streamlit lead cards, back-test, **best-find research**, demo video, pitch |

(With 3 people, merge "Scoring" into "Lead". Everyone helps check the best find by hand overnight.)

---

## 21-hour timeline

| Time | Milestone |
|---|---|
| **Fri 13:30–14:30** | Lock the idea and scope, create the repo skeleton (`collectors/`, `pipeline/`, `app/`), agree on the schema, get API keys |
| **18:00** | **Thin slice:** GitHub + one incubator list → SQLite → domain merge → website/Impressum crawl → simple score → Streamlit list |
| **23:00** | All 4 discovery sources live, Luna noise filter, funding check, full scorecard; shortlist of 30. **Start checking the best find by hand** |
| **03:00** | Earliness calculation and back-test (10 portfolio companies: how many days earlier we would have caught them), anti-gaming, lead cards polished, second star snapshot |
| **08:00** | **Feature freeze + data freeze.** Pick the best find and 2 backups, each with an evidence pack |
| **09:30** | 3-minute pitch video recorded |
| **10:15** | Submitted on BuilderBase (`git tag demo-final`), README with how to run it, which models where, and cost |

---

## Demo script (3 minutes)

1. **0:00–0:20:** "Once a startup is in Crunchbase, every investor calls. We find them while they're still writing their Impressum."
2. **0:20–1:30:** Dashboard: ranked German leads → open one card → signals with evidence links, team, funding badge ("CLEAN: 4 sources checked"), **lead time: seen 143 days before any database**. Then show the "thrown out" tab (hobby, agency, student project, already funded).
3. **1:30–2:15:** **Best find:** who they are, the 3 signals, why HTGF should call this week.
4. **2:15–2:40:** Back-test numbers (precision@10, median lead time, 0 funded leaks) and cost per lead. Then the "add a source in 50 lines" scalability slide.
5. **2:40–3:00:** Team, and what's next (more sources, a weekly digest for the HTGF sourcing team).

---

## Do before kickoff
- [ ] Everyone registered on BuilderBase; team joined
- [ ] Confirm with HTGF: does "best find" need to be contacted or verified? Is Benelux okay as a secondary focus?
- [ ] One free **GitHub token** per teammate (search limit is 10/min without one, 30/min with one)
- [ ] OpenAI key from BuilderBase › Resources
- [ ] Decide the stack (suggested above) so the repo skeleton can go up at 13:30
- [ ] Read `startup-sources.md`, `scoring-metrics.md` and `yc-research.md`
