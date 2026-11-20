"""Render papers/index.md from the verified paper records (papers/verification_results.json)."""
from __future__ import annotations

import json

from .lib.config import PAPERS

METRIC = {
    "haeussler2014": "has_patent_application", "farremensa2020": "has_patent_application",
    "block2014": "has_trademark", "catalini_guzman_stern": "has_trademark",
    "howell2017": "has_public_rd_grant", "santoleri2024": "has_public_rd_grant",
    "conti2025": "oss_external_engagement; github_activity_acceleration",
    "wright_nagle": "github_activity_acceleration (commit volume)", "hsu2007": "serial_founder",
    "zhang2011": "serial_founder", "cao2019": "producthunt_rank", "starscout": "fake_star_share (background)",
    "ross_index": "star_growth baseline (background)", "knn_icl": "kNN / distance method",
    "potanin2023": "evaluation design", "cao2022": "background survey",
}

IMPLEMENTATION_NOTES = """## What the evidence means for our implementation

| metric | evidence strength for *our* operationalisation | gap between paper and our metric |
|---|---|---|
| has_patent_application | medium | Haeussler et al.: application stock speeds first VC (biotech, DE/UK, observational). Farre-Mensa et al.: causal effect is of *approval*, not filing; small absolute effect (+3.0 pp on 6.4% over 3y). We use filing/publication (binary). |
| has_trademark | medium | Catalini/Guzman/Stern: early trademark filings *predict* VC and equity growth (predictive, not causal). Block et al.: all firms already VC-backed → supports valuation, not P(funding). |
| has_public_rd_grant | strong (causal) | Howell: DOE SBIR Phase I ≈ doubles P(later VC) (US). Santoleri et al.: EU SME Instrument **Phase II** (≈€1.6M) raises later private equity; Phase I shows no effect. We use any CORDIS grant (binary) → broader than the causal evidence. |
| oss_external_engagement | medium (association) | Conti et al. measure the **org account's** public events on repos it does not control (96% forks, 2% stars, 1% pushes), binary ever-engaged, DiD, “at least 36%” is a linear-probability effect / mean, not odds. **Our definition (user-fixed) is team-based: PRs opened + issues opened + pushes by team members to non-org repos.** Kept as specified; the org-level fork-based variant is a candidate for the improvement loop. |
| github_activity_acceleration | weak | No paper measures acceleration. Conti et al. find a *linear rising trend* in making repos public over the 12 months before the first round (Online App. A14), not an acceleration; Wright/Nagle/Greenstein use lagged commit **levels** (0.3% cross-sectional, 0.03–0.1% within-firm). Treat as near-exploratory. |
| serial_founder | medium | Hsu: prior founding → higher Series A valuation; Zhang: faster/more VC only when the founder's prior firm was VC-backed (matches our 0/0.5/1 ordinal with 1 = prior company with external share allotments). |
| producthunt_rank | medium (single WP) | Cao: exogenous better daily rank → more seed/Series A within 6 months (CrunchBase). |
"""


def render(records: list[dict]) -> str:
    out = ["# Research papers — verified index", "",
           "Each record was produced by one agent reading the full text and independently re-checked by a second agent "
           "(citation via Crossref/publisher metadata, quote verbatim on the stated page, fairness of the verdict). "
           "Quotes are verbatim from the version named. PDFs are stored in `pdf/` only for arXiv / CC-licensed texts.", "",
           "| key | metric | claim verdict | direction | verified (1st / recheck) | PDF |", "|---|---|---|---|---|---|"]
    for r in records:
        rc = r.get("recheck") or {}
        out.append(f"| {r['key']} | {METRIC.get(r['key'], '')} | {r['claim_verdict']} | {r['effect_direction']} | "
                   f"{'yes' if r['verified'] else 'partly'} / {'yes' if rc.get('final_verified') else 'no'} | "
                   f"{'yes' if r.get('pdf_saved') else 'link only'} |")
    out += ["", IMPLEMENTATION_NOTES, "## Records", ""]
    for r in records:
        rc = r.get("recheck") or {}
        out += [f"### {r['key']}", "",
                f"- **Citation:** {r['citation']}",
                f"- **Link:** {r['url']}" + (f" · open text read: {r['oa_url']}" if r.get("oa_url") else ""),
                f"- **Metric supported:** {METRIC.get(r['key'], '')}",
                f"- **Key finding:** {r['key_finding']}",
                f"- **Quote (p. {r['quote_page']}; {r.get('quote_version', '')}):** “{r['quote']}”",
                f"- **Claim verdict:** {r['claim_verdict']} — {r['claim_note']}",
                f"- **How the paper measures it:** {r.get('metric_definition_in_paper', '')}",
                f"- **Effect direction:** {r['effect_direction']}",
                f"- **Verified:** first pass {'yes' if r['verified'] else 'partly (see note)'}; independent recheck "
                f"{'yes' if rc.get('final_verified') else 'no'}. Recheck notes: {rc.get('corrections') or 'none'}",
                f"- **Licence / PDF:** {r.get('licence', '')}" + (f" → `{r['pdf_saved'].split('radar-backtest/')[-1]}`" if r.get("pdf_saved") else ""),
                ""]
    return "\n".join(out)


def main():
    recs = json.loads((PAPERS / "verification_results.json").read_text())
    (PAPERS / "index.md").write_text(render(recs))
    print(f"wrote papers/index.md ({len(recs)} papers)")


if __name__ == "__main__":
    main()
