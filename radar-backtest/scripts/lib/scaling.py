"""Cohort scaling to [0, 1]. These functions never receive labels.

Reference distribution = the same-year, outcome-blind background sample B.
Missing values (source does not cover the entity) stay NaN.
"""
from __future__ import annotations

import numpy as np


def _clean(ref) -> np.ndarray:
    ref = np.asarray(ref, dtype=float)
    return ref[~np.isnan(ref)]


def percentile_rank(values, ref) -> np.ndarray:
    """Mid-rank empirical CDF of `values` against `ref` (NaN stays NaN)."""
    v = np.asarray(values, dtype=float)
    r = np.sort(_clean(ref))
    out = np.full(v.shape, np.nan)
    if r.size == 0:
        return out
    ok = ~np.isnan(v)
    lo = np.searchsorted(r, v[ok], side="left")
    hi = np.searchsorted(r, v[ok], side="right")
    out[ok] = (lo + 0.5 * (hi - lo)) / r.size
    return out


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def log_robust_z_sigmoid(values, ref) -> np.ndarray:
    """log1p -> robust z (median / (IQR/1.349) of ref) -> sigmoid. Falls back to percentile if IQR == 0."""
    v = np.log1p(np.asarray(values, dtype=float))
    r = np.log1p(_clean(ref))
    if r.size == 0:
        return np.full(v.shape, np.nan)
    q1, med, q3 = np.percentile(r, [25, 50, 75])
    iqr = q3 - q1
    if iqr <= 0:
        return percentile_rank(values, ref)
    return sigmoid((v - med) / (iqr / 1.349))


def log_ratio_sigmoid(w1, w0, clip: float = 3.0) -> np.ndarray:
    """Activity acceleration: log((W1+1)/(W0+1)) -> clip to +-clip -> sigmoid. NaN stays NaN."""
    w1 = np.asarray(w1, dtype=float)
    w0 = np.asarray(w0, dtype=float)
    x = np.log((w1 + 1.0) / (w0 + 1.0))
    return sigmoid(np.clip(x, -clip, clip))
