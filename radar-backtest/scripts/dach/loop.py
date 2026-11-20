"""Improvement loop for the DACH cohort backtest (config: dach/config/loop_config.yaml, frozen before the run).

  python -m scripts.dach.loop            # real run + 20 shuffled-label controls; live state for the dashboard

Model: baseline (cohort source one-hot + cohort year) + current set, L2 LR (C fixed), standardized in-fold,
missing -> train-fold median + <feature>_missing. Grouped CV (groups = programme|cohort), 5 folds x 20 repeats.
Candidate vs current set on identical folds; 1000x paired bootstrap on out-of-fold predictions for delta AUC.
Keep: delta >= 0.02 AND 90% CI lower bound > 0 AND improves in >= 60% of repeats; max 5 features.
Forward pass in candidates.yaml order, then one backward pass. Tripwire: univariate AUC > 0.80 or timestamp-derived.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from .io import DACH

CFG = yaml.safe_load(open(DACH / "config" / "loop_config.yaml"))
RES = DACH / "results"
STATE = RES / "loop_state.json"
EVENTS = RES / "loop_events.jsonl"
LOG = RES / "loop_log.csv"


# ---------------------------------------------------------------- live state for the dashboard
class Live:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.state = {"stage": "starting", "started_at": datetime.now(timezone.utc).isoformat(), "current_set": [],
                      "attempts": [], "shuffled": [], "real": None, "baseline": None}
        if enabled:
            RES.mkdir(parents=True, exist_ok=True)
            EVENTS.write_text("")
            self.flush()

    def flush(self):
        if self.enabled:
            self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
            tmp = STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, default=float))
            tmp.replace(STATE)

    def event(self, kind: str, **kw):
        if self.enabled:
            with open(EVENTS, "a") as f:
                f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(), "kind": kind, **kw}, default=float) + "\n")

    def set(self, **kw):
        self.state.update(kw)
        self.flush()


# ---------------------------------------------------------------- CV machinery
def folds_for_repeat(groups: np.ndarray, n_splits: int, rng: np.random.Generator) -> np.ndarray:
    uniq = np.array(sorted(set(groups)))
    perm = rng.permutation(len(uniq))
    gfold = {g: perm[i] % n_splits for i, g in enumerate(uniq)}
    return np.array([gfold[g] for g in groups])


def design(train: pd.DataFrame, test: pd.DataFrame, feats: list[str], base: list[str]) -> tuple[np.ndarray, np.ndarray]:
    cols_tr, cols_te = [train[base].to_numpy(float)], [test[base].to_numpy(float)]
    for f in feats:
        tr, te = train[f].to_numpy(float), test[f].to_numpy(float)
        if np.isnan(tr).any() or np.isnan(te).any():
            med = np.nanmedian(tr) if np.isfinite(tr).any() else 0.0
            cols_tr += [np.where(np.isnan(tr), med, tr)[:, None], np.isnan(tr).astype(float)[:, None]]
            cols_te += [np.where(np.isnan(te), med, te)[:, None], np.isnan(te).astype(float)[:, None]]
        else:
            cols_tr.append(tr[:, None])
            cols_te.append(te[:, None])
    Xtr, Xte = np.hstack(cols_tr), np.hstack(cols_te)
    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd[sd == 0] = 1.0
    return (Xtr - mu) / sd, (Xte - mu) / sd


def fold_auc(y: np.ndarray, p: np.ndarray, fold: np.ndarray) -> float:
    """AUC computed WITHIN each test fold (one fitted model -> one intercept) and pooled by positive-negative pairs.

    Pooling raw out-of-fold predictions across folds biases weak models below 0.5 (each fold's intercept shifts
    against that fold's label share), which then makes ANY added noise feature look like an improvement."""
    num = den = 0.0
    for k in np.unique(fold):
        m = fold == k
        yk = y[m]
        npos, nneg = int(yk.sum()), int((1 - yk).sum())
        if npos == 0 or nneg == 0:
            continue
        num += npos * nneg * roc_auc_score(yk, p[m])
        den += npos * nneg
    return num / den if den else float("nan")


class Evaluator:
    def __init__(self, L: pd.DataFrame, base: list[str], y: np.ndarray, seed: int):
        self.L, self.base, self.y = L, base, y
        self.groups = (L["meta_cohort_source"].astype(str) + "|" + L["meta_cohort"].astype(str)).to_numpy()
        rng = np.random.default_rng(seed)
        self.folds = [folds_for_repeat(self.groups, CFG["cv"]["n_splits"], rng) for _ in range(CFG["cv"]["repeats"])]
        self.cache = {}

    def oof(self, feats: tuple[str, ...]) -> np.ndarray:
        if feats in self.cache:
            return self.cache[feats]
        R = len(self.folds)
        P = np.zeros((R, len(self.y)))
        for r, fold in enumerate(self.folds):
            for k in range(CFG["cv"]["n_splits"]):
                te = fold == k
                if te.sum() == 0:
                    continue
                tr = ~te
                if self.y[tr].min() == self.y[tr].max():
                    P[r, te] = self.y[tr].mean()
                    continue
                Xtr, Xte = design(self.L[tr], self.L[te], list(feats), self.base)
                m = LogisticRegression(C=CFG["model"]["C"], class_weight=CFG["model"]["class_weight"], max_iter=5000)
                m.fit(Xtr, self.y[tr])
                P[r, te] = m.predict_proba(Xte)[:, 1]
        self.cache[feats] = P
        return P

    def aucs(self, feats) -> np.ndarray:
        P = self.oof(tuple(feats))
        return np.array([fold_auc(self.y, p, f) for p, f in zip(P, self.folds)])

    def compare(self, cur, cand, seed: int) -> dict:
        Pc, Pn = self.oof(tuple(cur)), self.oof(tuple(cand))
        ac = np.array([fold_auc(self.y, p, f) for p, f in zip(Pc, self.folds)])
        an = np.array([fold_auc(self.y, p, f) for p, f in zip(Pn, self.folds)])
        d = an - ac
        rng = np.random.default_rng(seed)
        n, R, boots = len(self.y), len(self.folds), []
        for b in range(CFG["comparison"]["bootstrap_reps"]):     # paired: same rows, same repeat, same folds
            r = b % R
            idx = rng.integers(0, n, n)
            yy, ff = self.y[idx], self.folds[r][idx]
            dn, dc = fold_auc(yy, Pn[r][idx], ff), fold_auc(yy, Pc[r][idx], ff)
            if np.isfinite(dn) and np.isfinite(dc):
                boots.append(dn - dc)
        a = (1 - CFG["comparison"]["ci"]) / 2
        lo, hi = np.quantile(boots, [a, 1 - a])
        return {"auc_before": float(ac.mean()), "auc_after": float(an.mean()), "delta": float(d.mean()),
                "ci_lo": float(lo), "ci_hi": float(hi), "share_improved": float((d > 0).mean()),
                "per_repeat_delta": [round(float(x), 4) for x in d]}


def keep(res: dict) -> bool:
    k = CFG["keep_rule"]
    return (res["delta"] >= k["min_delta_auc"] and res["ci_lo"] > k["ci_lower_bound_gt"]
            and res["share_improved"] >= k["min_share_repeats_improved"])


def univariate_auc(x: np.ndarray, y: np.ndarray) -> float:
    x = np.asarray(x, float)
    if np.isnan(x).all() or np.nanstd(x) == 0:
        return 0.5
    x = np.where(np.isnan(x), np.nanmedian(x), x)        # missing -> neutral
    a = roc_auc_score(y, x)
    return max(a, 1 - a)


def tripwire(name: str, x: np.ndarray, y: np.ndarray, meta: dict) -> str | None:
    if meta.get("timestamp_derived"):
        return "derived from a timestamp"
    a = univariate_auc(x, y)
    if a > CFG["tripwire"]["univariate_auc_gt"]:
        return f"univariate AUC {a:.3f} > {CFG['tripwire']['univariate_auc_gt']}"
    return None


def usable(x: np.ndarray) -> bool:
    v = x[~np.isnan(x)]
    return len(v) > 0 and (np.unique(v).size > 1 or np.isnan(x).any() and np.isnan(x).sum() < len(x))


# ---------------------------------------------------------------- the loop
def run_loop(L: pd.DataFrame, y: np.ndarray, start: list[str], candidates: list[tuple[str, dict]], base: list[str],
             seed: int, live: Live | None = None, tag: str = "real", log_rows: list | None = None) -> dict:
    ev = Evaluator(L, base, y, seed)
    cur = [f for f in start if usable(L[f].to_numpy(float))]
    if live:
        live.set(stage=f"{tag}: baseline", current_set=list(cur))
    base_auc = float(ev.aucs([]).mean())
    start_auc = float(ev.aucs(cur).mean())
    if live:
        live.set(baseline={"auc": base_auc}, start={"features": list(cur), "auc": start_auc}, stage=f"{tag}: forward pass")
    for i, (name, meta) in enumerate(candidates):
        x = L[name].to_numpy(float)
        row = {"run": tag, "pass": "forward", "metric": name, "evidence": meta.get("evidence", "exploratory")}
        if meta.get("excluded"):
            row.update(decision="excluded_pre_run", reason=meta["excluded"])
        elif not usable(x):
            row.update(decision="skipped", reason="no variance / no as-of data")
        elif (tw := tripwire(name, x, y, meta)):
            row.update(decision="TRIPWIRE_excluded", reason="suspected leak: " + tw)
        elif len(cur) >= CFG["max_features"]:
            row.update(decision="skipped", reason="max_features reached")
        else:
            res = ev.compare(cur, cur + [name], seed + 1000 + i)
            row.update(res, decision="KEEP" if keep(res) else "drop")
            if keep(res):
                cur = cur + [name]
        if live:
            live.state["attempts"].append({k: v for k, v in row.items() if k != "per_repeat_delta"} | {
                "per_repeat_delta": row.get("per_repeat_delta", [])})
            live.set(current_set=list(cur), stage=f"{tag}: forward pass ({i + 1}/{len(candidates)}) {name}")
            live.event("attempt", **{k: v for k, v in row.items()})
        if log_rows is not None:
            log_rows.append(row)
    for name in list(cur):                                  # one backward pass
        rest = [f for f in cur if f != name]
        res = ev.compare(rest, cur, seed + 5000 + len(rest))
        dec = "kept" if keep(res) else "REMOVED"
        if dec == "REMOVED":
            cur = rest
        row = {"run": tag, "pass": "backward", "metric": name, **res, "decision": dec}
        if live:
            live.state["attempts"].append(row)
            live.set(current_set=list(cur), stage=f"{tag}: backward pass {name}")
            live.event("backward", **row)
        if log_rows is not None:
            log_rows.append(row)
    final_auc = ev.aucs(cur)
    vs_base = ev.compare([], cur, seed + 9000) if cur else None
    return {"final_set": cur, "oof_auc": float(final_auc.mean()), "baseline_auc": base_auc, "start_auc": start_auc,
            "vs_baseline": vs_base}


def permute_within_groups(y: np.ndarray, groups: np.ndarray, rng) -> np.ndarray:
    out = y.copy()
    for g in np.unique(groups):
        idx = np.flatnonzero(groups == g)
        out[idx] = rng.permutation(y[idx])
    return out


def _shuffled_run(args):
    """One shuffled-label control (module-level so it can run in a worker process; seeded -> order-independent)."""
    L, y, groups, start, clist, base, k = args
    ys = permute_within_groups(y, groups, np.random.default_rng(CFG["seed"] + 100 + k))
    r = run_loop(L, ys, start, clist, base, CFG["seed"] + 100 + k, None, f"shuffled_{k}", None)
    return {"run": k, "kept": len(r["final_set"]), "final_set": r["final_set"], "oof_auc": r["oof_auc"]}


def main(argv=None):
    from . import clean as C, features as FE, io, labels as LB
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shuffles", type=int, default=CFG["shuffled_control"]["runs"])
    ap.add_argument("--jobs", type=int, default=3, help="worker processes for the shuffled runs (results identical)")
    a = ap.parse_args(argv)
    t0 = time.time()
    live = Live()
    iso = io.read_iso()
    x, outcomes = C.split_outcomes(iso)
    cx, _ = C.clean(x)
    F, meta = FE.build_features(cx)
    L = LB.labelled(F, outcomes)
    y = L["y"].to_numpy()
    srcs = sorted(L["meta_cohort_source"].unique())
    for s in srcs:
        L[f"src_{s}"] = (L["meta_cohort_source"] == s).astype(float)
    base = [f"src_{s}" for s in srcs] + ["cohort_year"]
    cand = FE.load_candidates()
    start = [s["name"] for s in cand["starting_set"]]
    reg = meta["registry"]
    clist = []
    for c in cand["candidates"]:
        m = dict(reg.get(c["name"], {}))
        m["evidence"] = c.get("note", "see papers/index.md" if c["id"] <= 18 else "exploratory (LLM text)")
        if c["name"] not in L:
            L[c["name"]] = np.nan
        clist.append((c["name"], m))
    live.set(stage="real run", n_labelled=int(len(L)), n_pos=int(y.sum()), candidates=[c for c, _ in clist])
    log_rows = []
    groups = (L["meta_cohort_source"] + "|" + L["meta_cohort"]).to_numpy()
    jobs = [(L, y, groups, start, clist, base, k) for k in range(a.shuffles)]
    pool = None
    if a.jobs > 1 and a.shuffles:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        pool = ProcessPoolExecutor(a.jobs, mp_context=mp.get_context("fork"))
        futs = [pool.submit(_shuffled_run, j) for j in jobs]          # controls run while the real loop runs
    real = run_loop(L, y, start, clist, base, CFG["seed"], live, "real", log_rows)
    live.set(real=real, stage="shuffled-label control")
    shuf = []
    from concurrent.futures import as_completed
    for res in (as_completed(futs) if pool else map(_shuffled_run, jobs)):
        d = res.result() if pool else res
        shuf = sorted(shuf + [d], key=lambda e: e["run"])
        live.state["shuffled"] = shuf
        live.set(stage=f"shuffled-label control {len(shuf)}/{a.shuffles}")
        live.event("shuffled", **d)
    if pool:
        pool.shutdown()
    s_auc = np.array([s["oof_auc"] for s in shuf])
    pct = float((s_auc < real["oof_auc"]).mean()) if len(s_auc) else float("nan")
    q = CFG["shuffled_control"]
    verdict = ("final set distinguishable from noise" if len(s_auc) and real["oof_auc"] >= np.quantile(s_auc, 0.95)
               else "no metric distinguishable from noise")
    summary = {"real": real, "shuffled_oof_auc": s_auc.tolist(), "real_beats_share_of_shuffled": pct,
               "shuffled_mean_kept": float(np.mean([s["kept"] for s in shuf])) if shuf else None,
               "verdict": verdict, "n_labelled": int(len(L)), "n_pos": int(y.sum()), "seconds": round(time.time() - t0)}
    (RES / "loop_summary.json").write_text(json.dumps(summary, indent=1, default=float))
    with open(LOG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run", "pass", "metric", "evidence", "auc_before", "auc_after", "delta", "ci_lo", "ci_hi",
                    "share_improved", "per_repeat_delta", "decision", "reason"])
        for r in log_rows:
            w.writerow([r.get(k, "") for k in ["run", "pass", "metric", "evidence", "auc_before", "auc_after", "delta",
                                               "ci_lo", "ci_hi", "share_improved"]]
                       + [json.dumps(r.get("per_repeat_delta", [])), r.get("decision", ""), r.get("reason", "")])
    live.set(stage="done", summary=summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "shuffled_oof_auc"}, indent=1, default=float))


if __name__ == "__main__":
    main()
