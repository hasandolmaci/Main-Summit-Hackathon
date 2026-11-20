"""Step 5: snapshots -> main backtest -> sensitivity runs -> report (selection years only).

  main         R=5 kNN references, YC date = acceptance proxy, engagement robust z
  R3           kNN reference window 3 years (spec: compare R=5 vs R=3)
  batchstart   YC date = batch start (spec label definition; main uses the acceptance proxy)
  scaling_pct  engagement scaled by within-cohort percentile instead of log -> robust z -> sigmoid
Locked test years are never touched here (guard_years in every entry point).
"""
from __future__ import annotations

import argparse

from . import backtest, build_snapshots, report
from .lib.config import cfg, get_logger

log = get_logger("run_backtests")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=cfg()["bootstrap"]["reps"])
    ap.add_argument("--skip", nargs="*", default=[], help="any of: snapshots R3 batchstart scaling_pct")
    a = ap.parse_args(argv)
    years = [str(y) for y in range(2012, cfg()["years"]["selection"][1] + 1)]
    reps = ["--reps", str(a.reps)]
    sens = ["--reps", str(min(a.reps, 200))]          # sensitivity runs: AUC comparisons only, fewer resamples
    if "snapshots" not in a.skip:
        build_snapshots.main(["--years", *years])
    backtest.main([*reps, "--tag", "main"])
    if "R3" not in a.skip:
        backtest.main([*sens, "--R", "3", "--tag", "R3"])
    if "batchstart" not in a.skip:
        build_snapshots.main(["--years", *years, "--funding-file", "companies_batchstart.csv", "--suffix", "_batchstart"])
        backtest.main([*sens, "--suffix", "_batchstart", "--tag", "batchstart"])
    if "scaling_pct" not in a.skip:
        build_snapshots.main(["--years", *years, "--engagement-scaling", "percentile", "--suffix", "_pct"])
        backtest.main([*sens, "--suffix", "_pct", "--tag", "scaling_pct"])
        build_snapshots.ENGAGEMENT_SCALING = "robust_z"
    report.build("main")
    log.info("results/report.md written")


if __name__ == "__main__":
    main()
