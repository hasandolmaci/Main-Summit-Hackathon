# YC research results: 388 companies (run stopped early)

Status on 2026-11-20: the run was stopped at your request. **388 of the planned 1,000 companies** have results: **68 top, 160 other (active/acquired), 160 dead (inactive)**, all from the same YC batches.
- Research: GPT-6.1-Sol for 210 companies (web search, sources cited) and Claude Haiku for 178 (shallower). Where Sol left a field "unknown", the Haiku and earlier 60-company results were used to fill it.
- Cost: the budget counter shows $9.80, including a $1.80 safety margin. Check the exact amount at platform.openai.com → Usage.
- "Survived" = top + other (228); "Dead" = inactive (160).

## Main table

| Pattern | Survived | Dead | How strong |
|---|---|---|---|
| **Deep tech** (AI research, biotech, robotics, energy, space, quantum…) | 20% | 11% | **Likely** (p = 0.03); Sol-only subset 28% vs 11%, p = 0.003 |
| **B2B** (sells to businesses) | 72% | 61% | **Likely** (p = 0.03) |
| **Regulated or hardware market** | 46% | 36% | **Likely** (p = 0.048); same direction as the 60-company run |
| Product launched before YC* | 25% | 16% | Likely (p = 0.03), but see the caveat on documentation |
| Founder had a prior exit* | 11% | 5% | Likely (p = 0.04), small numbers |
| Pivoted at some point* | 36% | 26% | Borderline (p = 0.05) |
| Big-tech alumni (known cases only) | 36% | 56% | Reversed (p = 0.02): dead companies had *more* ex-big-tech founders; many unknowns |
| **Founder worked in this industry before** | 81% (known) / 50% (documented) | 85% / 42% | **No clear difference** (p = 0.58 / 0.12) |
| Technical founder | 98% / 74% | 97% / 67% | No difference |
| Elite university | 65% / 36% | 66% / 29% | No difference |
| PhD / researcher | 49% / 21% | 60% / 18% | No difference |
| University spin-off | 21% / 14% | 22% / 11% | No difference |
| Solo founder | 24% | 24% | No difference (top companies alone: 15%) |
| Average number of founders | 2.06 (other), **2.36 (top)** | 2.04 | Top companies had slightly bigger founding teams |
| Public trace before first funding | 13% | 13% | No difference; **about 87% of every group left no trace before funding** |

\* "Documented" view: share of *all* companies where the research found a clear "yes".
**No result survives the Holm correction for testing 20 features at once**, so treat everything as a hint, not proof.

## Failure reasons (160 dead companies)
For **102 of 160 (64%) the reason couldn't be found.** Among the 58 that could be traced:
product problems 6 · market timing 6 · couldn't raise the next round 5 · ran out of cash 5 · no market need 3 · competition 3 · unit economics 3 · regulation 2 · pivoted too late or too often 2 · team left / acquihire 1

## Important caveats
1. **Founder fields are very incomplete:** 40–90% "unknown", and *more* unknown for dead companies (they leave fewer traces). That cuts both ways:
   - Among *known* values, "yes" is inflated, because researchers wrote "unknown" instead of "no". For example, "business founder" came out at 100% vs 99%.
   - Counting "yes" over *all* companies favours survivors, simply because they're better documented.
   - **So company-level traits (deep tech, B2B, regulated) are the reliable results here.** Founder-level traits are not.
2. **This contradicts the 60-company run on domain expertise** (that run found 73% vs 43%). With 4× more data, the difference disappears. The earlier result was most likely chance on a small sample.
3. YC already filtered these founders, the data is mostly US companies, and these are associations, not causes.

## What this means for our scorecard
- **Strengthen:** "Regulated or hardware market" and **deep tech / thesis match**. The data now consistently supports them, and they fit HTGF's focus. **B2B** can be added as a small plus.
- **Keep, but don't over-weight:** "Founder worked in this industry before". The research papers support it (Azoulay et al. 2020 and others), but our YC data doesn't confirm it.
- **Lower or show only as information:** elite school, big-tech background, PhD. No positive effect here.
- **Confirmed limitation:** about 87% of companies, winners included, left no public trace before funding. That supports leaning on grant, incubator, register and event sources, and being honest about it in the pitch.
- **Don't score pivots** as negative. If anything, survivors pivoted slightly more often.
