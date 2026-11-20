"""Feature conversion for the DACH cohort backtest — the SAME code is used by the weekly n8n scoring.

  python -m scripts.dach.features --input <backtest csv (raw or ISO)> --output <features csv> [--freeze-ref]

Rules
  * allowlist only: features are built from the source columns named in dach/config/candidates.yaml (+ meta baseline);
    id_*, y_*, cb_*, needs_review, evidence_url, info_*, *_snapshot_ts, raw dates and person names never become features
  * per-row as-of gates: GitHub fields NaN if m_gh_created > meta_asof_date; snapshot-derived fields NaN unless the
    snapshot lies before as_of (month-precision Wayback timestamps: the whole month must lie before as_of)
  * missing != 0: a blank becomes 0 only where a valid as-of snapshot was actually checked; otherwise NaN
  * counts: log1p -> robust z against a frozen, outcome-blind reference (all cleaned rows) -> sigmoid (as the main radar)
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..lib.scaling import log_robust_z_sigmoid
from .io import DACH, read_iso, to_iso_frame

CFG = DACH / "config"
MAPS = DACH / "features" / "mappings"
SCALING_REF = DACH / "features" / "scaling_ref.json"
LLM_CACHE = DACH / "features" / "llm_text_features.csv"

FORBIDDEN_PREFIXES = ("id_", "y_", "cb_", "info_")
FORBIDDEN_EXACT = {"needs_review", "evidence_url", "s_founder_names", "s_managing_directors", "f_description_text",
                   "s_job_titles"}
TIMESTAMP_SOURCES = re.compile(r"(_snapshot_ts$|_snapshot_date$|^meta_asof_date$)")


def load_candidates() -> dict:
    return yaml.safe_load(open(CFG / "candidates.yaml"))


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", format="ISO8601")


# ---------------------------------------------------------------- gates
def gates(df: pd.DataFrame) -> dict[str, pd.Series]:
    asof = _date(df["meta_asof_date"])
    gh_created = _date(df.get("m_gh_created"))
    f_snap = _date(df.get("f_snapshot_date"))
    def month_before(col):                     # month-precision ts: valid only if the whole month is before as_of
        t = _date(df.get(col))
        return t.notna() & ((t + pd.offsets.MonthBegin(1)) <= asof)
    return {
        "github": gh_created.notna() & (gh_created <= asof),
        "f_snapshot": f_snap.notna() & (f_snap < asof),
        "homepage_snapshot": month_before("s_homepage_snapshot_ts"),
        "impressum_snapshot": month_before("s_impressum_snapshot_ts"),
        "none_before_filtered": pd.Series(True, index=df.index),
        "date_before_asof": pd.Series(True, index=df.index),
    }


# ---------------------------------------------------------------- transforms
def _mapping(name: str) -> dict:
    return json.load(open(MAPS / name))


def map_levels(values: pd.Series, mapping: dict) -> pd.Series:
    lut = {v: lvl["level"] for lvl in mapping["levels"] for v in lvl["values"]}
    return values.map(lambda v: lut.get(v, np.nan) if isinstance(v, str) else np.nan).astype(float)


def legal_form_level(df, g) -> pd.Series:
    m = _mapping("legal_form.json")
    a = map_levels(df["s_legal_form"].where(g["impressum_snapshot"]), m)
    b = map_levels(df["f_legal_entity"].where(g["f_snapshot"]), m)
    out = a.where(a.notna(), b)
    checked = g["impressum_snapshot"] | g["f_snapshot"]
    none_found = checked & df["s_legal_form"].isna() & df["f_legal_entity"].isna()
    return out.where(~none_found, 0.0)                     # page checked, no legal form stated -> level 0 ("none")


def binary(s: pd.Series, valid: pd.Series) -> pd.Series:
    return _num(s).where(valid).clip(0, 1)


def binary_present(s: pd.Series, valid: pd.Series) -> pd.Series:
    """1 if the field is filled, 0 if the as-of page was checked and it is blank, NaN if no valid page."""
    return pd.Series(np.where(~valid, np.nan, s.notna().astype(float)), index=s.index)


def growth_tooling(df, g) -> pd.Series:
    m = _mapping("analytics_tools.json")
    growth = set(m["categories"]["growth_tooling"])
    ab = map_levels(df["s_ab_testing_tool"], _mapping("ab_testing_tool.json"))
    def count(v):
        if not isinstance(v, str):
            return 0
        return sum(t.strip() in growth for t in v.split(","))
    n = df["s_analytics_tools"].map(count) + ab.fillna(0)
    return n.clip(upper=2).where(g["homepage_snapshot"])


def domain_age_steps(df, steps=(6, 12, 24, 48)) -> pd.Series:
    asof, first = _date(df["meta_asof_date"]), _date(df["f_first_capture"])
    months = ((asof - first).dt.days / 30.4375).where(first.notna())
    months = months.where(first.isna() | (first < asof), 0.0)      # first capture after as_of -> no capture yet -> age 0
    return pd.Series(np.digitize(months.fillna(-1), steps), index=df.index).astype(float).where(months.notna())


def academic_titles(df, g) -> pd.Series:
    s = df["s_managing_directors"].where(g["impressum_snapshot"])
    return s.map(lambda v: min(2, len(re.findall(r"\b(Dr|Prof)\.", v))) if isinstance(v, str) else np.nan).astype(float)


# ---------------------------------------------------------------- build
def build_features(df: pd.DataFrame, ref: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """df: cleaned ISO frame (NaN for blanks). Returns (features with id/meta columns, registry)."""
    g = gates(df)
    cand = load_candidates()
    F = pd.DataFrame({"id_company": df["id_company"].values, "meta_cohort_source": df["meta_cohort_source"].values,
                      "meta_cohort": df["meta_cohort"].values, "meta_asof_date": df["meta_asof_date"].values}, index=df.index)
    F["cohort_year"] = _date(df["meta_asof_date"]).dt.year.astype(float)
    reg, raw_counts = {}, {}

    def add(name, values, source, kind):
        F[name] = values.astype(float)
        srcs = source if isinstance(source, list) else [source]
        reg[name] = {"source": srcs, "kind": kind,
                     "timestamp_derived": any(TIMESTAMP_SOURCES.search(s) for s in srcs)}

    for spec in cand["starting_set"]:
        if spec["transform"] == "binary":
            add(spec["name"], binary(df[spec["source"]], g["github"]), spec["source"], "start")
        else:
            raw_counts[spec["name"]] = (_num(df[spec["source"]]).where(g["github"]), spec["source"], "start")

    for c in cand["candidates"]:
        if c.get("status") == "excluded_pre_run":
            reg[c["name"]] = {"source": c["source"] if isinstance(c["source"], list) else [c["source"]],
                              "kind": "candidate", "excluded": c["reason"], "timestamp_derived": False}
            continue
        n, t, src = c["name"], c["transform"], c["source"]
        gate = g.get(c.get("gate", "none_before_filtered"), g["none_before_filtered"])
        if t == "ordinal_map":
            add(n, legal_form_level(df, g), src, "candidate")
        elif t == "binary":
            add(n, binary(df[src], gate), src, "candidate")
        elif t == "binary_present":
            add(n, binary_present(df[src], gate), src, "candidate")
        elif t == "binary_positive":
            add(n, (_num(df[src]).where(gate) > 0).astype(float).where(_num(df[src]).where(gate).notna()), src, "candidate")
        elif t == "log_sigmoid":
            raw_counts[n] = (_num(df[src]).where(gate), src, "candidate")
        elif t == "count_capped":
            add(n, growth_tooling(df, g), src, "candidate")
        elif t == "ordinal_steps":
            add(n, domain_age_steps(df, tuple(c.get("steps_months", (6, 12, 24, 48)))), src, "candidate")
        elif t == "title_count_capped":
            add(n, academic_titles(df, g), src, "candidate")
        elif t.startswith("llm_"):
            vals = pd.Series(np.nan, index=df.index)
            if LLM_CACHE.exists():
                L = pd.read_csv(LLM_CACHE).set_index("id_company")
                if n in L:
                    vals = df["id_company"].map(L[n]).astype(float).where(gate)
            add(n, vals, src, "candidate_llm")

    # counts: log1p -> robust z vs frozen outcome-blind reference -> sigmoid
    ref = dict(ref or {})
    for n, (vals, src, kind) in raw_counts.items():
        r = np.asarray(ref[n], dtype=float) if n in ref else vals.dropna().to_numpy(float)
        ref[n] = r.tolist()
        add(n, pd.Series(log_robust_z_sigmoid(vals.to_numpy(float), r), index=df.index), src, kind)
    check_allowlist(F, reg)
    return F, {"registry": reg, "scaling_ref": ref}


def check_allowlist(F: pd.DataFrame, reg: dict):
    for name, meta in reg.items():
        for s in meta["source"]:
            if s.startswith(FORBIDDEN_PREFIXES) or (s in FORBIDDEN_EXACT and meta.get("kind") != "candidate_llm"
                                                    and not name == "academic_founders"):
                raise ValueError(f"feature {name} uses forbidden source {s}")
    bad = [c for c in F.columns if c.startswith(("y_", "cb_", "info_")) or c in ("needs_review", "evidence_url")]
    if bad:
        raise ValueError(f"forbidden columns in feature frame: {bad}")


def main(argv=None):
    from .clean import clean, split_outcomes
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--freeze-ref", action="store_true", help="(backtest only) write the scaling reference")
    a = ap.parse_args(argv)
    raw = pd.read_csv(a.input, dtype=str, keep_default_na=False)
    iso = to_iso_frame(raw).replace({"": np.nan})
    x, _ = split_outcomes(iso)
    cx, _ = clean(x)
    ref = None if a.freeze_ref or not SCALING_REF.exists() else json.load(open(SCALING_REF))
    F, meta = build_features(cx, ref)
    if a.freeze_ref:
        json.dump(meta["scaling_ref"], open(SCALING_REF, "w"))
    F.to_csv(a.output, index=False)
    print(f"wrote {a.output}: {len(F)} rows, {len(meta['registry'])} features")


if __name__ == "__main__":
    main()
