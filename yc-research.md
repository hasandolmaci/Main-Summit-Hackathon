# YC company histories: what separates survivors from failures?

Done 2026-11-20. 60 randomly picked YC companies from the same batches: 20 top, 20 other (active or acquired), 20 inactive (dead).
- **Research:** GPT-6.1-Sol (web search, every claim has a source link, 329 sources in total).
- **Coding:** Haiku turned each write-up into yes/no features.
- **Checking:** Opus spot-checked facts (Coinbase, Segment, Booktrope, Drivezy and others were correct) and tested a sample of 30 source links: 20 loaded and 10 blocked scripts, which is typical for news sites.

Raw data: scratchpad `yc/research/all_research.json` and `coded.json`.

## Results ("survived" = top + other, n=40; "dead" = inactive, n=20)

| Feature | Top | Other | Dead | Survived vs dead (Fisher p) |
|---|---|---|---|---|
| Founder had **domain expertise** in the problem before founding | 63% | 83% | 43% (6 unknown) | **73% vs 43%, p = 0.06** |
| **Regulated sector or hardware** (finance, health, energy, transport, physical product) | 50% | 60% | 25% | **55% vs 25%, p = 0.03** |
| Elite school or big tech | 75% | 71% | 53% | 73% vs 53%, p = 0.21 |
| **Public trace before first funding** (Show HN, GitHub, paper, award, customers…) | 45% | 40% | 25% | 42% vs 25%, p = 0.26 |
| Technical founder | 94% | 94% | 82% | about the same |
| Real pivot | 65% | 60% | 67% | **no difference** |
| Repeat founder | 35% | 20% | 64% (9 unknown!) | unreliable: too much missing data |
| Failure reason known | n/a | n/a | 6 of 20 | known reasons: couldn't raise the next round, unit economics, regulation, market timing |

*p below 0.05 is unlikely to be chance alone. With n=60 and several features tested, treat everything here as **a hint, not proof**.*

## What this means for our engine

1. **Domain expertise matters more than credentials.** "Founder worked in or studied this exact problem" is the strongest team signal. This supports our "relevant capability, not titles" metric (Team, 15 pts). Keep elite school and big tech as information only.
2. **Hard sectors survive more often** (regulated or hardware: 55% vs 25%). This fits HTGF's deep-tech thesis and supports the "Thesis match" metric. It's probably because hard sectors have higher barriers to entry, though it could also be selection.
3. **More than half of the top companies left no public trace before funding.** Our engine can't see every future winner early, so be upfront about that in the pitch ("we find the visible half, earlier than anyone"). It also means using sources beyond GitHub and HN: grants, incubators, competitions and registries.
4. **Pivots don't predict anything,** so don't penalise or reward them.
5. **Dead companies leave thin records.** For 14 of the 20 dead companies, the cause of death couldn't be found. Thin evidence is itself a warning sign, which supports our coverage % rule.
6. **The failures we could trace were about money, not the idea:** couldn't raise the next round, or the unit economics didn't work. A possible red-flag check is a business model with very thin margins, like a service marketplace.

## Pitch line (honest)

> "We researched 60 YC companies from the same batches. Survivors were more likely to have a founder with domain expertise (73% vs 43%) and to work in hard, regulated or hardware markets (55% vs 25%). Pivots made no difference. Small sample, so we use this to support our scorecard, not to set it."
