"""Leakage and feature tests on synthetic data (offline)."""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from scripts import build_snapshots as bs
from scripts.lib.schema import make_events

UTC = timezone.utc
A = datetime(2016, 1, 1, tzinfo=UTC)


def _events(extra=()):
    rows = [
        # team: u1 pushed before as_of, u2 only after as_of
        dict(entity_id="a.com", ts="2014-03-01", source="gharchive", type="team_first_push", value=1, detail={"actor": "u1", "org": "a"}),
        dict(entity_id="a.com", ts="2016-02-01", source="gharchive", type="team_first_push", value=1, detail={"actor": "u2", "org": "a"}),
        # external engagement months
        dict(entity_id="a.com", ts="2015-06-01", source="gharchive", type="ext_engagement_month", value=5, detail={"actor": "u1"}),
        dict(entity_id="a.com", ts="2014-12-01", source="gharchive", type="ext_engagement_month", value=7, detail={"actor": "u1"}),  # outside 12m
        dict(entity_id="a.com", ts="2015-07-01", source="gharchive", type="ext_engagement_month", value=11, detail={"actor": "u2"}),  # u2 not team yet
        # org activity: W1 = [2015-10-03, 2016-01-01), W0 = [2015-07-05, 2015-10-03)
        dict(entity_id="a.com", ts="2015-12-31", source="gharchive", type="org_activity", value=9, detail={}),
        dict(entity_id="a.com", ts="2015-08-01", source="gharchive", type="org_activity", value=2, detail={}),
        dict(entity_id="a.com", ts="2015-01-01", source="gharchive", type="org_activity", value=100, detail={}),  # outside both
        # grant
        dict(entity_id="b.de", ts="2015-05-05", source="cordis", type="grant_signed", value=1e5, detail={}),
    ]
    ev = make_events(rows + list(extra))
    ev["actor"] = ev["detail"].map(lambda d: json.loads(d).get("actor"))
    return ev


def test_raw_features_windows_and_team():
    raw = bs.compute_raw_features(_events(), ["a.com", "b.de"], A).set_index("entity_id")
    assert raw.loc["a.com", "eng_raw"] == 5          # only u1 (team before as_of), only within 12 months
    assert raw.loc["a.com", "act_w1"] == 9
    assert raw.loc["a.com", "act_w0"] == 2
    assert raw.loc["b.de", "grant_raw"] == 1
    assert raw.loc["a.com", "grant_raw"] == 0


@pytest.mark.parametrize("future", [
    dict(entity_id="a.com", ts="2016-01-01", source="gharchive", type="org_activity", value=1000, detail={}),
    dict(entity_id="a.com", ts="2016-01-01", source="gharchive", type="ext_engagement_month", value=1000, detail={"actor": "u1"}),
    dict(entity_id="a.com", ts="2017-01-01", source="gharchive", type="team_first_push", value=1, detail={"actor": "u9"}),
    dict(entity_id="a.com", ts="2016-03-01", source="cordis", type="grant_signed", value=1, detail={}),
    dict(entity_id="b.de", ts="2030-01-01", source="gharchive", type="org_activity", value=5, detail={}),
])
def test_no_feature_uses_events_at_or_after_as_of(future):
    base = bs.compute_raw_features(_events(), ["a.com", "b.de"], A)
    pert = bs.compute_raw_features(_events([future]), ["a.com", "b.de"], A)
    pd.testing.assert_frame_equal(base, pert)


def test_random_future_mutation_never_changes_features():
    rng = np.random.default_rng(0)
    base = bs.compute_raw_features(_events(), ["a.com", "b.de"], A)
    types = ["org_activity", "ext_engagement_month", "team_first_push", "grant_signed"]
    for _ in range(50):
        extra = [dict(entity_id=str(rng.choice(["a.com", "b.de"])),
                      ts=(pd.Timestamp(A) + pd.Timedelta(days=int(rng.integers(0, 3000)))).isoformat(),
                      source="gharchive", type=str(rng.choice(types)), value=float(rng.integers(1, 99)),
                      detail={"actor": str(rng.choice(["u1", "u2", "u3"]))}) for _ in range(5)]
        pd.testing.assert_frame_equal(base, bs.compute_raw_features(_events(extra), ["a.com", "b.de"], A))


def test_scale_missing_is_nan_not_zero_and_uses_reference_only():
    raw = pd.DataFrame({"entity_id": ["a.com", "b.de", "c.com"], "eng_raw": [5.0, 0.0, 3.0],
                        "act_w1": [9.0, 0.0, 0.0], "act_w0": [2.0, 0.0, 0.0], "grant_raw": [0.0, 1.0, 0.0]})
    cov = pd.DataFrame({"entity_id": ["a.com", "b.de", "c.com"], "has_github": [True, False, True],
                        "has_cordis": [False, True, False], "visible": [True, True, True]})
    out = bs.scale(raw, cov, ref_ids={"a.com", "c.com"}).set_index("entity_id")
    assert np.isnan(out.loc["b.de", "oss_external_engagement"])            # no GitHub -> missing
    assert np.isnan(out.loc["b.de", "github_activity_acceleration"])
    assert np.isnan(out.loc["a.com", "has_public_rd_grant"])               # not CORDIS-covered -> missing
    assert out.loc["b.de", "has_public_rd_grant"] == 1
    assert out.loc["a.com", "github_activity_acceleration"] > 0.5
    v = out[["oss_external_engagement", "github_activity_acceleration"]].dropna().values
    assert ((v >= 0) & (v <= 1)).all()


def test_scale_signature_has_no_labels():
    import inspect
    assert not set(inspect.signature(bs.scale).parameters) & {"label", "labels", "y", "funding_date"}
    assert not set(inspect.signature(bs.compute_raw_features).parameters) & {"label", "labels", "y", "funding_date"}


def test_engagement_scaling_alternatives_are_monotone_and_bounded():
    raw = pd.DataFrame({"entity_id": list("abcde"), "eng_raw": [0.0, 1.0, 3.0, 10.0, 50.0],
                        "act_w1": 0.0, "act_w0": 0.0, "grant_raw": 0.0})
    cov = pd.DataFrame({"entity_id": list("abcde"), "has_github": True, "has_cordis": False, "visible": True})
    for mode in ("robust_z", "percentile"):
        out = bs.scale(raw, cov, set("abcde"), eng_scaling=mode)["oss_external_engagement"].to_numpy()
        assert np.all(np.diff(out) > 0) and np.all((out >= 0) & (out <= 1))
