# Startup sources: HTGF startup finder (DE / NL / BE / LU, AI and deep tech)

Researched on 2026-11-20 by GPT-6-Astra (programmes, grants, competitions) and Opus (code, launches, research, registries, directories, news), then merged. Every link was tested from a script on the same day: ✅ loads, ⛔ blocks scripts (opens fine in a browser), ⚠️ other issue.

**Access key:** API = official or JSON endpoint · RSS = feed · Scrape = public HTML · Manual = do it by hand · Paid = needs payment or login
**Earliness:** 🟢 very early · 🟡 early · 🔴 later (probably already known to investors)

---

## Start with these (best for a 21-hour build)

| # | Source | Why | Access |
|---|---|---|---|
| 1 | [GitHub search API](https://docs.github.com/en/search-github/searching-on-github/searching-users) | New orgs by location and creation date; contributors show the team | API (free token) |
| 2 | [Show HN via HN Algolia](https://hn.algolia.com/api) | Every launch, with URL and author; no key needed | API |
| 3 | [UnternehmerTUM prototyping grants](https://funding.unternehmertum.de/supportedteams-2/) | Named prototype-stage deep-tech teams, with batch year and domain | Scrape |
| 4 | [KVK Open Data](https://www.kvk.nl/producten-bestellen/kvk-handelsregister-open-data-set/) + [KBO Open Data](https://kbopub.economie.fgov.be/kbo-open-data) | Free daily company registrations for NL and BE; the earliest official trace | Bulk download |
| 5 | [Hugging Face Hub API](https://huggingface.co/docs/hub/api) | AI orgs with real models; use it to add detail to companies found elsewhere | API |
| 6 | [HighTechXL](https://www.hightechxl.com/portfolio/) / [imec.istart](https://www.imecistart.com/portfolio) | Deep-tech ventures with stage, cohort and AI tags | Scrape |
| 7 | [crt.sh](https://crt.sh/) + [Arbeitnow jobs API](https://www.arbeitnow.com/api/job-board-api) | Enrichment: a first TLS certificate means a product is going live; first ML hires mean the team is growing | API |

---

## 1. University incubators and spin-off programmes

| Name | Link | What you get | Access | Country | Early |
|---|---|---|---|---|---|
| UnternehmerTUM prototyping grants | [link](https://funding.unternehmertum.de/supportedteams-2/) ✅ | Prototype-stage teams, batch, domain | Scrape | DE | 🟢 |
| TUM Venture Labs: Software & AI | [link](https://www.tum-venture-labs.de/labs/software-ai/) ✅ | AI/software ventures (includes funded alumni) | Scrape | DE | 🔴 |
| TUM Start-up Wall | [link](https://www.tum.de/en/innovation/entrepreneurship/for-alumni-corporates/tum-start-up-wall) ✅ | TUM spin-offs | Scrape | DE | 🟡 |
| KIT-Gründerschmiede | [link](https://kit-gruenderschmiede.de/gruendungen/) ✅ | Teams by sector and phase | Scrape | DE | 🟡 |
| TU Berlin Centre for Entrepreneurship | [link](https://www.tu.berlin/entrepreneurship/startup-support/tu-startups) ✅ | Ventures by year and sector | Scrape | DE | 🟡 |
| Strascheg Center (HM Munich) | [link](https://www.sce.de/en/start-ups/currently-supported-start-ups.html) ✅ | Currently supported teams | Scrape | DE | 🟢 |
| Startup Incubator Berlin (HWR) | [link](https://www.startup-incubator.berlin/startups/) ✅ | Filter by programme, cohort, sector | Scrape | DE | 🟢 |
| Uni Kassel UNIKAT | [link](https://www.uni-kassel.de/einrichtung/ukt/unikat-von-der-idee-zur-gruendung/gruendungsprojekte.html) ✅ | EXIST-funded projects, with dates | Scrape | DE | 🟢 |
| dresden\|exists | [link](https://www.dresden-exists.de/jobs/) ✅ | Projects looking for co-founders | Scrape | DE | 🟢 |
| UtrechtInc | [link](https://utrechtinc.nl/community/startups/) ✅ | Current AI and science teams | Scrape | NL | 🟡 |
| KU Leuven R&D spin-offs | [link](https://lrd.kuleuven.be/en/spinoff/spin-off-companies) ✅ | Research spin-offs, with websites | Scrape | BE | 🟡 |
| Ghent University TechTransfer | [link](https://techtransfer.ugent.be/en/overview-spin-companies-ghent-university) ✅ | Spin-off list (watch for new entries) | Scrape | BE | 🟡 |
| UGent/imec IDLab spin-offs | [link](https://idlab.ugent.be/spin-offs) ✅ | AI and data infrastructure spin-offs | Scrape | BE | 🔴 |
| University of Antwerp spin-offs | [link](https://www.uantwerpen.be/en/research/info-for-companies/spin-offs/overview-spin-offs/) ✅ | Spin-offs labelled by year | Scrape | BE | 🟡 |
| University of Luxembourg incubator | [link](https://www.uni.lu/life-en/incubator-and-entrepreneurship-programme/our-startups/) ⚠️ bot check | Current incubator teams | Manual | LU | 🟡 |

## 2. Accelerators and venture builders

| Name | Link | What you get | Access | Country | Early |
|---|---|---|---|---|---|
| HighTechXL | [link](https://www.hightechxl.com/portfolio/) ✅ | Deep-tech ventures, stage filters | Scrape | NL | 🟢 |
| imec.istart | [link](https://www.imecistart.com/portfolio) ✅ | Cohort, country, AI tags | Scrape | BE/NL/DE | 🟡 |
| CyberLab Karlsruhe | [link](https://www.cyberlab-karlsruhe.de/en/our-startups) ✅ | AI, security, quantum teams | Scrape | DE | 🟡 |
| SpinLab Leipzig | [link](https://www.spinlab.co/portfolio/all) ✅ | Dated startup profiles | Scrape | DE | 🟡 |
| Startupbootcamp | [link](https://startupbootcamp.org/startups/portfolio-companies) ✅ | Cohort, country, year filters | Scrape | NL/DE | 🟡 |
| Start it @KBC | [link](https://startit-x.com/en/accelerate/all-startups) ✅ | Belgian accelerator teams | Scrape | BE | 🟡 |
| Fit 4 Start (Luxinnovation) | [link](https://luxinnovation.lu/fr-lu/assess-and-accelerate/programme-acceleration-fit-4-start/fit-4-start-14/fit-4-start-14-selected-startups) ✅ | Selected startups (the page shows the current #17 list) | Scrape | LU | 🟢 |
| Luxembourg City Incubator | [link](https://cityincubator.lu/startups/) ✅ | Residents, **CSV export** | CSV / Scrape | LU | 🟡 |
| RWTH Innovation, Spring 2026 cohort | [link](https://www.linkedin.com/posts/rwthinnovation_rwth-incubation-program-spring-batch-2026-activity-7439279704928460800-onCK) ✅ | 12 named teams (a LinkedIn post) | Manual | DE | 🟢 |
| YC Startup Directory (Europe) | [link](https://www.ycombinator.com/companies?regions=Europe) ✅ | Filter by region and batch | Scrape (JSON) | multi | 🟡 |

## 3. Pitch competitions and awards

| Name | Link | What you get | Access | Country | Early |
|---|---|---|---|---|---|
| BayStartUP Munich, Phase 1 2026 | [link](https://www.baystartup.de/startupdate/sieger-startups-aus-phase-1-im-muenchener-businessplan-wettbewerb-2026) ✅ | 10 idea-stage winners (robotics, quantum) | Scrape | DE | 🟢 |
| Science4Life concept phase 2026 | [link](https://www.technologieland-hessen.de/news/Science4Life-praemiert-die-besten-Geschaeftskonzepte-2026) ✅ | Concept-stage life-science and energy teams | Scrape | DE | 🟢 |
| Falling Walls Venture | [link](https://falling-walls.com/activities/venture/finalists) ✅ | Science startup finalists by year | Scrape | DE/NL/BE | 🟡 |
| Gründungswettbewerb Digitale Innovationen | [link](https://www.bundeswirtschaftsministerium.de/Redaktion/DE/Dossier/Gruenderwettbewerb/Artikel/preistraegerinnen-und-preistraeger.html) ✅ | Winner archive (programme paused) | Manual | DE | 🟢 |

## 4. Public grants and funding lists

| Name | Link | What you get | Access | Country | Early |
|---|---|---|---|---|---|
| SPRIND | [link](https://www.sprind.org/en/actions/projects) ✅ | Deep-tech/AI projects | Scrape (dynamic) | DE | 🟢 |
| CORDIS Horizon Europe dataset | [link](https://data.europa.eu/data/datasets/cordis-eu-research-projects-under-horizon-europe-2021-2027?locale=en) ✅ | Projects and participants (filter SMEs) | Bulk CSV/JSON | EU | 🟡 |
| EIC Accelerator 2026 selection | [link](https://webgate.ec.europa.eu/circabc-ewpp/d/d/workspace/SpacesStore/20c01cdb-3f50-40ae-afd6-f75589e3e0af/download) ✅ | Company, technology, website, country | PDF | EU | 🔴 |
| Förderkatalog (FÖKAT) | [link](https://foerderportal.bund.de/foekat/jsp/SucheAction.do?actionMode=searchmask) ✅ | Federal R&D grant recipients (search for EXIST) | Manual | DE | 🟡 |
| NWO Take-off 2025 | [link](https://www.nwo.nl/onderzoeksprogrammas/take-off/toewijzingen-take-off/2025) ✅ | Early commercialisation awards | Manual | NL | 🟢 |
| RVO project database | [link](https://projecten.rvo.nl/nl) ✅ | Filter Vroegefasefinanciering / MIT / Innovatiekrediet | Scrape | NL | 🟡 |

## 5. Pre-seed investor portfolios (useful for training "what a good lead looks like")

| Name | Link | Access | Country |
|---|---|---|---|
| First Momentum Ventures | [link](https://www.firstmomentum.vc/portfolio) ✅ | Scrape | DE/NL |
| UnternehmerTUM Funding for Innovators | [link](https://funding.unternehmertum.de/portfolio) ✅ | Scrape | DE |
| LUMO Labs | [link](https://lumofunds.vc/portfolio) ✅ | Scrape | NL/DE/BE |
| Antler | [link](https://www.antler.co/portfolio) ✅ | Scrape | DE/NL |
| Merantix Capital | [link](https://www.merantix-capital.com/portfolio) ✅ | Scrape | DE |

## 6. Company registries and official gazettes

| Name | Link | What you get | Access | Country | Early |
|---|---|---|---|---|---|
| Handelsregister | [link](https://www.handelsregister.de) ✅ | New GmbH/UG; filter the business purpose for KI/Software/Robotik | Scrape (no API) | DE | 🟢 |
| bundesAPI handelsregister client | [link](https://github.com/bundesAPI/handelsregister) ✅ | Python client for the register | Unofficial API | DE | 🟢 |
| Unternehmensregister | [link](https://www.unternehmensregister.de/en) ✅ | Filings, announcements | Scrape | DE | 🟡 |
| Bundesanzeiger | [link](https://www.bundesanzeiger.de/pub/en/start?0=null) ✅ | Company announcements and accounts | Scrape | DE | 🟡 |
| OffeneRegister | [link](https://offeneregister.de) ✅ | Bulk DE companies (old, ~2018) | Bulk | DE | 🔴 |
| KVK Open Data | [link](https://www.kvk.nl/producten-bestellen/kvk-handelsregister-open-data-set/) ✅ | Daily BV/NV data (SBI code, start date) | Bulk / API | NL | 🟢 |
| KVK developer APIs | [link](https://developers.kvk.nl/documentation) ✅ | New-registration signals | Paid (cheap) | NL | 🟢 |
| KBO/BCE Open Data | [link](https://kbopub.economie.fgov.be/kbo-open-data) ✅ | Full BE company CSV (NACE codes, start date) | Bulk (free signup) | BE | 🟢 |
| Belgian Official Gazette annexes | [link](https://ejustice.just.fgov.be/cgi_tsv/tsv.pl) ✅ | Deeds of incorporation | Scrape | BE | 🟢 |
| Luxembourg Business Registers | [link](https://www.lbr.lu) ✅ | RCS + RESA incorporations | Manual | LU | 🟢 |
| North Data | [link](https://github.com/northdata/api/) ✅ | Company graph across EU registers | Paid | multi | 🟢 |
| OpenCorporates | [link](https://opencorporates.com/pricing/) ✅ | Global registers | Paid | multi | 🟡 |
| startupdetector | [link](https://www.startupdetector.de/neue-startups/) ✅ | New DE startups from the register, weekly | Paid (trial) | DE | 🟢 |

## 7. Code and ML platforms

| Name | Link | What you get | Access | Early |
|---|---|---|---|---|
| GitHub search API | [link](https://docs.github.com/en/search-github/searching-on-github/searching-users) ✅ | New orgs by location; repos by topic | API | 🟢 |
| GitHub Trending | [link](https://github.com/trending) ✅ | Fast-rising repos | Scrape | 🟢 |
| GitLab Explore | [link](https://gitlab.com/explore/projects) ✅ | Public projects | API | 🟢 |
| Hugging Face Hub API | [link](https://huggingface.co/docs/hub/api) ✅ | Orgs, models, datasets | API | 🟢 |
| HF Trending / Daily Papers | [link](https://huggingface.co/papers/trending) ✅ | Replaces Papers with Code (shut down 2025) | API | 🟡 |
| PyPI newest packages | [link](https://pypi.org/rss/packages.xml) ✅ | New Python SDKs | RSS | 🟢 |
| npm registry search | [link](https://registry.npmjs.org/-/v1/search?text=keywords:llm) ✅ | New JS SDKs | API | 🟢 |
| Docker Hub | [link](https://hub.docker.com/search) ✅ | New org images | Scrape | 🟢 |

## 8. Launch platforms

| Name | Link | Access | Early |
|---|---|---|---|
| Show HN (Algolia API) | [link](https://hn.algolia.com/api) ✅ | API, no key | 🟢 |
| Show HN newest | [link](https://news.ycombinator.com/shownew) ✅ | Scrape | 🟢 |
| Product Hunt API | [link](https://api.producthunt.com/v2/docs) ✅ | API (token, non-commercial) | 🟢 |
| BetaList Germany | [link](https://betalist.com/regions/germany) ✅ | Scrape (also /regions/netherlands) | 🟢 |
| Indie Hackers products | [link](https://www.indiehackers.com/products) ✅ | Scrape | 🟢 |
| Launching Next | [link](https://www.launchingnext.com) ✅ | Scrape | 🟢 |
| Peerlist Launchpad | [link](https://peerlist.io/launchpad) ✅ | Scrape | 🟢 |

## 9. Research and patents

| Name | Link | What you get | Access | Early |
|---|---|---|---|---|
| arXiv API | [link](https://info.arxiv.org/help/api/user-manual.html) ✅ | New AI/robotics/quantum papers | API | 🟢 |
| OpenAlex | [link](https://developers.openalex.org/api-reference/institutions/list-institutions.md) ✅ | Institutions by country; `type:company` = young research firms | API | 🟢 |
| Semantic Scholar | [link](https://www.semanticscholar.org/product/api) ✅ | Authors, affiliations | API | 🟢 |
| EPO Open Patent Services | [link](https://www.epo.org/en/searching-for-patents/data/web-services/ops) ✅ | Patent applications by applicant | API (free signup) | 🟡 |
| Espacenet | [link](https://worldwide.espacenet.com) ⛔ | Patent search | Manual | 🟡 |
| DPMAregister | [link](https://www.dpma.de/en/ip-search/databases/dpmaregister) ✅ | German patent filings | Manual | 🟡 |
| Google Patents | [link](https://patents.google.com) ✅ | Full-text search / BigQuery | Scrape | 🟡 |

## 10. Domain and web signals (enrichment)

| Name | Link | What you get | Access |
|---|---|---|---|
| crt.sh | [link](https://crt.sh/) ✅ | New TLS certificates = product going live | API (JSON) |
| Wayback CDX API | [link](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md) ✅ | First-seen date of a domain | API |
| Whoxy new domains | [link](https://www.whoxy.com/newly-registered-domains/) ✅ | Daily new .ai/.com domains | Partly free |
| ICANN CZDS | [link](https://czds.icann.org) ✅ | gTLD zone files | Bulk (approval) |
| BuiltWith free API | [link](https://api.builtwith.com/free-api) ✅ | Tech stack per domain | API |

## 11. Hiring signals

| Name | Link | Access | Country |
|---|---|---|---|
| Arbeitnow job API | [link](https://www.arbeitnow.com/api/job-board-api) ✅ | API, no key | DE |
| Bundesagentur Jobsuche (bundesAPI) | [link](https://github.com/bundesAPI/jobsuche-api) ✅ | Unofficial API | DE |
| JOIN: Deutsche Startups board | [link](https://join.com/en/job-boards/deutsche-startups) ✅ | Scrape | DE |
| Berlin Startup Jobs | [link](https://berlinstartupjobs.com) ✅ | Scrape / RSS | DE |
| Wellfound Berlin | [link](https://wellfound.com/location/berlin) ✅ | Scrape (anti-bot) | DE |
| startup.jobs | [link](https://startup.jobs/locations/berlin/ai-engineer) ⛔ | Manual | multi |
| YC jobs | [link](https://www.ycombinator.com/jobs) ✅ | Scrape | multi |

## 12. Startup directories and ecosystem maps

| Name | Link | Access | Country | Early |
|---|---|---|---|---|
| Startbase (~11k DE startups) | [link](https://www.startbase.de/country/deutschland/) ✅ | Scrape | DE | 🟡 |
| appliedAI German AI Startup Landscape | [link](https://www.appliedai-institute.de/en/hub/2025-ai-german-startup-landscape) ✅ | Manual | DE | 🟡 |
| hessian.AI Startup Landscape | [link](https://startup-landscape.hessian.ai/) ✅ | Scrape / PDF | DE | 🟡 |
| Munich Startup directory | [link](https://www.munich-startup.de/en/startups-and-ecosystem) ✅ | Scrape | DE | 🟡 |
| Berlin Startup Map (Dealroom) | [link](https://startup-map.berlin/companies) ⛔ | Manual (scraping not allowed by its terms) | DE | 🔴 |
| Amsterdam Startup Map (Dealroom) | [link](https://startupmap.iamsterdam.com/companies) ⛔ | Manual (scraping not allowed by its terms) | NL | 🔴 |
| Techleap Finder | [link](https://finder.techleap.nl/companies) ⛔ | Manual (login) | NL | 🟡 |
| Dealroom AI startups NL list | [link](https://dealroom.co/lists/ai-startups-netherlands/) ✅ | Manual | NL | 🔴 |
| Startup Luxembourg directory | [link](https://startupluxembourg.com/engage-with-the-ecosystem/explore-luxembourg-startup-ecosystem) ✅ | Manual | LU | 🟡 |
| StartupBlink Belgium | [link](https://startupblink.com/startup-ecosystem/belgium) ✅ | Manual / paid | BE | 🔴 |
| Dealroom | [link](https://app.dealroom.co) ⛔ | Paid | multi | 🔴 |
| Crunchbase | [link](https://www.crunchbase.com) ✅ | Paid; use it to **check funding**, not to discover | multi | 🔴 |

## 13. Tech news (mainly for the "already funded?" check)

| Name | Link | Access |
|---|---|---|
| EU-Startups | [link](https://www.eu-startups.com/feed/) ⛔ | RSS (blocks scripts; send a browser user agent) |
| Tech.eu | [link](https://tech.eu/feed/) ✅ | RSS |
| deutsche-startups.de | [link](https://www.deutsche-startups.de/feed/) ✅ | RSS |
| Gründerszene | [link](https://www.businessinsider.de/gruenderszene/) ✅ | Scrape (partly paywalled) |
| Silicon Canals | [link](https://siliconcanals.com/feed/) ✅ | RSS (looked stale) |
| Silicon Luxembourg | [link](https://siliconluxembourg.lu) ✅ | Scrape |
| Sifted | [link](https://sifted.eu) ✅ | Paywall |

## 14. Social and community (manual or limited)

| Name | Link | Access |
|---|---|---|
| Bluesky searchPosts | [link](https://docs.bsky.app/docs/api/app-bsky-feed-search-posts) ✅ | API (use api.bsky.app) |
| Mastodon hashtag timelines | [link](https://docs.joinmastodon.org/methods/timelines/) ✅ | API, no auth |
| Reddit r/startups | [link](https://www.reddit.com/r/startups/) ✅ | OAuth + approval needed |
| X (Twitter) API | [link](https://developer.x.com) ✅ | Paid |
| LinkedIn | [link](https://www.linkedin.com) ✅ | Manual only (scraping not allowed by its terms) |
| Discord | [link](https://discord.com) ✅ | Manual |

---

**Notes**
- Papers with Code shut down in July 2025; use HF Trending Papers instead.
- Reddit blocks anonymous JSON access, and the Product Hunt API is non-commercial only.
- Dealroom-powered maps (Berlin, Amsterdam, Techleap) don't allow scraping in their terms, so use them by hand only.
- Being in an incubator or on a grant list doesn't prove a company has no priced round yet. Always run the funding check.
