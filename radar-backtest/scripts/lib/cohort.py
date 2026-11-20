"""Who is a candidate at as_of, and what is their label. Shared by gate, snapshots and backtest.

visible_at() never sees labels. label_at() is the only place that reads funding dates.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from .config import cfg


def _years_before(ts: datetime, years: int) -> pd.Timestamp:
    return pd.Timestamp(ts) - pd.DateOffset(years=years)


def visible_at(ent: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Pool criteria at as_of (no labels). Returns boolean columns gh_cov, cordis_cov, visible.

    GitHub channel: matched org whose FIRST GH Archive event lies in [as_of - max_age, as_of) (org age proxy that is
    observable at as_of; GitHub's own org creation date is not available from the keyless sources).
    CORDIS channel: first grant signature (public) < as_of.
    """
    a = pd.Timestamp(as_of)
    lo = _years_before(a, cfg()["pool"]["org_max_age_years"])
    first_ev = pd.to_datetime(ent["gh_first_event_ts"], utc=True)
    cordis = pd.to_datetime(ent["cordis_first_public_ts"], utc=True)
    has_org = ent["github_org"].notna() if "github_org" in ent else first_ev.notna()
    gh = has_org & first_ev.notna() & (first_ev >= lo) & (first_ev < a)
    co = cordis.notna() & (cordis < a)
    return pd.DataFrame({"gh_cov": gh.values, "cordis_cov": co.values, "visible": (gh | co).values}, index=ent.index)


def label_at(funding_date: pd.Series, as_of: datetime, horizon_months: int | None = None) -> pd.DataFrame:
    """already_funded (excluded) and label (first funding in [as_of, as_of + horizon))."""
    h = horizon_months or cfg()["label"]["horizon_months"]
    a = pd.Timestamp(as_of)
    f = pd.to_datetime(funding_date, utc=True)
    end = a + pd.DateOffset(months=h)
    return pd.DataFrame({"already_funded": (f.notna() & (f < a)).values,
                         "label": (f.notna() & (f >= a) & (f < end)).astype(int).values}, index=funding_date.index)


def label_window_complete(as_of: datetime, cutoff: datetime, horizon_months: int | None = None) -> bool:
    h = horizon_months or cfg()["label"]["horizon_months"]
    return pd.Timestamp(as_of) + pd.DateOffset(months=h) <= pd.Timestamp(cutoff)


def case_cohort(ent: pd.DataFrame, funding_date: pd.Series, as_of: datetime) -> pd.DataFrame:
    """Case-cohort sample at as_of: all visible cases (weight 1) + visible B members with label 0 (weight 1/p).

    Labelled entities outside B that are not cases at as_of are excluded (keeps the design unbiased).
    """
    v = visible_at(ent, as_of)
    lab = label_at(funding_date, as_of)
    df = pd.concat([ent[["entity_id", "in_B", "inclusion_prob"]], v, lab], axis=1)
    df = df[df["visible"] & ~df["already_funded"]]
    case = df["label"] == 1
    ctrl = (~case) & df["in_B"] & (df["inclusion_prob"] > 0)
    df = df[case | ctrl].copy()
    df["weight"] = np.where(df["label"] == 1, 1.0, 1.0 / df["inclusion_prob"].where(df["inclusion_prob"] > 0, np.nan))
    return df
