"""Stem-only staged GitHub org search for every entity, capped at cfg pool.max_search_tier (identical for all).

Wraps scripts.sources.github_owners.find (GH Archive existence/org-like check + ecosyste.ms metadata) and limits the
candidate tiers it may use, so positives and controls are searched with exactly the same depth.

  python -m scripts.run_org_search --labelled-only   # YC/HTGF domains (before the pool sample exists)
  python -m scripts.run_org_search                   # universe ∪ labelled (data/processed/org_search_input.csv)
"""
from __future__ import annotations

import argparse

import pandas as pd

from .lib.config import LABELS, PROCESSED, cfg, get_logger
from .sources import github as gh
from .sources import github_owners as gho

log = get_logger("run_org_search")


def limit_tiers(max_tier: int):
    base = gh.tier_candidates

    def limited(entity_domain, names=()):
        t = base(entity_domain, ())                     # names never used: stem-only for every entity
        return {k: (v if k <= max_tier else []) for k, v in t.items()}
    return limited


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labelled-only", action="store_true")
    ap.add_argument("--max-requests", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    max_tier = int(cfg()["pool"].get("max_search_tier", 1))
    gho.tier_candidates = limit_tiers(max_tier)
    if a.labelled_only:
        lab = pd.read_csv(LABELS / "companies.csv", dtype=str, keep_default_na=False)
        inp = PROCESSED / "org_search_labelled.csv"
        pd.DataFrame({"entity_id": sorted(set(lab["domain"]) - {""}), "names": ""}).to_csv(inp, index=False)
    else:
        inp = PROCESSED / "org_search_input.csv"
    args = ["find", "--input", str(inp)]
    if a.max_requests:
        args += ["--max-requests", str(a.max_requests)]
    if a.dry_run:
        args += ["--dry-run"]
    log.info("org search: input %s, max tier %d", inp, max_tier)
    gho.main(args)


if __name__ == "__main__":
    main()
