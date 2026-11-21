"""Leak-free backtest of Startup Radar on the EU 2021-2023 dataset.

For each test year Y:
  freeze at 1 Jan Y
  reference winners  W = companies from earlier snapshots whose first round is dated before the freeze
  reference others   L = all other earlier-snapshot companies (what we knew at the freeze)
  test set           = companies with a snapshot in year Y, after the exclusion filters
  label              = raised a first equity round within 24 months after the snapshot

Usage:  python engine/prepare_eu.py && python engine/backtest.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

import radar

ENGINE = Path(__file__).resolve().parent
OUT = ENGINE / "out"
RNG = np.random.default_rng(7)
K = 10
TEST_YEARS = [2022, 2023]
FUNDED_CAP_EUR = 800_000


def auc(y: np.ndarray, s: np.ndarray) -> float:
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    r = pd.Series(np.concatenate([pos, neg])).rank().to_numpy()
    return (r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def auc_ci(y, s, n=1000):
    idx = RNG.integers(0, len(y), size=(n, len(y)))
    boots = [auc(y[i], s[i]) for i in idx]
    return np.nanpercentile(boots, [2.5, 97.5])


def precision_at(y, s, k):
    k = max(1, min(k, len(y)))
    return y[np.argsort(-s, kind="stable")[:k]].mean()


def evaluate(y, s):
    lo, hi = auc_ci(y, s)
    return {
        "n": len(y), "positives": int(y.sum()), "base_rate": y.mean(),
        "auc": auc(y, s), "auc_lo": lo, "auc_hi": hi,
        "p_at_50": precision_at(y, s, 50),
        "p_at_top10pct": precision_at(y, s, int(round(0.1 * len(y)))),
    }


def apply_exclusions(c: pd.DataFrame) -> pd.DataFrame:
    keep = (
        c["is_pure_open_source"].fillna(0).eq(0)
        & c["raised_equity_before"].eq(0)
        & ~(c["pre_asof_funding_eur"] >= FUNDED_CAP_EUR)
    )
    print(f"exclusions: {len(c)} -> {keep.sum()} rows "
          f"(open source {int(c.is_pure_open_source.eq(1).sum())}, "
          f"equity before as-of {int(c.raised_equity_before.eq(1).sum())})")
    return c[keep].copy()


def logreg_scores(Pref, yref, Ptest):
    def feats(P):
        X = P.copy()
        for col in P.columns:
            X[col + "_missing"] = P[col].isna().astype(float)
        return X.fillna(0.5).to_numpy()
    m = LogisticRegression(max_iter=1000, class_weight="balanced")
    m.fit(feats(Pref), yref)
    return m.predict_proba(feats(Ptest))[:, 1]


def run_year(Y, comp, P, metrics, weights):
    freeze = pd.Timestamp(f"{Y}-01-01")
    year = pd.to_datetime(comp["snapshot_date"]).dt.year
    fdate = pd.to_datetime(comp["funding_date"], errors="coerce")
    ref = comp.index[year < Y]
    is_w = (comp.loc[ref, "funded_within_24m"] == 1) & (fdate.loc[ref] < freeze)
    W, L = ref[is_w.to_numpy()], ref[~is_w.to_numpy()]
    test = comp.index[year == Y]
    y = comp.loc[test, "funded_within_24m"].to_numpy(int)
    print(f"\n== test year {Y}: {len(test)} companies, {y.sum()} later raised | "
          f"reference winners {len(W)}, reference others {len(L)}")

    rows, scored = [], {}
    runs = {
        "spec_knn (as in README, min overlap 1)": dict(method="spec", min_overlap=1),
        "contrast_knn (winners vs others, min overlap 2)": dict(method="contrast", min_overlap=2),
        "share_knn (k=100, min overlap 2)": dict(method="share", min_overlap=2, k=100),
        "prior_weighted_sum": dict(method="prior"),
    }
    for name, kw in runs.items():
        r = radar.score(P.loc[test], P.loc[W], P.loc[L], weights, **{"k": K, **kw})
        s = r["score"].to_numpy(float)
        coverage = np.isfinite(s).mean()
        s = np.where(np.isfinite(s), s, np.nanmin(s) - 1)  # unscorable rows go to the bottom
        scored[name] = (r.assign(score=s), s)
        rows.append({"year": Y, "model": name, "segment": "all", "scored_share": coverage, **evaluate(y, s)})
    s_lr = logreg_scores(P.loc[ref], is_w.to_numpy(int), P.loc[test])
    scored["logreg (diagnostic)"] = (None, s_lr)
    rows.append({"year": Y, "model": "logreg (diagnostic)", "segment": "all", "scored_share": 1.0,
                 **evaluate(y, s_lr)})

    # sanity: shuffle which reference companies are winners -> AUC should collapse towards 0.5
    for name, kw in list(runs.items())[:3]:
        perm = RNG.permutation(ref.to_numpy())
        Ws, Ls = pd.Index(perm[:len(W)]), pd.Index(perm[len(W):])
        r = radar.score(P.loc[test], P.loc[Ws], P.loc[Ls], weights, **{"k": K, **kw})
        s = r["score"].to_numpy(float)
        s = np.where(np.isfinite(s), s, np.nanmin(s) - 1)
        rows.append({"year": Y, "model": name + " [shuffled reference labels]", "segment": "all",
                     "scored_share": np.nan, **evaluate(y, s)})

    # segment splits
    seg = comp.loc[test, "cohort_type"].to_numpy()
    for name, (_, s) in scored.items():
        for sg in ["crunchbase", "program"]:
            mask = seg == sg
            if mask.sum() > 30 and y[mask].sum() > 0:
                rows.append({"year": Y, "model": name, "segment": sg, "scored_share": np.nan,
                             **evaluate(y[mask], s[mask])})
    return rows, scored, test, W


def shortlist(Y, comp, P, scored, test, W, weights_by_id, rank_model, explain_model, n=100):
    """Rank by `rank_model`; take the nearest past winner and the reasons from `explain_model`."""
    s = scored[rank_model][1]
    r = scored[explain_model][0]
    df = comp.loc[test, ["company_id", "name", "country", "cohort_source", "snapshot_date",
                         "categories", "funded_within_24m", "funding_date"]].copy()
    df["score"] = s
    nw = r["nearest_winner_idx"].to_numpy()
    df["nearest_winner"] = [comp.loc[W[i], "name"] if i >= 0 else "" for i in nw]
    df["why"] = [radar.explain(P.loc[t], P.loc[W[i]], weights_by_id) if i >= 0 else ""
                 for t, i in zip(test, nw)]
    for col in P.columns:
        df[f"v_{col}"] = P.loc[test, col].round(3)
    df = df.sort_values("score", ascending=False).head(n)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def main():
    OUT.mkdir(exist_ok=True)
    comp = apply_exclusions(pd.read_csv(ENGINE / "build" / "companies_eu.csv", low_memory=False))
    comp = comp.reset_index(drop=True)
    metrics = radar.load_metrics()
    print("active metrics:", ", ".join(metrics.metric_id))
    weights = metrics["weight"].to_numpy(float)
    weights_by_id = dict(zip(metrics.metric_id, weights))
    P = radar.build_profiles(comp, metrics)
    print("coverage per metric:\n" + P.notna().mean().round(3).to_string())

    all_rows = []
    for Y in TEST_YEARS:
        rows, scored, test, W = run_year(Y, comp, P, metrics, weights)
        all_rows += rows
        sl = shortlist(Y, comp, P, scored, test, W, weights_by_id,
                       "prior_weighted_sum", "spec_knn (as in README, min overlap 1)")
        sl.to_csv(OUT / f"shortlist_{Y}.csv", index=False)

    res = pd.DataFrame(all_rows)
    res.to_csv(OUT / "backtest_results.csv", index=False)
    show = res.copy()
    show["auc (95% CI)"] = show.apply(lambda r: f"{r.auc:.3f} ({r.auc_lo:.3f}-{r.auc_hi:.3f})", axis=1)
    show["lift@top10%"] = (show.p_at_top10pct / show.base_rate).round(2)
    cols = ["year", "segment", "model", "n", "positives", "base_rate", "auc (95% CI)",
            "p_at_50", "p_at_top10pct", "lift@top10%"]
    print("\n" + show[cols].round(3).to_string(index=False))
    t = show[cols].round(3).astype(str)
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(r) + " |" for r in t.to_numpy()]
    (OUT / "backtest_results.md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    main()
