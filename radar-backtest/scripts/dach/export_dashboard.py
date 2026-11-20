"""Flatten dach/results/loop_state.json into dach/results/dashboard_data.json (rows for the hosted dashboard).

  python -m scripts.dach.export_dashboard
"""
from __future__ import annotations

import json
import re

from .io import DACH

RES = DACH / "results"
N_SHUFFLED = 20


def _f(v):
    return None if v is None else round(float(v), 4)


def export() -> dict:
    s = json.loads((RES / "loop_state.json").read_text())
    stage = s.get("stage", "")
    m = re.search(r"\((\d+)/(\d+)\)", stage)
    real = s.get("real") or {}
    attempts = [a for a in s.get("attempts", []) if a.get("run") == "real"]
    shuffled = s.get("shuffled", [])
    cur_auc = real.get("oof_auc") if real else (attempts[-1]["auc_before"] if attempts else (s.get("start") or {}).get("auc"))
    if attempts and not real:
        last = attempts[-1]
        cur_auc = last["auc_after"] if last.get("decision") == "keep" else last["auc_before"]
    phase = ("done" if stage == "done" else "shuffled-label control" if stage.startswith("shuffled")
             else "backward pass" if "backward" in stage else "forward pass" if "forward" in stage else "starting")
    summ = s.get("summary") or {}
    status = {
        "stage": stage, "phase": phase,
        "step": int(m.group(1)) if m else None, "steps": int(m.group(2)) if m else None,
        "n_labelled": s.get("n_labelled"), "n_pos": s.get("n_pos"),
        "baseline_auc": _f((s.get("baseline") or {}).get("auc")),
        "start_auc": _f((s.get("start") or {}).get("auc")),
        "current_auc": _f(cur_auc),
        "current_set": ", ".join(real.get("final_set", s.get("current_set", []))) if real or s.get("current_set") else "",
        "kept_so_far": sum(a.get("decision") == "keep" for a in attempts),
        "tried_so_far": len(attempts),
        "real_done": bool(real),
        "shuffled_done": len(shuffled), "shuffled_total": N_SHUFFLED,
        "verdict": summ.get("verdict", ""),
        "real_beats_share": _f(summ.get("real_beats_share_of_shuffled")),
        "started_at": s.get("started_at"), "updated_at": s.get("updated_at"),
    }
    att = [{"n": i + 1, "pass": a.get("pass"), "metric": a.get("metric"),
            "auc_before": _f(a.get("auc_before")), "auc_after": _f(a.get("auc_after")), "delta": _f(a.get("delta")),
            "ci_lo": _f(a.get("ci_lo")), "ci_hi": _f(a.get("ci_hi")), "share_improved": _f(a.get("share_improved")),
            "decision": a.get("decision"), "reason": a.get("reason") or ""} for i, a in enumerate(attempts)]
    shf = [{"run": d["run"], "kept": d["kept"], "oof_auc": _f(d["oof_auc"]), "final_set": ", ".join(d["final_set"])}
           for d in shuffled]
    out = {"status": [status], "attempts": att, "shuffled": shf}
    (RES / "dashboard_data.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    o = export()
    print(o["status"][0]["stage"], "|", len(o["attempts"]), "attempts |", len(o["shuffled"]), "shuffled")
