"""Outcome-blind GitHub org pool sample with a team-activity entry rule (pool channel a, v2).

Why: a plain random sample of org-like GH Archive owners yields ~1-5% usable company orgs (most are dormant
side projects without a website), i.e. ~5-15 controls per year. The pool is therefore defined as GitHub orgs that
look like a TEAM early on, using only information from their own first 12 months of public activity:

  entry rule (same for every entity, positives included — see scripts/lib/cohort.py):
    within [first_event, first_event + 365d): >= MIN_PUSHERS distinct non-bot actors pushed to the org's repos AND
    >= MIN_ACT non-bot Push/PullRequest/Issues/Release events on the org's repos.
    The entity enters the pool on the day the rule is first met (point-in-time; never later than first year).
  findability (same as the stem-only staged search used for every entity, scripts/sources/github_owners.find_org):
    ecosyste.ms kind == organization, website registrable domain D (platform domains only if the org IS the
    platform), login in search tiers <= MAX_TIER of D's stem, login not recycled (scripts/sources/gh_id_dates).

Sampling: per first-activity year Y, owners with cityHash64(owner) % 10000 < m_Y (org-like rule from
github_owners), m_Y = smallest bound giving >= target owners that meet the entry rule -> inclusion_prob = m_Y/10000.
NEVER reads labels.

Output: data/processed/github_org_sample.parquet (+ _years.csv), schema compatible with scripts/select_controls.py.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from ..lib.config import PROCESSED, cfg, get_logger
from ..lib.domains import company_domain
from ..lib.meta import write_meta
from . import gharchive as gha
from . import github_owners as gho
from .github import tier_candidates

log = get_logger("pool_sample")

OUT = PROCESSED / "github_org_sample.parquet"
OUT_YEARS = PROCESSED / "github_org_sample_years.csv"
ACT_TYPES = ("PushEvent", "PullRequestEvent", "IssuesEvent", "ReleaseEvent")
CRIT_COLUMNS = ["login", "n_act_12m", "n_pushers_12m"]


def pool_cfg() -> dict:
    p = cfg()["pool"]
    return {"min_act": int(p.get("min_activity_12m", 10)), "min_pushers": int(p.get("min_pushers_12m", 2)),
            "max_tier": int(p.get("max_search_tier", 1)), "pilot_m": int(p.get("pilot_m", 300)),
            "per_year": int(p.get("owner_lookups_per_year", 300))}


def sql_first_year_activity(logins_first: list[tuple[str, pd.Timestamp]], regex: str | None = None) -> str:
    """Per owner: Push/PR/Issues/Release events and distinct pushers in [first_event, first_event + 365d)."""
    if not logins_first:
        raise ValueError("no logins")
    logins = [l for l, _ in logins_first]
    gha.sql_in_list(logins)                                    # validates [a-z0-9-] logins (raises otherwise)
    ends = [pd.Timestamp(t).tz_convert("UTC") + pd.Timedelta(days=365) for _, t in logins_first]
    arr_l = ", ".join(gha.sql_str(l) for l in logins)
    arr_t = ", ".join(f"toDateTime('{e:%Y-%m-%d %H:%M:%S}', 'UTC')" for e in ends)
    rx = gha.sql_str(regex or gha.bots_regex())
    types = ", ".join(gha.sql_str(t) for t in ACT_TYPES)
    sql = f"""-- first_year_activity
SELECT {gha.OWNER_SQL} AS login,
  countIf(event_type IN ({types})) AS n_act_12m,
  uniqExactIf(lower(actor_login), event_type = 'PushEvent') AS n_pushers_12m
FROM github_events
WHERE {gha.OWNER_SQL} IN [{arr_l}]
  AND created_at < transform({gha.OWNER_SQL}, [{arr_l}], [{arr_t}], toDateTime('1970-01-01 00:00:00', 'UTC'))
  AND NOT match(lower(actor_login), {rx})
GROUP BY login
ORDER BY login
FORMAT TSVWithNames"""
    if len(sql.encode()) > gha.MAX_SQL_BYTES:
        raise ValueError("SQL too large; use a smaller chunk")
    return sql


def first_year_activity(listing: pd.DataFrame, ch: gha.ClickHouse, chunk: int = 2500) -> pd.DataFrame:
    """Entry-rule statistics for the given owners (one full-table scan per chunk — mind the playground quota)."""
    rows = listing[["login", "first_event_ts"]].dropna().drop_duplicates("login")
    out = []
    pairs = list(zip(rows["login"], pd.to_datetime(rows["first_event_ts"], utc=True)))
    for i in range(0, len(pairs), chunk):
        part = pairs[i:i + chunk]
        out.append(ch.query("first_year_activity", sql_first_year_activity(part), CRIT_COLUMNS))
        log.info("first-year activity: %d/%d owners", min(i + chunk, len(pairs)), len(pairs))
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=CRIT_COLUMNS)
    for c in ("n_act_12m", "n_pushers_12m"):
        res[c] = pd.to_numeric(res[c]).astype("int64")
    return res


def incremental_entry_scan(listing: pd.DataFrame, ch: gha.ClickHouse, target: int, pilot_m: int, min_act: int,
                           min_pushers: int, chunk: int = 2500, fya=None) -> tuple[pd.DataFrame, dict]:
    """Scan owners in hash-bucket order; a year stops as soon as buckets [0, m) hold >= target owners meeting the
    entry rule. Returns (scanned owners with entry stats, {year: (m_Y, reached)}). Exact: for every year, ALL owners
    with bucket < m_Y were scanned, so inclusion_prob = m_Y / 10000."""
    fya = fya or first_year_activity
    lst = listing.sort_values(["hash_bucket", "first_year", "login"]).reset_index(drop=True)
    years = sorted(int(y) for y in lst["first_year"].unique())
    active = set(years)
    done_m = {y: (pilot_m, False) for y in years}
    scanned = []
    b = 0
    while active and b < pilot_m:
        batch, b_end = [], b
        while b_end < pilot_m:                       # take whole buckets until the chunk is full
            nxt = lst[(lst["hash_bucket"] == b_end) & lst["first_year"].isin(active)]
            if batch and sum(len(x) for x in batch) + len(nxt) > chunk:
                break
            batch.append(nxt)
            b_end += 1
        part = pd.concat(batch, ignore_index=True) if batch else lst.iloc[0:0]
        if len(part):
            crit = fya(part, ch, chunk=max(chunk, len(part)))
            part = part.merge(crit, on="login", how="left").fillna({"n_act_12m": 0, "n_pushers_12m": 0})
            part["entry_ok"] = meets_entry_rule(part["n_act_12m"], part["n_pushers_12m"], min_act, min_pushers)
            scanned.append(part)
        b = b_end
        allp = pd.concat(scanned, ignore_index=True) if scanned else lst.iloc[0:0].assign(entry_ok=False)
        for y in sorted(active):
            ok = allp[(allp["first_year"] == y) & allp["entry_ok"]]
            m, reached = choose_m(ok["hash_bucket"].to_numpy(), target, b)   # only buckets < b are complete
            if reached:
                done_m[y] = (m, True)
                active.discard(y)
        log.info("entry scan: buckets < %d done; years still below target: %s", b, sorted(active))
    for y in active:
        done_m[y] = (b if b < pilot_m else pilot_m, False)
    allp = pd.concat(scanned, ignore_index=True) if scanned else lst.iloc[0:0].assign(entry_ok=False)
    return allp, done_m


def meets_entry_rule(n_act, n_pushers, min_act: int, min_pushers: int) -> np.ndarray:
    return (np.asarray(n_act) >= min_act) & (np.asarray(n_pushers) >= min_pushers)


def choose_m(buckets: np.ndarray, target: int, pilot_m: int) -> tuple[int, bool]:
    """Smallest m such that #owners (meeting the entry rule) with bucket < m >= target."""
    counts = np.bincount(np.asarray(buckets, dtype=int), minlength=pilot_m)[:pilot_m]
    cum = np.cumsum(counts)
    hit = np.nonzero(cum >= target)[0]
    return (int(hit[0]) + 1, True) if len(hit) else (pilot_m, False)


def findable(login: str, kind, website_url, display_name, max_tier: int) -> tuple[bool, str | None]:
    """Same acceptance the stem-only staged search applies (kind, website == D, login in tiers <= max_tier of D)."""
    if kind != "organization" or not isinstance(login, str):
        return False, None
    d = company_domain(website_url, (login, display_name if isinstance(display_name, str) else ""))
    if d is None:
        return False, None
    tiers = tier_candidates(d, ())
    ok = login.lower() in {c for t in range(1, max_tier + 1) for c in tiers.get(t, [])}
    return ok, d


def recycled(uuid, first_event_ts, id_map: pd.DataFrame | None, slack_days: int = 90) -> bool:
    if id_map is None or uuid is None or pd.isna(uuid) or pd.isna(first_event_ts):
        return False
    from .gh_id_dates import creation_bounds
    b = creation_bounds([uuid], id_map).iloc[0]
    lo = b["created_after"]
    return bool(pd.notna(lo) and lo > pd.Timestamp(first_event_ts) + pd.Timedelta(days=slack_days))


def run(years=None, per_year=None, max_requests=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    pc = pool_cfg()
    years = gho._year_list(years)
    target = int(per_year or pc["per_year"])
    ch = gha.ClickHouse()
    log.info("pool: listing org-like owners, buckets < %d, years %s", pc["pilot_m"], years)
    listing = gho.frame_sample_list(ch.query("owner_sample", gho.sql_owner_sample({y: pc["pilot_m"] for y in years}),
                                             gho.SAMPLE_RAW_COLUMNS))
    lst, chosen = incremental_entry_scan(listing, ch, target, pc["pilot_m"], pc["min_act"], pc["min_pushers"])
    for y in years:
        chosen.setdefault(y, (pc["pilot_m"], False))
    todo = []
    for y in years:
        m, _ = chosen[y]
        todo += lst.loc[(lst["first_year"] == y) & lst["entry_ok"] & (lst["hash_bucket"] < m), "login"].tolist()
    log.info("pool: m per year %s; %d owners meet the entry rule -> ecosyste.ms lookups",
             {y: m for y, (m, _) in chosen.items()}, len(todo))
    budget = gho.Budget(max_requests) if max_requests else gho.Budget()
    looked = gho.owner_frame(gho.lookup_owners(sorted(set(todo)), budget=budget)).set_index("query_login") \
        if todo else gho.owner_frame([]).set_index("query_login")
    idp = PROCESSED / "gh_id_date_map.csv"
    id_map = pd.read_csv(idp) if idp.exists() else None
    rows, stats = [], []
    for y in years:
        m, reached = chosen[y]
        sub = lst[(lst["first_year"] == y) & (lst["hash_bucket"] < m)]
        n = {"listed": len(sub), "entry_ok": int(sub["entry_ok"].sum()), "looked": 0, "pending": 0, "org": 0,
             "findable": 0, "recycled": 0, "pool": 0}
        for r in sub[sub["entry_ok"]].to_dict("records"):
            o = looked.loc[r["login"]].to_dict() if r["login"] in looked.index else None
            if o is None or o.get("lookup_error") is not None:
                n["pending"] += 1
                continue
            n["looked"] += 1
            kind = o["kind"] if o.get("note") is None else None
            n["org"] += int(kind == "organization")
            ok, dom = findable(o["login"], kind, o["website_url"], o["display_name"], pc["max_tier"])
            n["findable"] += int(ok)
            rec = recycled(o.get("uuid"), r["first_event_ts"], id_map) if ok else False
            n["recycled"] += int(rec)
            in_pool = ok and not rec
            n["pool"] += int(in_pool)
            rows.append({"login": o["login"], "uuid": o.get("uuid"), "first_event_ts": r["first_event_ts"],
                         "first_event_year": y, "hash_bucket": r["hash_bucket"], "hash_m": m,
                         "inclusion_prob": m / gho.HASH_MOD, "kind": kind, "website_domain": dom,
                         "display_name": o["display_name"], "n_act_12m": r["n_act_12m"],
                         "n_pushers_12m": r["n_pushers_12m"], "login_recycled": rec, "in_pool": in_pool})
        stats.append({"year": y, "hash_m": m, "target_reached": reached, "inclusion_prob": m / gho.HASH_MOD, **n})
    df = pd.DataFrame(rows)
    ys = pd.DataFrame(stats)
    if len(df):
        df["first_event_ts"] = pd.to_datetime(df["first_event_ts"], utc=True)
    df.to_parquet(OUT, index=False)
    ys.to_csv(OUT_YEARS, index=False)
    for p in (OUT, OUT_YEARS):
        write_meta(p, "scripts/sources/pool_sample.py", rows=len(df) if p == OUT else len(ys),
                   sources=["play.clickhouse.com (GH Archive mirror)", "repos.ecosyste.ms (CC BY-SA 4.0)"],
                   params={**pc, "years": years, "target": target},
                   notes="Outcome-blind pool: org-like owners hashed per first-activity year; entry rule = first-12-"
                         "month team activity; findable by the stem-only search; recycled logins excluded.")
    return df, ys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", nargs="*", type=int)
    ap.add_argument("--per-year", type=int)
    ap.add_argument("--max-requests", type=int)
    a = ap.parse_args(argv)
    df, ys = run(a.years, a.per_year, a.max_requests)
    print(ys.to_string(index=False))
    print(f"in_pool: {int(df['in_pool'].sum()) if len(df) else 0} orgs")


if __name__ == "__main__":
    main()
