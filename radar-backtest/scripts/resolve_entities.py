"""Entity resolution: one company = one registrable domain.

Steps
  targets : write data/processed/org_search_input.csv (entity_id, names) for universe ∪ labelled companies,
            to be fed to `python -m scripts.sources.github find` (same staged search for everyone).
  build   : link GitHub org (website == domain AND login rule; high confidence only) and CORDIS (domain equality,
            high confidence), derive country symmetrically (ccTLD, else CORDIS country), and write
            data/processed/entities.csv + data/processed/events.parquet.

Point-in-time notes
  - Links are made on stable keys (domain equality); org website is current state (documented limitation).
  - Every event keeps the time the information became public: GH Archive event time, CORDIS signature date,
    org creation time. Snapshots filter ts < as_of; nothing here uses labels except the `is_labelled` flag,
    which build_snapshots needs to form the case-cohort sample.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from .lib.config import LABELS, PROCESSED, cfg, get_logger
from .lib.domains import country_from_domain
from .lib.meta import write_meta
from .lib.schema import make_events, validate_events

log = get_logger("resolve_entities")


def _names(v) -> list[str]:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return []
    if isinstance(v, str):
        return [x for x in v.split("|") if x]
    return [x for x in list(v) if x]


def load_labels() -> pd.DataFrame:
    p = LABELS / "companies.csv"
    return pd.read_csv(p, dtype=str, keep_default_na=False) if p.exists() else pd.DataFrame(columns=["company", "domain"])


def targets() -> pd.DataFrame:
    u = pd.read_parquet(PROCESSED / "universe.parquet")
    lab = load_labels()
    rows = {}
    for r in u.itertuples():
        rows.setdefault(r.entity_id, set()).update(_names(r.names))
    for r in lab.itertuples():
        rows.setdefault(r.domain, set()).update([r.company] if isinstance(r.company, str) else [])
    df = pd.DataFrame({"entity_id": list(rows), "names": ["|".join(sorted(v)) for v in rows.values()]})
    df = df.sort_values("entity_id").reset_index(drop=True)
    out = PROCESSED / "org_search_input.csv"
    df.to_csv(out, index=False)
    write_meta(out, "scripts/resolve_entities.py targets", rows=len(df),
               inputs=[PROCESSED / "universe.parquet", LABELS / "companies.csv"],
               notes="Search input for the staged GitHub org search; identical procedure for every entity.")
    log.info("org search targets: %d entities", len(df))
    return df


CORDIS_TO_ISO = {"EL": "GR", "UK": "GB"}


def cordis_entity_summary(links: pd.DataFrame, part: pd.DataFrame) -> pd.DataFrame:
    """Per entity: first public grant time, CORDIS country, match method — from entity_cordis links (R1/R2)."""
    p = part[(part["activity_type"] == "PRC") & part["public_ts"].notna()]
    m = links.merge(p[["organisation_id", "public_ts", "country"]], on="organisation_id", how="inner")
    if m.empty:
        return pd.DataFrame(columns=["entity_id", "cordis_first_public_ts", "cordis_country", "cordis_match_method",
                                     "cordis_match_confidence"])
    m["country"] = m["country"].map(lambda c: CORDIS_TO_ISO.get(c, c) if isinstance(c, str) else None)
    g = m.groupby("entity_id")
    return pd.DataFrame({
        "cordis_first_public_ts": g["public_ts"].min(),
        "cordis_country": g["country"].agg(lambda x: x.mode().iat[0] if x.notna().any() else None),
        "cordis_match_method": g["match_method"].first(),
        "cordis_match_confidence": "high",
    }).reset_index()


def build_entities(universe: pd.DataFrame, labels: pd.DataFrame, gh_links: pd.DataFrame | None,
                   cordis_companies: pd.DataFrame | None, gh_span: pd.DataFrame | None) -> pd.DataFrame:
    ent = pd.DataFrame({"entity_id": sorted(set(universe["entity_id"]) | set(labels["domain"].dropna()))})
    ent = ent.merge(universe[["entity_id", "channels", "in_B", "inclusion_prob"]], on="entity_id", how="left")
    ent["in_B"] = ent["in_B"].fillna(False).astype(bool)
    ent["inclusion_prob"] = ent["inclusion_prob"].fillna(0.0)
    ent["is_labelled"] = ent["entity_id"].isin(set(labels["domain"]))
    if gh_links is not None and len(gh_links):
        if "org_first_event_ts" not in gh_links and "org_created_at" in gh_links:   # GitHub-API route
            gh_links = gh_links.rename(columns={"org_created_at": "org_first_event_ts"})
        g = gh_links.rename(columns={"org_login": "github_org", "org_first_event_ts": "github_org_created_at",
                                     "match_method": "github_match_method",
                                     "match_confidence": "github_match_confidence", "tier": "github_tier"})
        g = g[g["github_match_confidence"] == "high"]
        ent = ent.merge(g[["entity_id", "github_org", "github_org_created_at", "github_match_method",
                           "github_match_confidence", "github_tier"]], on="entity_id", how="left")
    else:
        for c in ["github_org", "github_org_created_at", "github_match_method", "github_match_confidence", "github_tier"]:
            ent[c] = None
    if gh_span is not None and len(gh_span):
        s = gh_span.rename(columns={"owner": "github_org", "first_event_ts": "gh_first_event_ts",
                                    "last_event_ts": "gh_last_event_ts", "n_events": "gh_n_events"})
        s["github_org"] = s["github_org"].str.lower()
        ent["_org_l"] = ent["github_org"].str.lower()
        ent = ent.merge(s.rename(columns={"github_org": "_org_l"})[["_org_l", "gh_first_event_ts", "gh_last_event_ts",
                                                                   "gh_n_events"]], on="_org_l", how="left")
        ent = ent.drop(columns="_org_l")
    else:
        ent["gh_first_event_ts"] = pd.NaT
        ent["gh_n_events"] = np.nan
    if cordis_companies is not None and len(cordis_companies):
        if "cordis_first_public_ts" in cordis_companies:            # per-entity summary from entity_cordis links
            c = cordis_companies
        else:                                                         # fallback: domain-only cordis_companies table
            c = cordis_companies.rename(columns={"domain": "entity_id", "first_public_ts": "cordis_first_public_ts",
                                                 "country": "cordis_country"})
            c["cordis_match_method"] = "domain"
            c["cordis_match_confidence"] = "high"
        ent = ent.merge(c[["entity_id", "cordis_first_public_ts", "cordis_country", "cordis_match_method",
                           "cordis_match_confidence"]], on="entity_id", how="left")
    else:
        for col in ["cordis_first_public_ts", "cordis_country", "cordis_match_method", "cordis_match_confidence"]:
            ent[col] = None
    # Country: symmetric sources only (ccTLD, then CORDIS). YC/HTGF country is never used here.
    cc = ent["entity_id"].map(country_from_domain)
    ent["country"] = cc.where(cc.notna(), ent["cordis_country"])
    ent["country_source"] = np.where(cc.notna(), "ccTLD", np.where(ent["cordis_country"].notna(), "cordis", None))
    eu = set(cfg()["europe_iso2"])
    he = set(cfg()["horizon_eligible_iso2"])
    ent["europe"] = ent["country"].isin(eu) | ent["entity_id"].str.endswith(".eu")
    ent["horizon_eligible"] = ent["country"].isin(he) | ent["entity_id"].str.endswith(".eu")
    for col in ["github_org_created_at", "gh_first_event_ts", "cordis_first_public_ts"]:
        ent[col] = pd.to_datetime(ent[col], utc=True)
    return ent.sort_values("entity_id").reset_index(drop=True)


def build_events(ent: pd.DataFrame) -> pd.DataFrame:
    recs = []
    org2ent: dict[str, list[str]] = {}
    for r in ent[ent["github_org"].notna()].itertuples():
        org2ent.setdefault(r.github_org.lower(), []).append(r.entity_id)
    p = PROCESSED / "gh_org_span.parquet"
    if p.exists():
        for r in pd.read_parquet(p).itertuples():
            for e in org2ent.get(str(r.owner).lower(), []):
                recs.append({"entity_id": e, "ts": r.first_event_ts, "source": "gharchive", "type": "org_first_event",
                             "value": 1.0, "detail": {"org": r.owner}})
    p = PROCESSED / "gh_org_activity_daily.parquet"
    if p.exists():
        d = pd.read_parquet(p)
        d["owner_l"] = d["owner"].str.lower()
        for r in d.itertuples():
            for e in org2ent.get(r.owner_l, []):
                total = float(r.push + r.pr + r.issues + r.release)
                recs.append({"entity_id": e, "ts": pd.Timestamp(r.day, tz="UTC"), "source": "gharchive",
                             "type": "org_activity", "value": total,
                             "detail": {"org": r.owner, "push": int(r.push), "pr": int(r.pr),
                                        "issues": int(r.issues), "release": int(r.release)}})
    team = PROCESSED / "gh_team.parquet"
    ext = PROCESSED / "gh_ext_monthly.parquet"
    if team.exists():
        t = pd.read_parquet(team)
        t["owner_l"] = t["owner"].str.lower()
        actor2orgs: dict[str, set] = {}
        for r in t.itertuples():
            actor2orgs.setdefault(r.actor_hash, set()).add(r.owner_l)
            for e in org2ent.get(r.owner_l, []):
                recs.append({"entity_id": e, "ts": r.first_push_ts, "source": "gharchive", "type": "team_first_push",
                             "value": 1.0, "detail": {"org": r.owner, "actor": r.actor_hash}})
        if ext.exists():
            x = pd.read_parquet(ext)
            for r in x.itertuples():
                for org in actor2orgs.get(r.actor_hash, ()):
                    if str(r.owner_bucket).lower() == org:      # repos owned by this org are not external
                        continue
                    n = float(r.pr_opened + r.issues_opened + r.pushes)
                    if n <= 0:
                        continue
                    for e in org2ent.get(org, []):
                        recs.append({"entity_id": e, "ts": pd.Timestamp(r.month, tz="UTC") if pd.Timestamp(r.month).tzinfo is None else r.month,
                                     "source": "gharchive", "type": "ext_engagement_month", "value": n,
                                     "detail": {"org": org, "actor": r.actor_hash, "pr_opened": int(r.pr_opened),
                                                "issues_opened": int(r.issues_opened), "pushes": int(r.pushes)}})
    p = PROCESSED / "cordis_participations.parquet"
    lp = PROCESSED / "entity_cordis.parquet"
    if p.exists():
        cp = pd.read_parquet(p)
        cp = cp[(cp["activity_type"] == "PRC") & cp["public_ts"].notna()]
        if lp.exists():
            links = pd.read_parquet(lp)
            links = links[links["entity_id"].isin(set(ent["entity_id"]))]
            cp = cp.merge(links[["entity_id", "organisation_id", "match_method"]], on="organisation_id", how="inner")
        else:
            cp = cp[cp["domain"].isin(set(ent["entity_id"]))].assign(entity_id=lambda d: d["domain"], match_method="domain")
        for r in cp.itertuples():
            recs.append({"entity_id": r.entity_id, "ts": r.public_ts, "source": "cordis", "type": "grant_signed",
                         "value": float(r.ec_contribution) if pd.notna(r.ec_contribution) else 0.0,
                         "detail": {"programme": r.programme, "project": str(r.project_id),
                                    "match": r.match_method}})
    ev = make_events(recs)
    ev = ev.sort_values(["entity_id", "ts", "source", "type", "detail"]).reset_index(drop=True)
    errs = validate_events(ev)
    if errs:
        raise ValueError(f"event validation failed: {errs}")
    return ev


def build():
    universe = pd.read_parquet(PROCESSED / "universe.parquet")
    labels = load_labels()
    paths = {k: PROCESSED / f for k, f in [("links", "entity_github_org.parquet"), ("cordis", "cordis_companies.parquet"),
                                            ("span", "gh_org_span.parquet"), ("cordis_links", "entity_cordis.parquet"),
                                            ("cordis_part", "cordis_participations.parquet")]}
    get = lambda k: pd.read_parquet(paths[k]) if paths[k].exists() else None
    cordis = (cordis_entity_summary(get("cordis_links"), get("cordis_part"))
              if paths["cordis_links"].exists() and paths["cordis_part"].exists() else get("cordis"))
    ent = build_entities(universe, labels, get("links"), cordis, get("span"))
    out = PROCESSED / "entities.csv"
    ent.to_csv(out, index=False)
    write_meta(out, "scripts/resolve_entities.py build", rows=len(ent),
               inputs=[PROCESSED / "universe.parquet", LABELS / "companies.csv"] + [p for p in paths.values() if p.exists()],
               notes="One row per domain. Only high-confidence links. Country from ccTLD else CORDIS (symmetric).")
    log.info("entities: %d (labelled %d, in_B %d, with github %d, with cordis %d)", len(ent), ent["is_labelled"].sum(),
             ent["in_B"].sum(), ent["github_org"].notna().sum(), ent["cordis_first_public_ts"].notna().sum())
    ev = build_events(ent)
    evp = PROCESSED / "events.parquet"
    ev.to_parquet(evp, index=False)
    write_meta(evp, "scripts/resolve_entities.py build", rows=len(ev), inputs=[out],
               notes="Unified event schema: entity_id, ts (public time, UTC), source, type, value, detail.")
    log.info("events: %d rows %s", len(ev), ev.groupby(["source", "type"]).size().to_dict())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("step", choices=["targets", "build"])
    a = ap.parse_args(argv)
    targets() if a.step == "targets" else build()


if __name__ == "__main__":
    main()
