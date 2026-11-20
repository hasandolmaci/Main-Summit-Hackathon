"""Validate labels, entities and events: schema, duplicates, date sanity, coverage per source and year.

Exit code 1 on any hard error. Writes results/validation.md and data/processed/coverage_by_source_year.csv.
"""
from __future__ import annotations

import sys

import pandas as pd

from .lib.config import LABELS, PROCESSED, RESULTS, data_cutoff, get_logger
from .lib.meta import write_meta
from .lib.schema import validate_events, validate_labels

log = get_logger("validate")


def check_labels(lab: pd.DataFrame) -> tuple[list[str], list[str]]:
    errs = validate_labels(lab)
    warns = []
    d = pd.to_datetime(lab["first_funding_date"], errors="coerce", utc=True)
    if (d > data_cutoff()).any():
        warns.append(f"{int((d > data_cutoff()).sum())} labels dated after data_cutoff (excluded by window logic)")
    if (d < pd.Timestamp("2004-01-01", tz="UTC")).any():
        errs.append("labels before 2004 (YC founded 2005) — check parsing")
    return errs, warns


def check_entities(ent: pd.DataFrame) -> list[str]:
    errs = []
    if ent["entity_id"].duplicated().any():
        errs.append("duplicate entity_id")
    if ent["github_org"].dropna().str.lower().duplicated().any():
        errs.append("one GitHub org linked to >1 entity")
    for c in ["github_org_created_at", "gh_first_event_ts", "cordis_first_public_ts"]:
        t = pd.to_datetime(ent[c], utc=True, errors="coerce")
        if (t > data_cutoff() + pd.Timedelta(days=60)).any():
            errs.append(f"{c} in the future")
    if "github_match_confidence" in ent and (ent["github_match_confidence"].dropna() != "high").any():
        errs.append("non-high-confidence GitHub link in entities.csv")
    return errs


def check_events(ev: pd.DataFrame) -> list[str]:
    errs = validate_events(ev[["entity_id", "ts", "source", "type", "value", "detail"]])
    if ev.duplicated(["entity_id", "ts", "source", "type", "detail"]).any():
        errs.append(f"{int(ev.duplicated(['entity_id', 'ts', 'source', 'type', 'detail']).sum())} duplicate events")
    if (ev["ts"] < pd.Timestamp("2007-01-01", tz="UTC")).any():
        errs.append("events before 2007")
    if (ev["value"] < 0).any():
        errs.append("negative event values")
    return errs


def coverage(ev: pd.DataFrame, ent: pd.DataFrame) -> pd.DataFrame:
    ev = ev.assign(year=ev["ts"].dt.year)
    t = ev.groupby(["source", "type", "year"]).agg(events=("value", "size"), entities=("entity_id", "nunique")).reset_index()
    lab = set(ent.loc[ent["is_labelled"], "entity_id"])
    t2 = ev[ev["entity_id"].isin(lab)].groupby(["source", "type", "year"])["entity_id"].nunique().rename("labelled_entities")
    return t.merge(t2.reset_index(), on=["source", "type", "year"], how="left").fillna({"labelled_entities": 0})


def main():
    lines, hard = ["# Data validation", ""], []
    lab = pd.read_csv(LABELS / "companies.csv", dtype=str, keep_default_na=False)
    e, w = check_labels(lab)
    hard += e
    lines += [f"- labels: {len(lab)} rows; errors: {e or 'none'}; warnings: {w or 'none'}"]
    ent = pd.read_csv(PROCESSED / "entities.csv")
    e = check_entities(ent)
    hard += e
    lines += [f"- entities: {len(ent)} rows (labelled {int(ent.is_labelled.sum())}, B {int(ent.in_B.sum())}); errors: {e or 'none'}"]
    ev = pd.read_parquet(PROCESSED / "events.parquet")
    e = check_events(ev)
    hard += e
    lines += [f"- events: {len(ev)} rows; errors: {e or 'none'}", ""]
    cov = coverage(ev, ent)
    out = PROCESSED / "coverage_by_source_year.csv"
    cov.to_csv(out, index=False)
    write_meta(out, "scripts/validate_data.py", inputs=[PROCESSED / "events.parquet", PROCESSED / "entities.csv"], rows=len(cov))
    piv = cov.pivot_table(index="year", columns=["source", "type"], values="entities", aggfunc="sum").fillna(0).astype(int)
    lines += ["## Entities with ≥1 event, per source/type and year", "", piv.to_markdown(), ""]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "validation.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:6]))
    if hard:
        log.error("validation failed: %s", hard)
        sys.exit(1)


if __name__ == "__main__":
    main()
