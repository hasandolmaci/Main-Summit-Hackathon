"""End-to-end smoke test on synthetic data: entities/events/labels -> snapshots -> backtest -> report."""
import json

import numpy as np
import pandas as pd
import pytest

from scripts import backtest, build_snapshots, report
from scripts.lib.schema import make_events


def _synthetic(tmp, n=400, seed=0):
    rng = np.random.default_rng(seed)
    proc, res, lab = tmp / "processed", tmp / "results", tmp / "labels"
    for d in (proc, res, lab, res / "figures"):
        d.mkdir(parents=True, exist_ok=True)
    ents, events, labels, prov = [], [], [], []
    for i in range(n):
        e = f"c{i}.{'de' if i % 4 == 0 else 'com'}"
        first = pd.Timestamp("2010-06-01", tz="UTC") + pd.Timedelta(days=int(rng.integers(0, 365 * 7)))
        funded = rng.random() < 0.25
        fdate = first + pd.Timedelta(days=int(rng.integers(400, 1800))) if funded else None
        strength = 3.0 if funded else 1.0
        ents.append({"entity_id": e, "channels": "github_sample", "in_B": (not funded) or rng.random() < 0.3,
                     "inclusion_prob": 0.5, "is_labelled": funded, "github_org": f"c{i}",
                     "github_org_created_at": first, "gh_first_event_ts": first, "gh_pool_entry_ts": first + pd.Timedelta(days=30),
                     "cordis_first_public_ts": pd.NaT, "horizon_eligible": e.endswith(".de"), "europe": e.endswith(".de")})
        events.append({"entity_id": e, "ts": first, "source": "gharchive", "type": "team_first_push", "value": 1,
                       "detail": {"actor": f"a{i}"}})
        for m in range(0, 96, 3):
            t = first + pd.DateOffset(months=m)
            boost = 2.5 if (fdate is not None and fdate - pd.Timedelta(days=400) < t < fdate) else 1.0
            events.append({"entity_id": e, "ts": t, "source": "gharchive", "type": "org_activity",
                           "value": float(rng.poisson(5 * strength * boost)), "detail": {}})
            events.append({"entity_id": e, "ts": t.normalize().replace(day=1), "source": "gharchive",
                           "type": "ext_engagement_month", "value": float(rng.poisson(2 * strength)), "detail": {"actor": f"a{i}"}})
        if e.endswith(".de") and rng.random() < (0.4 if funded else 0.1):
            events.append({"entity_id": e, "ts": first + pd.Timedelta(days=200), "source": "cordis",
                           "type": "grant_signed", "value": 1e5, "detail": {}})
        if funded:
            labels.append({"company": f"C{i}", "domain": e, "first_funding_date": fdate.date().isoformat(),
                           "round_type": "accelerator", "sector": "B2B", "country": "US",
                           "source_url": "https://example.org", "verified_by_human": "n"})
            prov.append({"domain": e, "label_source": "htgf" if i % 7 == 0 else "yc"})
    ent = pd.DataFrame(ents)
    gr = pd.Series([ev["entity_id"] for ev in events if ev["type"] == "grant_signed"])
    first_grant = {e: min(ev["ts"] for ev in events if ev["type"] == "grant_signed" and ev["entity_id"] == e) for e in gr.unique()}
    ent["cordis_first_public_ts"] = ent["entity_id"].map(first_grant)
    ent.to_csv(proc / "entities.csv", index=False)
    make_events(events).to_parquet(proc / "events.parquet", index=False)
    pd.DataFrame(labels).to_csv(lab / "companies.csv", index=False)
    pd.DataFrame(prov).to_csv(lab / "label_provenance.csv", index=False)
    return proc, res, lab


@pytest.fixture
def paths(tmp_path, monkeypatch):
    proc, res, lab = _synthetic(tmp_path)
    for mod in (build_snapshots, backtest, report):
        for name, val in (("PROCESSED", proc), ("RESULTS", res), ("LABELS", lab), ("FIGURES", res / "figures")):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, val)
    monkeypatch.setattr(build_snapshots, "write_meta", lambda *a, **k: None)
    monkeypatch.setattr(backtest, "write_meta", lambda *a, **k: None)
    return proc, res, lab


def test_end_to_end(paths):
    proc, res, lab = paths
    build_snapshots.main(["--years", *map(str, range(2012, 2020))])
    assert (proc / "snapshots_2016.parquet").exists()
    s = pd.read_parquet(proc / "snapshots_2016.parquet")
    assert s["label"].sum() > 0 and (s["label"] == 0).sum() > 0
    assert s.loc[~s["entity_id"].str.endswith(".de"), "has_public_rd_grant"].isna().all()   # .com -> not covered
    backtest.main(["--reps", "30"])
    met = pd.read_csv(res / "metrics_main.csv")
    lr = met[(met.subset == "all") & (met.method == "logreg")].iloc[0]
    rnd = met[(met.subset == "all") & (met.method == "random")].iloc[0]
    assert lr["auc"] > 0.6 > rnd["auc"] - 0.1       # synthetic signal is found; random is ~0.5
    md = report.build("main")
    assert "Headline" in md and "of" in md and (res / "report.md").exists()
    assert (res / "figures" / "auc_by_method.png").exists()
