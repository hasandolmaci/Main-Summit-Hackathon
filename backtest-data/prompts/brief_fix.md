# Decision brief: the best fix plan for backtest.csv

Read: prompts/review_astra.md and prompts/review_opus.md (two independent reviews of backtest.csv), CONTRACT.md, scripts/assemble.py, and the data (backtest.csv, raw/*). Student hackathon team, HTGF track; they have limited hours, a funding-label script (scripts/label_funding.py, Google News + deutsche-startups + homepage), Crunchbase rounds without dates, Wayback-based features, free sources only now (Apify budget used up; OpenAI API credits and Codex/Claude agents are available).

Decide:
1. A prioritised fix plan (ordered list). For each fix: what exactly to change (which file/columns/rule), who/what does it (script vs manual vs LLM), time estimate, and expected effect on label counts / bias / leakage.
2. Which goal to optimise the dataset for, given that weight learning is impossible: e.g. (i) honest validation of prior weights, (ii) demo/pitch evidence, (iii) both. Recommend one and say what "done" looks like.
3. Whether any fix is not worth it in the remaining time, and what NOT to claim in the pitch.
4. A minimal analysis your friend should run afterwards (exact comparisons, with year control) and how to present results.

Output (under ~600 words): ordered fix table (Fix | How | Time | Effect), goal recommendation, skip list, analysis plan, confidence. Do not modify files.
