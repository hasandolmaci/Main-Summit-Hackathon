"""Labels (frozen definition) and the label-review list for the DACH cohort backtest.

Train/eval set: y_label in {0, 1} only. FUNDED_BEFORE, EXCLUDE_*, blanks are never used.
label_review.csv: the CB_ROUND_NO_DATE and UNKNOWN rows with the evidence needed to date a first round
(not used by the loop — more dated positives is the biggest lever).
"""
from __future__ import annotations

import pandas as pd

from .io import DACH

REVIEW = DACH / "results" / "label_review.csv"


def labelled(features: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    y = outcomes.set_index("id_company")["y_label"]
    out = features.copy()
    out["y"] = out["id_company"].map(y)
    out = out[out["y"].isin(["0", "1", 0, 1])].copy()
    out["y"] = out["y"].astype(int)
    return out.reset_index(drop=True)


def write_label_review(iso: pd.DataFrame) -> pd.DataFrame:
    r = iso[iso["y_funding_status"].isin(["CB_ROUND_NO_DATE", "UNKNOWN"])].copy()
    asof = pd.to_datetime(r["meta_asof_date"])
    out = pd.DataFrame({
        "id_company": r["id_company"], "name": r["id_name"], "domain": r["id_domain"],
        "cohort": r["meta_cohort_source"] + " | " + r["meta_cohort"], "asof": r["meta_asof_date"],
        "label_window_end_18m": (asof + pd.DateOffset(months=18)).dt.strftime("%Y-%m-%d"),
        "status": r["y_funding_status"], "basis": r["y_label_basis"],
        "evidence_crunchbase_rounds": r["cb_rounds"], "cb_has_equity_round": r["cb_has_equity_round"],
        "evidence_url": r["evidence_url"], "htgf_evidence": r["info_e_htgf_match_evidence"],
        "first_news_mention": r["e_news_first_mention_date"],
        "needed": ["date of the first external round (pre-seed / seed / institutional) — e.g. press release, "
                   "HTGF 'invested since', Handelsregister capital increase, investor portfolio page"] * len(r),
        "fill_first_round_date": "", "fill_round_type": "", "fill_source_url": "", "fill_reviewer": ""})
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(REVIEW, index=False)
    return out
