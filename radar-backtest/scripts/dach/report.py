"""Reports for the DACH cohort backtest: cleaning_report.md, label_review.csv and the loop report.

Label counts are read here ONLY to report them; the cleaning code itself never sees y_* columns.
"""
from __future__ import annotations

import json
from collections import defaultdict

import numpy as np
import pandas as pd

from . import clean as C
from . import features as FE
from . import io
from . import labels as LB
from .io import DACH

RES = DACH / "results"


def cleaning_report() -> str:
    iso = io.read_iso()
    x, outcomes = C.split_outcomes(iso)
    cx, log = C.clean(x)
    y = outcomes.set_index("id_company")["y_label"]
    def counts(ids):
        v = y.reindex(list(ids))
        return int((v == "1").sum()), int((v == "0").sum())
    by = defaultdict(list)
    for e in log:
        by[e["rule"]].append(e)
    p0, n0 = counts(iso["id_company"])
    p1, n1 = counts(cx["id_company"])
    raw = io.load_raw()
    L = ["# Cleaning report — DACH cohort backtest", "",
         "Rules were fixed before the loop; the cleaning code receives the data **without** any `y_*` column "
         "(`clean.split_outcomes`). Labels below are counted only for this report.", "",
         "## 0. Re-export (no Excel)", "",
         f"- Input `dach/data/raw/backtest_upload.csv`: {raw.shape[0]} rows × {raw.shape[1]} columns.",
         "- All dates were `dd/mm/yyyy` → re-exported as ISO `yyyy-mm-dd` (`dach/data/clean/backtest_iso.csv`).",
         f"- `s_homepage_snapshot_ts` / `s_impressum_snapshot_ts` had been turned into scientific notation by Excel "
         f"({int(raw['s_homepage_snapshot_ts'].str.contains('E+', regex=False).sum())} / "
         f"{int(raw['s_impressum_snapshot_ts'].str.contains('E+', regex=False).sum())} values, e.g. `2.02202E+13`). "
         "Only 6 significant digits survive, so they are recovered to **month precision** (flag `<col>_precision`); "
         "an as-of check passes only if the whole snapshot month lies before as_of. The generating pipeline is not in "
         "this repository: an exact (second-precision) re-export has to be produced there; `read_iso` refuses any file "
         "that contains scientific notation again.", "",
         "## Rules", "",
         "| rule | what | rows affected |", "|---|---|---|",
         "| a | missing ≠ 0: blanks stay NaN end to end; nothing is filled with 0; missing indicators are added in-fold | all |",
         f"| b | `domain_invalid`: platform / university domain or snapshot of another site → all domain- and snapshot-derived fields blanked (GitHub match blanked too when it was made through that domain); row kept | {len(by['b_domain_invalid'])} |",
         f"| c | `f_snapshot_invalid`: parked / for-sale / unrelated page → page-derived fields blanked; row kept | {len(by['c_snapshot_invalid'])} |",
         f"| d | not a startup: legal form e.V. or cohort description says student group/club → row dropped | {len(by['d_not_a_startup'])} |", "",
         "## Rows affected", "", "| rule | company | reason |", "|---|---|---|"]
    L += [f"| {e['rule']} | {e['id_company']} | {e['reason']} |" for e in log]
    L += ["", "## Labelled rows before / after", "", "| | positives | negatives |", "|---|---|---|",
          f"| before cleaning | {p0} | {n0} |", f"| after cleaning | {p1} | {n1} |", ""]
    md = "\n".join(L)
    (RES / "cleaning_report.md").write_text(md)
    return md


def loop_report() -> str:
    S = json.loads((RES / "loop_summary.json").read_text())
    log = pd.read_csv(RES / "loop_log.csv")
    real = S["real"]
    vb = real.get("vs_baseline") or {}
    s_auc = np.array(S["shuffled_oof_auc"])
    iso = io.read_iso()
    x, outcomes = C.split_outcomes(iso)
    lab = outcomes[outcomes["y_label"].isin(["0", "1"])]
    from .loop import univariate_auc
    nr_auc = univariate_auc(lab["needs_review"].astype(float).to_numpy(), lab["y_label"].astype(int).to_numpy())
    L = ["# DACH cohort backtest — improvement loop (exploratory)", "",
         f"**Verdict: {S['verdict']}.** Real-label OOF AUC **{real['oof_auc']:.3f}** beats "
         f"**{S['real_beats_share_of_shuffled']:.0%}** of the {len(s_auc)} shuffled-label runs "
         f"(needs ≥ 95%; shuffled OOF AUC median {np.median(s_auc):.3f}, 95th pct {np.quantile(s_auc, 0.95):.3f}; "
         f"shuffled runs kept on average {S['shuffled_mean_kept']:.1f} metrics).", "",
         f"- Labelled companies: {S['n_labelled']} ({S['n_pos']} positives) after cleaning; label definition frozen.",
         f"- Baseline floor (cohort source + cohort year): OOF AUC **{real['baseline_auc']:.3f}**.",
         f"- Starting set (GitHub, as-of gated): OOF AUC {real['start_auc']:.3f}.",
         f"- Final set: **{', '.join(real['final_set']) or 'baseline only'}** → OOF AUC {real['oof_auc']:.3f}"
         + (f" (vs baseline Δ {vb['delta']:+.3f}, 90% CI [{vb['ci_lo']:+.3f}, {vb['ci_hi']:+.3f}])" if vb else "") + ".",
         "", "AUC = within-fold AUC pooled over grouped folds (groups = programme × cohort), 5 folds × 20 repeats; "
         "Δ CIs from a 1,000× paired bootstrap on out-of-fold predictions.", "",
         "## Every attempt (real labels)", "",
         log[log["run"] == "real"][["pass", "metric", "auc_before", "auc_after", "delta", "ci_lo", "ci_hi", "share_improved",
                                    "decision", "reason"]].round(3).fillna("").to_markdown(index=False), "",
         "## Leakage guards", "",
         f"- Tripwire check: `needs_review` scores univariate AUC **{nr_auc:.2f}** on the labelled rows and is caught "
         "(test `test_tripwire_catches_needs_review`). Any candidate with univariate AUC > 0.80 or derived from a "
         "timestamp is halted and logged as a suspected leak.",
         "- GitHub metrics NaN when the account was created after as_of; snapshot-derived fields NaN unless the "
         "snapshot lies before as_of; patents only via `m_patent_verified_before_asof` (raw assignee matches collide).",
         "- `m_serial_score` excluded before the run (provenance not shown to be as-of).",
         "- Text candidates 19–21, 23, 24 (step 4c) were not computed: no Claude API key in this environment. "
         "They remain pre-registered; running them later is a separate, clearly labelled run.", "",
         "## Shuffled-label control", "",
         "| run | kept | OOF AUC |", "|---|---|---|"]
    L += [f"| {i} | {k} | {a:.3f} |" for i, (k, a) in enumerate(zip([None] * len(s_auc), s_auc))]
    md = "\n".join(L).replace("| None |", "| – |")
    (RES / "report.md").write_text(md)
    return md


def main():
    cleaning_report()
    LB.write_label_review(io.read_iso())
    if (RES / "loop_summary.json").exists():
        loop_report()
    print("wrote dach/results/cleaning_report.md, label_review.csv" +
          (", report.md" if (RES / "loop_summary.json").exists() else ""))


if __name__ == "__main__":
    main()
