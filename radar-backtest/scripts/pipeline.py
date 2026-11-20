"""Run the data pipeline in order, resumably, waiting out the ClickHouse playground quota.

Steps (each idempotent; HTTP/ClickHouse responses are cached, so a rerun only does missing work):
  org_search_labelled  stem-only tier-capped GitHub org search for YC/HTGF domains
  pool_sample          outcome-blind GitHub org pool (entry rule) — never reads labels
  select_controls      universe + B from the pool sample
  targets              org search input = universe ∪ labelled
  org_search_all       same search for every entity
  cordis_bulk          (re)build CORDIS tables from the bulk zips in data/raw/cordis
  gh_features          GH Archive span/team/daily/external for all matched orgs (until cfg gharchive.until)
  match_cordis         grant links (domain, unique name + country)
  resolve_build        entities.csv + events.parquet (+ pool entry rule, recycled-login guard)
  validate, gate
Usage: python -m scripts.pipeline [--from STEP] [--to STEP]
"""
from __future__ import annotations

import argparse
import re
import time
from datetime import datetime, timezone

from .lib.config import get_logger

log = get_logger("pipeline")


def _steps():
    from . import gate, match_cordis, resolve_entities, run_org_search, select_controls, validate_data
    from .sources import cordis, gh_features, pool_sample
    return [
        ("org_search_labelled", lambda: run_org_search.main(["--labelled-only"])),
        ("pool_sample", lambda: pool_sample.main([])),
        ("select_controls", lambda: select_controls.main([])),
        ("targets", lambda: resolve_entities.main(["targets"])),
        ("org_search_all", lambda: run_org_search.main([])),
        ("cordis_bulk", lambda: cordis.main(["--skip-download"])),
        ("gh_features", lambda: gh_features.main([])),
        ("match_cordis", lambda: match_cordis.main([])),
        ("resolve_build", lambda: resolve_entities.main(["build"])),
        ("validate", lambda: validate_data.main()),
        ("gate", lambda: gate.main()),
    ]


def quota_reset_wait(msg: str) -> float | None:
    """Seconds until the ClickHouse quota interval ends (parsed from the error text), else None."""
    if "Quota for user" not in msg:
        return None
    m = re.search(r"Interval will end at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", msg)
    if not m:
        return 600.0
    end = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return max(30.0, (end - datetime.now(timezone.utc)).total_seconds() + 30.0)


def run(start: str | None = None, stop: str | None = None, max_waits: int = 6):
    steps = _steps()
    names = [n for n, _ in steps]
    i0 = names.index(start) if start else 0
    i1 = names.index(stop) + 1 if stop else len(steps)
    for name, fn in steps[i0:i1]:
        waits = 0
        while True:
            t0 = time.monotonic()
            log.info("=== step %s", name)
            try:
                rc = fn()
                if isinstance(rc, int) and rc != 0:
                    raise RuntimeError(f"step {name} returned exit code {rc}")
                log.info("=== step %s done in %.0fs", name, time.monotonic() - t0)
                break
            except SystemExit as e:
                if e.code not in (None, 0):
                    raise
                break
            except Exception as e:  # noqa: BLE001
                w = quota_reset_wait(str(e))
                if w is None or waits >= max_waits:
                    raise
                waits += 1
                log.warning("step %s hit the ClickHouse quota; waiting %.0f s, then resuming (cached work is kept)", name, w)
                time.sleep(w)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="stop")
    a = ap.parse_args(argv)
    run(a.start, a.stop)


if __name__ == "__main__":
    main()
