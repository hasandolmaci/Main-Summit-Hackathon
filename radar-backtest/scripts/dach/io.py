"""DACH cohort backtest — loading and re-export without Excel.

The uploaded backtest.csv passed through Excel:
  * dates are dd/mm/yyyy (day first)                         -> parsed explicitly, re-exported as ISO yyyy-mm-dd
  * Wayback timestamps s_homepage_snapshot_ts / s_impressum_snapshot_ts became '2.02202E+13'
    (6 significant digits = yyyymm only)                     -> recovered to month precision, flagged
The generating pipeline is not in this repository, so a true re-export must happen there; `export_clean_raw`
writes the repaired, ISO-only file that every later step reads (data/clean/backtest_iso.csv).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..lib.config import ROOT

DACH = ROOT / "dach"
RAW = DACH / "data" / "raw" / "backtest_upload.csv"
ISO = DACH / "data" / "clean" / "backtest_iso.csv"

DATE_COLS = ["meta_asof_date", "f_first_capture", "f_snapshot_date", "f_first_cert_date", "y_round_date",
             "e_news_first_mention_date", "m_gh_created", "m_cordis_first_start_date", "m_patent_first_priority_date",
             "m_patent_verified_first_priority", "info_m_ph_launch_date_any"]
WAYBACK_TS_COLS = ["s_homepage_snapshot_ts", "s_impressum_snapshot_ts"]
_SCI = re.compile(r"^\s*([0-9])\.([0-9]+)E\+([0-9]+)\s*$", re.I)


def load_raw(path: Path = RAW) -> pd.DataFrame:
    """Everything as string; blanks stay '' here and become NaN in later steps (never 0)."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def parse_dmy(s: pd.Series) -> pd.Series:
    """dd/mm/yyyy -> Timestamp (NaT for blanks). Also accepts already-ISO values."""
    s = s.fillna("").astype(str).str.strip()
    iso = pd.to_datetime(s.where(s.str.match(r"^\d{4}-\d{2}-\d{2}")), format="ISO8601", errors="coerce")
    dmy = pd.to_datetime(s.where(s.str.match(r"^\d{1,2}/\d{1,2}/\d{4}$")), format="%d/%m/%Y", errors="coerce")
    return iso.fillna(dmy)


def repair_wayback_ts(v: str) -> tuple[pd.Timestamp | None, str]:
    """'20220214093000' -> exact; '2.02202E+13' (Excel) -> first day of 2022-02, precision 'month'."""
    v = (v or "").strip()
    if not v:
        return None, ""
    if re.fullmatch(r"\d{14}", v):
        return pd.Timestamp(f"{v[:4]}-{v[4:6]}-{v[6:8]}"), "day"
    m = _SCI.match(v)
    if m:
        digits = (m.group(1) + m.group(2)).ljust(14, "0")       # '2.0221E+13' -> '20221000000000' (2022-10)
        if int(m.group(3)) == 13:
            y, mo = int(digits[:4]), int(digits[4:6])
            if 1 <= mo <= 12:
                return pd.Timestamp(year=y, month=mo, day=1), "month"
    return None, "unparseable"


def to_iso_frame(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    for c in DATE_COLS:
        if c in df:
            d = parse_dmy(df[c])
            bad = (df[c].str.strip() != "") & d.isna()
            if bad.any():
                raise ValueError(f"{c}: {int(bad.sum())} unparseable dates")
            df[c] = d.dt.strftime("%Y-%m-%d").fillna("")
    for c in WAYBACK_TS_COLS:
        if c in df:
            rep = df[c].map(repair_wayback_ts)
            df[c] = [t.strftime("%Y-%m-%d") if t is not None else "" for t, _ in rep]
            df[c + "_precision"] = [p for _, p in rep]
    return df


def export_clean_raw() -> pd.DataFrame:
    df = to_iso_frame(load_raw())
    ISO.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ISO, index=False)
    return df


def read_iso(path: Path = ISO) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for c in df.columns:
        if df[c].astype(str).str.contains(r"^\d\.\d+E\+\d+$", regex=True).any():
            raise ValueError(f"{c} contains scientific-notation values: the file went through Excel again")
    return df.replace({"": np.nan})
