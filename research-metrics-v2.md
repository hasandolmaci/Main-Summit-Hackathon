# New research-backed metrics (round 2)

Sources: two independent reviews (Codex GPT-6-Astra + Claude Opus), merged and checked by Opus.
Where the two cite different numbers from the same paper (different versions/samples), both are shown.
"Stealth" = works even if the website shows nothing.

## Add these (strong evidence, easy, free)

| # | Metric | How to compute (free source, before as-of) | Evidence | Effort | Stealth |
|---|---|---|---|---|---|
| 1 | **Patent application filed** | Company or founder is applicant/inventor on an EP/DE/PCT filing, use priority/filing date. EPO OPS, DPMA, Google Patents | Guzman & Stern 2015 (Science / NBER, ~0.5-0.8M US firms): patent odds ratio ~25-54 for IPO/big acquisition. Farre-Mensa et al. 2020 (J. Finance): first patent grant -> +55% employment, +80% sales after 5y | 2-5h | yes |
| 2 | **Trademark filed** | DPMA / EUIPO / TMview: marks filed by the company or founder, count and first filing date | Hornuf & Mattusch 2025 (892 German fintechs): +16.5% funding likelihood per extra trademark. Guzman & Stern: OR 5.4-8.3. Zhou et al. 2016: patents+trademarks help in early VC rounds | 1.5-5h | yes |
| 3 | **Founder industry experience >= 3 years** | Max years a founder worked in the startup's sector before founding (LinkedIn/bio, bins 0 / 1-2 / 3+) | Azoulay et al. 2020 (AER:I, 2.7M US founders): 3+ years roughly doubles top-growth probability | 3-5h | yes |
| 4 | **Prior VC-backed or exited founder** (replaces plain "serial founder") | Founder previously founded/led a company that raised VC or exited | Gompers et al. 2010: 30% vs 21% success. Zhang 2011: only VC-backed prior experience speeds early rounds. Gottschalk et al. 2017 (7,400 German firms): plain repeat founders do NOT do better | 2-5h | yes |
| 5 | **Certified star academic founder** | Founder is professor with external certification: highly cited researcher, ERC grant, Leibniz prize, academy member (OpenAlex + university page) | Roche, Conti & Rothaermel 2020 (1,723 US biomed startups): star-professor startups significantly more likely to get VC; productive but uncertified professors no better. Fuller & Rothaermel 2012: star faculty OR 2.56 for IPO | 3-8h | **yes (Synthesia fix)** |
| 6 | **Number of co-founders** | Count of founders/managing directors (Impressum, Handelsregister) | Jin et al. 2017 meta-analysis r=.10; Roche et al. 2020: log team size predicts VC funding and amount | 1-2h | partly |
| 7 | **Short, non-eponymous company name** | Name <=3 words (without legal suffix); flag if it contains a founder's surname | Guzman & Stern: short name OR ~2.0-2.4; eponymous OR 0.14-0.2 (70%+ less likely to grow). Not validated for Germany | 0.5h | yes |
| 8 | **Technical + commercial founder mix** | >=1 technical/research founder AND >=1 with commercial experience | Jin et al. 2017: r=.06 (weak, small bonus only) | +1h | yes |

## Keep (already in our system, evidence confirmed)
- **Outside open-source contributions**: Conti, Peukert & Roche 2025 (160k US startups): >=36% higher funding likelihood. Strongest digital-trace signal.
- **University spin-off / EXIST**: Czarnitzki et al. 2014: about +3.4 pp employment growth for German spin-offs.
- **Selective accelerator (not generic)**: Gonzalez-Uribe & Leatherbee 2018: ~3x capital raised; Yu 2020: effects mixed.

## Down-weight or turn into gates (little or no evidence)
- **Pricing page, waitlist, Impressum, customer logos, jobs page, TLS age**: no empirical support found. Impressum is legally required in Germany, so it carries almost no information. Use as gates/context, not big points.
- **Plain serial founder** (any prior company): no effect in German data unless prior company was VC-backed/exited.
- **Top university degree, raw h-index / paper counts**: not significant once certification is controlled (Roche 2020; Lyonnet & Stern 2025 says VCs over-weight elite education).
- **Hiring velocity / headcount, Product Hunt, web traction**: high leakage (often follows funding). Lee & Kim 2024: early scaling linked to 20-40% more failure.

## Method changes both reviewers recommend
- Stop scoring missing data as 0; score only observed metrics and show coverage separately.
- Measure everything at a fixed point in time (filing dates, not grant dates; citations as of the as-of date).
- US odds ratios do not transfer 1:1 to Germany or to "first VC round within 18 months": test the new metrics in the backtest before giving them big weights.

## Main sources
- Guzman & Stern 2015, Science: https://reap.mit.edu/assets/Guzman-Stern-Science-Where-is-Silicon-Valley-INTEGRATED.pdf
- Hornuf & Mattusch 2025: https://www.tandfonline.com/doi/full/10.1080/1351847X.2024.2433021
- Azoulay et al. 2020: https://pubs.aeaweb.org/doi/pdfplus/10.1257/aeri.20180582
- Gompers et al. 2010: https://leeds-faculty.colorado.edu/bhagat/PerformancePersistencein%20Entrepreneurship.pdf
- Roche, Conti & Rothaermel 2020: https://www.scheller.gatech.edu/directory/research/strategy-innovation/rothaermel/pdf/2020_rp.pdf
- Fuller & Rothaermel 2012: https://www.scheller.gatech.edu/directory/research/strategy-innovation/rothaermel/pdf/2012_sej_forthcoming.pdf
- Conti, Peukert & Roche 2025: https://pubsonline.informs.org/doi/full/10.1287/orsc.2023.18348
- Jin et al. 2017: https://effectuation.org/hubfs/Journal%20Articles/2017/06/Jin_et_al-2016-Entrepreneurship_Theory_and_Practice-1.pdf
- Farre-Mensa et al. 2020: https://www.nber.org/papers/w23268
- Zhang 2011: https://ideas.repec.org/p/iza/izadps/dp2964.html
- Gottschalk, Greene & Mueller 2017: https://link.springer.com/10.1007/s11187-016-9780-3
- Zhou et al. 2016: https://pure.eur.nl/en/publications/patents-trademarks-and-their-complementarity-in-venture-capital-f/
- Lee & Kim 2024: https://sms.onlinelibrary.wiley.com/doi/full/10.1002/smj.3596
- Gonzalez-Uribe & Leatherbee 2018: https://researchonline.lse.ac.uk/id/eprint/84553/1/Gonzalez-Uribe_Effects%20of%20Business%20Accelerators.pdf
