"""Gate check before full collection: covered positives per as_of year and metric coverage.

Reads entities.csv (+ labels for funding dates). Writes data/processed/gate_counts.csv and results/gate.md.
"""
from __future__ import annotations

import pandas as pd

from .lib.cohort import case_cohort, label_window_complete
from .lib.config import LABELS, PROCESSED, RESULTS, as_of_date, cfg, data_cutoff, get_logger
from .lib.meta import write_meta

log = get_logger("gate")


def gate_table(ent: pd.DataFrame, labels: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    fd = ent["entity_id"].map(labels.set_index("domain")["first_funding_date"])
    rows = []
    for y in years:
        a = as_of_date(y)
        cc = case_cohort(ent, fd, a)
        e = ent.set_index("entity_id").loc[cc["entity_id"]]
        pos = cc["label"].values == 1
        for subset, mask in [("all", slice(None)), ("europe", e["europe"].fillna(False).values)]:
            c = cc[mask] if not isinstance(mask, slice) else cc
            ee = e[mask] if not isinstance(mask, slice) else e
            p = c["label"].values == 1
            n = ~p
            def share(x, m):
                return float(x[m].mean()) if m.sum() else float("nan")
            hc = ee["horizon_eligible"].fillna(False).values
            rows.append({
                "as_of": y, "subset": subset, "labels_complete": label_window_complete(a, data_cutoff()),
                "positives": int(p.sum()), "controls_B": int(n.sum()),
                "pos_via_github": int((p & c["gh_cov"].values).sum()), "pos_via_cordis": int((p & c["cordis_cov"].values).sum()),
                "cov_github_pos": share(c["gh_cov"].values, p), "cov_github_ctrl": share(c["gh_cov"].values, n),
                "cov_grant_pos": share(hc, p), "cov_grant_ctrl": share(hc, n),
                "grant1_pos": share(c["cordis_cov"].values, p), "grant1_ctrl": share(c["cordis_cov"].values, n),
                "weighted_base_rate": float(p.sum() / (p.sum() + c.loc[~p, "weight"].sum())) if len(c) else float("nan"),
            })
    return pd.DataFrame(rows)


def main():
    ent = pd.read_csv(PROCESSED / "entities.csv")
    labels = pd.read_csv(LABELS / "companies.csv")
    years = list(range(2012, 2026))
    t = gate_table(ent, labels, years)
    out = PROCESSED / "gate_counts.csv"
    t.to_csv(out, index=False)
    write_meta(out, "scripts/gate.py", inputs=[PROCESSED / "entities.csv", LABELS / "companies.csv"], rows=len(t))
    sel = cfg()["years"]["selection"]
    complete = t[(t.subset == "all") & t.labels_complete]
    lines = ["# Gate check", "", f"Positives (label = first funding in [as_of, as_of+24m)) and outcome-blind controls (B) "
             f"per as_of year. Labels complete up to as_of {complete.as_of.max() if len(complete) else 'n/a'}.", "",
             t.round(3).to_markdown(index=False), "",
             f"Pooled covered positives in configured selection years {sel}: "
             f"{int(t[(t.subset=='all') & t.as_of.between(*sel)].positives.sum())}",
             f"Target ≥ {cfg()['min_positives']['target']}; below {cfg()['min_positives']['exploratory_below']} → exploratory only."]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "gate.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
