"""Point-in-time org-like check for every matched GitHub org (review finding temporal-org-like-all-time-counts).

The org search and the pool listing pre-filtered owners with an org-like rule computed on ALL of GH Archive up to
today. Re-check every matched org using only its FIRST calendar year of activity: personal actor events
(any event type except Fork/Public/Member/TeamAdd/Event/Sponsorship; pre-2015 CreateEvents by the owner itself count
as org-ish, Timeline era) must be <= 1% of the owner's non-bot events in that year. The first calendar year always ends
before any 1 January on which the org can be visible (visibility requires first_event < as_of), so the rule is
point-in-time. Identical for positives and controls. One ClickHouse scan.

Output: data/processed/org_like_pit.parquet (login, first_year, n_events_fy, n_personal_fy, org_like_pit)
"""
from __future__ import annotations

import pandas as pd

from ..lib.config import PROCESSED, get_logger
from ..lib.meta import write_meta
from . import gharchive as gha
from . import github_owners as gho

log = get_logger("org_like_pit")
OUT = PROCESSED / "org_like_pit.parquet"
COLUMNS = ["login", "y", "n_events", "n_personal"]


def sql_owner_year(logins: list[str]) -> str:
    lst = gha.sql_in_list(logins)[1:-1]
    cands = "(SELECT login FROM cands)"
    orgish = ", ".join(gha.sql_str(t) for t in gho.ORGISH_ACTOR_TYPES)
    return gho._check_sql(f"""-- owner_year_orglike
WITH cands AS (SELECT arrayJoin([{lst}]) AS login)
SELECT tupleElement(p, 1) AS login, toYear(created_at, 'UTC') AS y,
  countIf(tupleElement(p, 2) = 1) AS n_events,
  countIf(tupleElement(p, 2) = 2 AND event_type NOT IN ({orgish})
          AND NOT (event_type = 'CreateEvent' AND created_at < toDateTime('2015-01-01 00:00:00', 'UTC'))) AS n_personal
FROM ({gho._scan(f"{gha.OWNER_SQL} IN {cands} OR lower(actor_login) IN {cands}")})
WHERE tupleElement(p, 1) IN {cands}
GROUP BY login, y
ORDER BY login, y
FORMAT TSVWithNames""")


def evaluate(rows: pd.DataFrame, ratio: float = gho.PERSONAL_RATIO) -> pd.DataFrame:
    r = rows.copy()
    for c in ("y", "n_events", "n_personal"):
        r[c] = pd.to_numeric(r[c]).astype("int64")
    out = []
    for login, g in r.groupby("login"):
        ev = g[g["n_events"] > 0]
        if ev.empty:
            out.append({"login": login, "first_year": None, "n_events_fy": 0, "n_personal_fy": 0, "org_like_pit": False})
            continue
        fy = int(ev["y"].min())
        row = g[g["y"] == fy].iloc[0]
        out.append({"login": login, "first_year": fy, "n_events_fy": int(row["n_events"]),
                    "n_personal_fy": int(row["n_personal"]),
                    "org_like_pit": bool(row["n_personal"] <= ratio * row["n_events"])})
    return pd.DataFrame(out)


def main(argv=None):
    links = pd.read_parquet(PROCESSED / "entity_github_org.parquet")
    logins = sorted(set(links["org_login"].dropna().str.lower()))
    logins, rejected = gha.normalize_logins(logins, "org")
    ch = gha.ClickHouse()
    raw = ch.run_chunked("owner_year_orglike", sql_owner_year, logins, COLUMNS, chunk_size=4000)
    res = evaluate(raw)
    missing = sorted(set(logins) - set(res["login"]))
    if missing:
        res = pd.concat([res, pd.DataFrame({"login": missing, "first_year": None, "n_events_fy": 0,
                                            "n_personal_fy": 0, "org_like_pit": False})], ignore_index=True)
    res.to_parquet(OUT, index=False)
    write_meta(OUT, "scripts/sources/org_like_pit.py", rows=len(res), params={"ratio": gho.PERSONAL_RATIO},
               notes="First-calendar-year org-like rule; applied to every matched org (positives and controls).")
    log.info("org_like_pit: %d orgs, %d pass, %d fail", len(res), int(res["org_like_pit"].sum()),
             int((~res["org_like_pit"]).sum()))


if __name__ == "__main__":
    main()
