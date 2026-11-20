"""Build the outcome-blind universe (pool P) and background sample B.

THIS MODULE MUST NEVER READ LABELS. It only uses pool sources:
  (a) data/processed/github_org_sample.parquet  (hash sample of GH Archive owners per first-activity year, in_pool rule applied)
  (b) data/processed/cordis_companies.parquet   (CORDIS private companies with a company domain)
B membership is decided by sampling probabilities fixed here, before any label lookup.
Controls = B minus labelled entities (done later, in resolve_entities / build_snapshots).

Output: data/processed/universe.parquet
  entity_id, channels, in_B, inclusion_prob, entry_ts_github_sample, entry_ts_cordis, names, sampled_org_login
"""
from __future__ import annotations

import argparse
import hashlib

import numpy as np
import pandas as pd

from .lib.config import PROCESSED, cfg, get_logger, seed
from .lib.meta import write_meta

log = get_logger("select_controls")


def hash_uniform(key: str, salt: int) -> float:
    """Deterministic U(0,1) from a string key (stable across runs/machines)."""
    h = hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()
    return int(h[:15], 16) / float(16 ** 15)


def github_channel(sample: pd.DataFrame) -> pd.DataFrame:
    s = sample[sample["in_pool"] & sample["website_domain"].notna()].copy()
    if "first_event_ts" not in s:            # GitHub-API route (scripts/sources/github.py) has created_at instead
        s["first_event_ts"] = s["created_at"]
    # one entity per domain: keep earliest-created sampled org (deterministic)
    s = s.sort_values(["website_domain", "first_event_ts", "login"]).drop_duplicates("website_domain")
    return pd.DataFrame({
        "entity_id": s["website_domain"].values,
        "p_github": s["inclusion_prob"].astype(float).values,
        "entry_ts_github_sample": pd.to_datetime(s["first_event_ts"], utc=True).values,
        "sampled_org_login": s["login"].values,
        "names_github": s["display_name"].fillna("").map(lambda x: (x,) if x else ()).values,
    })


def cordis_channel(companies: pd.DataFrame, fraction: float, salt: int) -> pd.DataFrame:
    c = companies[companies["domain"].notna()].copy()
    c["u"] = c["domain"].map(lambda d: hash_uniform(d, salt))
    c = c[c["u"] < fraction]
    names = c["names"].map(lambda v: tuple(v) if isinstance(v, (list, tuple, np.ndarray)) else ((v,) if v else ()))
    return pd.DataFrame({
        "entity_id": c["domain"].values,
        "p_cordis": fraction,
        "entry_ts_cordis": pd.to_datetime(c["first_public_ts"], utc=True).values,
        "names_cordis": names.values,
    })


def build_universe(sample: pd.DataFrame | None, companies: pd.DataFrame | None, fraction: float, salt: int) -> pd.DataFrame:
    parts = []
    g = github_channel(sample) if sample is not None else pd.DataFrame(columns=["entity_id"])
    c = cordis_channel(companies, fraction, salt) if companies is not None else pd.DataFrame(columns=["entity_id"])
    u = g.merge(c, on="entity_id", how="outer") if len(g) or len(c) else pd.DataFrame(columns=["entity_id"])
    for col in ["p_github", "p_cordis"]:
        if col not in u:
            u[col] = np.nan
    pa = u["p_github"].fillna(0.0)
    pb = u["p_cordis"].fillna(0.0)
    u["inclusion_prob"] = 1.0 - (1.0 - pa) * (1.0 - pb)
    u["in_B"] = True
    u["channels"] = [",".join(x for x, ok in (("github_sample", a > 0), ("cordis", b > 0)) if ok) for a, b in zip(pa, pb)]
    def _names(r):
        out = []
        for col in ("names_github", "names_cordis"):
            v = r.get(col)
            if isinstance(v, tuple):
                out.extend(v)
        return tuple(dict.fromkeys(n for n in out if n))
    u["names"] = u.apply(_names, axis=1) if len(u) else []
    keep = ["entity_id", "channels", "in_B", "inclusion_prob", "entry_ts_github_sample", "entry_ts_cordis", "names",
            "sampled_org_login"]
    for k in keep:
        if k not in u:
            u[k] = None
    parts.append(u[keep])
    return parts[0].sort_values("entity_id").reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cordis-fraction", type=float, default=cfg()["pool"]["cordis_sample_fraction"])
    args = ap.parse_args(argv)
    sp = PROCESSED / "github_org_sample.parquet"
    cp = PROCESSED / "cordis_companies.parquet"
    sample = pd.read_parquet(sp) if sp.exists() else None
    companies = pd.read_parquet(cp) if cp.exists() else None
    if sample is None and companies is None:
        raise SystemExit("no pool sources found; run `make orgs` and `make cordis` first")
    u = build_universe(sample, companies, args.cordis_fraction, seed())
    out = PROCESSED / "universe.parquet"
    u2 = u.copy()
    u2["names"] = u2["names"].map(list)
    u2.to_parquet(out, index=False)
    write_meta(out, "scripts/select_controls.py", inputs=[p for p in (sp, cp) if p.exists()],
               params={"cordis_fraction": args.cordis_fraction, "seed": seed()}, rows=len(u),
               notes="Outcome-blind pool sample B. Never reads labels.")
    log.info("universe: %d entities (%s)", len(u), u["channels"].value_counts().to_dict())


if __name__ == "__main__":
    main()
