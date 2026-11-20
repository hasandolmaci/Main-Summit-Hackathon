"""Write results/report.md (+ figures) from backtest outputs. Selection years only; locked years never read.

Primary method is fixed in advance (PRIMARY = logistic regression), so the headline is not cherry-picked from the
method table. Every number printed here is read from results/*.csv / predictions computed by scripts/backtest.py.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from .build_snapshots import FEATURES, HAS_FLAGS
from .lib.config import FIGURES, LABELS, PROCESSED, RESULTS, as_of_date, cfg, get_logger, seed, selection_years
from .lib.metrics import auc, rank_order, weighted_top_mask

log = get_logger("report")
PRIMARY = "logreg"
TOP_K = 50
TOP_SHARE = float(cfg().get("evaluation", {}).get("top_share", 0.05))
TOP_SHARES = [float(x) for x in cfg().get("evaluation", {}).get("top_shares", [TOP_SHARE])]
EXPECTED_SIGN = {"oss_external_engagement": "+ (Conti et al. 2025; association)",
                 "github_activity_acceleration": "+ (weak: no paper measures acceleration)",
                 "has_public_rd_grant": "+ (Howell 2017; Santoleri et al. 2024; causal)"}
# chart tokens (dataviz reference palette, light surface)
SURFACE, INK, INK2, MUTED, GRID, AXIS, SERIES1 = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#2a78d6"


def fmt_ci(v, lo, hi, nd=3):
    if pd.isna(v):
        return "n/a"
    if pd.isna(lo) or pd.isna(hi):
        return f"{v:.{nd}f}"
    return f"{v:.{nd}f} [{lo:.{nd}f}, {hi:.{nd}f}]"


def load(tag: str):
    met = pd.read_csv(RESULTS / f"metrics_{tag}.csv")
    pred = pd.read_parquet(RESULTS / f"predictions_{tag}.parquet")
    coefs = pd.read_csv(RESULTS / f"coefficients_{tag}.csv") if (RESULTS / f"coefficients_{tag}.csv").exists() else pd.DataFrame()
    cboot = pd.read_csv(RESULTS / f"coef_bootstrap_{tag}.csv") if (RESULTS / f"coef_bootstrap_{tag}.csv").exists() else pd.DataFrame()
    return met, pred, coefs, cboot


def snapshots(years) -> pd.DataFrame:
    parts = [pd.read_parquet(PROCESSED / f"snapshots_{y}.parquet") for y in years
             if (PROCESSED / f"snapshots_{y}.parquet").exists()]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def flagged_table(pred: pd.DataFrame, method: str, share: float | None = None) -> pd.DataFrame:
    """Per funded entity: first as_of at which it was in the POOL-EQUIVALENT top `share` of its cohort
    (case-cohort weights; before funding by construction of the label window)."""
    share = share if share is not None else TOP_SHARE
    p = pred[pred["method"] == method]
    rows = []
    for i, (a, g) in enumerate(p.groupby("as_of")):
        order = rank_order(g["score"].values, seed() + i)
        rank = np.empty(len(g), dtype=int)
        rank[order] = np.arange(1, len(g) + 1)
        top = weighted_top_mask(g["score"].to_numpy(float), g["weight"].to_numpy(float), share, seed() + i)
        gg = g.assign(rank=rank, n=len(g), in_top=top)
        rows.append(gg[gg["label"] == 1])
    pos = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if pos.empty:
        return pos
    out = []
    for e, g in pos.groupby("entity_id"):
        g = g.sort_values("as_of")
        top = g[g["in_top"]]
        first = top.iloc[0] if len(top) else None
        fd = pd.Timestamp(g["funding_date"].iloc[0])
        out.append({"entity_id": e, "funding_date": fd, "flagged": first is not None,
                    "first_flag_as_of": first["as_of"] if first is not None else pd.NaT,
                    "lead_months": ((fd - pd.Timestamp(first["as_of"])).days / 30.4375) if first is not None else np.nan,
                    "best_rank": int(g["rank"].min()), "cohort_size": int(g.loc[g["rank"].idxmin(), "n"]),
                    "europe": bool(g["europe"].iloc[0])})
    return pd.DataFrame(out)


def reasons(snap_row: pd.Series, coef_row: pd.Series, cohort: pd.DataFrame) -> str:
    """Plain-English reasons from LR contributions (coef × value), positive contributions only."""
    parts = []
    for f in FEATURES:
        x = snap_row.get(f)
        c = coef_row.get(f, np.nan)
        if pd.isna(x) or pd.isna(c) or c * x <= 0:
            continue
        if f == "has_public_rd_grant" and x >= 1:
            parts.append((c * x, "EU R&D grant (CORDIS) before this date"))
        elif f == "github_activity_acceleration":
            w1, w0 = snap_row.get("act_w1", 0), snap_row.get("act_w0", 0)
            ratio = (w1 + 1) / (w0 + 1)
            if ratio > 1.2:
                parts.append((c * x, f"GitHub activity {ratio:.1f}× the previous quarter ({int(w1)} vs {int(w0)} events)"))
        elif f == "oss_external_engagement":
            raw = snap_row.get("eng_raw", 0)
            ref = cohort["eng_raw"].dropna()
            pct = (ref < raw).mean() * 100 if len(ref) else np.nan
            if raw > 0:
                parts.append((c * x, f"team made {int(raw)} contributions to outside open-source projects in 12 months"
                                     + (f" (top {100 - pct:.0f}% of the cohort)" if pd.notna(pct) else "")))
    parts.sort(key=lambda t: -t[0])
    return "; ".join(p for _, p in parts) or "no single strong signal (ranked by combination / missing-data flags)"


def link_precision_proxy(labels: pd.DataFrame) -> dict | None:
    """Share of labelled companies whose matched GitHub org's login/display name agrees with the company name.

    Label names are used ONLY for this evaluation, never for matching. A proxy until the human check is done."""
    import re
    from .lib.domains import name_slug
    lp, op = PROCESSED / "entity_github_org.parquet", PROCESSED / "github_owners.parquet"
    if not (lp.exists() and op.exists()):
        return None
    links = pd.read_parquet(lp)
    own = pd.read_parquet(op)
    own = own.assign(login_l=own["login"].str.lower()).drop_duplicates("login_l").set_index("login_l")
    lab = labels.set_index("domain")
    key = lambda s: re.sub(r"[^a-z0-9]", "", name_slug(s if isinstance(s, str) else ""))  # noqa: E731
    m = links[links["entity_id"].isin(lab.index)].copy()
    if m.empty:
        return None
    kc = m["entity_id"].map(lab["company"]).map(key)
    kd = m["org_login"].str.lower().map(own["display_name"]).map(key)
    kl = m["org_login"].map(key)
    exact = (kd == kc) | (kl == kc)
    cont = [bool(c) and (c in (d or "") or (d or "") in c or c in l or l in c) for c, d, l in zip(kc, kd, kl)]
    return {"n": len(m), "exact": float(exact.mean()), "contained": float((exact | pd.Series(cont, index=m.index)).mean())}


def dot_plot(met: pd.DataFrame, path, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    m = met.dropna(subset=["auc"]).sort_values("auc")
    fig, ax = plt.subplots(figsize=(7.5, 0.42 * len(m) + 1.3), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    y = np.arange(len(m))
    lo = (m["auc"] - m.get("auc_lo", m["auc"])).fillna(0).clip(lower=0)
    hi = (m.get("auc_hi", m["auc"]) - m["auc"]).fillna(0).clip(lower=0)
    ax.errorbar(m["auc"], y, xerr=[lo, hi], fmt="none", ecolor=SERIES1, elinewidth=2, capsize=0, alpha=0.6)
    ax.scatter(m["auc"], y, s=64, color=SERIES1, zorder=3, edgecolors=SURFACE, linewidths=2)
    ax.axvline(0.5, color=MUTED, lw=1, ls="--")
    ax.text(0.5, len(m) - 0.4, "random", color=MUTED, fontsize=8, ha="center")
    ax.set_yticks(y, m["method"], color=INK2, fontsize=9)
    ax.set_xlabel("Pooled ROC AUC (95% bootstrap CI)", color=INK2, fontsize=9)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.grid(axis="x", color=GRID, lw=0.8)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, length=0)
    ax.set_xlim(min(0.4, m["auc"].min() - 0.05), max(0.8, (m["auc"] + hi).max() + 0.02))
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def build(tag: str = "main") -> str:
    years = selection_years()
    met, pred, coefs, cboot = load(tag)
    snap = snapshots(years)
    ent = pd.read_csv(PROCESSED / "entities.csv")
    labels = pd.read_csv(LABELS / "companies.csv", dtype=str, keep_default_na=False)
    prov = pd.read_csv(LABELS / "label_provenance.csv", dtype=str, keep_default_na=False)
    src = prov.drop_duplicates("domain").set_index("domain")["label_source"]
    L = []

    # ---------- headline
    ft = flagged_table(pred, PRIMARY)
    allm = met[(met["subset"] == "all")].set_index("method")
    prim = allm.loc[PRIMARY] if PRIMARY in allm.index else None
    if len(ft) and prim is not None:
        X, Y = int(ft["flagged"].sum()), len(ft)
        N = ft.loc[ft["flagged"], "lead_months"].median()
        expl = bool(prim["exploratory"]) or Y < cfg()["min_positives"]["exploratory_below"]
        L += ["# Startup radar backtest — results (v1, selection years only)", "",
              ("> **EXPLORATORY RESULT** — only %d funded companies are visible to the radar before their first round "
               "(target ≥ %d; below %d the plan reports AUC as exploratory only). Treat every number below as "
               "indicative, with wide confidence intervals." % (Y, cfg()["min_positives"]["target"],
                                                                 cfg()["min_positives"]["exploratory_below"]))
              if expl else "", "",
              f"**Headline:** we flagged **{X} of {Y}** funded startups in our candidate set a median of "
              f"**{N:.0f} months** before their first round (in the top {TOP_SHARE:.0%} of the estimated candidate "
              f"pool on a 1 January before funding; method: L2 logistic regression, as_of years {years[0]}–{years[-1]}).", "",
              f"Ranking quality: pooled ROC AUC **{fmt_ci(prim['auc'], prim.get('auc_lo'), prim.get('auc_hi'))}** "
              f"(0.5 = random). In the top {TOP_SHARE:.0%} of the pool the funded share is estimated at "
              f"**{prim.get('top_precision_w', float('nan')):.4f}** vs **{prim.get('base_rate_w', float('nan')):.4f}** "
              f"overall (lift **{fmt_ci(prim.get('top_lift', float('nan')), prim.get('top_lift_lo'), prim.get('top_lift_hi'), 1)}×**).", "",
              (f"Geography floor: a flag that only says 'European country domain' reaches AUC "
               f"**{allm.loc['geo_floor_has_cordis', 'auc']:.3f}** on its own — because the labels are mostly US (YC), "
               "geography alone separates the groups a little; the logistic regression is scored with this flag "
               "neutralised." if "geo_floor_has_cordis" in allm.index else ""), "",
              "> Read this as: among companies visible to the radar on 1 January of each year, how well the score "
              "ranks the ones that raised a first round (YC / HTGF) in the next 24 months above the ones that did not. "
              "Controls are an outcome-blind random sample of GitHub organisations and are *assumed* unfunded.", ""]
    else:
        L += ["# Startup radar backtest — results", "", "No positives in the evaluated cohorts — see gate.md.", ""]

    # ---------- dataset
    L += ["## 1. Dataset", ""]
    if len(snap):
        per = snap.groupby(snap["as_of"].dt.year).agg(positives=("label", "sum"), rows=("label", "size"),
                                                      europe_pos=("europe", lambda s: int(s[snap.loc[s.index, 'label'] == 1].sum())))
        per["controls"] = per["rows"] - per["positives"]
        per["weighted_base_rate"] = [
            float(g["label"].sum() / (g["label"].sum() + g.loc[g["label"] == 0, "weight"].sum()))
            for _, g in snap.groupby(snap["as_of"].dt.year)]
        L += ["Candidates per as_of year (1 January). Positives = first funding in the next 24 months; controls = "
              "outcome-blind background sample B (assumed unfunded, NOT verified). The weighted base rate re-weights "
              "controls by 1/inclusion-probability, i.e. estimates the rate in the full GitHub pool.", "",
              per[["positives", "controls", "europe_pos", "weighted_base_rate"]].round(4).to_markdown(), ""]
        posids = snap.loc[snap["label"] == 1, "entity_id"].unique()
        lab = labels.set_index("domain")
        mix = lab.loc[lab.index.intersection(posids), "sector"].replace("", "unknown").value_counts().head(12)
        srcs = src.reindex(posids).value_counts()
        L += [f"Distinct funded companies in the evaluated cohorts: **{len(posids)}** "
              f"(label sources: {', '.join(f'{k} {v}' for k, v in srcs.items())}). Sector mix of positives "
              f"(sector is known only for labelled companies, so it is descriptive only):", "",
              mix.to_frame("positives").to_markdown(), ""]
    cov_p = PROCESSED / "coverage_by_source_year.csv"
    if len(snap):
        cov = pd.DataFrame({f: [snap.loc[snap["label"] == 1, f].notna().mean(), snap.loc[snap["label"] == 0, f].notna().mean()]
                            for f in FEATURES}, index=["positives", "controls"]).T
        L += ["Metric coverage (share of cohort rows where the source covers the company; missing ≠ 0):", "",
              cov.round(3).to_markdown(), ""]
    if cov_p.exists():
        L += [f"Per-source event coverage by year: `data/processed/coverage_by_source_year.csv`.", ""]
    pp = link_precision_proxy(labels)
    L += ["Entity matching: GitHub org = website equals company domain AND login is the domain stem (stem-only, "
          "identical for every company); EU grants = website domain, else unique legal name + same country. "
          + (f"Precision proxy for the {pp['n']} matched labelled companies: the org's login/display name equals the "
             f"company name in **{pp['exact']:.1%}** of links ({pp['contained']:.1%} if one name contains the other). "
             if pp else "")
          + "Hand-check samples: `data/processed/match_sample_github.csv`, `data/processed/match_sample_cordis.csv` "
          "(**not yet verified by a human**).", ""]

    # ---------- methods
    L += ["## 2. Methods vs baselines", "",
          "Pooled AUC compares positives and controls only within the same as_of cohort (case-cohort weights); 95% CIs "
          "from an entity-clustered, label-stratified bootstrap. **P@20 / P@50 / R@50 are computed inside the "
          "case-cohort sample** (all funded companies + a small random sample of controls), so they overstate real-world "
          f"precision; the **top {TOP_SHARE:.0%} recall and lift are pool-weighted** (controls weighted by 1/inclusion "
          "probability) and estimate what a radar user would see. Lead time = months from the first 1 January on which "
          f"the company was in the pool's top {TOP_SHARE:.0%} to its first funding.", ""]
    def col(m, c):
        return m[c] if c in m else pd.Series([np.nan] * len(m), index=m.index)

    for sub in ["all", "europe", "dach", "rest_not_dach", "with_github", "without_github"]:
        m = met[met["subset"] == sub]
        if m.empty or m["n_pos_entities"].max() == 0:
            L += [f"**Subset: {sub}** — no funded companies in this subset.", ""]
            continue
        if m["n_pos_entities"].max() < 5:
            L += [f"**Subset: {sub}** — only {int(m['n_pos_entities'].max())} funded company/companies: too few to report "
                  "any metric (an AUC of 0 or 1 from one company is noise).", ""]
            continue
        t = pd.DataFrame({
            "method": m["method"], "n_pos": m["n_pos_entities"],
            "AUC [95% CI]": [fmt_ci(a, l, h) for a, l, h in zip(m["auc"], col(m, "auc_lo"), col(m, "auc_hi"))],
            "P@20 [95% CI]": [fmt_ci(a, l, h) for a, l, h in zip(col(m, "p_at_20"), col(m, "p_at_20_lo"), col(m, "p_at_20_hi"))],
            "P@50 [95% CI]": [fmt_ci(a, l, h) for a, l, h in zip(m["p_at_50"], col(m, "p_at_50_lo"), col(m, "p_at_50_hi"))],
            "R@50 [95% CI]": [fmt_ci(a, l, h) for a, l, h in zip(col(m, "recall_at_50"), col(m, "recall_at_50_lo"), col(m, "recall_at_50_hi"))],
            "sample base rate": m["p_at_50_random"].round(3),
            f"top {TOP_SHARE:.0%} recall [95% CI]": [fmt_ci(a, l, h) for a, l, h in zip(col(m, "top_recall"), col(m, "top_recall_lo"), col(m, "top_recall_hi"))],
            f"top {TOP_SHARE:.0%} lift [95% CI]": [fmt_ci(a, l, h, 1) for a, l, h in zip(col(m, "top_lift"), col(m, "top_lift_lo"), col(m, "top_lift_hi"))],
            **{f"top {q:.0%} recall / lift": [f"{a:.2f} / {b:.1f}×" if pd.notna(a) and pd.notna(b) else "n/a"
                                              for a, b in zip(col(m, f"top{int(round(q * 100))}_recall"), col(m, f"top{int(round(q * 100))}_lift"))]
               for q in TOP_SHARES if abs(q - TOP_SHARE) > 1e-9},
            "median lead (months)": m["median_lead_months"].round(1),
            "exploratory": m["exploratory"]})
        L += [f"**Subset: {sub}**", "", t.to_markdown(index=False), ""]
    L += ["*Without GitHub* is empty by design: v1 candidates come from GitHub organisations only (the CORDIS pool "
          "channel was dropped). *By sector* is not reported: sector is known only for labelled companies, not for "
          "the controls, so a per-sector AUC would be meaningless. Gradient boosting was skipped: the spec allows it "
          "only with several hundred positives per training fold, which v1 does not have.", ""]
    FIGURES.mkdir(parents=True, exist_ok=True)
    if len(met[met["subset"] == "all"]):
        dot_plot(met[met["subset"] == "all"], FIGURES / "auc_by_method.png", "Ranking quality by method (all companies)")
        L += ["![AUC by method](figures/auc_by_method.png)", ""]

    # ---------- cosine failure demo
    if len(snap):
        X = snap[FEATURES].dropna()
        if len(X) > 5:
            weak = X.loc[X.sum(axis=1).idxmin()].to_numpy(float)
            strong = X.loc[X.sum(axis=1).idxmax()].to_numpy(float)
            mu = X.mean().to_numpy(float)
            cos = lambda a, b: float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b))) if np.linalg.norm(a) * np.linalg.norm(b) else float("nan")
            L += ["## Why the distance method uses Euclidean distance, not raw cosine", "",
                  f"On 0–1 metric vectors, uncentred cosine similarity ignores magnitude. The weakest complete profile "
                  f"in the cohorts {np.round(weak, 2).tolist()} and the strongest {np.round(strong, 2).tolist()} have "
                  f"uncentred cosine **{cos(weak, strong):.2f}** (near-identical direction), centred cosine "
                  f"**{cos(weak - mu, strong - mu):.2f}**, Euclidean distance **{np.linalg.norm(weak - strong):.2f}**. "
                  "Raw cosine would call a dormant company 'similar' to a strong one; we therefore use Euclidean "
                  "distance (and would only use cosine on centred vectors).", ""]

    # ---------- LR coefficients
    if len(coefs):
        names = FEATURES + HAS_FLAGS
        L += ["## 3. Logistic regression: coefficients", "",
              "Re-learned from scratch for every as_of year on training years ≤ Y−2 (labels complete and public at Y). "
              "Features are on a 0–1 scale, so coefficients are comparable. `has_github` is constant in v1 (every "
              "candidate comes from GitHub), so its coefficient carries no information. `has_cordis` (European country "
              "domain) is fitted but neutralised when scoring (geography artefact of US-centric labels).", "",
              coefs[["as_of", "n_train", "n_train_pos"] + [c for c in names if c in coefs]].round(3).to_markdown(index=False), ""]
        if len(cboot):
            cb = cboot.copy()
            cb["expected (papers)"] = cb["feature"].map(EXPECTED_SIGN).fillna("— (missing-data flag)")
            cb["sign agrees"] = [("yes" if v > 0 else "no") if f in EXPECTED_SIGN else "" for f, v in zip(cb["feature"], cb["coef_full"])]
            L += ["Bootstrap (entity resample) of the last fold's fit: sign stability = share of resamples with the same sign.", "",
                  cb[["feature", "coef_full", "ci_lo", "ci_hi", "sign_stability", "expected (papers)", "sign agrees"]]
                  .round(3).to_markdown(index=False), ""]
        if len(coefs) > 1:
            sd = coefs[[c for c in FEATURES if c in coefs]].agg(["mean", "std", "min", "max"]).T.round(3)
            L += ["Variation across folds:", "", sd.to_markdown(), ""]

    # ---------- missingness audit
    if len(snap):
        from .lib.metrics import pooled_auc
        rows = []
        for f in HAS_FLAGS:
            g = snap.assign(score=snap[f].fillna(False).astype(float))
            rows.append({"flag": f, "pooled within-cohort AUC vs label": pooled_auc(g)})
        L += ["## 4. Missing-data audit", "",
              "If a 'has data' flag alone predicts funding, part of the signal is survivorship or discovery bias "
              "(e.g. dead companies lose repos). Values far from 0.5 are a warning.", "",
              pd.DataFrame(rows).round(3).to_markdown(index=False), ""]

    # ---------- R=5 vs R=3, sensitivities
    sens = []
    for t, label in [("R3", "kNN reference window R=3 (main R=5)"), ("batchstart", "YC date = batch start (main = acceptance)"),
                     ("scaling_pct", "engagement scaled by percentile (main = robust z)")]:
        p = RESULTS / f"metrics_{t}.csv"
        if p.exists():
            s = pd.read_csv(p)
            for c in ("auc_lo", "auc_hi"):
                if c not in s:
                    s[c] = np.nan
            s = s[s["subset"] == "all"][["method", "auc", "auc_lo", "auc_hi"]].rename(columns={"auc": f"AUC {t}"})
            mm = met[met["subset"] == "all"][["method", "auc"]].rename(columns={"auc": "AUC main"})
            sens.append((label, mm.merge(s, on="method")))
    if sens:
        L += ["## 5. Sensitivity runs", ""]
        for label, t in sens:
            L += [f"**{label}**", "", t.round(3).to_markdown(index=False), ""]

    # ---------- HTGF table, failure cases, reasons
    if len(ft) and len(snap):
        cf = coefs.set_index("as_of") if len(coefs) else pd.DataFrame()
        ftv = ft.assign(source=ft["entity_id"].map(src))
        h = ftv[ftv["source"] == "htgf"].copy()
        L += ["## 6. Would we have found HTGF's deals first?", ""]
        if len(h):
            h["htgf_invest_year"] = pd.to_datetime(h["funding_date"]).dt.year
            h["flagged_on"] = pd.to_datetime(h["first_flag_as_of"]).dt.date
            L += [f"HTGF portfolio companies in the evaluated cohorts: **{len(h)}**; flagged in the pool's top {TOP_SHARE:.0%} no later than 1 January of "
                  f"HTGF's investment year: **{int(h['flagged'].sum())}**. HTGF dates are year-precise "
                  f"('Invested since'), so a flag on 1 January of the investment year counts as 'no later than', not "
                  f"'before'.", "",
                  h[["entity_id", "htgf_invest_year", "flagged", "flagged_on", "best_rank", "cohort_size"]]
                  .sort_values(["flagged", "best_rank"], ascending=[False, True]).to_markdown(index=False), ""]
            miss = h[~h["flagged"]].sort_values("best_rank")
            if len(miss):
                mrow = miss.iloc[-1]
                s = snap[(snap["entity_id"] == mrow["entity_id"]) & (snap["label"] == 1)].sort_values("as_of").iloc[-1]
                L += [f"**Honest miss:** `{mrow['entity_id']}` (HTGF {int(mrow['htgf_invest_year'])}) — best rank "
                      f"{int(mrow['best_rank'])} of {int(mrow['cohort_size'])}. What the radar saw on "
                      f"{pd.Timestamp(s['as_of']).date()}: outside-OSS contributions (12 m) = {s.get('eng_raw', np.nan):.0f}, "
                      f"activity last quarter vs previous = {s.get('act_w1', np.nan):.0f} vs {s.get('act_w0', np.nan):.0f}, "
                      f"EU grant = {'yes' if s.get('has_public_rd_grant') == 1 else ('no' if s.get('has_public_rd_grant') == 0 else 'not covered')}. "
                      "The public traces we measure were weak at that date.", ""]
        else:
            L += ["No HTGF portfolio company is visible in the evaluated cohorts (no matched GitHub org meeting the "
                  "pool rule) — the DACH deep-tech claim cannot be tested with these sources yet.", ""]

        L += ["## 7. Failure cases and reasons", ""]
        p = pred[pred["method"] == PRIMARY].merge(snap, on=["entity_id", "as_of", "label"], how="left", suffixes=("", "_s"))
        top_ctrl = p[p["label"] == 0].sort_values("score", ascending=False).head(10)
        rows = []
        for r in top_ctrl.itertuples():
            crow = cf.loc[pd.Timestamp(r.as_of).year] if len(cf) and pd.Timestamp(r.as_of).year in cf.index else pd.Series(dtype=float)
            rows.append({"company (domain)": r.entity_id, "as_of": pd.Timestamp(r.as_of).date(), "score": round(r.score, 3),
                         "why it ranked high": reasons(pd.Series(r._asdict()), crow, snap[snap["as_of"] == r.as_of])})
        L += ["**Highest-ranked companies that did not raise (per our labels) within 24 months** — some may be "
              "unlabelled funded companies (controls are not verified):", "", pd.DataFrame(rows).to_markdown(index=False), ""]
        missed = ft[~ft["flagged"]].sort_values("best_rank", ascending=False).head(10)
        L += ["**Funded companies the radar ranked lowest:**", "",
              missed[["entity_id", "funding_date", "best_rank", "cohort_size"]].to_markdown(index=False), ""]
        tp = p[p["label"] == 1].sort_values("score", ascending=False).head(10)
        rows = []
        for r in tp.itertuples():
            crow = cf.loc[pd.Timestamp(r.as_of).year] if len(cf) and pd.Timestamp(r.as_of).year in cf.index else pd.Series(dtype=float)
            rows.append({"company": r.entity_id, "as_of": pd.Timestamp(r.as_of).date(),
                         "funded": pd.Timestamp(r.funding_date).date() if pd.notna(r.funding_date) else "",
                         "why it ranked high": reasons(pd.Series(r._asdict()), crow, snap[snap["as_of"] == r.as_of])})
        L += ["**Top-ranked funded companies with plain-English reasons:**", "", pd.DataFrame(rows).to_markdown(index=False), ""]

    # ---------- limitations
    L += ["## 8. Limitations (stated plainly)", "",
          "- **Selection bias in labels:** positives are YC and HTGF companies only; companies funded by other investors "
          "sit among the controls and make the radar look worse than it is (and differently so by region).",
          "- **Unverified negatives:** controls are assumed unfunded; nobody checked.",
          "- **Coverage:** a company is only visible if it has a GitHub organisation whose website is its domain and "
          "whose login is the domain name, and that showed team activity in its first year. Most deep-tech, hardware and "
          "biotech startups (HTGF's core) leave no such trace; university spin-offs with only a university web page are invisible.",
          "- **Current-state fields:** org websites and names are today's values; renamed orgs lose history; logins reused "
          "by a new owner are detected with an account-id→date map and dropped.",
          "- **Survivorship:** deleted orgs are missing for funded and unfunded companies alike, but survival correlates with funding.",
          "- **EU grants:** CORDIS stores websites only for FP7-era companies; newer grantees are matched by exact legal "
          "name + country, which needs a country-code domain (.de, .fr …); .com companies are 'not covered'.",
          "- **Stealth companies** leave few public traces and are invisible by construction.",
          "- **Non-UK registries** (Handelsregister) have no free bulk API; registry-based signals (serial founder) are roadmap.",
          "- **HTGF dates are year-precise**; YC dates are acceptance proxies (batch start − 76 days; batch start as sensitivity).",
          "- **Weak evidence for acceleration:** no paper measures GitHub *acceleration*; the engagement definition differs "
          "from Conti et al. (team-level PRs/issues/pushes vs org-level forks). See papers/index.md.",
          "- **Exploratory sample size:** for most YC companies the GitHub org appears around funding (median −0.6 months); "
          "only ~23% of matched funded companies had public GitHub activity before funding, so few are visible on a "
          "1 January before their round.",
          "- **Control listing pre-filter:** owners were pre-filtered with an all-time org-like rule before the point-in-time "
          "first-year rule was added; small early (2011–2014) orgs that never grew may be under-represented among controls. "
          "Re-listing with the point-in-time rule is an open item (≈1 h of quota/lookups).",
          "- **Failed metadata lookups:** a few sampled owners whose ecosyste.ms lookup failed were dropped without adjusting "
          "inclusion probabilities.",
          "- **GH Archive cut-off 2020-01-01:** features exist only for as_of ≤ 2019; locked years need a later fetch.",
          "- **Locked test years were not evaluated**; all numbers are from selection years used during development.", ""]
    md = "\n".join(L)
    (RESULTS / "report.md").write_text(md)
    if len(ft):
        ft.to_csv(RESULTS / f"flagged_{tag}.csv", index=False)
    return md


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", default="main")
    a = ap.parse_args(argv)
    md = build(a.tag)
    print(md[:3000])


if __name__ == "__main__":
    main()
