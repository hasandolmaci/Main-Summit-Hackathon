"""Turn data/eu_startups/eu_startups_2021_2023_train.csv into the engine's companies.csv format.

One row per company per snapshot (as-of) date. Empty cell = unknown, never 0.
Only point-in-time columns are kept as raw metric inputs; label columns are kept separately
and are never used for scaling.

Usage:  python engine/prepare_eu.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "eu_startups" / "eu_startups_2021_2023_train.csv"
OUT = ROOT / "engine" / "build" / "companies_eu.csv"
EUR_PER_GBP = 1.17  # rough 2021-2023 average; Innovate UK awards are in GBP, EIC grants in EUR


def main() -> None:
    d = pd.read_csv(SRC, low_memory=False)
    snap = pd.to_datetime(d["asof_date"])
    is_iuk = d["cohort_source"].str.startswith("Innovate UK")
    is_program = d["program_type"].ne("none")

    # Common Crawl rank is only point-in-time if the release predates the as-of date.
    cc_ok = d["cc_release_after_asof"].eq(0) & d["cc_in_graph"].notna()
    web_visible = d["cc_in_graph"].where(cc_ok)
    web_centrality = d["cc_rank_pct"].where(cc_ok & d["cc_in_graph"].eq(1))

    grant_amount = d["grant_amount"].where(~is_iuk, d["grant_amount"] * EUR_PER_GBP)
    age = (snap.dt.year - d["founded_year"]).clip(lower=0)

    out = pd.DataFrame({
        "company_id": d["id"],
        "name": d["name"],
        "domain": d["domain"],
        "country": d["country"],
        "cohort_source": d["cohort_source"],
        "cohort_type": np.where(is_program, "program", "crunchbase"),
        "snapshot_date": snap.dt.date.astype(str),
        # exclusion inputs (section 4b)
        "is_pure_open_source": d["open_source_hint"],
        "pre_asof_funding_eur": d["pre_asof_funding_eur"],
        # an equity round before the as-of date means this cannot be a "first round" lead
        "raised_equity_before": d["label_basis"].eq("cb_last_round_before_asof").astype(int),
        # raw metric inputs (names match metrics.csv source_column)
        "web_visible": web_visible,
        "web_centrality_pct": web_centrality,
        "grant_won": np.where(d["program_type"].eq("grant_competition"), 1.0, np.nan),
        "grant_amount": grant_amount.where(is_program),
        "categories": d["categories"],
        "company_age_years": age,
        "pat_any": d["pat_any"],
        "tm_any": d["tm_any"],
        "gh_ext_contrib": d["gh_ext_contrib"],
        "gh_commit_accel": d["gh_commit_accel"],
        # labels: used only to pick reference winners and to evaluate
        "funded_within_24m": d["label"],
        "funding_date": d["first_round_date"],
        "label_basis": d["label_basis"],
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(out)} rows, {int(out.funded_within_24m.sum())} positives")


if __name__ == "__main__":
    main()
