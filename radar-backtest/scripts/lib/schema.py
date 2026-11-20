"""Shared table schemas and validators."""
from __future__ import annotations

import json

import pandas as pd

EVENT_COLUMNS = ["entity_id", "ts", "source", "type", "value", "detail"]
SOURCES = {"gharchive", "github_api", "cordis", "patents", "trademarks", "companies_house", "producthunt"}

LABEL_COLUMNS = ["company", "domain", "first_funding_date", "round_type", "sector", "country", "source_url",
                 "verified_by_human"]
ROUND_TYPES = {"pre-seed", "accelerator", "seed", "series_a"}


def make_events(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records, columns=EVENT_COLUMNS)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["value"] = df["value"].astype(float)
    df["detail"] = df["detail"].map(lambda d: d if isinstance(d, str) else json.dumps(d, sort_keys=True))
    return df


def validate_events(df: pd.DataFrame) -> list[str]:
    errs = []
    if list(df.columns) != EVENT_COLUMNS:
        errs.append(f"columns {list(df.columns)} != {EVENT_COLUMNS}")
        return errs
    if df["entity_id"].isna().any():
        errs.append("null entity_id")
    if not isinstance(df["ts"].dtype, pd.DatetimeTZDtype) or str(df["ts"].dt.tz) != "UTC":
        errs.append("ts must be tz-aware UTC")
    if df["ts"].isna().any():
        errs.append("null ts")
    bad = set(df["source"].unique()) - SOURCES
    if bad:
        errs.append(f"unknown sources {bad}")
    if not pd.api.types.is_float_dtype(df["value"]):
        errs.append("value must be float")
    return errs


def validate_labels(df: pd.DataFrame) -> list[str]:
    errs = []
    if list(df.columns) != LABEL_COLUMNS:
        errs.append(f"columns {list(df.columns)} != {LABEL_COLUMNS}")
        return errs
    if df["domain"].isna().any() or df["source_url"].isna().any():
        errs.append("every row needs domain and source_url")
    if df["domain"].duplicated().any():
        errs.append(f"duplicate domains: {df.loc[df['domain'].duplicated(), 'domain'].tolist()[:10]}")
    bad = set(df["round_type"].unique()) - ROUND_TYPES
    if bad:
        errs.append(f"bad round_type {bad}")
    d = pd.to_datetime(df["first_funding_date"], errors="coerce")
    if d.isna().any():
        errs.append("unparseable first_funding_date")
    return errs
