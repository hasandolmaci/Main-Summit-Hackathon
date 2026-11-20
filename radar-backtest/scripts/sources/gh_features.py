"""Quota-aware GH Archive feature collection for matched orgs (step 2).

The ClickHouse public playground allows ~300e9 rows read per hour (~25 full scans of github_events). This module
minimises scans:
  1. ONE combined scan per org chunk: every event is emitted under two keys via ARRAY JOIN — (owner, day) and
     (owner, actor) — and aggregated once. From it we derive the org span, daily Push/PR/Issues/Release counts and
     each actor's first push (team membership). Previously 3 scans.
  2. External engagement: actor-based scans with large actor chunks.
  3. Every query is bounded by `until` (default cfg gharchive.until = 2020-01-01: selection years + references);
     locked test years are fetched later only when the user unlocks them.
Writes the same processed tables as scripts/sources/gharchive.py:
  gh_org_span.parquet, gh_team.parquet (actor_hash only), gh_org_activity_daily.parquet, gh_ext_monthly.parquet
"""
from __future__ import annotations

import argparse

import pandas as pd

from ..lib.config import PROCESSED, cfg, get_logger
from ..lib.meta import write_meta
from . import gharchive as gha

log = get_logger("gh_features")
ORG_CHUNK = 800
ACTOR_CHUNK = 7000
COMBINED_COLUMNS = ["owner", "day_s", "actor", "push", "pr", "issues", "release", "n_all", "first_ts", "last_ts",
                    "first_push_ts"]
EXT_COLUMNS_RAW = ["actor", "month", "owner_bucket", "pr_opened", "issues_opened", "pushes"]


def until_default() -> str:
    return str(cfg()["gharchive"].get("until", "2020-01-01"))


def _until_sql(until: str) -> str:
    t = pd.Timestamp(until)
    return f"toDateTime('{t:%Y-%m-%d %H:%M:%S}', 'UTC')"


def sql_org_combined(orgs: list[str], until: str, regex: str | None = None, max_rows: int = gha.MAX_ROWS) -> str:
    f = gha._TS_FMT
    return gha._check_sql(f"""-- org_combined
SELECT owner, tupleElement(k, 1) AS day_s, tupleElement(k, 2) AS actor,
  countIf(event_type = 'PushEvent') AS push,
  countIf(event_type = 'PullRequestEvent') AS pr,
  countIf(event_type = 'IssuesEvent') AS issues,
  countIf(event_type = 'ReleaseEvent') AS release,
  count() AS n_all,
  formatDateTime(min(created_at), '{f}', 'UTC') AS first_ts,
  formatDateTime(max(created_at), '{f}', 'UTC') AS last_ts,
  formatDateTime(minIf(created_at, event_type = 'PushEvent'), '{f}', 'UTC') AS first_push_ts
FROM (SELECT {gha.OWNER_SQL} AS owner, created_at, event_type, lower(actor_login) AS al
      FROM github_events
      WHERE owner IN {gha.sql_in_list(orgs)} AND created_at < {_until_sql(until)}
        AND actor_login != '' AND {gha.bot_filter_sql(regex)})
ARRAY JOIN [(toString(toDate(created_at, 'UTC')), ''), ('', al)] AS k
GROUP BY owner, day_s, actor
ORDER BY owner, day_s, actor
LIMIT {max_rows + 1}
FORMAT TSVWithNames""")


def sql_external_until(actors: list[str], orgs: list[str], until: str, regex: str | None = None,
                       max_rows: int = gha.MAX_ROWS) -> str:
    bucket = f"if(owner IN {gha.sql_in_list(orgs)}, owner, {gha.sql_str(gha.EXT_BUCKET)})" if orgs \
        else gha.sql_str(gha.EXT_BUCKET)
    return gha._check_sql(f"""-- external_until
SELECT lower(actor_login) AS actor, toStartOfMonth(created_at, 'UTC') AS month,
  {bucket} AS owner_bucket,
  countIf(event_type = 'PullRequestEvent' AND action = 'opened') AS pr_opened,
  countIf(event_type = 'IssuesEvent' AND action = 'opened') AS issues_opened,
  countIf(event_type = 'PushEvent') AS pushes
FROM (SELECT actor_login, created_at, event_type, action, {gha.OWNER_SQL} AS owner
      FROM github_events
      WHERE event_type IN ('PushEvent', 'PullRequestEvent', 'IssuesEvent')
        AND lower(actor_login) IN {gha.sql_in_list(actors)} AND created_at < {_until_sql(until)}
        AND {gha.bot_filter_sql(regex)})
GROUP BY actor, month, owner_bucket
HAVING pr_opened + issues_opened + pushes > 0
ORDER BY actor, month, owner_bucket
LIMIT {max_rows + 1}
FORMAT TSVWithNames""")


def split_combined(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """combined rows -> (span, daily, team) in the gharchive.py typed-frame schemas."""
    day = raw[(raw["day_s"] != "") & (raw["actor"] == "")]
    act = raw[(raw["day_s"] == "") & (raw["actor"] != "")]
    for c in ("push", "pr", "issues", "release", "n_all"):
        day = day.assign(**{c: pd.to_numeric(day[c]).astype("int64")})
    span_raw = day.groupby("owner", as_index=False).agg(first_event_ts=("first_ts", "min"),
                                                        last_event_ts=("last_ts", "max"), n_events=("n_all", "sum"))
    span = gha.frame_span(span_raw) if len(span_raw) else gha._empty(gha.SPAN_COLUMNS)
    d4 = day[(day[["push", "pr", "issues", "release"]].sum(axis=1)) > 0].rename(columns={"day_s": "day"})
    daily = gha.frame_daily(d4) if len(d4) else gha._empty(gha.DAILY_COLUMNS)
    t = act[~act["first_push_ts"].astype(str).str.startswith("1970")]          # minIf default = no push
    team = gha.frame_team(t[["owner", "actor", "first_push_ts"]]) if len(t) else \
        gha._empty(["owner", "actor_login", "first_push_ts"])
    return span, daily, team


def _write(df: pd.DataFrame, name: str, params: dict, notes: str):
    p = PROCESSED / name
    df.to_parquet(p, index=False)
    write_meta(p, "scripts/sources/gh_features.py", rows=len(df), params=params,
               sources=["play.clickhouse.com github_events (GH Archive mirror)"], notes=notes)
    log.info("wrote %s (%d rows)", name, len(df))


def run(orgs: list[str], until: str | None = None, org_chunk: int = ORG_CHUNK, actor_chunk: int = ACTOR_CHUNK,
        dry_run: bool = False) -> dict:
    until = until or until_default()
    orgs, rejected = gha.normalize_logins(orgs, "org")
    n_org_q = len(gha.chunked(orgs, org_chunk))
    print(f"gh_features: {len(orgs)} orgs (rejected {len(rejected)}), until {until}: {n_org_q} combined scans "
          f"+ external scans after team size is known")
    if dry_run:
        return {"orgs": len(orgs), "combined_scans": n_org_q}
    ch = gha.ClickHouse()
    raw = ch.run_chunked("org_combined", lambda c: sql_org_combined(c, until), orgs, COMBINED_COLUMNS, org_chunk)
    span, daily, team = split_combined(raw)
    params = {"until": until, "org_chunk": org_chunk, "orgs": len(orgs)}
    _write(span, "gh_org_span.parquet", params, "first/last non-bot event and event count per org (combined scan)")
    _write(daily, "gh_org_activity_daily.parquet", params, "daily Push/PR/Issues/Release counts per org")
    _write(gha.team_to_processed(team), "gh_team.parquet", params, "first push per non-bot actor (hashed) per org")
    actors = sorted(set(team["actor_login"]))
    n_ext_q = len(gha.chunked(actors, actor_chunk))
    print(f"gh_features: {len(actors)} team actors -> {n_ext_q} external scans")
    ext_raw = ch.run_chunked("external_until", lambda c: sql_external_until(c, orgs, until), actors,
                             EXT_COLUMNS_RAW, actor_chunk) if actors else pd.DataFrame(columns=EXT_COLUMNS_RAW)
    ext = gha.frame_external(ext_raw) if len(ext_raw) else gha._empty(["actor_login", "month", "owner_bucket",
                                                                       "pr_opened", "issues_opened", "pushes"])
    _write(gha.external_to_processed(ext) if len(ext) else pd.DataFrame(columns=gha.EXT_COLUMNS),
           "gh_ext_monthly.parquet", {**params, "actor_chunk": actor_chunk, "actors": len(actors)},
           "team actors' monthly PRs opened / issues opened / pushes by owner bucket (own orgs vs _ext)")
    return {"orgs": len(orgs), "combined_scans": n_org_q, "actors": len(actors), "external_scans": n_ext_q}


def orgs_from_links() -> list[str]:
    p = PROCESSED / "entity_github_org.parquet"
    return sorted(set(pd.read_parquet(p)["org_login"].dropna().str.lower())) if p.exists() else []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orgs", help="file with one login per line (default: all orgs in entity_github_org.parquet)")
    ap.add_argument("--until", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    orgs = [l.strip() for l in open(a.orgs) if l.strip()] if a.orgs else orgs_from_links()
    print(run(orgs, a.until, dry_run=a.dry_run))


if __name__ == "__main__":
    main()
