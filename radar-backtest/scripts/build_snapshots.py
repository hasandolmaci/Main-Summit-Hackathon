"""Point-in-time snapshots: features + labels per as_of date.

THE rule: compute_raw_features() first restricts events to ts < as_of; every feature is derived only from that
slice. Visibility uses entity timestamps compared with as_of. Scaling uses only the outcome-blind background sample
B (visible at the same date). Labels are attached afterwards and never enter feature code.

Base metrics in this first pass
  oss_external_engagement      team(as_of) = actors with a push to the org's repos before as_of; count their PRs
                               opened + issues opened + pushes in repos NOT owned by the org in [as_of-12m, as_of);
                               log1p -> robust z vs same-date B -> sigmoid (IQR=0 -> percentile).
  github_activity_acceleration log((W1+1)/(W0+1)), W1=[as_of-90d, as_of), W0=[as_of-180d, as_of-90d) of org-repo
                               Push/PR/Issues/Release events -> clip ±3 -> sigmoid.
  has_public_rd_grant          1 if any CORDIS grant signed before as_of; 0 if covered (Horizon-eligible country or
                               CORDIS-linked) without one; MISSING otherwise.
Missing source -> NaN (kNN handles partial dimensions; LR fills 0 and uses has_<source>).

Outputs: data/processed/snapshots_<year>.parquet, data/processed/reference_profiles.parquet (+ meta)
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from .lib.cohort import case_cohort, label_at, label_window_complete, visible_at
from .lib.config import LABELS, PROCESSED, as_of_date, cfg, data_cutoff, get_logger, guard_years
from .lib.meta import write_meta
from .lib.scaling import log_ratio_sigmoid, log_robust_z_sigmoid

log = get_logger("build_snapshots")

FEATURES = ["oss_external_engagement", "github_activity_acceleration", "has_public_rd_grant"]
HAS_FLAGS = ["has_github", "has_cordis"]
RAW = ["eng_raw", "act_w1", "act_w0", "grant_raw"]


def load_events() -> pd.DataFrame:
    ev = pd.read_parquet(PROCESSED / "events.parquet")
    need = ev["type"].isin(["team_first_push", "ext_engagement_month"])
    ev["actor"] = None
    ev.loc[need, "actor"] = ev.loc[need, "detail"].map(lambda d: json.loads(d).get("actor"))
    return ev


def compute_raw_features(events: pd.DataFrame, entity_ids, as_of) -> pd.DataFrame:
    """Raw (unscaled) features at as_of from events with ts < as_of ONLY."""
    a = pd.Timestamp(as_of)
    w1 = pd.Timedelta(days=cfg()["windows"]["w1_days"])
    w0 = pd.Timedelta(days=cfg()["windows"]["w0_days"])
    eng_lo = a - pd.DateOffset(months=cfg()["windows"]["engagement_months"])
    ids = pd.Index(sorted(set(entity_ids)), name="entity_id")
    ev = events[(events["ts"] < a) & events["entity_id"].isin(ids)]          # <- the point-in-time cut

    team = ev[ev["type"] == "team_first_push"][["entity_id", "actor"]].drop_duplicates()
    ext = ev[(ev["type"] == "ext_engagement_month") & (ev["ts"] >= eng_lo)][["entity_id", "actor", "value"]]
    ext = ext.merge(team, on=["entity_id", "actor"], how="inner")
    eng = ext.groupby("entity_id")["value"].sum()

    act = ev[ev["type"] == "org_activity"]
    aw1 = act[act["ts"] >= a - w1].groupby("entity_id")["value"].sum()
    aw0 = act[(act["ts"] >= a - w1 - w0) & (act["ts"] < a - w1)].groupby("entity_id")["value"].sum()
    grant = ev[ev["type"] == "grant_signed"].groupby("entity_id").size()

    out = pd.DataFrame(index=ids)
    out["eng_raw"] = eng.reindex(ids).fillna(0.0)
    out["act_w1"] = aw1.reindex(ids).fillna(0.0)
    out["act_w0"] = aw0.reindex(ids).fillna(0.0)
    out["grant_raw"] = (grant.reindex(ids).fillna(0) > 0).astype(float)
    return out.reset_index()


def coverage(ent: pd.DataFrame, as_of) -> pd.DataFrame:
    v = visible_at(ent, as_of)
    cordis_cov = ent["horizon_eligible"].fillna(False).astype(bool) | ent["cordis_first_public_ts"].notna()
    return pd.DataFrame({"entity_id": ent["entity_id"].values, "has_github": v["gh_cov"].values,
                         "has_cordis": cordis_cov.values, "visible": v["visible"].values})


def scale(raw: pd.DataFrame, cov: pd.DataFrame, ref_ids: set) -> pd.DataFrame:
    """Scale raw features; reference distribution = outcome-blind B entities (no labels involved)."""
    df = raw.merge(cov, on="entity_id", how="left")
    gh = df["has_github"].fillna(False).astype(bool)
    eng = df["eng_raw"].where(gh)
    ref = eng[df["entity_id"].isin(ref_ids) & gh].values
    df["oss_external_engagement"] = log_robust_z_sigmoid(eng.values, ref)
    df["github_activity_acceleration"] = np.where(gh, log_ratio_sigmoid(df["act_w1"], df["act_w0"],
                                                                       cfg()["windows"]["accel_clip"]), np.nan)
    cc = df["has_cordis"].fillna(False).astype(bool)
    df["has_public_rd_grant"] = df["grant_raw"].where(cc)
    return df


def load_inputs(funding_file: str = "companies.csv"):
    ent = pd.read_csv(PROCESSED / "entities.csv")
    for c in ["github_org_created_at", "gh_first_event_ts", "cordis_first_public_ts"]:
        ent[c] = pd.to_datetime(ent[c], utc=True)
    labels = pd.read_csv(LABELS / funding_file, dtype=str, keep_default_na=False)
    fd = ent["entity_id"].map(labels.set_index("domain")["first_funding_date"])
    return ent, fd


def snapshot(year: int, ent: pd.DataFrame, fd: pd.Series, events: pd.DataFrame) -> pd.DataFrame:
    a = as_of_date(year)
    cc = case_cohort(ent, fd, a)                          # visible, not already funded, cases + B controls
    B_vis = set(ent.loc[ent["in_B"].astype(bool) & visible_at(ent, a)["visible"], "entity_id"])
    ids = sorted(set(cc["entity_id"]) | B_vis)
    raw = compute_raw_features(events, ids, a)
    cov = coverage(ent, a)
    feat = scale(raw, cov, B_vis)
    snap = cc[["entity_id", "in_B", "inclusion_prob", "weight", "label"]].merge(feat, on="entity_id", how="left")
    e = ent.set_index("entity_id")
    snap["europe"] = snap["entity_id"].map(e["europe"]).fillna(False).astype(bool)
    snap["channels"] = snap["entity_id"].map(e["channels"])
    snap["funding_date"] = snap["entity_id"].map(pd.to_datetime(fd, utc=True).set_axis(ent["entity_id"]))
    snap.insert(1, "as_of", a)
    snap["labels_complete"] = label_window_complete(a, data_cutoff())
    return snap.sort_values("entity_id").reset_index(drop=True)


def reference_profiles(ent: pd.DataFrame, fd: pd.Series, events: pd.DataFrame) -> pd.DataFrame:
    """Profile of every labelled entity at month_floor(funding_date - 90d), scaled vs B visible at that date.

    Used as kNN references only when funding_date < as_of (enforced in backtest).
    """
    off = pd.Timedelta(days=cfg()["reference"]["profile_offset_days"])
    f = pd.to_datetime(fd, utc=True)
    rows = []
    lab = ent.assign(funding_date=f.values)[f.notna().values]
    lab = lab.assign(profile_date=(lab["funding_date"] - off).dt.tz_localize(None).dt.to_period("M").dt.to_timestamp()
                     .dt.tz_localize("UTC"))
    for d, grp in lab.groupby("profile_date"):
        v = visible_at(ent, d)
        vis_ids = set(grp.loc[v.loc[grp.index, "visible"].values, "entity_id"])
        if not vis_ids:
            continue
        B_vis = set(ent.loc[ent["in_B"].astype(bool) & v["visible"], "entity_id"])
        raw = compute_raw_features(events, sorted(vis_ids | B_vis), d)
        feat = scale(raw, coverage(ent, d), B_vis)
        feat = feat[feat["entity_id"].isin(vis_ids)].copy()
        feat["profile_date"] = d
        feat["funding_date"] = feat["entity_id"].map(lab.set_index("entity_id")["funding_date"])
        rows.append(feat)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", nargs="*", type=int, help="as_of years (default: 2012..last selection year)")
    ap.add_argument("--funding-file", default="companies.csv", help="companies_batchstart.csv for the sensitivity run")
    ap.add_argument("--suffix", default="", help="output suffix, e.g. _batchstart")
    a = ap.parse_args(argv)
    years = a.years or list(range(2012, cfg()["years"]["selection"][1] + 1))
    years = guard_years(years)          # locked test years are refused
    ent, fd = load_inputs(a.funding_file)
    events = load_events()
    for y in years:
        s = snapshot(y, ent, fd, events)
        out = PROCESSED / f"snapshots_{y}{a.suffix}.parquet"
        s.to_parquet(out, index=False)
        write_meta(out, "scripts/build_snapshots.py", inputs=[PROCESSED / "entities.csv", PROCESSED / "events.parquet",
                                                                LABELS / a.funding_file],
                   params={"as_of": str(as_of_date(y)), "windows": cfg()["windows"]}, rows=len(s))
        log.info("as_of %d: %d rows, %d positives, %d B controls", y, len(s), int(s.label.sum()), int((s.label == 0).sum()))
    ref = reference_profiles(ent, fd, events)
    out = PROCESSED / f"reference_profiles{a.suffix}.parquet"
    ref.to_parquet(out, index=False)
    write_meta(out, "scripts/build_snapshots.py", rows=len(ref), params={"offset_days": cfg()["reference"]["profile_offset_days"]})
    log.info("reference profiles: %d", len(ref))


if __name__ == "__main__":
    main()
