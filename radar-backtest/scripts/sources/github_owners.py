"""Keyless GitHub org discovery: GH Archive (ClickHouse mirror) + ecosyste.ms owner metadata.

Replaces the GitHub-API route of scripts/sources/github.py (GraphQL and non-repo REST paths are blocked in this
session) with two keyless sources:
  * GH Archive through the ClickHouse public playground (scripts.sources.gharchive.ClickHouse: cached, rate limited,
    every SQL + TSV result persisted under data/raw/gharchive/): which logins own repositories, when they were first
    active, and whether the account behaves like an organisation;
  * ecosyste.ms (GET https://repos.ecosyste.ms/api/v1/hosts/GitHub/owners/<login>; data licence CC BY-SA 4.0;
    anonymous limit 5000 requests/hour, config interval 0.75 s): owner kind (organization | user), website, display
    name and uuid (= GitHub database id). 404 = owner unknown to ecosyste.ms.

Definitions (identical for every login)
  owner        = lower-cased repo_name prefix (gharchive.OWNER_SQL); bots = cfg gharchive.bots_regex on actor_login.
  n_events     = non-bot events (any type) on repos owned by the login; first/last_event_ts = min/max created_at (UTC:
                 the time the event became public). exists_as_owner = n_events > 0.
  is_actor     = the login appears as actor_login anywhere (n_actor_events > 0).
  org_like     = exists_as_owner AND n_personal_actor_events <= PERSONAL_RATIO * n_events, where personal actor events
                 are the login's actor events of any type EXCEPT ORGISH_ACTOR_TYPES (Fork, Public, Member, TeamAdd,
                 Event, Sponsorship) - the types GH Archive attributes to organisation accounts.
  Why not "org_like = NOT is_actor": organisations DO appear as actor_login (an org that forks a repo, adds a member,
  opens a repo). Measured with `calibrate` (owner kind from the `org` field of 2015+ GH Archive events, 4 hours
  2016-2023, ~5,000 org + ~5,000 user owners): NOT is_actor keeps only ~13% of organisations; the org_like rule keeps
  ~98% of organisations and ~0.2% of users (data/processed/github_owner_kind_calibration.csv). Every org_like owner
  is then confirmed by ecosyste.ms kind == 'organization'; org_like only decides which owners are looked up.
  Timeline era (review check 2026-11-20, ground truth = repository.organization of the 2013-05-08-15 hour, 954 orgs /
  2,000 users): org recall is only ~91% (~69% for orgs with 11-100 owned events), user pass ~0.6%, because GH Archive
  attributes 2011-2013 CreateEvents to the organisation as actor. Treating pre-2015 CreateEvents as org-ish raises
  that recall to ~98% (~97%) but multiplies org_like owners of first-year cohorts 2011-2014 by 2.5-6.6x (small users
  pass), i.e. many more ecosyste.ms lookups per organisation found; not adopted here (same rule for both groups, so
  the loss is coverage, not bias).

Functions
  owner_activity(logins)   one ClickHouse scan per chunk (<= ACT_CHUNK logins, <= MAX_LIST_BYTES of IN-list): the
                           owner role and the actor role of every event are read in the SAME scan (two separate scans
                           exceed the playground's 20-billion-rows-read limit).
  lookup_owners(logins)    ecosyste.ms lookups. Responses are fetched WITHOUT the HTTP cache (they carry e-mail /
                           twitter / location fields); only whitelisted fields are written to
                           data/raw/ecosystems/owners_<sha8>.jsonl.gz (+ .meta.json). Organisations keep login, kind,
                           uuid, name, website, html_url, counts and ecosyste.ms record times; any other kind (natural
                           persons) keeps login + kind only; string values that look like e-mail addresses are blanked.
                           Consolidated table data/processed/github_owners.parquet (dedupe by owner_key =
                           sha256(lower(login))[:16]; login / uuid / website / display_name stored for organisations
                           only). It is the resume cache: a login in it is not requested again (unless refresh).
                           Failed lookups are not stored, so a rerun retries them.
  find_org(entities)       staged search identical for every entity, same tiers as scripts/sources/github.py
                           (tier 1 stem + name slug, tier 2 common affixes, tier 3 rest of
                           scripts.lib.domains.login_candidates). Per tier: owner_activity -> keep org_like ->
                           lookup_owners -> accept iff kind == 'organization' and login_rule(login, website_url,
                           entity_domain, display_name, extra_names=names). Stop at the first tier with >= 1 accepted;
                           tie-break earliest first_event_ts, then login. An entity with an unanswered lookup (after a
                           retry pass) or a lookup skipped by --max-requests in a tier is HELD (absent from the output,
                           note in the search log) and never descends to a lower tier; rerunning find resumes it.
                           -> data/processed/entity_github_org.parquet + entity_github_org_search.parquet
  sample_pool(years)       outcome-blind pool channel (a); never reads labels. Owners whose FIRST non-bot event falls in
                           year Y, deterministic hash sample cityHash64(lower(owner)) % 10000 < m_Y. m_Y is the
                           smallest m whose buckets hold >= target org_like owners (pilot counts per year x bucket,
                           buckets < pilot_m); inclusion_prob = m_Y / 10000 exactly. The org_like owners are looked up;
                           in_pool = kind == 'organization' and website_domain not None and login_rule(login,
                           website_url, website_domain, display_name). Only years whose lookups all completed are
                           written. -> data/processed/github_org_sample.parquet + github_org_sample_years.csv
  calibrate(hours)         reproduces the org_like calibration above (raw hourly files are deleted after counting;
                           only manifests stay, as in gharchive fidelity).

Point in time: first_event_ts is GH Archive created_at (public time). ecosyste.ms kind / website / name are CURRENT
values observed at fetched_at (as GitHub websiteUrl was in the API route). ecosyste.ms created_at is the time
ecosyste.ms created its record, NOT the GitHub creation date, and is never used as a date.
Limitations: GH Archive starts 2011-02-12, so first_event_year 2011 is left-censored (owners active before). Renamed
owners keep old events under the old login. Owners unknown to ecosyste.ms (404) cannot be confirmed and are dropped.

CLI
    .venv/bin/python -m scripts.sources.github_owners sample [--years 2014 2018] [--per-year N] [--max-requests N]
    .venv/bin/python -m scripts.sources.github_owners find [--input data/processed/org_search_input.csv]
                                                          [--max-requests N] [--dry-run [--org-like-rate R]]
    .venv/bin/python -m scripts.sources.github_owners activity LOGIN [LOGIN ...]
    .venv/bin/python -m scripts.sources.github_owners lookup LOGIN [LOGIN ...]
    .venv/bin/python -m scripts.sources.github_owners calibrate [--hours 2016-05-11-15 ...] [--per-class N]
  find input: CSV with entity_id[,names] ('|'-separated names or a JSON list; UTF-8 with or without BOM).
  --max-requests caps ecosyste.ms requests of the run; everything fetched is persisted, so rerunning continues.
  Exit codes: 0 ok, 2 bad input or an existing output with other columns (scripts/sources/github.py writes the same
  three paths; checked before any request), 3 a source is unavailable (rerun later; caches make reruns cheap).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from scripts.lib.config import PROCESSED, RAW, cfg, get_logger
from scripts.lib.domains import company_domain, login_rule
from scripts.lib.http import Client
from scripts.lib.meta import write_meta
from scripts.sources import gharchive as gha
from scripts.sources.github import _prep_entities, tier_candidates

log = get_logger("github_owners")
SCRIPT = "scripts/sources/github_owners.py"

# ---------------------------------------------------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------------------------------------------------
ECO_HOST = "repos.ecosyste.ms"
OWNER_URL = "https://repos.ecosyste.ms/api/v1/hosts/GitHub/owners/{login}"
ECO_LICENCE = "CC BY-SA 4.0 (ecosyste.ms open data; attribution: ecosyste.ms)"
ECO_RAW = RAW / "ecosystems"
OWNERS_PATH = PROCESSED / "github_owners.parquet"
ENTITY_ORG_PATH = PROCESSED / "entity_github_org.parquet"
SEARCH_LOG_PATH = PROCESSED / "entity_github_org_search.parquet"
SAMPLE_PATH = PROCESSED / "github_org_sample.parquet"
SAMPLE_YEARS_PATH = PROCESSED / "github_org_sample_years.csv"
CALIB_PATH = PROCESSED / "github_owner_kind_calibration.csv"
SEARCH_INPUT_PATH = PROCESSED / "org_search_input.csv"

ORGISH_ACTOR_TYPES = ("ForkEvent", "PublicEvent", "MemberEvent", "TeamAddEvent", "Event", "SponsorshipEvent")
PERSONAL_RATIO = 0.01
HASH_MOD = 10_000
PILOT_M = 400                 # pilot buckets (4% of owners); early years need m ~ 200 for 300 org_like owners
ACT_CHUNK = 10_000            # logins per owner_activity query (one full scan ~10-15 s, almost independent of size)
MAX_LIST_BYTES = 200_000      # IN-list bytes per query (ClickHouse max_query_size 256 KiB)
CH_SECONDS_PER_QUERY = 15.0   # measured 7-19 s per uncached full scan; used for ETAs only
FLUSH_EVERY = 100             # persist ecosyste.ms lookups every N requests (resume after interruption)
MAX_FAILED_STREAK = 20        # consecutive failed ecosyste.ms requests before the run stops
GHARCHIVE_START_YEAR = 2011   # first GH Archive event 2011-02-12 -> first_event_year 2011 is left-censored
DEFAULT_CALIB_HOURS = ["2016-05-11-15", "2019-03-13-15", "2021-05-12-15", "2023-10-11-15"]

MATCH_METHOD = "website_equals_domain+login_rule+org_kind (ecosyste.ms)"
MATCH_CONFIDENCE = "high"
ACT_RAW_COLUMNS = ["login", "n_events", "first_event_ts", "last_event_ts", "n_actor_events", "n_personal_actor_events"]
ACTIVITY_COLUMNS = ["login", "exists_as_owner", "first_event_ts", "last_event_ts", "n_events", "is_actor",
                    "n_actor_events", "n_personal_actor_events", "org_like"]
PILOT_COLUMNS = ["first_year", "hash_bucket", "n_owners", "n_never_actor", "n_org_like"]
SAMPLE_RAW_COLUMNS = ["login", "hash_bucket", "first_year", "first_event_ts", "last_event_ts", "n_events",
                      "n_actor_events", "n_personal_actor_events"]
OWNER_COLUMNS = ["owner_key", "login", "exists", "kind", "uuid", "website_url", "website_domain", "display_name",
                 "fetched_at", "note"]
LOOKUP_COLUMNS = ["query_login"] + OWNER_COLUMNS + ["lookup_error"]
ENTITY_ORG_COLUMNS = ["entity_id", "org_login", "org_uuid", "org_first_event_ts", "tier", "match_method",
                      "match_confidence", "n_accepted_in_tier"]
SEARCH_LOG_COLUMNS = ["entity_id", "entity_domain", "names", "n_cand_tier1", "n_cand_tier2", "n_cand_tier3",
                      "n_exists_searched", "n_org_like_searched", "tiers_searched", "resolved", "n_lookup_errors",
                      "note"]
SAMPLE_COLUMNS = ["login", "owner_key", "uuid", "first_event_ts", "first_event_year", "last_event_ts", "n_events",
                  "hash_bucket", "hash_m", "inclusion_prob", "kind", "website_url", "website_domain", "display_name",
                  "in_pool", "fetched_at"]
YEAR_COLUMNS = ["year", "target", "pilot_m", "hash_m", "inclusion_prob", "target_reached", "left_censored",
                "n_owners_sampled", "n_never_actor_sampled", "n_org_like_pilot", "n_org_like_listed", "n_looked_up",
                "n_pending", "n_not_found", "n_login_mismatch", "n_kind_organization", "n_kind_user", "n_kind_other",
                "n_org_with_website_domain", "n_in_pool", "org_like_share", "kind_org_share", "website_share",
                "in_pool_share", "in_pool_share_of_orgs", "complete"]

# ecosyste.ms fields written to raw. Everything else (email, twitter, location, company, icon_url, funding_links,
# metadata, description, ...) is dropped before anything is written.
ORG_RAW_FIELDS = ("login", "kind", "uuid", "name", "website", "html_url", "repositories_count", "total_stars",
                  "followers", "created_at", "updated_at", "last_synced_at")
OTHER_RAW_FIELDS = ("login", "kind")
_EMAIL_RE = re.compile(r"[^@\s/]+@[^@\s/]+\.[a-z]{2,}", re.I)


class OwnersError(RuntimeError):
    pass


class EcosystemsUnavailable(OwnersError):
    """ecosyste.ms kept failing; everything answered so far is persisted, rerun later."""


class OutputConflict(OwnersError):
    """An existing output file has other columns (scripts/sources/github.py writes the same paths)."""


def _check_output(path: Path | None, columns: list[str]):
    """Fail fast, BEFORE any request, when an existing output at `path` has other columns than this module writes
    (scripts/sources/github.py uses the same three paths with different columns). Never overwritten silently."""
    if path is None or not Path(path).exists():
        return
    if str(path).endswith(".csv"):
        cols = list(pd.read_csv(path, nrows=0).columns)
    else:
        import pyarrow.parquet as pq
        cols = list(pq.read_schema(path).names)
        cols = [c for c in cols if c != "__index_level_0__"]
    if cols != list(columns):
        raise OutputConflict(f"{path} has columns {cols} (written by another collector, e.g. scripts/sources/github.py?)"
                             f"; expected {list(columns)}. Move it away or pass another --out.")


@dataclass
class RunStats:
    eco_requests: int = 0
    eco_seconds: float = 0.0
    eco_reused: int = 0
    eco_errors: int = 0
    eco_not_requested: int = 0
    started: float = field(default_factory=time.monotonic)

    def as_dict(self) -> dict:
        return {"eco_requests": self.eco_requests, "eco_reused": self.eco_reused, "eco_errors": self.eco_errors,
                "eco_not_requested": self.eco_not_requested, "eco_seconds": round(self.eco_seconds, 1),
                "eco_lookups_per_sec": round(self.eco_requests / self.eco_seconds, 3) if self.eco_seconds else None,
                "wall_seconds": round(time.monotonic() - self.started, 1)}


STATS = RunStats()
_FAILED_STREAK = [0]


@dataclass
class Budget:
    """Cap on ecosyste.ms requests for one run (None = unlimited)."""
    max_requests: int | None = None
    used: int = 0

    @property
    def remaining(self) -> int | None:
        return None if self.max_requests is None else max(0, self.max_requests - self.used)

    def take(self) -> bool:
        if self.max_requests is not None and self.used >= self.max_requests:
            return False
        self.used += 1
        return True


def owner_key(login: str) -> str:
    """sha256 of the lower-cased login, first 16 hex chars (same as gharchive.actor_hash)."""
    return gha.actor_hash(login)


def eco_interval() -> float:
    iv = cfg()["http"]["min_interval_s"]
    return float(iv.get(ECO_HOST, iv["default"]))


# ---------------------------------------------------------------------------------------------------------------------
# ClickHouse SQL builders (pure; tested offline)
# ---------------------------------------------------------------------------------------------------------------------
_FMT = "%Y-%m-%d %H:%i:%S"


def _scan(where: str, regex: str | None = None) -> str:
    """ONE scan of github_events: every event yields (owner, role) and (actor, 2).
    role 1 = owner of a non-bot event, 3 = owner of a bot event (ignored), 2 = actor (any actor, bots included)."""
    rx = gha.sql_str(regex or gha.bots_regex())
    return (f"SELECT created_at, event_type,\n"
            f"         arrayJoin(if(match(lower(actor_login), {rx}),\n"
            f"                      [({gha.OWNER_SQL}, 3), (lower(actor_login), 2)],\n"
            f"                      [({gha.OWNER_SQL}, 1), (lower(actor_login), 2)])) AS p\n"
            f"  FROM github_events\n"
            f"  WHERE {where}")


def _agg(with_year: bool = False) -> str:
    orgish = ", ".join(gha.sql_str(t) for t in ORGISH_ACTOR_TYPES)
    year = "  toYear(minIf(created_at, tupleElement(p, 2) = 1), 'UTC') AS first_year,\n" if with_year else ""
    return (f"countIf(tupleElement(p, 2) = 1) AS n_events,\n{year}"
            f"  if(n_events > 0, formatDateTime(minIf(created_at, tupleElement(p, 2) = 1), '{_FMT}', 'UTC'), '')"
            f" AS first_event_ts,\n"
            f"  if(n_events > 0, formatDateTime(maxIf(created_at, tupleElement(p, 2) = 1), '{_FMT}', 'UTC'), '')"
            f" AS last_event_ts,\n"
            f"  countIf(tupleElement(p, 2) = 2) AS n_actor_events,\n"
            f"  countIf(tupleElement(p, 2) = 2 AND event_type NOT IN ({orgish})) AS n_personal_actor_events")


def _org_like_sql() -> str:
    return f"n_personal_actor_events <= {PERSONAL_RATIO!r} * n_events"


def _check_sql(sql: str) -> str:
    if len(sql.encode()) > gha.MAX_SQL_BYTES:
        raise ValueError(f"SQL too large ({len(sql.encode())} bytes); use a smaller chunk")
    return sql


def sql_owner_activity(logins: list[str], regex: str | None = None, max_rows: int = gha.MAX_ROWS) -> str:
    """Owner + actor statistics for the given logins. Logins must be [a-z0-9-]{1,39} (else ValueError)."""
    lst = gha.sql_in_list(logins)[1:-1]
    cands = "(SELECT login FROM cands)"
    return _check_sql(f"""-- owner_activity
WITH cands AS (SELECT arrayJoin([{lst}]) AS login)
SELECT tupleElement(p, 1) AS login,
  {_agg()}
FROM ({_scan(f"{gha.OWNER_SQL} IN {cands} OR lower(actor_login) IN {cands}", regex)})
WHERE tupleElement(p, 1) IN {cands}
GROUP BY login
ORDER BY login
LIMIT {max_rows + 1}
FORMAT TSVWithNames""")


def _hash(expr: str) -> str:
    return f"cityHash64({expr}) % {HASH_MOD}"


def _bucket_sub(m: int, regex: str | None = None) -> str:
    """Per-owner statistics for every owner (non-bot events > 0) whose hash bucket is < m."""
    if not isinstance(m, (int, np.integer)) or not 1 <= int(m) <= HASH_MOD:
        raise ValueError(f"bucket bound must be an int in [1, {HASH_MOD}]: {m!r}")
    m = int(m)
    where = f"{_hash(gha.OWNER_SQL)} < {m} OR {_hash('lower(actor_login)')} < {m}"
    return f"""SELECT tupleElement(p, 1) AS login, {_hash('login')} AS hash_bucket,
    {_agg(with_year=True)}
  FROM ({_scan(where, regex)})
  WHERE tupleElement(p, 1) != '' AND {_hash('tupleElement(p, 1)')} < {m}
  GROUP BY login
  HAVING n_events > 0"""


def sql_owner_pilot(pilot_m: int = PILOT_M, regex: str | None = None) -> str:
    """Counts per (first_year, hash_bucket) for buckets < pilot_m: owners, never-actor owners, org_like owners."""
    return _check_sql(f"""-- owner_pilot
SELECT first_year, hash_bucket, count() AS n_owners, countIf(n_actor_events = 0) AS n_never_actor,
  countIf({_org_like_sql()}) AS n_org_like
FROM ({_bucket_sub(pilot_m, regex)})
GROUP BY first_year, hash_bucket
ORDER BY first_year, hash_bucket
FORMAT TSVWithNames""")


def sql_owner_sample(m_by_year: dict[int, int], regex: str | None = None, max_rows: int = gha.MAX_ROWS) -> str:
    """org_like owners with first_year == Y and hash_bucket < m_Y, for every Y in m_by_year."""
    if not m_by_year:
        raise ValueError("no years")
    conds = []
    for y, m in sorted(m_by_year.items()):
        if int(y) != y or not 1 <= int(m) <= HASH_MOD:
            raise ValueError(f"bad year/m {y!r}/{m!r}")
        conds.append(f"(first_year = {int(y)} AND hash_bucket < {int(m)})")
    return _check_sql(f"""-- owner_sample
SELECT login, hash_bucket, first_year, first_event_ts, last_event_ts, n_events, n_actor_events, n_personal_actor_events
FROM ({_bucket_sub(max(int(m) for m in m_by_year.values()), regex)})
WHERE {_org_like_sql()} AND ({' OR '.join(conds)})
ORDER BY first_year, hash_bucket, login
LIMIT {max_rows + 1}
FORMAT TSVWithNames""")


def byte_chunks(items: list[str], max_items: int = ACT_CHUNK, max_bytes: int = MAX_LIST_BYTES) -> list[list[str]]:
    """Consecutive chunks with <= max_items logins and <= max_bytes of quoted IN-list text."""
    if max_items < 1 or max_bytes < 1:
        raise ValueError("chunk limits must be >= 1")
    out, cur, size = [], [], 0
    for x in items:
        b = len(x.encode()) + 4                      # quotes + ", "
        if cur and (len(cur) >= max_items or size + b > max_bytes):
            out.append(cur)
            cur, size = [], 0
        cur.append(x)
        size += b
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# typed frames
# ---------------------------------------------------------------------------------------------------------------------
def _ts_or_nat(s: pd.Series, name: str) -> pd.Series:
    s = s.astype(object).where(s.astype(str) != "", None)
    out = pd.to_datetime(s, format="%Y-%m-%d %H:%M:%S", errors="coerce", utc=True)
    if (out.isna() & s.notna()).any():
        raise OwnersError(f"{name}: unparseable timestamp(s)")
    return out


def is_org_like(n_events, n_personal) -> np.ndarray:
    n = np.asarray(n_events, dtype="int64")
    p = np.asarray(n_personal, dtype="int64")
    return (n > 0) & (p <= PERSONAL_RATIO * n)


def frame_activity(raw: pd.DataFrame, logins: Iterable[str] | None = None) -> pd.DataFrame:
    """Typed activity frame; with `logins`, one row per login (absent logins: no events, not an actor)."""
    if len(raw):
        df = pd.DataFrame({"login": raw["login"].astype(str).str.lower(),
                           "n_events": gha._to_int(raw["n_events"], "n_events"),
                           "first_event_ts": _ts_or_nat(raw["first_event_ts"], "first_event_ts"),
                           "last_event_ts": _ts_or_nat(raw["last_event_ts"], "last_event_ts"),
                           "n_actor_events": gha._to_int(raw["n_actor_events"], "n_actor_events"),
                           "n_personal_actor_events": gha._to_int(raw["n_personal_actor_events"],
                                                                  "n_personal_actor_events")})
        if df["login"].duplicated().any():
            raise OwnersError("duplicate login rows in owner_activity result")
        if ((df["n_events"] > 0) != df["first_event_ts"].notna()).any():
            raise OwnersError("first_event_ts must be set exactly when n_events > 0")
        if (df["n_personal_actor_events"] > df["n_actor_events"]).any():
            raise OwnersError("personal actor events exceed actor events")
    else:
        df = pd.DataFrame({"login": pd.Series(dtype=object), "n_events": pd.Series(dtype="int64"),
                           "first_event_ts": pd.Series(dtype="datetime64[ns, UTC]"),
                           "last_event_ts": pd.Series(dtype="datetime64[ns, UTC]"),
                           "n_actor_events": pd.Series(dtype="int64"),
                           "n_personal_actor_events": pd.Series(dtype="int64")})
    if logins is not None:
        want = sorted(set(logins))
        df = df.set_index("login").reindex(want)
        df.index.name = "login"
        df = df.reset_index()
        for c in ("n_events", "n_actor_events", "n_personal_actor_events"):
            df[c] = df[c].fillna(0).astype("int64")
    df["login"] = df["login"].astype(object)
    df["exists_as_owner"] = df["n_events"] > 0
    df["is_actor"] = df["n_actor_events"] > 0
    df["org_like"] = is_org_like(df["n_events"], df["n_personal_actor_events"])
    for c in ("first_event_ts", "last_event_ts"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df[ACTIVITY_COLUMNS].sort_values("login").reset_index(drop=True)


def frame_pilot(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({c: gha._to_int(raw[c], c) for c in PILOT_COLUMNS}) if len(raw) else \
        pd.DataFrame({c: pd.Series(dtype="int64") for c in PILOT_COLUMNS})
    if len(df) and (df["hash_bucket"].lt(0) | df["hash_bucket"].ge(HASH_MOD)).any():
        raise OwnersError("hash_bucket out of range")
    return df.sort_values(["first_year", "hash_bucket"]).reset_index(drop=True)


def frame_sample_list(raw: pd.DataFrame) -> pd.DataFrame:
    if not len(raw):
        return pd.DataFrame({c: pd.Series(dtype=object) for c in SAMPLE_RAW_COLUMNS})
    df = pd.DataFrame({"login": raw["login"].astype(str).str.lower().astype(object),
                       "hash_bucket": gha._to_int(raw["hash_bucket"], "hash_bucket"),
                       "first_year": gha._to_int(raw["first_year"], "first_year"),
                       "first_event_ts": _ts_or_nat(raw["first_event_ts"], "first_event_ts"),
                       "last_event_ts": _ts_or_nat(raw["last_event_ts"], "last_event_ts")})
    for c in ("n_events", "n_actor_events", "n_personal_actor_events"):
        df[c] = gha._to_int(raw[c], c)
    if df["first_event_ts"].isna().any() or (df["first_event_ts"].dt.year != df["first_year"]).any():
        raise OwnersError("first_event_ts missing or inconsistent with first_year")
    if not is_org_like(df["n_events"], df["n_personal_actor_events"]).all():
        raise OwnersError("sample listing contains owners that are not org_like")
    if df["login"].duplicated().any():
        raise OwnersError("duplicate owners in sample listing")
    bad = ~df["login"].map(lambda s: bool(gha.LOGIN_RE.match(s)))
    if bad.any():
        # lookup_owners would drop them, leaving the year 'pending' forever; fail loudly instead (0 of 3.5M owners in
        # buckets < 400, 2011-2026, were invalid when checked on 2026-11-20)
        raise OwnersError(f"sample listing contains {int(bad.sum())} owner(s) that are not [a-z0-9-]{{1,39}}")
    return df.sort_values(["first_year", "hash_bucket", "login"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------------------
# GH Archive owner activity
# ---------------------------------------------------------------------------------------------------------------------
def _ch(ch: gha.ClickHouse | None) -> gha.ClickHouse:
    return ch if ch is not None else gha.backend()


def owner_activity(logins: Iterable, ch: gha.ClickHouse | None = None, chunk_size: int = ACT_CHUNK,
                   max_bytes: int = MAX_LIST_BYTES) -> pd.DataFrame:
    """One row per valid (lower-cased, [a-z0-9-]{1,39}) login; invalid values are dropped with a warning."""
    ok, _ = gha.normalize_logins(logins, "owner")
    if not ok:
        return frame_activity(pd.DataFrame(columns=ACT_RAW_COLUMNS), [])
    ch = _ch(ch)
    chunks = byte_chunks(ok, chunk_size, max_bytes)
    log.info("owner_activity: %d logins in %d ClickHouse quer%s (~%.0f s if uncached)", len(ok), len(chunks),
             "y" if len(chunks) == 1 else "ies", len(chunks) * CH_SECONDS_PER_QUERY)
    frames = [ch.run_chunked("owner_activity", lambda c: sql_owner_activity(c), chunk, ACT_RAW_COLUMNS,
                             chunk_size=len(chunk)) for chunk in chunks]
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=ACT_RAW_COLUMNS)
    return frame_activity(raw, ok)


# ---------------------------------------------------------------------------------------------------------------------
# ecosyste.ms owner lookup
# ---------------------------------------------------------------------------------------------------------------------
def _json_or_none(text: str):
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _clean_str(v) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not v or _EMAIL_RE.search(v):
        return None
    return v


def scrub_owner(payload) -> dict | None:
    """Whitelisted fields only; non-organisations keep login + kind; e-mail-like strings are blanked."""
    if not isinstance(payload, dict):
        return None
    if "login" not in payload:                              # 404 body {"error": "not found"}
        return {k: payload[k] for k in ("error",) if isinstance(payload.get(k), str)}
    keep = ORG_RAW_FIELDS if payload.get("kind") == "organization" else OTHER_RAW_FIELDS
    out = {}
    for k in keep:
        if k in payload:
            v = payload[k]
            if isinstance(v, str) and _EMAIL_RE.search(v):
                v = None
            out[k] = v
    return out


def _uuid(v) -> int | None:
    s = str(v).strip() if v is not None else ""
    return int(s) if s.isdigit() else None


def parse_owner(query_login: str, status: int, text: str, fetched_at) -> tuple[dict, dict | None]:
    """-> (lookup row, scrubbed payload for the raw log)."""
    row = {"query_login": query_login, "owner_key": owner_key(query_login), "login": None, "exists": None,
           "kind": None, "uuid": None, "website_url": None, "website_domain": None, "display_name": None,
           "fetched_at": fetched_at, "note": None, "lookup_error": None}
    payload = _json_or_none(text)
    if status == 404:
        row["exists"] = False
        return row, scrub_owner(payload)
    if status != 200:
        row["lookup_error"] = f"HTTP_{status}"
        return row, None
    if not isinstance(payload, dict) or not isinstance(payload.get("login"), str) or not payload.get("kind"):
        row["lookup_error"] = "BAD_PAYLOAD"
        return row, None
    kind = str(payload["kind"])
    row.update(exists=True, kind=kind)
    if payload["login"].strip().lower() != query_login:
        row["note"] = "login_mismatch"                      # another account answered (rename?): never accepted
        return row, scrub_owner(payload)
    if kind == "organization":
        url = _clean_str(payload.get("website"))
        login, name = payload["login"].strip(), _clean_str(payload.get("name"))
        # company_domain with the owner's names, as in scripts/sources/github.py and login_rule: a platform domain is
        # kept only when the owner IS that platform (org 'gitlab' + gitlab.com); hosted pages stay excluded.
        row.update(login=login, uuid=_uuid(payload.get("uuid")), website_url=url,
                   website_domain=company_domain(url, tuple(x for x in (login, name) if x)), display_name=name)
    return row, scrub_owner(payload)


def owner_frame(rows, with_query: bool = True) -> pd.DataFrame:
    cols = LOOKUP_COLUMNS if with_query else OWNER_COLUMNS
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame.from_records(list(rows), columns=cols)
    for c in cols:
        if c not in df:
            df[c] = None
    df = df[cols].copy()
    df["exists"] = df["exists"].astype("boolean")
    df["uuid"] = pd.to_numeric(df["uuid"], errors="coerce").astype("Int64")
    df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True, format="ISO8601")
    for c in cols:
        if c not in ("exists", "uuid", "fetched_at"):
            df[c] = df[c].astype(object).where(df[c].notna(), None)
    return df.reset_index(drop=True)


def load_owners(path: Path | None = OWNERS_PATH) -> pd.DataFrame:
    if path is None or not Path(path).exists():
        return owner_frame([], with_query=False)
    return owner_frame(pd.read_parquet(path), with_query=False)


def _merge_owners(new: pd.DataFrame, path: Path):
    new = new[new["lookup_error"].isna()] if "lookup_error" in new else new
    if not len(new):
        return
    new = owner_frame(new, with_query=False)
    old = load_owners(path)
    df = pd.concat([old, new], ignore_index=True) if len(old) else new
    df = owner_frame(df.drop_duplicates("owner_key", keep="last"), with_query=False)
    df = df.sort_values("owner_key").reset_index(drop=True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    kinds = df["kind"].fillna("(not found)").value_counts().sort_index().to_dict()
    write_meta(path, script=SCRIPT, rows=len(df), sources=[OWNER_URL.format(login="<login>")],
               params={"kinds": kinds, "with_website_domain": int(df["website_domain"].notna().sum()),
                       "licence": ECO_LICENCE},
               notes="Consolidated ecosyste.ms owner lookups (dedupe by owner_key = sha256(lower(login))[:16], newest "
                     "wins). login / uuid / website / display_name only for kind == 'organization'; natural persons "
                     "and unknown owners (exists = False, HTTP 404) keep owner_key + kind only. kind / website / name "
                     "are current values observed at fetched_at. Raw responses: data/raw/ecosystems/owners_*.jsonl.gz. "
                     "Licence: " + ECO_LICENCE)


class RawJsonl:
    """data/raw/ecosystems/owners_<sha8>.jsonl.gz (sha8 of the requested logins); gzip mtime 0; .meta.json on close.

    ecosyste.ms responses are not HTTP-cached, so a second request of the same login list (refresh, or a rerun after a
    failed run) yields NEW responses: they go to owners_<sha8>_r<k>.jsonl.gz and earlier raw files are never
    overwritten (they remain the provenance of rows kept in github_owners.parquet)."""

    def __init__(self, directory: Path, logins: list[str], params: dict | None = None):
        self.dir = Path(directory)
        sha8 = hashlib.sha256("\n".join(logins).encode()).hexdigest()[:8]
        self.path = self.dir / f"owners_{sha8}.jsonl.gz"
        k = 2
        while self.path.exists() or Path(str(self.path) + ".part").exists():   # .part = left by a hard crash
            self.path = self.dir / f"owners_{sha8}_r{k}.jsonl.gz"
            k += 1
        self.tmp = Path(str(self.path) + ".part")
        self.params = params or {}
        self.lines = 0
        self._fh = self._gz = None

    def write(self, rec: dict):
        if self._gz is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.tmp, "wb")
            self._gz = gzip.GzipFile(filename="", mode="wb", fileobj=self._fh, mtime=0)
        self._gz.write((json.dumps(rec, sort_keys=True, default=str) + "\n").encode())
        self.lines += 1

    def close(self) -> Path | None:
        if self._gz is None:
            return None
        self._gz.close()
        self._fh.close()
        self._gz = self._fh = None
        os.replace(self.tmp, self.path)
        write_meta(self.path, script=SCRIPT, rows=self.lines, sources=[OWNER_URL.format(login="<login>")],
                   params={**self.params, "licence": ECO_LICENCE, "kept_fields_organization": list(ORG_RAW_FIELDS),
                           "kept_fields_other_kinds": list(OTHER_RAW_FIELDS)},
                   notes="ecosyste.ms owner responses, one line per HTTP request (incl. retries). Personal and "
                         "contact fields (email, twitter, location, company, ...) were dropped before writing; "
                         "e-mail-like strings blanked. created_at / updated_at are ecosyste.ms record times, NOT "
                         "GitHub dates. Licence: " + ECO_LICENCE)
        return self.path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _fetch_owner(client, login: str, raw: RawJsonl, attempt: int) -> dict:
    url = OWNER_URL.format(login=gha.validate_login(login))
    t0 = time.monotonic()
    r = client.get(url, use_cache=False)              # full responses carry personal fields: never cached on disk
    STATS.eco_seconds += time.monotonic() - t0
    STATS.eco_requests += 1
    row, scrubbed = parse_owner(login, r.status, r.text, r.fetched_at)
    raw.write({"query_login": login, "url": url, "status": r.status, "fetched_at": r.fetched_at,
               "attempt": attempt, "lookup_error": row["lookup_error"], "response": scrubbed})
    if row["lookup_error"]:
        STATS.eco_errors += 1
        _FAILED_STREAK[0] += 1
        log.warning("ecosyste.ms lookup failed for %s: %s", login, row["lookup_error"])
        if _FAILED_STREAK[0] >= MAX_FAILED_STREAK:
            _FAILED_STREAK[0] = 0
            raise EcosystemsUnavailable(f"ecosyste.ms: {MAX_FAILED_STREAK} consecutive failed requests "
                                        f"(last {row['lookup_error']}); rerun later")
    else:
        _FAILED_STREAK[0] = 0
    return row


def lookup_owners(logins: Iterable, client=None, owners_path: Path | None = OWNERS_PATH, raw_dir: Path = ECO_RAW,
                  refresh: bool = False, persist: bool = True, budget: Budget | None = None,
                  flush_every: int = FLUSH_EVERY, retry_rounds: int = 1) -> pd.DataFrame:
    """ecosyste.ms owner metadata; one row per distinct valid login (sorted). Columns LOOKUP_COLUMNS.

    lookup_error: HTTP_<status> / BAD_PAYLOAD (transient, retried once more at the end of the run, never persisted)
    or NOT_REQUESTED (skipped by the request budget). exists False = 404 (unknown to ecosyste.ms).
    """
    wanted, _ = gha.normalize_logins(logins, "owner")
    budget = budget or Budget()
    known = load_owners(owners_path) if (owners_path is not None and not refresh) else owner_frame([], False)
    known_by_key = {r["owner_key"]: r for r in known.to_dict("records")}
    todo = [l for l in wanted if owner_key(l) not in known_by_key]
    STATS.eco_reused += len(wanted) - len(todo)
    rem = budget.remaining
    allowed = todo if rem is None else todo[:rem]
    rows: dict[str, dict] = {}
    for l in todo[len(allowed):]:
        rows[l] = {**parse_owner(l, -1, "", None)[0], "lookup_error": "NOT_REQUESTED"}
    if allowed:
        client = client or Client()
        print(f"ecosyste.ms: {len(allowed)} lookups ({len(wanted) - len(todo)} reused, "
              f"{len(todo) - len(allowed)} over the request cap); ETA ~{len(allowed) * eco_interval() / 60:.1f} min")
        done: list[dict] = []
        try:
            with RawJsonl(raw_dir, allowed, params={"n_logins": len(allowed)}) as raw:
                for i, l in enumerate(allowed, 1):
                    budget.take()
                    rows[l] = _fetch_owner(client, l, raw, attempt=0)
                    done.append(rows[l])
                    if persist and owners_path is not None and i % flush_every == 0:
                        _merge_owners(owner_frame(done), Path(owners_path))
                        done = []
                    if i % 250 == 0:
                        log.info("ecosyste.ms: %d / %d", i, len(allowed))
                for attempt in range(1, retry_rounds + 1):
                    failed = [l for l in allowed if rows[l]["lookup_error"] not in (None, "NOT_REQUESTED")]
                    for l in failed:
                        if not budget.take():
                            break
                        log.info("ecosyste.ms retry %d for %s", attempt, l)
                        rows[l] = _fetch_owner(client, l, raw, attempt=attempt)
                        done.append(rows[l])
        finally:
            if persist and owners_path is not None and done:
                _merge_owners(owner_frame(done), Path(owners_path))
    STATS.eco_not_requested += sum(1 for r in rows.values() if r["lookup_error"] == "NOT_REQUESTED")
    out = []
    for l in wanted:
        if l in rows:
            out.append(rows[l])
        else:
            out.append({**known_by_key[owner_key(l)], "query_login": l, "lookup_error": None})
    return owner_frame(out).sort_values("query_login").reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------------------
# staged entity -> org search
# ---------------------------------------------------------------------------------------------------------------------
def _true(x) -> bool:
    """True only for a real True value (bool / numpy bool / pandas boolean); None / NA / False -> False."""
    return x is not None and x is not pd.NA and not (isinstance(x, float) and math.isnan(x)) and bool(x)


def _first_key(ts) -> tuple:
    return (1, 0) if ts is None or pd.isna(ts) else (0, pd.Timestamp(ts).value)


def accept(owner: dict, entity_domain: str, names: tuple = ()) -> bool:
    """Acceptance rule: ecosyste.ms kind == 'organization' and scripts.lib.domains.login_rule."""
    if owner is None or owner.get("lookup_error") or owner.get("note"):
        return False
    if not _true(owner.get("exists")) or owner.get("kind") != "organization" or not owner.get("login"):
        return False
    return bool(login_rule(owner["login"], owner.get("website_url"), entity_domain, owner.get("display_name"),
                           extra_names=names))


def plan_find(entities, chunk_size: int = ACT_CHUNK) -> dict:
    """Candidate counts per tier without any request (tiers 2/3: upper bound = nothing resolved earlier)."""
    ents = _prep_entities(entities)
    per_tier = {1: set(), 2: set(), 3: set()}
    n_company = 0
    pairs = {1: 0, 2: 0, 3: 0}
    for eid, names in ents:
        dom = company_domain(eid, names)
        if dom is None:
            continue
        n_company += 1
        tiers = tier_candidates(dom, names)
        for t in (1, 2, 3):
            per_tier[t].update(tiers[t])
            pairs[t] += len(tiers[t])
    out = {"entities": len(ents), "company_domains": n_company}
    for t in (1, 2, 3):
        ls = sorted(per_tier[t])
        out[f"tier{t}_entity_candidate_pairs"] = pairs[t]
        out[f"tier{t}_distinct_logins"] = len(ls)
        out[f"tier{t}_clickhouse_queries"] = len(byte_chunks(ls, chunk_size)) if ls else 0
    return out


def find_org(entities, ch: gha.ClickHouse | None = None, client=None,
             activity: Callable[[list[str]], pd.DataFrame] | None = None,
             lookup: Callable[[list[str]], pd.DataFrame] | None = None,
             out_path: Path | None = ENTITY_ORG_PATH, search_log_path: Path | None = SEARCH_LOG_PATH,
             persist: bool = True, budget: Budget | None = None, **lookup_kw) -> pd.DataFrame:
    """Staged org search for entities (entity_id = registrable domain, optional names). Returns matched rows only."""
    ents = _prep_entities(entities)
    budget = budget or Budget()
    if persist:
        _check_output(out_path, ENTITY_ORG_COLUMNS)
        _check_output(search_log_path, SEARCH_LOG_COLUMNS)
    if activity is None:
        def activity(ls):  # noqa: E306
            return owner_activity(ls, ch=ch)
    if lookup is None:
        def lookup(ls):  # noqa: E306
            return lookup_owners(ls, client=client, budget=budget, **lookup_kw)
    info: dict[str, dict] = {}
    unresolved: dict[str, tuple[str, tuple, dict]] = {}
    for eid, names in ents:
        dom = company_domain(eid, names)        # same as scripts/sources/github.py (platform owners kept by name)
        rec = {"entity_id": eid, "entity_domain": dom, "names": "|".join(names), "n_cand_tier1": 0,
               "n_cand_tier2": 0, "n_cand_tier3": 0, "n_exists_searched": 0, "n_org_like_searched": 0,
               "tiers_searched": 0, "resolved": False, "n_lookup_errors": 0, "note": None}
        info[eid] = rec
        if dom is None:
            rec["note"] = "not_a_company_domain"
            continue
        if dom != eid:
            rec["note"] = "entity_id_normalised"
        tiers = tier_candidates(dom, names)
        for t in (1, 2, 3):
            rec[f"n_cand_tier{t}"] = len(tiers[t])
        unresolved[eid] = (dom, names, tiers)

    matches: list[dict] = []
    held: set[str] = set()
    for tier in (1, 2, 3):
        need = sorted({l for _, _, tiers in unresolved.values() for l in tiers[tier]})
        if not need:
            continue
        print(f"find tier {tier}: {len(unresolved)} unresolved entities, {len(need)} candidate logins "
              f"-> {len(byte_chunks(need))} ClickHouse quer(ies)")
        act = activity(need).set_index("login")
        org_like = sorted(act.index[act["org_like"]])
        looked = owner_frame(lookup(org_like)).set_index("query_login") if org_like else \
            owner_frame([]).set_index("query_login")
        for eid in sorted(unresolved):
            dom, names, tiers = unresolved[eid]
            rec = info[eid]
            rec["tiers_searched"] = tier
            accepted, n_err, capped = [], 0, False
            for l in tiers[tier]:
                if l not in act.index:
                    continue
                a = act.loc[l]
                rec["n_exists_searched"] += int(bool(a["exists_as_owner"]))
                if not bool(a["org_like"]):
                    continue
                rec["n_org_like_searched"] += 1
                o = looked.loc[l].to_dict() if l in looked.index else None
                if o is None or o["lookup_error"] is not None:
                    n_err += 1
                    capped = capped or (o is not None and o["lookup_error"] == "NOT_REQUESTED")
                    continue
                if accept(o, dom, names):
                    accepted.append({"login": o["login"], "uuid": o["uuid"], "first": a["first_event_ts"]})
            rec["n_lookup_errors"] += n_err
            if n_err:
                # An unanswered org_like candidate could be the (earliest) accepted org: emitting a match or descending
                # would make the result depend on transient failures. Hold; a rerun resumes from the caches.
                tag = f"request_cap_tier{tier}" if capped else f"lookup_error_tier{tier}"
                rec["note"] = ";".join(x for x in (rec["note"], tag) if x)
                held.add(eid)
                continue
            if accepted:
                best = min(accepted, key=lambda o: (_first_key(o["first"]), str(o["login"]).lower()))
                matches.append({"entity_id": eid, "org_login": best["login"], "org_uuid": best["uuid"],
                                "org_first_event_ts": best["first"], "tier": tier, "match_method": MATCH_METHOD,
                                "match_confidence": MATCH_CONFIDENCE, "n_accepted_in_tier": len(accepted)})
                rec["resolved"] = True
        unresolved = {e: v for e, v in unresolved.items() if not info[e]["resolved"] and e not in held}

    out = entity_frame(matches)
    search = search_frame(list(info.values()))
    log.info("find_org: %d entities, %d matched (%s), %d held", len(ents), len(out),
             out["tier"].value_counts().sort_index().to_dict() if len(out) else {}, len(held))
    if held:
        log.warning("find_org: %d entities held back (lookup error or request cap; see note in the search log); "
                    "rerun find for them", len(held))
    if persist:
        searched = set(info)
        if out_path is not None:
            _merge_write(out, Path(out_path), searched, ENTITY_ORG_COLUMNS, entity_frame,
                         notes="Entity -> GitHub org via staged search (tier 1 exact, 2 affixes, 3 rest): GH Archive "
                               "owner activity (org_like) -> ecosyste.ms kind == 'organization' -> login_rule. "
                               "Unresolved and held entities are absent. org_first_event_ts = first non-bot GH Archive "
                               "event on the org's repos (public time); the website used for matching is the current "
                               "ecosyste.ms value.")
        if search_log_path is not None:
            _merge_write(search, Path(search_log_path), searched, SEARCH_LOG_COLUMNS, search_frame,
                         notes="One row per entity searched by github_owners find (resolved or not). note "
                               "lookup_error_tier<N> / request_cap_tier<N> = held, rerun find.")
    return out


def entity_frame(rows) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame.from_records(rows, columns=ENTITY_ORG_COLUMNS)
    df = df[ENTITY_ORG_COLUMNS].copy()
    df["org_uuid"] = pd.to_numeric(df["org_uuid"], errors="coerce").astype("Int64")
    df["org_first_event_ts"] = pd.to_datetime(df["org_first_event_ts"], utc=True)
    df["tier"] = df["tier"].astype("int64")
    df["n_accepted_in_tier"] = df["n_accepted_in_tier"].astype("int64")
    for c in ("entity_id", "org_login", "match_method", "match_confidence"):     # never null
        df[c] = df[c].astype(str)
    return df.sort_values("entity_id").reset_index(drop=True)


def search_frame(rows) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame.from_records(rows, columns=SEARCH_LOG_COLUMNS)
    df = df[SEARCH_LOG_COLUMNS].copy()
    for c in ("n_cand_tier1", "n_cand_tier2", "n_cand_tier3", "n_exists_searched", "n_org_like_searched",
              "tiers_searched", "n_lookup_errors"):
        df[c] = df[c].astype("int64")
    df["resolved"] = df["resolved"].astype(bool)
    for c in ("entity_id", "entity_domain", "names", "note"):
        df[c] = df[c].astype(object).where(df[c].notna(), None)
    return df.sort_values("entity_id").reset_index(drop=True)


def _merge_write(new: pd.DataFrame, path: Path, replaced: set, columns: list, norm, notes: str):
    """Keep rows of other entities from earlier runs; rows of the entities searched now are replaced."""
    if path.exists():
        _check_output(path, columns)
        old = pd.read_parquet(path)
        old = old[~old["entity_id"].isin(replaced)]
        df = pd.concat([norm(old), new], ignore_index=True) if len(old) else new
    else:
        df = new
    df = norm(df)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    write_meta(path, script=SCRIPT, rows=len(df), sources=[cfg()["gharchive"]["clickhouse_url"],
                                                          OWNER_URL.format(login="<login>")],
               inputs=[OWNERS_PATH] if OWNERS_PATH.exists() else [],
               params={"entities_searched_this_run": len(replaced), "org_like_personal_ratio": PERSONAL_RATIO,
                       "orgish_actor_types": list(ORGISH_ACTOR_TYPES), "licence_ecosystems": ECO_LICENCE},
               notes=notes)


def read_search_input(path: Path) -> pd.DataFrame:
    """entity_id[,names] CSV; BOM-safe; names '|'-separated or a JSON list."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    if "entity_id" not in df.columns:
        raise OwnersError(f"{path}: needs an entity_id column (got {list(df.columns)})")
    if "names" not in df.columns:
        df["names"] = ""
    return df[["entity_id", "names"]]


# ---------------------------------------------------------------------------------------------------------------------
# outcome-blind pool sample (channel a)
# ---------------------------------------------------------------------------------------------------------------------
def choose_m(pilot_year: pd.DataFrame, target: int, pilot_m: int = PILOT_M) -> tuple[int, bool]:
    """Smallest m in [1, pilot_m] such that buckets [0, m) hold >= target org_like owners -> (m, reached)."""
    if target < 1:
        raise ValueError("target must be >= 1")
    counts = np.zeros(pilot_m, dtype="int64")
    for b, n in zip(pilot_year["hash_bucket"], pilot_year["n_org_like"]):
        if 0 <= int(b) < pilot_m:
            counts[int(b)] += int(n)
    cum = np.cumsum(counts)
    hit = np.nonzero(cum >= target)[0]
    if len(hit):
        return int(hit[0]) + 1, True
    return int(pilot_m), False


def inclusion_prob(m: int) -> float:
    return int(m) / HASH_MOD


def pool_rule(kind, login, website_url, website_domain, display_name) -> bool:
    """in_pool: an organisation whose own website domain satisfies the login rule (same rule as find)."""
    if kind != "organization" or not isinstance(login, str) or not login:
        return False
    if not isinstance(website_domain, str) or not website_domain:
        return False
    return bool(login_rule(login, website_url, website_domain, display_name if isinstance(display_name, str) else None))


def _year_list(years) -> list[int]:
    if years is None:
        y0, y1 = cfg()["pool"]["org_sample_years"]
        years = range(int(y0), int(y1) + 1)
    return sorted({int(y) for y in years})


def sample_pool(years: Iterable[int] | None = None, per_year: int | None = None, ch: gha.ClickHouse | None = None,
                client=None, lookup: Callable[[list[str]], pd.DataFrame] | None = None, pilot_m: int = PILOT_M,
                out_path: Path | None = SAMPLE_PATH, years_path: Path | None = SAMPLE_YEARS_PATH,
                budget: Budget | None = None, **lookup_kw) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Outcome-blind GitHub org sample per first-activity year. Returns (sample rows, per-year stats)."""
    years = _year_list(years)
    target = int(cfg()["pool"]["owner_lookups_per_year"] if per_year is None else per_year)
    budget = budget or Budget()
    _check_output(out_path, SAMPLE_COLUMNS)
    _check_output(years_path, YEAR_COLUMNS)
    ch = _ch(ch)
    if lookup is None:
        def lookup(ls):  # noqa: E306
            return lookup_owners(ls, client=client, budget=budget, **lookup_kw)

    print(f"sample: pilot query (buckets < {pilot_m}) ~{CH_SECONDS_PER_QUERY * 2:.0f} s if uncached")
    pilot = frame_pilot(ch.query("owner_pilot", sql_owner_pilot(pilot_m), PILOT_COLUMNS))
    chosen: dict[int, tuple[int, bool]] = {}
    for y in years:
        py = pilot[pilot["first_year"] == y]
        chosen[y] = choose_m(py, target, pilot_m) if int(py["n_owners"].sum()) else (int(pilot_m), False)
    m_list = {y: m for y, (m, _) in chosen.items() if int(pilot.loc[pilot["first_year"] == y, "n_org_like"].sum())}
    listing = frame_sample_list(ch.query("owner_sample", sql_owner_sample(m_list), SAMPLE_RAW_COLUMNS)) \
        if m_list else frame_sample_list(pd.DataFrame())
    if len(listing) > gha.MAX_ROWS:
        raise OwnersError("sample listing hit the row guard; lower per_year")
    for y, m in m_list.items():
        bad = listing[(listing["first_year"] == y) & (listing["hash_bucket"] >= m)]
        if len(bad):
            raise OwnersError(f"listing returned buckets >= m for {y}")
    print("sample: m per year " + ", ".join(f"{y}:{chosen[y][0]}" for y in years) +
          f"; {len(listing)} org_like owners to look up")
    looked = owner_frame(lookup(listing["login"].tolist())).set_index("query_login") if len(listing) else \
        owner_frame([]).set_index("query_login")

    rows, stats = [], []
    for y in years:
        m, reached = chosen[y]
        py = pilot[(pilot["first_year"] == y) & (pilot["hash_bucket"] < m)]
        sub = listing[listing["first_year"] == y]
        st = {"year": y, "target": target, "pilot_m": pilot_m, "hash_m": m, "inclusion_prob": inclusion_prob(m),
              "target_reached": reached, "left_censored": y <= GHARCHIVE_START_YEAR,
              "n_owners_sampled": int(py["n_owners"].sum()), "n_never_actor_sampled": int(py["n_never_actor"].sum()),
              "n_org_like_pilot": int(py["n_org_like"].sum()), "n_org_like_listed": len(sub)}
        n = {"looked": 0, "pending": 0, "nf": 0, "mm": 0, "org": 0, "user": 0, "other": 0, "web": 0, "pool": 0}
        for r in sub.to_dict("records"):
            o = looked.loc[r["login"]].to_dict() if r["login"] in looked.index else None
            if o is None or o["lookup_error"] is not None:
                n["pending"] += 1
                continue
            n["looked"] += 1
            kind = o["kind"] if o["note"] is None else None
            if not _true(o["exists"]):
                n["nf"] += 1
            elif o["note"] == "login_mismatch":
                n["mm"] += 1
            elif kind == "organization":
                n["org"] += 1
                n["web"] += int(o["website_domain"] is not None)
            elif kind == "user":
                n["user"] += 1
            else:
                n["other"] += 1
            is_org = kind == "organization"
            in_pool = pool_rule(kind, o["login"], o["website_url"], o["website_domain"], o["display_name"])
            n["pool"] += int(in_pool)
            rows.append({"login": o["login"] if is_org else None, "owner_key": o["owner_key"],
                         "uuid": o["uuid"] if is_org else None, "first_event_ts": r["first_event_ts"],
                         "first_event_year": y, "last_event_ts": r["last_event_ts"], "n_events": r["n_events"],
                         "hash_bucket": r["hash_bucket"], "hash_m": m, "inclusion_prob": inclusion_prob(m),
                         "kind": o["kind"] if o["note"] is None else None,
                         "website_url": o["website_url"] if is_org else None,
                         "website_domain": o["website_domain"] if is_org else None,
                         "display_name": o["display_name"] if is_org else None, "in_pool": in_pool,
                         "fetched_at": o["fetched_at"]})
        div = lambda a, b: (a / b) if b else float("nan")  # noqa: E731
        st.update({"n_looked_up": n["looked"], "n_pending": n["pending"], "n_not_found": n["nf"],
                   "n_login_mismatch": n["mm"], "n_kind_organization": n["org"], "n_kind_user": n["user"],
                   "n_kind_other": n["other"], "n_org_with_website_domain": n["web"], "n_in_pool": n["pool"],
                   "org_like_share": div(len(sub), st["n_owners_sampled"]),
                   "kind_org_share": div(n["org"], n["looked"]), "website_share": div(n["web"], n["org"]),
                   "in_pool_share": div(n["pool"], n["looked"]), "in_pool_share_of_orgs": div(n["pool"], n["org"]),
                   "complete": n["pending"] == 0})
        stats.append(st)
    df = sample_frame(rows)
    ystats = pd.DataFrame(stats, columns=YEAR_COLUMNS)
    complete = [int(y) for y in ystats.loc[ystats["complete"], "year"]]
    incomplete = [int(y) for y in ystats.loc[~ystats["complete"], "year"]]
    if incomplete:
        log.warning("sample: years %s have pending lookups (request cap / errors) and are NOT written; rerun",
                    incomplete)
    if out_path is not None and complete:
        _write_sample(df[df["first_event_year"].isin(complete)], Path(out_path), complete, target, pilot_m)
    if years_path is not None:
        _write_years(ystats, Path(years_path))
    return df, ystats


def sample_frame(rows) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame.from_records(rows, columns=SAMPLE_COLUMNS)
    df = df[SAMPLE_COLUMNS].copy()
    df["uuid"] = pd.to_numeric(df["uuid"], errors="coerce").astype("Int64")
    for c in ("first_event_ts", "last_event_ts", "fetched_at"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601") if c == "fetched_at" else \
            pd.to_datetime(df[c], utc=True)
    for c in ("first_event_year", "n_events", "hash_bucket", "hash_m"):
        df[c] = df[c].astype("int64")
    df["inclusion_prob"] = df["inclusion_prob"].astype(float)
    df["in_pool"] = df["in_pool"].astype(bool)
    for c in ("login", "owner_key", "kind", "website_url", "website_domain", "display_name"):
        df[c] = df[c].astype(object).where(df[c].notna(), None)
    return df.sort_values(["first_event_year", "hash_bucket", "owner_key"]).reset_index(drop=True)


def _write_sample(df: pd.DataFrame, path: Path, years: list[int], target: int, pilot_m: int):
    if path.exists():
        _check_output(path, SAMPLE_COLUMNS)
        old = pd.read_parquet(path)
        old = old[~old["first_event_year"].isin(years)]
        full = sample_frame(pd.concat([sample_frame(old), df], ignore_index=True)) if len(old) else sample_frame(df)
    else:
        full = sample_frame(df)
    path.parent.mkdir(parents=True, exist_ok=True)
    full.to_parquet(path, index=False)
    per_year = full.groupby("first_event_year").agg(rows=("owner_key", "size"), in_pool=("in_pool", "sum"),
                                                    hash_m=("hash_m", "first")).reset_index()
    write_meta(path, script=SCRIPT + " sample", rows=len(full),
               sources=[cfg()["gharchive"]["clickhouse_url"], OWNER_URL.format(login="<login>")],
               inputs=[OWNERS_PATH] if OWNERS_PATH.exists() else [],
               params={"years_this_run": years, "target_org_like_per_year": target, "pilot_m": pilot_m,
                       "hash": f"cityHash64(lower(owner)) % {HASH_MOD} < hash_m", "personal_ratio": PERSONAL_RATIO,
                       "orgish_actor_types": list(ORGISH_ACTOR_TYPES), "bots_regex": gha.bots_regex(),
                       "per_year": {str(int(r.first_event_year)): {"rows": int(r.rows), "in_pool": int(r.in_pool),
                                                                    "hash_m": int(r.hash_m)}
                                    for r in per_year.itertuples()},
                       "licence_ecosystems": ECO_LICENCE},
               notes="Outcome-blind GitHub org sample (pool channel a); no label data read. Population of year Y = "
                     "GH Archive repo owners whose first non-bot event is in Y; sampled iff hash bucket < hash_m "
                     "(inclusion_prob = hash_m / 10000, exact); org_like owners looked up on ecosyste.ms. in_pool = "
                     "kind organization + website domain + login_rule. login / uuid / website only for "
                     "organisations. Website and name are current values (fetched_at). first_event_year 2011 is "
                     "left-censored (GH Archive starts 2011-02-12).")


def _write_years(ystats: pd.DataFrame, path: Path):
    if path.exists():
        _check_output(path, YEAR_COLUMNS)
        old = pd.read_csv(path)
        old = old[~old["year"].isin(set(ystats["year"]))]
        ystats = pd.concat([old, ystats], ignore_index=True) if len(old) else ystats
    ystats = ystats.sort_values("year").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    ystats.to_csv(path, index=False, float_format="%.6g")
    write_meta(path, script=SCRIPT + " sample", rows=len(ystats),
               params={"columns": YEAR_COLUMNS},
               notes="Per first-activity year: hash_m and inclusion_prob, pilot counts (owners / never-actor / "
                     "org_like in the sampled buckets), ecosyste.ms outcomes and shares. complete = no pending lookup "
                     "(only complete years are written to github_org_sample.parquet).")


# ---------------------------------------------------------------------------------------------------------------------
# calibration of the org_like rule (GH Archive `org` field as ground truth)
# ---------------------------------------------------------------------------------------------------------------------
def owner_kinds_from_events(objs: Iterable[dict]) -> tuple[set, set]:
    """(org owners, user owners) from 2015+ GH Archive events: `org` is present iff the repo belongs to an org."""
    orgs, users = set(), set()
    for e in objs:
        if not isinstance(e, dict):
            continue
        name = (e.get("repo") or {}).get("name") if isinstance(e.get("repo"), dict) else None
        if not isinstance(name, str) or "/" not in name:
            continue
        owner = name.split("/", 1)[0].lower()
        org = e.get("org")
        org_login = (org.get("login") or "").lower() if isinstance(org, dict) else ""
        if org_login and org_login == owner:
            orgs.add(owner)
        elif not org_login:
            users.add(owner)
    return orgs, users


def calibration_labels(orgs: set, users: set, per_class: int) -> pd.DataFrame:
    """Deterministic subsample (sha256 order) of unambiguous, SQL-safe owners: per_class orgs + per_class users."""
    amb = orgs & users
    ok = lambda s: sorted((x for x in s - amb if gha.LOGIN_RE.match(x)),  # noqa: E731
                          key=lambda x: hashlib.sha256(x.encode()).hexdigest())[:per_class]
    o, u = ok(orgs), ok(users)
    return pd.DataFrame({"login": o + u, "label": ["org"] * len(o) + ["user"] * len(u)})


CALIB_RULES = {
    "spec_not_actor": lambda a: a["exists_as_owner"] & ~a["is_actor"],
    "no_personal_actor_events": lambda a: a["exists_as_owner"] & (a["n_personal_actor_events"] == 0),
    "org_like": lambda a: a["org_like"],
}
CALIB_BINS = [("all", 1, math.inf), ("1", 1, 1), ("2-10", 2, 10), ("11-100", 11, 100), (">100", 101, math.inf)]


def calibration_metrics(act: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    df = labels.merge(act, on="login", how="left")
    df = df[df["exists_as_owner"].fillna(False).astype(bool)]
    out = []
    for name, rule in CALIB_RULES.items():
        pred = rule(df).astype(bool)
        for b, lo, hi in CALIB_BINS:
            m = (df["n_events"] >= lo) & (df["n_events"] <= hi)
            o, u = pred[m & (df["label"] == "org")], pred[m & (df["label"] == "user")]
            out.append({"rule": name, "n_events_bin": b, "n_org": len(o), "n_user": len(u),
                        "org_recall": o.mean() if len(o) else float("nan"),
                        "user_pass_rate": u.mean() if len(u) else float("nan")})
    return pd.DataFrame(out)


def calibrate(hours: list[str] | None = None, per_class: int = 5000, ch: gha.ClickHouse | None = None,
              client=None, out_path: Path | None = CALIB_PATH, cache_dir: Path = gha.HOURLY_CACHE_DIR,
              manifest_dir: Path = gha.HOURLY_MANIFEST_DIR) -> pd.DataFrame:
    hours = hours or DEFAULT_CALIB_HOURS
    orgs, users = set(), set()
    dl = client or Client()
    for h in hours:
        if gha.parse_hour(h).year < 2015:
            raise ValueError(f"{h}: Timeline-era events (pre-2015) carry no reliable `org` field")
        path = gha.fetch_hour(h, client=dl, cache_dir=cache_dir, manifest_dir=manifest_dir, keep=False)
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
                o, u = owner_kinds_from_events(obj for obj, ok in gha.iter_json_objects(f) if ok)
        finally:
            path.unlink(missing_ok=True)            # personal e-mails inside: keep only the manifest
        orgs |= o
        users |= u
    labels = calibration_labels(orgs, users, per_class)
    ch = _ch(ch)
    act = owner_activity(labels["login"].tolist(), ch=ch)
    res = calibration_metrics(act, labels)
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        res.to_csv(out_path, index=False, float_format="%.6g")
        write_meta(out_path, script=SCRIPT + " calibrate", rows=len(res),
                   sources=[gha.GHARCHIVE_URL.format(hour=h) for h in hours] + [ch.url],
                   inputs=[manifest_dir / f"{h}.json.gz.manifest.json" for h in hours]
                   + sorted({Path(q.raw_path) for q in ch.log if q.name == "owner_activity"}),
                   params={"hours": hours, "per_class": per_class, "org_owners_seen": len(orgs - (orgs & users)),
                           "user_owners_seen": len(users - (orgs & users)), "ambiguous": len(orgs & users),
                           "personal_ratio": PERSONAL_RATIO, "orgish_actor_types": list(ORGISH_ACTOR_TYPES)},
                   notes="Ground truth: GH Archive 2015+ events carry `org` iff the repo belongs to an organisation "
                         "(owner == org.login -> org; no org field -> user; owners seen both ways dropped). "
                         "org_recall / user_pass_rate = share of labelled owners (with >= 1 non-bot owned event) "
                         "kept by each rule, overall and per n_events bin. Only aggregates are stored.")
    return res


# ---------------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------------
def _print_ch(ch: gha.ClickHouse | None):
    if ch is None or not ch.log:
        print("clickhouse: 0 queries")
        return
    df = pd.DataFrame([q.__dict__ for q in ch.log])
    net = df[~df["from_cache"]]
    print(f"clickhouse: queries={len(df)} network={len(net)} cache_hits={int(df['from_cache'].sum())} "
          f"rows={int(df['rows'].sum())} seconds={df['seconds'].sum():.1f}")


def _report(ch, budget: Budget):
    _print_ch(ch)
    s = STATS.as_dict()
    cap = "" if budget.max_requests is None else f" (cap {budget.max_requests}, used {budget.used})"
    print("ecosyste.ms: " + json.dumps(s, sort_keys=True) + cap)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.sources.github_owners", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_s = sub.add_parser("sample", help="outcome-blind org sample per first-activity year (pool channel a)")
    p_s.add_argument("--years", type=int, nargs="+", default=None, help="default: cfg pool.org_sample_years")
    p_s.add_argument("--per-year", type=int, default=None, help="org_like owners to look up per year "
                                                                "(default: cfg pool.owner_lookups_per_year)")
    p_s.add_argument("--pilot-m", type=int, default=PILOT_M)
    p_s.add_argument("--out", type=Path, default=SAMPLE_PATH)
    p_s.add_argument("--years-out", type=Path, default=None, help="default: <out stem>_years.csv")
    p_f = sub.add_parser("find", help="staged entity -> GitHub org search")
    p_f.add_argument("--input", type=Path, default=SEARCH_INPUT_PATH, help="CSV entity_id[,names]")
    p_f.add_argument("--out", type=Path, default=ENTITY_ORG_PATH)
    p_f.add_argument("--search-log", type=Path, default=None, help="default: <out stem>_search.parquet")
    p_f.add_argument("--dry-run", action="store_true", help="count candidates per tier; no requests")
    p_f.add_argument("--org-like-rate", type=float, default=None,
                     help="dry run: org_like candidates per candidate login, for the lookup ETA")
    p_a = sub.add_parser("activity", help="GH Archive owner activity for logins")
    p_a.add_argument("logins", nargs="+")
    p_l = sub.add_parser("lookup", help="ecosyste.ms lookup for logins")
    p_l.add_argument("logins", nargs="+")
    p_c = sub.add_parser("calibrate", help="measure the org_like rule against the GH Archive `org` field")
    p_c.add_argument("--hours", nargs="+", default=None)
    p_c.add_argument("--per-class", type=int, default=5000)
    p_c.add_argument("--out", type=Path, default=CALIB_PATH)
    for p in (p_s, p_f, p_l):
        p.add_argument("--max-requests", type=int, default=None, help="cap on ecosyste.ms requests in this run")
    a = ap.parse_args(argv)
    budget = Budget(getattr(a, "max_requests", None))
    ch = None
    t0 = time.monotonic()
    try:
        if a.cmd == "find":
            try:
                ents = read_search_input(a.input)
            except (OSError, OwnersError) as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            plan = plan_find(ents)
            print("plan: " + json.dumps(plan, sort_keys=True))
            if a.dry_run:
                q = sum(plan[f"tier{t}_clickhouse_queries"] for t in (1, 2, 3))
                print(f"upper bound (no entity resolved before tier 3): {q} ClickHouse queries "
                      f"~{q * CH_SECONDS_PER_QUERY / 60:.1f} min")
                if a.org_like_rate is not None:
                    n = sum(plan[f"tier{t}_distinct_logins"] for t in (1, 2, 3)) * a.org_like_rate
                    print(f"expected ecosyste.ms lookups (upper bound) ~{n:.0f} ~{n * eco_interval() / 60:.1f} min")
                return 0
            ch = gha.backend()
            slog = a.search_log or (SEARCH_LOG_PATH if a.out == ENTITY_ORG_PATH
                                    else a.out.with_name(a.out.stem + "_search.parquet"))
            out = find_org(ents, ch=ch, out_path=a.out, search_log_path=slog, budget=budget)
            s = pd.read_parquet(slog)
            s = s[s["entity_id"].isin(set(_e for _e, _ in _prep_entities(ents)))]
            held = int(s["note"].fillna("").str.contains("_tier").sum())
            n = len(s)
            print(f"entities={n} matched={len(out)} ({len(out) / max(n, 1):.1%}) by tier "
                  f"{out['tier'].value_counts().sort_index().to_dict()} held={held} -> {a.out} ; log {slog}")
        elif a.cmd == "sample":
            ch = gha.backend()
            yo = a.years_out or (SAMPLE_YEARS_PATH if a.out == SAMPLE_PATH
                                 else a.out.with_name(a.out.stem + "_years.csv"))
            df, st = sample_pool(years=a.years, per_year=a.per_year, ch=ch, pilot_m=a.pilot_m, out_path=a.out,
                                 years_path=yo, budget=budget)
            cols = ["year", "hash_m", "inclusion_prob", "n_owners_sampled", "n_org_like_listed", "n_looked_up",
                    "n_pending", "n_not_found", "n_kind_organization", "n_kind_user", "n_org_with_website_domain",
                    "n_in_pool", "org_like_share", "kind_org_share", "website_share", "in_pool_share", "complete"]
            print(st[cols].to_string(index=False))
            print(f"-> {a.out} ; {yo}")
        elif a.cmd == "activity":
            ch = gha.backend()
            print(owner_activity(a.logins, ch=ch).to_string(index=False))
        elif a.cmd == "lookup":
            df = lookup_owners(a.logins, budget=budget)
            print(df[["query_login", "exists", "kind", "login", "uuid", "website_domain", "lookup_error"]]
                  .to_string(index=False))
        elif a.cmd == "calibrate":
            ch = gha.backend()
            res = calibrate(a.hours, a.per_class, ch=ch, out_path=a.out)
            print(res.to_string(index=False))
    except OutputConflict as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (gha.ClickHouseError, gha.GHArchiveError, EcosystemsUnavailable) as e:
        log.error("%s", e)
        print(f"ERROR: {e}", file=sys.stderr)
        _report(ch, budget)
        return 3
    _report(ch, budget)
    print(f"elapsed={time.monotonic() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
