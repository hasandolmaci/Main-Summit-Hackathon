import numpy as np
import pandas as pd
import pytest

from scripts import backtest as bt
from scripts.build_snapshots import FEATURES
from scripts.lib.config import as_of_date
from scripts.lib.metrics import pooled_auc


def test_partial_distance_rescaling():
    X = np.array([[0.0, 0.0, np.nan]])
    R = np.array([[1.0, 0.0, 0.5], [np.nan, np.nan, 0.2]])
    d = bt.partial_distance(X, R)
    assert d[0, 0] == pytest.approx(np.sqrt(1.0 * 3 / 2))     # 2 of 3 dims observed -> * sqrt(3/2)
    assert np.isinf(d[0, 1])                                   # no shared dimension
    w = np.array([2.0, 0.0, 1.0])
    dw = bt.partial_distance(X, R, w)
    assert dw[0, 0] == pytest.approx(np.sqrt(2.0 * 1.0 * 3 / 2))


def _refs():
    return pd.DataFrame({"entity_id": ["r1", "r2", "c1", "r_old", "r_future"],
                         "funding_date": pd.to_datetime(["2015-06-01", "2014-01-01", "2015-01-01", "2009-01-01", "2016-02-01"], utc=True),
                         "profile_date": pd.to_datetime(["2015-03-01", "2013-10-01", "2014-10-01", "2008-10-01", "2015-11-01"], utc=True),
                         **{f: [0.5, 0.5, 0.5, 0.5, 0.5] for f in FEATURES}})


def test_reference_set_only_past_funding_within_window():
    rs = bt.reference_set(_refs(), as_of_date(2016), window_years=5)
    assert set(rs["entity_id"]) == {"r1", "r2", "c1"}          # r_old outside 5y, r_future funded after as_of
    rs3 = bt.reference_set(_refs(), as_of_date(2016), window_years=1)
    assert set(rs3["entity_id"]) == {"r1", "c1"}


def test_never_own_reference():
    cand = pd.DataFrame({"entity_id": ["c1"], **{f: [0.5] for f in FEATURES}})
    refs = bt.reference_set(_refs(), as_of_date(2016), 5)
    s_all = bt.knn_scores(cand, refs, k=1)
    # c1 itself (distance 0) is excluded; others are also at distance 0 -> score 1 from r1/r2
    assert s_all[0] == pytest.approx(1.0)
    only_self = refs[refs.entity_id == "c1"]
    assert np.isnan(bt.knn_scores(cand, only_self, k=1)[0])


def _synthetic_snaps(n_per_year=300, years=range(2010, 2020), seed=0, signal=1.0):
    rng = np.random.default_rng(seed)
    rows = []
    for y in years:
        x = rng.random((n_per_year, 3))
        logit = -2.5 + signal * 3 * (x[:, 0] - 0.5)
        lab = (rng.random(n_per_year) < 1 / (1 + np.exp(-logit))).astype(int)
        for i in range(n_per_year):
            rows.append({"entity_id": f"e{y}_{i}", "as_of": as_of_date(y), "label": lab[i], "weight": 1.0 if lab[i] else 3.0,
                         "in_B": True, "inclusion_prob": 1 / 3, "europe": bool(i % 5 == 0), "has_github": True,
                         "has_cordis": bool(i % 2), "labels_complete": True, "funding_date": pd.Timestamp(f"{y}-06-01", tz="UTC") if lab[i] else pd.NaT,
                         FEATURES[0]: x[i, 0], FEATURES[1]: x[i, 1], FEATURES[2]: float(x[i, 2] > 0.5)})
    return pd.DataFrame(rows)


def test_training_embargo():
    snaps = _synthetic_snaps()
    captured = {}
    orig = bt.train_logreg
    def spy(train, features=FEATURES):
        captured["max_as_of"] = train["as_of"].max()
        return orig(train, features)
    bt.train_logreg = spy
    try:
        bt.score_year(2016, snaps, pd.DataFrame())
    finally:
        bt.train_logreg = orig
    assert captured["max_as_of"] <= as_of_date(2014)


def test_signal_detected_and_shuffled_labels_near_half():
    snaps = _synthetic_snaps(signal=1.5)
    pred = pd.concat([bt.score_year(y, snaps, pd.DataFrame())[0] for y in range(2015, 2020)])
    lr = pred[pred.method == "logreg"]
    assert pooled_auc(lr) > 0.6
    shuffled = snaps.copy()
    rng = np.random.default_rng(5)
    shuffled["label"] = shuffled.groupby("as_of")["label"].transform(lambda s: rng.permutation(s.values))
    pred_s = pd.concat([bt.score_year(y, shuffled, pd.DataFrame())[0] for y in range(2015, 2020)])
    assert abs(pooled_auc(pred_s[pred_s.method == "random"]) - 0.5) < 0.06
    assert abs(pooled_auc(pred_s[pred_s.method == "logreg"]) - 0.5) < 0.08


def test_deterministic():
    snaps = _synthetic_snaps()
    a = bt.score_year(2017, snaps, pd.DataFrame())[0]
    b = bt.score_year(2017, snaps, pd.DataFrame())[0]
    pd.testing.assert_frame_equal(a, b)
