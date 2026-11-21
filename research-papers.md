# Research-backed success factors (for the HTGF scorecard)

Found on 2026-11-20 by GPT-6-Astra (founders, teams, VC decisions) and Opus (digital traces, ML prediction, German spin-offs).
**All 33 papers were checked by Opus against Crossref and arXiv:** every DOI resolves to the right author, title and year. The papers were checked through their records and abstracts, not by reading the full texts.

---

## Factors to use, and how our engine can measure them

| # | Factor | Evidence | Strength | How we measure it from public data | Where it goes in our scorecard |
|---|---|---|---|---|---|
| 1 | **Industry / domain experience** (founder worked in or studied this exact problem) | Azoulay+ 2020 (AER:I); Delmar & Shane 2006; Cooper+ 1994; Brüderl+ 1992 (German founders) | **Strong** | Founder bios, papers, patents, prior jobs in the same sector | Team → Relevant capability (15) ✅ confirms it |
| 2 | **Task-relevant skills beat credentials** | Unger+ 2011 meta-analysis (70 samples): human capital has only a small effect overall (r=.098); relevant skills matter more than schooling | **Strong** | GitHub / HF / paper work that matches the product, not degree names | Confirms "relevant work, not titles" ✅ |
| 3 | **Prior *successful* founder** | Gompers+ 2010 (JFE): 30% success after a prior success vs 21% for first-timers and 22% after a failure | Moderate | Earlier company plus a documented exit or IPO (registry, news) | Small bonus inside Team. A failed or unknown earlier startup counts as neutral |
| 4 | **Complementary team** (technical + commercial) | Eesley+ 2014 (SMJ); Jin+ 2017 meta-analysis: team human capital r=.17, team size r=.10 | Moderate | Founder roles: engineer/researcher plus sales/industry person; 2+ founders | New sub-metric in Team |
| 5 | **VCs weight the team most** | Gompers+ 2020 (JFE, 885 VCs): 95% say team matters, 47% say it matters most; Bernstein+ 2017 (JF experiment): showing team info raises investor interest | Strong (as investor preference) | n/a | Justifies Team as the biggest group; the lead card must show the team ✅ |
| 6 | **Contributing to outside open-source projects** | Conti, Peukert & Roche 2025 (Org. Science): startups doing this were at least **36% more likely** to raise early funding | Moderate–strong | Founders' and org's GitHub contributions to **other** repos (not just their own) | **New signal** under Traction / creativity ⭐ |
| 7 | **Patents** (and how examination went) | Häussler+ 2014 (Research Policy); Hsu & Ziedonis 2013 (SMJ) | Strong for deep tech | EPO OPS / Google Patents applications by the company or its founders | Product/tech → Technical substance |
| 8 | **University spin-off origin** | Czarnitzki+ 2014 (German spin-offs: about 3.4 percentage points faster employment growth); Shane & Stuart 2002; Wennberg+ 2011 | Moderate | Incubator, EXIST or tech-transfer lists; university email domains in commits | HTGF fit → Programme validation ✅ |
| 9 | **Public start-up grants** | Hottenrott & Richstein 2020 (Research Policy, German subsidies) | Moderate | EXIST, SPRIND, EIC and Förderkatalog lists | Programme validation ✅ (never proof of being unfunded) |
| 10 | **Online attention and legitimacy** | Sharchilev+ 2018 (CIKM, web-based prediction); Antretter+ 2019 (Twitter signals separate survivors from failures in up to 76% of cases); Garkavenko+ 2022 | Moderate | Web mentions, HN/social posts, star growth | Traction → Momentum ✅ |
| 11 | **Text of the self-description, read by an LLM** | Maarouf, Feuerriegel & Pröllochs 2025 (EJOR, LLM model); Kaiser & Kuhn 2020 | Moderate | Luna reads the website "about" text and the register's business purpose | Market + Thesis match (Luna) ✅ |
| 12 | **Uses experimentation tools** (A/B testing) | Koning, Hasan & Chatterji 2022 (Mgmt Sci): 30–100% better performance after a year | Moderate | BuiltWith / website scripts (Optimizely, PostHog, GrowthBook…) | **New signal** under Product readiness ⭐ |
| 13 | **Network centrality** (founders connected to strong investors and founders) | Bonaventura+ 2020 (Sci. Reports); Shane & Stuart 2002 | Moderate | Co-contributors on GitHub, incubator cohort peers, co-authors | Optional; hard in 21h |
| 14 | **Accelerator *training*** (not just cash) | Gonzalez-Uribe & Leatherbee 2018 (RFS): capital raised about 3×; Hallen+ 2020: depends on the programme | Moderate | Programme name and type | Programme validation, but check whether the programme took equity (then BACKED) |
| 15 | **Launch traction** (Product Hunt / Show HN) | Cao, Koning & Nanda 2024 (Mgmt Sci): only meaningful once adjusted for how well the launch audience fits | Weak–moderate | HN points + comments, relative to the category | Keep momentum weight modest ✅ |

### Things the research says NOT to do
- **Don't favour young founders.** The average founder of the fastest-growing companies is **45** (Azoulay+ 2020).
- **Don't score gender, and don't copy funding gaps into the score.** Investor bias is documented (Ewens & Townsend 2020; Kanze+ 2018).
- **Don't score personality guesses.** McCarthy+ 2023 and Baum & Locke 2004 are interesting, but too noisy to automate.
- **Don't let the team score dominate completely.** The business often stays stable while the management team changes (Kaplan+ 2009).

---

## Suggested scorecard changes (sum still 100)

| Group | Before | After | What changes |
|---|---|---|---|
| Team | 25 | **25** | Split into: **domain experience 12** · **complementary team 5** · execution continuity 5 · **prior successful founder 3** |
| Product / tech | 20 | 20 | Patents count as technical substance; **A/B-testing / analytics tools** add to product readiness |
| Traction | 15 | 15 | Add **outside open-source contributions** as a sub-signal of momentum |
| Others | 40 | 40 | No change; these now have citations behind them |

**Pitch line:** "Our metrics come from 33 peer-reviewed studies. For example, founders' industry experience (Azoulay et al., AER 2020), contributions to outside open source (+36% funding odds, Conti et al. 2025) and German university spin-off origin (Czarnitzki et al. 2014)."

---

## Caveats
- **Survivorship bias:** most machine-learning studies use Crunchbase, which only includes startups that already became visible.
- **US-heavy data and different outcomes:** the studies predict different things (funding, survival, growth, IPO). The effects are associations, not causes, and shouldn't be copied straight into weights.
- **Timing:** measure features as of the date we find the lead, so later information doesn't leak in. Missing data means "unknown", not "weak".

---

## All 33 papers (DOI checked)

**Founders, teams, VC decisions (GPT-6-Astra)**
1. Unger, Rauch, Frese & Rosenbusch (2011). Human capital and entrepreneurial success: A meta-analytical review. *J. Business Venturing*. 10.1016/j.jbusvent.2009.09.004
2. Gompers, Kovner, Lerner & Scharfstein (2010). Performance persistence in entrepreneurship. *J. Financial Economics*. 10.1016/j.jfineco.2009.11.001
3. Azoulay, Jones, Kim & Miranda (2020). Age and High-Growth Entrepreneurship. *AER: Insights*. 10.1257/aeri.20180582
4. Eesley, Hsu & Roberts (2014). The contingent effects of top management teams on venture performance. *Strategic Management J.* 10.1002/smj.2183
5. Jin et al. (2017). Entrepreneurial Team Composition Characteristics and New Venture Performance: A Meta-Analysis. *ETP*. 10.1111/etap.12232
6. Ewens & Townsend (2020). Are early stage investors biased against women? *JFE*. 10.1016/j.jfineco.2019.07.002
7. Kanze, Huang, Conley & Higgins (2018). We Ask Men to Win and Women Not to Lose. *Academy of Management J.* 10.5465/amj.2016.1215
8. McCarthy et al. (2023). The impact of founder personalities on startup success. *Scientific Reports*. 10.1038/s41598-023-41980-y
9. Baum & Locke (2004). The relationship of entrepreneurial traits, skill, and motivation to subsequent venture growth. *J. Applied Psychology*. 10.1037/0021-9010.89.4.587
10. Gompers, Gornall, Kaplan & Strebulaev (2020). How do venture capitalists make decisions? *JFE*. 10.1016/j.jfineco.2019.06.011
11. Bernstein, Korteweg & Laws (2017). Attracting Early-Stage Investors: Evidence from a Randomized Field Experiment. *J. Finance*. 10.1111/jofi.12470
12. Kaplan, Sensoy & Strömberg (2009). Should Investors Bet on the Jockey or the Horse? *J. Finance*. 10.1111/j.1540-6261.2008.01429.x
13. Gonzalez-Uribe & Leatherbee (2018). The Effects of Business Accelerators on Venture Performance: Evidence from Start-Up Chile. *RFS*. 10.1093/rfs/hhx103
14. Hallen, Cohen & Bingham (2020). Do Accelerators Work? If So, How? *Organization Science*. 10.1287/orsc.2019.1304
15. Delmar & Shane (2006). Does experience matter? *Strategic Organization*. 10.1177/1476127006066596
16. Cooper, Gimeno-Gascon & Woo (1994). Initial human and financial capital as predictors of new venture performance. *JBV*. 10.1016/0883-9026(94)90013-2
17. Brüderl, Preisendörfer & Ziegler (1992). Survival Chances of Newly Founded Business Organizations. *American Sociological Review*. 10.2307/2096207

**Digital traces, ML prediction, spin-offs (Opus)**
18. Żbikowski & Antosiuk (2021). A machine learning, bias-free approach for predicting business success using Crunchbase data. *Information Processing & Management*. 10.1016/j.ipm.2021.102555
19. Sharchilev et al. (2018). Web-based Startup Success Prediction. *CIKM*. 10.1145/3269206.3272011
20. Garkavenko et al. (2022). Where Do You Want To Invest? Predicting Startup Funding From Freely, Publicly Available Web Information. arXiv:2204.06479
21. Maarouf, Feuerriegel & Pröllochs (2025). A Fused Large Language Model for Predicting Startup Success. *EJOR*. arXiv:2409.03668
22. Kaiser & Kuhn (2020). The value of publicly available, textual and non-textual information for startup performance prediction. *J. Business Venturing Insights*. 10.1016/j.jbvi.2020.e00179
23. Antretter et al. (2019). Predicting new venture survival: A Twitter-based machine learning approach. *JBVI*. 10.1016/j.jbvi.2018.e00109
24. Bonaventura et al. (2020). Predicting success in the worldwide start-up network. *Scientific Reports*. 10.1038/s41598-019-57209-w
25. Conti, Peukert & Roche (2025). Beefing IT Up for Your Investor? Engagement with Open Source Communities, Innovation and Startup Funding. *Organization Science*. 10.1287/orsc.2023.18348
26. Koning, Hasan & Chatterji (2022). Experimentation and Start-up Performance: Evidence from A/B Testing. *Management Science*. 10.1287/mnsc.2021.4209
27. Cao, Koning & Nanda (2024). Sampling Bias in Entrepreneurial Experiments. *Management Science*. 10.1287/mnsc.2021.01740
28. Häussler, Harhoff & Müller (2014). How patenting informs VC investors: The case of biotechnology. *Research Policy*. 10.1016/j.respol.2014.03.012
29. Hsu & Ziedonis (2013). Resources as dual sources of advantage: Implications for valuing entrepreneurial-firm patents. *SMJ*. 10.1002/smj.2037
30. Shane & Stuart (2002). Organizational Endowments and the Performance of University Start-ups. *Management Science*. 10.1287/mnsc.48.1.154.14280
31. Czarnitzki, Rammer & Toole (2014). University spin-offs and the "performance premium". *Small Business Economics*. 10.1007/s11187-013-9538-0
32. Wennberg, Wiklund & Wright (2011). The effectiveness of university knowledge spillovers: Performance differences between university spinoffs and corporate spinoffs. *Research Policy*. 10.1016/j.respol.2011.05.014
33. Hottenrott & Richstein (2020). Start-up subsidies: Does the policy instrument matter? *Research Policy*. 10.1016/j.respol.2019.103888
