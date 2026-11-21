"""Startup Radar scoring engine.

Everything about how a metric is processed lives in metrics.csv; this module only executes it.
No LLM is called here: categorical labels are mapped with a frozen table written once beforehand.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ENGINE = Path(__file__).resolve().parent
MAD_K = 1.4826


# ---------------------------------------------------------------- registry
def load_metrics(path: Path = ENGINE / "metrics.csv", only_active: bool = True) -> pd.DataFrame:
    m = pd.read_csv(path, dtype={"ordinal_map": str})
    m["weight"] = m["weight"].astype(float)
    if only_active:
        m = m[m["active"] == 1]
    return m.reset_index(drop=True)


def _ordinal_map(spec: str) -> dict[str, float]:
    if spec.startswith("file:"):
        t = pd.read_csv(ENGINE / spec[5:])
        return dict(zip(t["label"].str.strip().str.lower(), t["value"].astype(float)))
    pairs = (p.split(":") for p in spec.split("|"))
    return {k.strip().lower(): float(v) for k, v in pairs}


def _encode_categorical(col: pd.Series, spec: str, aggregate: str) -> pd.Series:
    """Map (possibly comma-separated multi-label) text to numbers via the frozen map."""
    mp = _ordinal_map(spec)
    agg = {"max": max, "mean": lambda xs: sum(xs) / len(xs), "min": min}[aggregate or "max"]

    def one(cell):
        if not isinstance(cell, str) or not cell.strip():
            return np.nan
        vals = [mp[t.strip().lower()] for t in cell.split(",") if t.strip().lower() in mp]
        return agg(vals) if vals else np.nan

    return col.map(one)


# ---------------------------------------------------------------- transforms
def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def _robust_z(x: pd.Series) -> pd.Series:
    obs = x.dropna()
    if obs.empty:
        return x * np.nan
    med = obs.median()
    scale = MAD_K * (obs - med).abs().median()
    if not scale or np.isnan(scale):  # e.g. more than half the sample shares one value
        scale = obs.std(ddof=0)
    if not scale or np.isnan(scale):
        return x * 0.0
    return (x - med) / scale


def _transform(raw: pd.Series, kind: str, ord_max: float | None) -> pd.Series:
    if kind == "binary":
        return (raw > 0).astype(float).where(raw.notna())
    if kind == "identity":
        return raw.clip(0, 1)
    if kind == "ordinal_scale":
        return (raw / ord_max).clip(0, 1)
    if kind == "z_sigmoid":
        return _sigmoid(_robust_z(raw))
    if kind == "log_z_sigmoid":
        return _sigmoid(_robust_z(np.log1p(raw.clip(lower=0))))
    if kind == "ratio_sigmoid":  # raw is already last/prev
        return _sigmoid(np.log(raw.clip(lower=1e-6)))
    raise ValueError(f"unknown transform {kind}")


def build_profiles(companies: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    """Return one [0,1] column per metric. Scaling uses the same-year sample only (never labels)."""
    year = pd.to_datetime(companies["snapshot_date"]).dt.year
    out = pd.DataFrame(index=companies.index)
    for _, m in metrics.iterrows():
        raw = companies[m["source_column"]]
        ord_max = None
        if m["data_type"] in ("categorical", "ordinal"):
            raw = _encode_categorical(raw, m["ordinal_map"], m.get("aggregate"))
            ord_max = max(_ordinal_map(m["ordinal_map"]).values())
        raw = pd.to_numeric(raw, errors="coerce")
        parts = [_transform(raw[year == y], m["transform"], ord_max) for y in sorted(year.unique())]
        out[m["metric_id"]] = pd.concat(parts).reindex(companies.index)
    return out


# ---------------------------------------------------------------- distance + score
def pairwise_distance(A: np.ndarray, B: np.ndarray, w: np.ndarray, min_overlap: int,
                      chunk: int = 400) -> np.ndarray:
    """Weighted RMS distance over metrics known in both profiles, renormalised by the known weights.
    Pairs with fewer than `min_overlap` shared metrics get +inf."""
    out = np.empty((A.shape[0], B.shape[0]), dtype=np.float32)
    Bk = ~np.isnan(B)
    B0 = np.nan_to_num(B)
    for s in range(0, A.shape[0], chunk):
        a = A[s:s + chunk]
        ak = ~np.isnan(a)
        both = ak[:, None, :] & Bk[None, :, :]
        diff2 = (np.nan_to_num(a)[:, None, :] - B0[None, :, :]) ** 2
        num = (diff2 * w * both).sum(-1)
        den = (w * both).sum(-1)
        n = both.sum(-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            d = np.sqrt(num / den)
        d[(n < min_overlap) | (den == 0)] = np.inf
        out[s:s + chunk] = d
    return out


def _knn_mean(D: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    k = min(k, D.shape[1])
    idx = np.argpartition(D, k - 1, axis=1)[:, :k]
    near = np.take_along_axis(D, idx, axis=1)
    finite = np.isfinite(near)
    with np.errstate(invalid="ignore"):
        mean = np.where(finite, near, 0).sum(1) / finite.sum(1)
    mean[finite.sum(1) == 0] = np.nan
    return mean, np.argmin(D, axis=1)


def score(P: pd.DataFrame, refW: pd.DataFrame, refL: pd.DataFrame | None, weights: np.ndarray,
          method: str, k: int = 10, min_overlap: int = 1) -> pd.DataFrame:
    """method:
    - 'spec'     : 1 - mean distance to k nearest winners (README section 7, as written)
    - 'contrast' : mean distance to k nearest non-winners - mean distance to k nearest winners
    - 'share'    : share of winners among the k nearest companies in W+L (a plain kNN classifier)
    - 'prior'    : weighted mean of metric values (all metrics assumed to point up)
    """
    A = P.to_numpy(float)
    res = pd.DataFrame(index=P.index)
    if method == "prior":
        known = ~np.isnan(A)
        res["score"] = (np.nan_to_num(A) * weights).sum(1) / np.where(known, weights, 0).sum(1)
        res["nearest_winner_idx"] = -1
        return res
    DW = pairwise_distance(A, refW.to_numpy(float), weights, min_overlap)
    dW, nearest = _knn_mean(DW, k)
    res["nearest_winner_idx"] = np.where(np.isfinite(DW.min(1)), nearest, -1)
    res["nearest_winner_dist"] = DW.min(1)
    if method == "spec":
        res["score"] = 1 - dW
    elif method == "share":
        R = pd.concat([refW, refL])
        isw = np.r_[np.ones(len(refW)), np.zeros(len(refL))]
        D = pairwise_distance(A, R.to_numpy(float), weights, min_overlap)
        kk = min(k, D.shape[1])
        idx = np.argpartition(D, kk - 1, axis=1)[:, :kk]
        ok = np.isfinite(np.take_along_axis(D, idx, axis=1))
        res["score"] = np.where(ok.any(1), (isw[idx] * ok).sum(1) / np.maximum(ok.sum(1), 1), np.nan)
    elif method == "contrast":
        DL = pairwise_distance(A, refL.to_numpy(float), weights, min_overlap)
        dL, _ = _knn_mean(DL, k)
        res["score"] = dL - dW
    else:
        raise ValueError(method)
    return res


def explain(v: pd.Series, u: pd.Series, weights: dict[str, float], top: int = 3,
            strong: float = 0.6) -> str:
    """Metrics that drive the similarity: both high (>= strong) and close. Shared zeros never count."""
    rows = []
    for mid, w in weights.items():
        a, b = v.get(mid), u.get(mid)
        if pd.isna(a) or pd.isna(b) or min(a, b) < strong:
            continue
        rows.append((w * (1 - abs(a - b)) * min(a, b), f"{mid}={a:.2f}"))
    rows.sort(reverse=True)
    return " + ".join(r[1] for r in rows[:top]) or "no shared strong signal"
