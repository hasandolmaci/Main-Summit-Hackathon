"""Walk-forward backtest of the scoring methods (selection years only; locked years refused).

Methods (in the user's order)
  1 random                          seeded uniform scores
  2 single-metric baselines         score = one scaled metric (missing ranks last)
  3 logreg                          L2, class_weight=balanced, default C; X = metrics filled 0 + has_<source>
  4 knn_k{1,3,5}                    1 / (1 + mean distance to k nearest funded references); distance over
                                    dimensions observed for both, rescaled by sqrt(D/d)
  5 lr_knn_k{1,3,5}                 same with per-metric weights = LR coefficients clipped at 0

Point-in-time rules
  - Model for year Y trains on snapshots with as_of <= Y-2 (their 24m labels are complete at Y).
  - kNN references for Y: labelled entities funded in [Y-R, Y) (funding < Y), profile at funding-90d,
    never the candidate itself.
  - Coefficients are re-learned from scratch for every year (fold).
Outputs: results/predictions.parquet, results/metrics.csv, results/coefficients.csv, results/backtest_meta.json
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .build_snapshots import FEATURES, HAS_FLAGS
from .lib.config import PROCESSED, RESULTS, as_of_date, cfg, get_logger, guard_years, seed
from .lib.meta import write_meta
from .lib.metrics import (ci, cluster_bootstrap, mean_precision_at_k, mean_recall_at_k, pooled_auc, rank_order,
                          top_share_metrics, weighted_top_mask)

log = get_logger("backtest")


# ---------------------------------------------------------------- data
def load_snapshots(years, suffix="") -> pd.DataFrame:
    parts = []
    for y in years:
        p = PROCESSED / f"snapshots_{y}{suffix}.parquet"
        if p.exists():
            parts.append(pd.read_parquet(p))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def design(df: pd.DataFrame, features=FEATURES) -> np.ndarray:
    X = df[list(features)].fillna(0.0).to_numpy(float)
    H = df[HAS_FLAGS].fillna(False).astype(float).to_numpy()
    return np.hstack([X, H])


# ---------------------------------------------------------------- models
def train_logreg(train: pd.DataFrame, features=FEATURES) -> LogisticRegression | None:
    y = train["label"].values
    if len(train) == 0 or y.min() == y.max():
        return None
    w = train["weight"].values.astype(float)
    for cls in (0, 1):                      # keep relative case-cohort weights within class, mean 1 per class
        m = y == cls
        w[m] = w[m] / w[m].mean()
    lr = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)   # default penalty = L2
    lr.fit(design(train, features), y, sample_weight=w)
    return lr


def partial_distance(X: np.ndarray, R: np.ndarray, w: np.ndarray | None = None) -> np.ndarray:
    """Distances between candidates X (n×D) and references R (m×D) with NaN = missing.

    d = sqrt( sum_obs w_j (x_j - r_j)^2 * W / W_obs ), W = sum of weights over all D dims, W_obs over dims observed
    in both. With w = 1 this is the sqrt(D/d) rescaling. Pairs with no shared dimension get +inf.
    """
    D = X.shape[1]
    w = np.ones(D) if w is None else np.asarray(w, float)
    W = w.sum()
    obs = (~np.isnan(X))[:, None, :] & (~np.isnan(R))[None, :, :]
    diff = np.where(obs, np.nan_to_num(X)[:, None, :] - np.nan_to_num(R)[None, :, :], 0.0)
    num = (w * diff ** 2).sum(-1)
    wobs = (w * obs).sum(-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.sqrt(num * W / wobs)
    d[wobs <= 0] = np.inf
    return d


def knn_scores(cand: pd.DataFrame, refs: pd.DataFrame, k: int, w=None, features=FEATURES) -> np.ndarray:
    if len(refs) == 0:
        return np.full(len(cand), np.nan)
    X = cand[list(features)].to_numpy(float)
    R = refs[list(features)].to_numpy(float)
    d = partial_distance(X, R, w)
    same = np.asarray(cand["entity_id"], dtype=object)[:, None] == np.asarray(refs["entity_id"], dtype=object)[None, :]
    d[same] = np.inf                                  # never your own reference
    kk = min(k, d.shape[1])
    near = np.sort(d, axis=1)[:, :kk]
    with np.errstate(invalid="ignore"):
        md = np.where(np.isinf(near), np.nan, near)
        mean = np.nanmean(md, axis=1)
    return 1.0 / (1.0 + mean)


def reference_set(refs: pd.DataFrame, as_of, window_years: int) -> pd.DataFrame:
    a = pd.Timestamp(as_of)
    f = pd.to_datetime(refs["funding_date"], utc=True)
    m = (f < a) & (f >= a - pd.DateOffset(years=window_years)) & (pd.to_datetime(refs["profile_date"], utc=True) < a)
    if "funding_known_at" in refs:          # the funding must have been PUBLIC before as_of (YC: batch start)
        m &= pd.to_datetime(refs["funding_known_at"], utc=True) < a
    return refs[m]


def training_rows(snaps: pd.DataFrame, y: int) -> pd.DataFrame:
    """Training data for year Y: as_of <= Y-2 with complete labels; positives whose funding was not yet PUBLIC
    at Y (e.g. a YC acceptance before its batch started) are dropped, not relabelled."""
    a = as_of_date(y)
    t = snaps[(snaps["as_of"] <= as_of_date(y - 2)) & snaps["labels_complete"]]
    if "funding_known_at" in t:
        kn = pd.to_datetime(t["funding_known_at"], utc=True)
        t = t[~((t["label"] == 1) & kn.notna() & (kn >= a))]
    return t


# ---------------------------------------------------------------- walk-forward
def score_year(y: int, snaps: pd.DataFrame, refs: pd.DataFrame, features=FEATURES, R=None) -> tuple[pd.DataFrame, list]:
    R = R or cfg()["reference"]["window_years"]
    s0 = seed()
    a = as_of_date(y)
    test = snaps[snaps["as_of"] == a].copy()
    train = training_rows(snaps, y)
    out, coefs = [], []

    def add(name, scores):
        cols = ["entity_id", "as_of", "label", "weight", "europe", "has_github", "funding_date"]
        t = test[cols + [c for c in ("dach", "in_B") if c in test]].copy()
        if "dach" not in t:
            t["dach"] = False
        t["method"] = name
        t["score"] = scores
        out.append(t)

    add("random", np.random.default_rng(s0 + y).random(len(test)))
    for f in features:            # missing = neutral rank (median of the outcome-blind B rows), not "last"
        col = test[f]
        fill = col[test["in_B"].astype(bool)].median() if "in_B" in test else col.median()
        add(f"baseline_{f}", col.fillna(0.0 if pd.isna(fill) else fill).values)
    if "has_cordis" in test:      # geography floor: how far a ccTLD/Europe flag alone 'predicts' (label-source artefact)
        add("geo_floor_has_cordis", test["has_cordis"].fillna(False).astype(float).values)
    lr = train_logreg(train, features)
    nan = np.full(len(test), np.nan)                 # every method gets a row for every year (NaN ranks last)
    if lr is not None:
        # has_cordis stays in the fit (fill-0 + has_<source> design) but is neutralised when scoring, so the model
        # cannot reorder companies by country domain (labels are mostly US/YC -> a geography artefact).
        neutral = test.assign(**{c: False for c in HAS_FLAGS if c in test})
        add("logreg", lr.predict_proba(design(neutral, features))[:, 1])
        names = list(features) + HAS_FLAGS
        coefs.append({"as_of": y, "n_train": len(train), "n_train_pos": int(train["label"].sum()),
                      "intercept": float(lr.intercept_[0]), **dict(zip(names, lr.coef_[0].astype(float)))})
    else:
        add("logreg", nan)
    rs = reference_set(refs, a, R) if len(refs) else refs
    w_lr = np.clip(lr.coef_[0][: len(features)], 0, None) if lr is not None else None
    for k in cfg()["knn"]["ks"]:
        add(f"knn_k{k}", knn_scores(test, rs, k, None, features))
        ok = w_lr is not None and w_lr.sum() > 0
        add(f"lr_knn_k{k}", knn_scores(test, rs, k, w_lr, features) if ok else nan)
    log.info("as_of %d: test %d (pos %d), train %d (pos %d), refs %d", y, len(test), int(test.label.sum()),
             len(train), int(train.label.sum()) if len(train) else 0, len(rs))
    return pd.concat(out, ignore_index=True), coefs


def lead_times(pred: pd.DataFrame, share: float, s: int) -> pd.Series:
    """Months between the first as_of at which a funded entity is in the pool-equivalent top `share` and its funding."""
    first_top = {}
    for (method, a), g in pred.groupby(["method", "as_of"]):
        m = weighted_top_mask(g["score"].to_numpy(float), g["weight"].to_numpy(float), share, s,
                              g["entity_id"].astype(str).to_numpy())
        top = g[m]
        for r in top[top["label"] == 1].itertuples():
            key = (method, r.entity_id)
            if key not in first_top or a < first_top[key][0]:
                first_top[key] = (a, r.funding_date)
    rows = [(m, (pd.Timestamp(f) - pd.Timestamp(a)).days / 30.4375) for (m, _), (a, f) in first_top.items()]
    df = pd.DataFrame(rows, columns=["method", "lead_months"])
    return df.groupby("method")["lead_months"].median() if len(df) else pd.Series(dtype=float)


def evaluate(pred: pd.DataFrame, reps: int, s: int, min_pos_ci: int = 15) -> pd.DataFrame:
    share = float(cfg().get("evaluation", {}).get("top_share", 0.05))
    shares = [float(x) for x in cfg().get("evaluation", {}).get("top_shares", [share])]
    rows = []
    dach = pred["dach"] if "dach" in pred else pd.Series(False, index=pred.index)
    subsets = {"all": pred, "europe": pred[pred["europe"]], "dach": pred[dach.astype(bool)],
               "rest_not_dach": pred[~dach.astype(bool)], "with_github": pred[pred["has_github"]],
               "without_github": pred[~pred["has_github"]]}
    for sub, d in subsets.items():
        for method, g in d.groupby("method"):
            npos = int(g.groupby("entity_id")["label"].max().sum())
            r = {"subset": sub, "method": method, "n_rows": len(g), "n_pos_rows": int(g.label.sum()),
                 "n_pos_entities": npos, "base_rate": float(g.label.mean()),
                 "auc": pooled_auc(g), "p_at_50": mean_precision_at_k(g, 50, s),
                 "p_at_20": mean_precision_at_k(g, 20, s), "recall_at_50": mean_recall_at_k(g, 50, s),
                 "p_at_50_random": float(g.groupby("as_of")["label"].mean().mean())}
            ts = top_share_metrics(g, share, s)
            r.update({"top_share": share, "top_precision_w": ts["precision"], "top_recall": ts["recall"],
                      "top_lift": ts["lift"],
                      "base_rate_w": float((g["weight"] * g["label"]).sum() / g["weight"].sum())})
            for q in shares:
                tq = top_share_metrics(g, q, s)
                r[f"top{int(round(q * 100))}_recall"], r[f"top{int(round(q * 100))}_lift"] = tq["recall"], tq["lift"]
            if npos >= min_pos_ci and reps > 0:
                b = cluster_bootstrap(g, pooled_auc, reps, s)
                r["auc_lo"], r["auc_hi"] = ci(b)
                nb = min(reps, max(200, reps // 5))
                b2 = cluster_bootstrap(g, lambda x: mean_precision_at_k(x, 50, s), nb, s + 1)
                r["p_at_50_lo"], r["p_at_50_hi"] = ci(b2)
                b3 = cluster_bootstrap(g, lambda x: mean_precision_at_k(x, 20, s), nb, s + 2)
                r["p_at_20_lo"], r["p_at_20_hi"] = ci(b3)
                b4 = cluster_bootstrap(g, lambda x: mean_recall_at_k(x, 50, s), nb, s + 3)
                r["recall_at_50_lo"], r["recall_at_50_hi"] = ci(b4)
                b5 = cluster_bootstrap(g, lambda x: top_share_metrics(x, share, s)["recall"], nb, s + 4)
                r["top_recall_lo"], r["top_recall_hi"] = ci(b5)
                b6 = cluster_bootstrap(g, lambda x: top_share_metrics(x, share, s)["lift"], nb, s + 5)
                r["top_lift_lo"], r["top_lift_hi"] = ci(b6)
            r["exploratory"] = npos < cfg()["min_positives"]["exploratory_below"]
            rows.append(r)
    m = pd.DataFrame(rows)
    lt = lead_times(pred, share, s)
    m["median_lead_months"] = m.apply(lambda r: lt.get(r["method"], np.nan) if r["subset"] == "all" else np.nan, axis=1)
    return m


def coef_bootstrap(snaps: pd.DataFrame, years, reps: int, s: int, features=FEATURES) -> pd.DataFrame:
    """Bootstrap the LR fit on the training data of the last selection year: sign stability per coefficient."""
    y = max(years)
    train = training_rows(snaps, y)
    names = list(features) + HAS_FLAGS
    rng = np.random.default_rng(s)
    ents = train["entity_id"].unique()
    vals = []
    for _ in range(reps):
        pick = rng.choice(ents, size=len(ents), replace=True)
        bt = pd.concat([train[train["entity_id"] == e] for e in pick], ignore_index=True) if len(ents) < 3000 else \
            train.set_index("entity_id").loc[pick].reset_index()
        lr = train_logreg(bt, features)
        if lr is not None:
            vals.append(lr.coef_[0])
    V = np.array(vals)
    full = train_logreg(train, features)
    rows = []
    for j, n in enumerate(names):
        col = V[:, j] if len(V) else np.array([np.nan])
        est = float(full.coef_[0][j]) if full is not None else np.nan
        lo, hi = ci(col)
        rows.append({"feature": n, "coef_full": est, "ci_lo": lo, "ci_hi": hi,
                     "sign_stability": float(np.mean(np.sign(col) == np.sign(est))) if len(V) else np.nan,
                     "n_boot": len(V), "train_as_of_max": y - 2})
    return pd.DataFrame(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", nargs="*", type=int)
    ap.add_argument("--suffix", default="")
    ap.add_argument("--R", type=int, default=None)
    ap.add_argument("--reps", type=int, default=cfg()["bootstrap"]["reps"])
    ap.add_argument("--tag", default="main")
    a = ap.parse_args(argv)
    years = guard_years(a.years or list(range(cfg()["years"]["selection"][0], cfg()["years"]["selection"][1] + 1)))
    snaps = load_snapshots(range(min(years) - 6, max(years) + 1), a.suffix)
    snaps = snaps[snaps["as_of"] < as_of_date(max(years) + 1)]
    refs_p = PROCESSED / f"reference_profiles{a.suffix}.parquet"
    refs = pd.read_parquet(refs_p) if refs_p.exists() else pd.DataFrame()
    preds, coefs = [], []
    for y in years:
        p, c = score_year(y, snaps, refs, R=a.R)
        preds.append(p)
        coefs.extend(c)
    pred = pd.concat(preds, ignore_index=True)
    RESULTS.mkdir(exist_ok=True)
    tag = a.tag
    pp = RESULTS / f"predictions_{tag}.parquet"
    pred.to_parquet(pp, index=False)
    met = evaluate(pred, a.reps, seed())
    mp = RESULTS / f"metrics_{tag}.csv"
    met.to_csv(mp, index=False)
    cf = pd.DataFrame(coefs)
    cf.to_csv(RESULTS / f"coefficients_{tag}.csv", index=False)
    cb = coef_bootstrap(snaps, years, min(200, a.reps), seed())
    cb.to_csv(RESULTS / f"coef_bootstrap_{tag}.csv", index=False)
    for p in (pp, mp):
        write_meta(p, "scripts/backtest.py", params={"years": years, "R": a.R or cfg()["reference"]["window_years"],
                                                     "suffix": a.suffix, "reps": a.reps, "seed": seed()}, rows=None)
    print(met[met.subset == "all"].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
