import numpy as np
import pandas as pd
import pytest

from scripts.lib import metrics as m


def test_rank_order_ties_not_by_row_order():
    s = [1.0, 1.0, 1.0, 1.0, 0.0]
    a = m.rank_order(s, seed=1)
    b = m.rank_order(s, seed=2)
    assert a[-1] == 4 and b[-1] == 4
    assert set(a[:4]) == {0, 1, 2, 3}
    # shuffling row order must not change precision (labels sorted first + stable sort would inflate it)
    y = np.array([1, 1, 0, 0, 0])
    p1 = m.precision_at_k(y, s, 2, seed=3)
    perm = np.array([4, 3, 2, 1, 0])
    vals = [m.precision_at_k(y[perm], np.asarray(s)[perm], 2, seed=k) for k in range(200)]
    assert abs(np.mean(vals) - 0.5) < 0.1 and p1 in (0.0, 0.5, 1.0)


def test_nan_scores_rank_last():
    o = m.rank_order([np.nan, 0.2, 0.9], seed=0)
    assert list(o) == [2, 1, 0]


def test_pooled_auc_within_cohort_only():
    # year 1 scores are all high, year 2 all low; perfect within each year -> pooled AUC = 1
    df = pd.DataFrame({"as_of": [1, 1, 1, 2, 2, 2], "label": [1, 0, 0, 1, 0, 0],
                       "score": [10, 9, 8, 3, 2, 1], "weight": 1.0})
    assert m.pooled_auc(df) == pytest.approx(1.0)
    naive = m.auc(df.label, df.score)
    assert naive < 1.0


def test_shuffled_labels_auc_near_half():
    rng = np.random.default_rng(0)
    n = 4000
    s = rng.random(n)
    y = (rng.random(n) < 0.1).astype(int)
    df = pd.DataFrame({"as_of": rng.integers(0, 3, n), "label": rng.permutation(y), "score": s, "weight": 1.0})
    assert abs(m.pooled_auc(df) - 0.5) < 0.03


def test_cluster_bootstrap_deterministic_and_ci():
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"entity_id": np.repeat(np.arange(200), 2), "as_of": np.tile([1, 2], 200)})
    df["label"] = (rng.random(400) < 0.2).astype(int)
    df["score"] = df["label"] + rng.normal(0, 1, 400)
    df["weight"] = 1.0
    a = m.cluster_bootstrap(df, m.pooled_auc, reps=50, seed=7)
    b = m.cluster_bootstrap(df, m.pooled_auc, reps=50, seed=7)
    assert np.array_equal(a, b)
    lo, hi = m.ci(a)
    assert lo < m.pooled_auc(df) < hi


def test_auc_nan_scores_rank_last():
    assert m.auc([1, 0, 0], [np.nan, 0.2, 0.1]) == 0.0
    assert m.auc([1, 0], [np.nan, np.nan]) == 0.5
    assert m.auc([1, 0, 0], [0.9, np.nan, 0.1]) == 1.0


def test_mean_recall_at_k():
    df = pd.DataFrame({"as_of": [1, 1, 1, 2, 2], "label": [1, 1, 0, 1, 0], "score": [3, 1, 2, 5, 4]})
    # year 1: top-1 = score 3 (pos) -> recall 1/2; year 2: top-1 = pos -> 1/1  => mean 0.75
    assert m.mean_recall_at_k(df, 1, seed=0) == pytest.approx(0.75)
