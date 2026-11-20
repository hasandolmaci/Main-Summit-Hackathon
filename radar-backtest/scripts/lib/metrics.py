"""Evaluation metrics with deterministic tie-breaking and entity-clustered bootstrap."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


def auc(y, s, w=None) -> float:
    y = np.asarray(y)
    if y.min() == y.max():
        return float("nan")
    return float(roc_auc_score(y, s, sample_weight=w))


def pooled_auc(df: pd.DataFrame, score: str = "score", weight: str | None = "weight", group: str = "as_of") -> float:
    """AUC pooled across as_of cohorts, comparing positives and negatives only WITHIN the same cohort.

    = sum_g pairs_g * AUC_g / sum_g pairs_g  (pairs weighted by case-cohort weights).
    """
    num = den = 0.0
    for _, g in df.groupby(group):
        y = g["label"].values
        if y.min() == y.max():
            continue
        w = g[weight].values if weight else np.ones(len(g))
        pairs = w[y == 1].sum() * w[y == 0].sum()
        num += pairs * auc(y, g[score].values, w)
        den += pairs
    return num / den if den else float("nan")


def rank_order(scores, seed: int) -> np.ndarray:
    """Indices sorted by score desc; ties broken by a seeded random key (never by row order)."""
    s = np.nan_to_num(np.asarray(scores, dtype=float), nan=-np.inf)
    tie = np.random.default_rng(seed).random(len(s))
    return np.lexsort((tie, -s))


def precision_at_k(y, scores, k: int, seed: int) -> float:
    y = np.asarray(y)
    if len(y) == 0:
        return float("nan")
    top = rank_order(scores, seed)[: min(k, len(y))]
    return float(y[top].mean())


def recall_at_k(y, scores, k: int, seed: int) -> float:
    y = np.asarray(y)
    if y.sum() == 0:
        return float("nan")
    top = rank_order(scores, seed)[: min(k, len(y))]
    return float(y[top].sum() / y.sum())


def mean_precision_at_k(df: pd.DataFrame, k: int, seed: int, score: str = "score") -> float:
    """precision@k computed per as_of cohort, averaged across cohorts (k is per-year)."""
    vals = [precision_at_k(g["label"].values, g[score].values, k, seed + i) for i, (_, g) in enumerate(df.groupby("as_of"))]
    return float(np.nanmean(vals)) if vals else float("nan")


def cluster_bootstrap(df: pd.DataFrame, stat, reps: int, seed: int, cluster: str = "entity_id",
                      strata: str = "ever_positive") -> np.ndarray:
    """Resample clusters (entities) with replacement within strata (ever positive / never); returns stat per rep."""
    rng = np.random.default_rng(seed)
    df = df.copy()
    if strata not in df:
        df[strata] = df.groupby(cluster)["label"].transform("max")
    groups = {k: g for k, g in df.groupby(cluster)}
    keys_by_stratum = {s: np.array(sorted(df.loc[df[strata] == s, cluster].unique())) for s in df[strata].unique()}
    out = np.empty(reps)
    for r in range(reps):
        parts = []
        for s, keys in keys_by_stratum.items():
            pick = rng.choice(keys, size=len(keys), replace=True)
            parts.extend(groups[k] for k in pick)
        out[r] = stat(pd.concat(parts, ignore_index=True))
    return out


def ci(values: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    v = np.asarray(values)[~np.isnan(values)]
    if v.size == 0:
        return float("nan"), float("nan")
    a = (1 - level) / 2
    return float(np.quantile(v, a)), float(np.quantile(v, 1 - a))
