# Review brief: is backtest.csv actually useful?

Context: student team, HTGF hackathon (find German startups from public traces BEFORE their first institutional round). Read backtest-data-spec.md in the parent folder (../backtest-data-spec.md) for the intended design, CONTRACT.md here, and the data itself: backtest.csv (205 companies, 104 columns; meta_asof_date = first appearance in a cohort list; y_label 1 = first institutional round within 18 months after as-of, 0 = none found, empty = excluded/unknown), evidence.csv, discovery_leads.csv, and the raw files in raw/. Column prefixes: f_ = point-in-time website features (Wayback before as-of), m_ = priority metrics (grants, GitHub, patents, serial entrepreneur, Product Hunt), e_/s_ = enrichment (HTGF portfolio, news, Wayback Impressum/analytics/team), info_ = today's values (do not train on), cb_ = Crunchbase rounds.
Known facts: 93 labelled (14 positive, 79 negative); 112 unlabelled (76 unknown, 23 Crunchbase round without date, 13 funded before as-of). Quick look: pricing page yes 4/9 funded vs 10/84; Impressum 10/45 vs 4/48; grant before as-of 0/8 vs 14/85; GitHub OSS 1/7 vs 13/86.

Please actually load backtest.csv (Python stdlib csv is fine) and check:
1. Label quality: plausibility of positives/negatives, leakage risks (features measured after as-of? info_ columns, cohort descriptions written today, label derived from same news source as features), negatives that may be quietly funded, the 76 unknowns.
2. Statistical power: with 14 positives, what can and cannot be learned? Compute a few quick things (e.g. Fisher exact p-values or Wilson intervals for the strongest signals, maybe a tiny regularised logistic regression or a simple 3-feature score with leave-one-out AUC) to show what the data supports.
3. Biases: source/cohort confounding (UnternehmerTUM vs SpinLab vs others, batch years), survivorship, missingness correlated with outcome (e.g. f_impressum missing when no Wayback snapshot -> maybe dead/obscure companies).
4. Verdict: is it useful (a) to learn weights, (b) to validate/sanity-check prior weights, (c) as pitch evidence, (d) for the live demo? Be honest.
5. The 3-5 highest-value next steps to make it more useful in the remaining time (more positives? 24-month window? resolve unknowns? specific features?), with expected effect.

Output (under ~700 words): verdict per use (a-d), the key numbers you computed, top risks, next steps, confidence. Do not modify any files.
