"""GH Archive backend: org activity span, team, daily org activity, team external engagement.

Primary backend: the ClickHouse public playground (free, keyless mirror of GH Archive), table `github_events`
(sorting key event_type, repo_name, created_at; created_at is DateTime in UTC). SQL is POSTed through
scripts.lib.http.Client (cached + rate limited), and every query's SQL + result is persisted as
data/raw/gharchive/<name>_<sha8>.sql / .tsv.gz (+ .meta.json).

Definitions (identical for every org / actor):
  owner      = lower-cased part of repo_name before the first '/'. repo_name without '/' -> owner '' (never matches).
  bots       = actor_login matching cfg gharchive.bots_regex (on the lower-cased login); excluded everywhere.
  logins     = lower-cased; only [a-z0-9-]{1,39} is accepted into SQL (anything else is rejected, never escaped).
  timestamps = created_at, the time at which the event became public (UTC). Nothing is derived from later data.

Functions
  org_activity_span(orgs)          -> owner, first_event_ts, last_event_ts, n_events      (all event types)
  team_first_push(orgs)            -> owner, actor_login, first_push_ts                    (PushEvent)
  org_activity_daily(orgs)         -> owner, day, push, pr, issues, release                (all actions)
  external_monthly(actors, orgs)   -> actor_login, month, owner_bucket, pr_opened, issues_opened, pushes
  schema_check()                   -> per year x event_type field coverage (pre-2015 Timeline era check)
  fidelity_check(hours)            -> ClickHouse vs raw data.gharchive.org hourly files, counts per type

Robustness to the pre-2015 (Timeline API) era - explicit, verified by `schema-check`:
  * owner is parsed only when repo_name contains '/'; otherwise the event has no owner and matches no org.
  * pr_opened / issues_opened require action = 'opened'. The schema check reports, per year, the share of
    PullRequestEvent / IssuesEvent rows with action set and with action = 'opened'; years where action is not
    populated would be undercounted, so `schema-check` flags them (column `flag`).
  * author_association is NOT used by any feature query (it is empty before ~2017 and 'NONE' is also a real value).
  * Pushes are counted as PushEvent rows (one push = one event), not commits (push_size changes meaning over time).
  * Time: only created_at (UTC; Timeline-era offsets such as -07:00 are converted correctly, verified by `fidelity`)
    is used. file_time is the GH Archive file label, which is US/Pacific LOCAL time before 2015 (7-8 h off UTC):
    never used as an event time.
  * Measured (schema-check, 2011-2026): repo_name contains '/' in ~100% of rows every year; action is set on 100% of
    PullRequestEvent / IssuesEvent rows every year (incl. 2011-2014); author_association is empty before 2017;
    ReleaseEvent exists from 2013; ~0.3-0.5% of 2011-2014 events have an empty actor_login (excluded).

Processed outputs (parquet + .meta.json; no raw logins in processed data, actors are sha256(login)[:16]):
  data/processed/gh_org_span.parquet, gh_team.parquet, gh_org_activity_daily.parquet, gh_ext_monthly.parquet,
  gharchive_schema_coverage.csv, gharchive_fidelity.csv

Limitations: github_events has NO repo / org / actor id column. A renamed org (or user) keeps its old events under the
old name, so history before a rename is lost for the new login (and vice versa). Transferred repos count for the
owner at event time.

Alternative backend: BigQuery `githubarchive.month.*` (BigQueryBackend). Not run in this session (no GCP creds);
every query is dry-run first and refused above cfg gharchive.bigquery_max_gb.

CLI
    .venv/bin/python -m scripts.sources.gharchive {span|team|activity|external|all} [--orgs FILE]
    .venv/bin/python -m scripts.sources.gharchive schema-check
    .venv/bin/python -m scripts.sources.gharchive fidelity [--hours 2013-05-08-15 ...] [--keep-hourly]
  fidelity downloads raw hourly files (they contain personal e-mails), counts them and deletes them again; only a
  manifest (url, sha256, bytes, fetched_at) stays in data/raw/gharchive/hourly/. --keep-hourly keeps them.
    .venv/bin/python -m scripts.sources.gharchive bq-dry-run --orgs FILE        # needs GCP creds (not run now)
  --orgs: one login per line ('#' comments allowed). Without --orgs, span/team/activity/all use the orgs in
  data/processed/entity_github_org.parquet (org_login) + github_org_sample.parquet (login, in_pool).
  `external` reads gh_team.parquet (actors) and the raw team TSVs listed in its meta to resolve hashes to logins.
  No subcommand = `all` (Makefile target `gharchive`).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from scripts.lib.config import CACHE, PROCESSED, RAW, ROOT, cfg, data_cutoff, get_logger
from scripts.lib.http import Client, _key
from scripts.lib.meta import sha256_file, write_meta

log = get_logger("gharchive")
SCRIPT = "scripts/sources/gharchive.py"

RAW_DIR = RAW / "gharchive"
HOURLY_MANIFEST_DIR = RAW_DIR / "hourly"
HOURLY_CACHE_DIR = CACHE / "data.gharchive.org" / "hourly"       # gitignored; re-downloadable (manifests in raw)
GHARCHIVE_URL = "https://data.gharchive.org/{hour}.json.gz"

SPAN_PATH = PROCESSED / "gh_org_span.parquet"
TEAM_PATH = PROCESSED / "gh_team.parquet"
DAILY_PATH = PROCESSED / "gh_org_activity_daily.parquet"
EXT_PATH = PROCESSED / "gh_ext_monthly.parquet"
SCHEMA_PATH = PROCESSED / "gharchive_schema_coverage.csv"
FIDELITY_PATH = PROCESSED / "gharchive_fidelity.csv"

SPAN_COLUMNS = ["owner", "first_event_ts", "last_event_ts", "n_events"]
TEAM_COLUMNS = ["owner", "actor_hash", "first_push_ts"]
DAILY_COLUMNS = ["owner", "day", "push", "pr", "issues", "release"]
EXT_COLUMNS = ["actor_hash", "month", "owner_bucket", "pr_opened", "issues_opened", "pushes"]

EXT_BUCKET = "_ext"
CHUNK = 500                      # logins per IN-list
MAX_ROWS = 500_000               # LIMIT guard; server max_result_rows is 1,000,000 (overflow truncates silently)
MAX_SQL_BYTES = 250_000          # ClickHouse default max_query_size is 256 KiB
SPLITTABLE_CODES = {"159", "160", "241", "396", "307", "158"}   # timeout, too slow, memory, too many rows/bytes
DEFAULT_FIDELITY_HOURS = ["2013-05-08-15", "2016-05-11-15", "2021-05-12-15"]

LOGIN_RE = re.compile(r"^[a-z0-9-]{1,39}$")
_EXC_RE = re.compile(r"Code: (\d+)\. DB::Exception")
_TS_FMT = "%Y-%m-%d %H:%i:%S"

FEATURE_TYPES = ["PushEvent", "PullRequestEvent", "IssuesEvent", "ReleaseEvent"]
ALL_EVENT_TYPES = ["CommitCommentEvent", "CreateEvent", "DeleteEvent", "ForkEvent", "GollumEvent", "IssueCommentEvent",
                   "IssuesEvent", "MemberEvent", "PublicEvent", "PullRequestEvent", "PullRequestReviewCommentEvent",
                   "PushEvent", "ReleaseEvent", "SponsorshipEvent", "WatchEvent", "GistEvent", "FollowEvent",
                   "DownloadEvent", "PullRequestReviewEvent", "ForkApplyEvent", "Event", "TeamAddEvent",
                   "DiscussionEvent"]


# ----------------------------------------------------------------------------------------------------------------
# pure helpers (tested offline)
# ----------------------------------------------------------------------------------------------------------------
class GHArchiveError(RuntimeError):
    pass


class ClickHouseError(GHArchiveError):
    def __init__(self, code: str | None, message: str, status: int | None = None):
        super().__init__(f"ClickHouse error code={code} status={status}: {message[:300]}")
        self.code = code
        self.status = status

    @property
    def splittable(self) -> bool:
        return self.code in SPLITTABLE_CODES or self.status == -1


class ResultTooLarge(GHArchiveError):
    pass


class BigQueryBudgetError(GHArchiveError):
    pass


def bots_regex() -> str:
    return cfg()["gharchive"]["bots_regex"]


def is_bot(login: str | None, regex: str | None = None) -> bool:
    return bool(re.search(regex or bots_regex(), str(login or "").lower()))


def validate_login(login: str) -> str:
    """Return the login if it is exactly [a-z0-9-]{1,39}; raise otherwise (no escaping is ever attempted)."""
    if not isinstance(login, str) or not LOGIN_RE.match(login):
        raise ValueError(f"invalid login for SQL IN-list: {login!r}")
    return login


def normalize_logins(logins: Iterable, what: str = "login") -> tuple[list[str], list[str]]:
    """Lower-case, strip, dedupe and sort; returns (valid, rejected). Rejected values never reach SQL."""
    ok, bad = set(), set()
    for x in logins:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            continue
        s = str(x).strip().lower()
        if not s or s.startswith("#"):
            continue
        (ok if LOGIN_RE.match(s) else bad).add(s)
    if bad:
        log.warning("rejected %d invalid %s value(s) (not [a-z0-9-]{1,39})", len(bad), what)
    return sorted(ok), sorted(bad)


def chunked(items: list, size: int = CHUNK) -> list[list]:
    if size < 1:
        raise ValueError("chunk size must be >= 1")
    return [items[i:i + size] for i in range(0, len(items), size)]


def sql_str(s: str) -> str:
    """ClickHouse single-quoted string literal."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def sql_in_list(logins: Iterable[str]) -> str:
    vals = [validate_login(x) for x in logins]
    if not vals:
        raise ValueError("empty IN-list")
    return "(" + ", ".join(f"'{v}'" for v in vals) + ")"


OWNER_SQL = "lower(if(position(repo_name, '/') > 0, substring(repo_name, 1, position(repo_name, '/') - 1), ''))"


def owner_of(repo_name: str | None) -> str:
    """Python twin of OWNER_SQL."""
    s = str(repo_name or "")
    return s.split("/", 1)[0].lower() if "/" in s else ""


def bot_filter_sql(regex: str | None = None) -> str:
    return f"NOT match(lower(actor_login), {sql_str(regex or bots_regex())})"


def _check_sql(sql: str) -> str:
    if len(sql.encode()) > MAX_SQL_BYTES:
        raise ValueError(f"SQL too large ({len(sql.encode())} bytes); use a smaller chunk")
    return sql


def sql_org_span(orgs: list[str], regex: str | None = None, max_rows: int = MAX_ROWS) -> str:
    return _check_sql(f"""SELECT {OWNER_SQL} AS owner,
  formatDateTime(min(created_at), '{_TS_FMT}', 'UTC') AS first_event_ts,
  formatDateTime(max(created_at), '{_TS_FMT}', 'UTC') AS last_event_ts,
  count() AS n_events
FROM github_events
WHERE owner IN {sql_in_list(orgs)} AND {bot_filter_sql(regex)}
GROUP BY owner
ORDER BY owner
LIMIT {max_rows + 1}
FORMAT TSVWithNames""")


def sql_team_first_push(orgs: list[str], regex: str | None = None, max_rows: int = MAX_ROWS) -> str:
    return _check_sql(f"""SELECT {OWNER_SQL} AS owner, lower(actor_login) AS actor,
  formatDateTime(min(created_at), '{_TS_FMT}', 'UTC') AS first_push_ts
FROM github_events
WHERE event_type = 'PushEvent' AND owner IN {sql_in_list(orgs)} AND actor_login != '' AND {bot_filter_sql(regex)}
GROUP BY owner, actor
ORDER BY owner, actor
LIMIT {max_rows + 1}
FORMAT TSVWithNames""")


def sql_org_activity_daily(orgs: list[str], regex: str | None = None, max_rows: int = MAX_ROWS) -> str:
    return _check_sql(f"""SELECT {OWNER_SQL} AS owner, toDate(created_at, 'UTC') AS day,
  countIf(event_type = 'PushEvent') AS push,
  countIf(event_type = 'PullRequestEvent') AS pr,
  countIf(event_type = 'IssuesEvent') AS issues,
  countIf(event_type = 'ReleaseEvent') AS release
FROM github_events
WHERE event_type IN ('PushEvent', 'PullRequestEvent', 'IssuesEvent', 'ReleaseEvent')
  AND owner IN {sql_in_list(orgs)} AND {bot_filter_sql(regex)}
GROUP BY owner, day
ORDER BY owner, day
LIMIT {max_rows + 1}
FORMAT TSVWithNames""")


def sql_external_monthly(actors: list[str], orgs: list[str], regex: str | None = None,
                         max_rows: int = MAX_ROWS) -> str:
    bucket = f"if(owner IN {sql_in_list(orgs)}, owner, {sql_str(EXT_BUCKET)})" if orgs else sql_str(EXT_BUCKET)
    return _check_sql(f"""SELECT lower(actor_login) AS actor, toStartOfMonth(created_at, 'UTC') AS month,
  {bucket} AS owner_bucket,
  countIf(event_type = 'PullRequestEvent' AND action = 'opened') AS pr_opened,
  countIf(event_type = 'IssuesEvent' AND action = 'opened') AS issues_opened,
  countIf(event_type = 'PushEvent') AS pushes
FROM (SELECT actor_login, created_at, event_type, action, {OWNER_SQL} AS owner
      FROM github_events
      WHERE event_type IN ('PushEvent', 'PullRequestEvent', 'IssuesEvent')
        AND lower(actor_login) IN {sql_in_list(actors)} AND {bot_filter_sql(regex)})
GROUP BY actor, month, owner_bucket
HAVING pr_opened + issues_opened + pushes > 0
ORDER BY actor, month, owner_bucket
LIMIT {max_rows + 1}
FORMAT TSVWithNames""")


def sql_schema_coverage(event_type: str) -> str:
    if event_type not in ALL_EVENT_TYPES:
        raise ValueError(f"unknown event_type {event_type!r}")
    return f"""SELECT toYear(created_at, 'UTC') AS year, toString(event_type) AS event_type, count() AS n_events,
  countIf(action != 'none') AS n_action_set,
  countIf(action = 'opened') AS n_action_opened,
  countIf(author_association != 'NONE') AS n_author_association_set,
  countIf(position(repo_name, '/') > 0) AS n_repo_slash,
  countIf(actor_login = '') AS n_actor_empty,
  countIf(NOT match(lower(actor_login), '^[a-z0-9-]{{1,39}}$') AND NOT is_bot) AS n_actor_nonstandard,
  countIf(is_bot) AS n_bot
FROM (SELECT created_at, event_type, action, author_association, repo_name, actor_login,
             match(lower(actor_login), {sql_str(bots_regex())}) AS is_bot
      FROM github_events WHERE event_type = {sql_str(event_type)})
GROUP BY year, event_type
ORDER BY year
FORMAT TSVWithNames"""


def parse_hour(hour: str) -> datetime:
    """'2013-05-08-15' (GH Archive file name, UTC) -> aware datetime."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})-(\d{1,2})", hour)
    if not m:
        raise ValueError(f"bad hour {hour!r}; expected YYYY-MM-DD-H")
    y, mo, d, h = map(int, m.groups())
    if not 0 <= h <= 23:
        raise ValueError(f"bad hour {hour!r}")
    return datetime(y, mo, d, h, tzinfo=timezone.utc)


def sql_fidelity_hour(hour: str, by: str = "created_at", offset_h: int = 0) -> str:
    """Per-type counts for one GH Archive file hour.

    by='file_time'          rows imported from the file labelled `hour` (file_time is the file label, NOT UTC
                            before 2015: Timeline-era files are labelled in US/Pacific local time)
    by='created_same_file'  rows of that file with created_at in the file's effective UTC hour [h+off, h+off+1)
    by='created_at'         all rows with created_at in [h+off, h+off+1) UTC, whatever file they came from
    """
    if by not in ("created_at", "file_time", "created_same_file"):
        raise ValueError(by)
    h0 = parse_hour(hour)
    w0 = h0 + timedelta(hours=int(offset_h))
    lab = h0.strftime("%Y-%m-%d %H:%M:%S")
    a = w0.strftime("%Y-%m-%d %H:%M:%S")
    b = (w0 + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    created = f"created_at >= toDateTime('{a}', 'UTC') AND created_at < toDateTime('{b}', 'UTC')"
    file_ = f"file_time = toDateTime('{lab}', 'UTC')"
    where = {"created_at": created, "file_time": file_, "created_same_file": f"{file_} AND {created}"}[by]
    return f"""SELECT toString(event_type) AS event_type, count() AS n
FROM github_events
WHERE {where}
GROUP BY event_type
ORDER BY event_type
FORMAT TSVWithNames"""


_TSV_UNESCAPE = {"\\t": "\t", "\\n": "\n", "\\\\": "\\", "\\'": "'", "\\r": "\r", "\\0": "\0", "\\b": "\b",
                 "\\f": "\f"}
_TSV_ESC_RE = re.compile(r"\\[tn\\'r0bf]")


def _unescape(v: str) -> str:
    return _TSV_ESC_RE.sub(lambda m: _TSV_UNESCAPE[m.group(0)], v) if "\\" in v else v


def detect_exception(text: str) -> str | None:
    """ClickHouse may append 'Code: N. DB::Exception: ...' to an HTTP-200 body when a query fails mid-stream."""
    m = _EXC_RE.search(text[-4000:]) or _EXC_RE.search(text[:4000])
    return m.group(1) if m else None


def exception_text(text: str, limit: int = 1000) -> str:
    """Only the ClickHouse exception message (from its 'Code: N. DB::Exception' marker). An HTTP-200 body that fails
    mid-stream starts with data rows (logins); those must never end up in error messages, logs or stderr."""
    tail = text[-4000:]
    last = None
    for last in _EXC_RE.finditer(tail):
        pass
    if last is not None:
        return tail[last.start():][:limit]
    m = _EXC_RE.search(text[:4000])
    return text[m.start():][:limit] if m else "(no ClickHouse exception text)"


def parse_tsv(text: str, expected_columns: list[str]) -> pd.DataFrame:
    """Strict TSVWithNames parser: header must match, every row must have exactly len(header) fields."""
    code = detect_exception(text)
    if code is not None:
        raise ClickHouseError(code, exception_text(text))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines:
        raise GHArchiveError("empty TSV (no header)")
    header = lines[0].split("\t")
    if header != list(expected_columns):
        raise GHArchiveError(f"TSV header {header} != expected {expected_columns}")
    rows = []
    for i, ln in enumerate(lines[1:], start=2):
        parts = ln.split("\t")
        if len(parts) != len(header):
            raise GHArchiveError(f"TSV line {i}: {len(parts)} fields, expected {len(header)} (truncated result?)")
        rows.append([_unescape(p) for p in parts])
    return pd.DataFrame(rows, columns=header, dtype=object)


def _to_int(s: pd.Series, name: str) -> pd.Series:
    out = pd.to_numeric(s, errors="coerce")
    if out.isna().any():
        raise GHArchiveError(f"{name}: {int(out.isna().sum())} non-integer value(s)")
    return out.astype("int64")


def _to_ts(s: pd.Series, name: str) -> pd.Series:
    out = pd.to_datetime(s, format="%Y-%m-%d %H:%M:%S", errors="coerce", utc=True)
    if out.isna().any():
        raise GHArchiveError(f"{name}: {int(out.isna().sum())} unparseable timestamp(s)")
    return out


def _to_date(s: pd.Series, name: str) -> pd.Series:
    out = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    if out.isna().any():
        raise GHArchiveError(f"{name}: {int(out.isna().sum())} unparseable date(s)")
    return out.dt.date


def actor_hash(login: str) -> str:
    """sha256 of the lower-cased login, first 16 hex chars. Processed data never stores raw logins."""
    return hashlib.sha256(str(login).strip().lower().encode()).hexdigest()[:16]


def month_start(ts) -> date:
    """Floor a date/datetime/ISO string to the first day of its UTC month."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC")
    return date(t.year, t.month, 1)


def months_window(as_of, months: int = 12) -> tuple[date, date]:
    """[start, end) of `months` full calendar months ending at month_start(as_of) (as_of floored to month start)."""
    end = month_start(as_of)
    y, m = end.year, end.month - months
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, 1), end


# ----------------------------------------------------------------------------------------------------------------
# typed result frames
# ----------------------------------------------------------------------------------------------------------------
def frame_span(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({"owner": raw["owner"].astype(str),
                       "first_event_ts": _to_ts(raw["first_event_ts"], "first_event_ts"),
                       "last_event_ts": _to_ts(raw["last_event_ts"], "last_event_ts"),
                       "n_events": _to_int(raw["n_events"], "n_events")})
    return df.sort_values("owner").reset_index(drop=True)


def frame_team(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({"owner": raw["owner"].astype(str), "actor_login": raw["actor"].astype(str),
                       "first_push_ts": _to_ts(raw["first_push_ts"], "first_push_ts")})
    return df.sort_values(["owner", "actor_login"]).reset_index(drop=True)


def frame_daily(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({"owner": raw["owner"].astype(str), "day": _to_date(raw["day"], "day")})
    for c in ("push", "pr", "issues", "release"):
        df[c] = _to_int(raw[c], c)
    return df.sort_values(["owner", "day"]).reset_index(drop=True)


def frame_external(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({"actor_login": raw["actor"].astype(str), "month": _to_date(raw["month"], "month"),
                       "owner_bucket": raw["owner_bucket"].astype(str)})
    if any(d.day != 1 for d in df["month"]):
        raise GHArchiveError("month bucket is not a month start")
    for c in ("pr_opened", "issues_opened", "pushes"):
        df[c] = _to_int(raw[c], c)
    return df.sort_values(["actor_login", "month", "owner_bucket"]).reset_index(drop=True)


def team_to_processed(team: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"owner": team["owner"], "actor_hash": team["actor_login"].map(actor_hash),
                        "first_push_ts": team["first_push_ts"]})
    if out.duplicated(["owner", "actor_hash"]).any():
        raise GHArchiveError("actor_hash collision within an org")
    return out.sort_values(["owner", "actor_hash"]).reset_index(drop=True)[TEAM_COLUMNS]


def external_to_processed(ext: pd.DataFrame) -> pd.DataFrame:
    out = ext.assign(actor_hash=ext["actor_login"].map(actor_hash))
    out = out.groupby(["actor_hash", "month", "owner_bucket"], as_index=False, sort=True)[
        ["pr_opened", "issues_opened", "pushes"]].sum()
    return out[EXT_COLUMNS].reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------
# ClickHouse backend
# ----------------------------------------------------------------------------------------------------------------
def _gz_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as f:      # mtime=0 -> byte-identical re-runs
        f.write(text.encode())
    return buf.getvalue()


@dataclass
class QueryLog:
    name: str
    sql_sha8: str
    rows: int
    seconds: float
    from_cache: bool
    raw_path: str


@dataclass
class ClickHouse:
    url: str = field(default_factory=lambda: cfg()["gharchive"]["clickhouse_url"])
    client: Client | None = None
    raw_dir: Path = RAW_DIR
    write_raw: bool = True
    max_retries: int = 2          # deterministic server errors (timeout / too many rows) are split, not retried 6x
    log: list = field(default_factory=list)

    def __post_init__(self):
        if self.client is None:
            self.client = Client()
            self.client.max_retries = self.max_retries

    def _evict(self, sql: str):
        host = urlparse(self.url).netloc
        p = self.client._cache_path(host, _key("POST", self.url, None, sql))
        if p.exists():
            p.unlink()

    def query(self, name: str, sql: str, expected_columns: list[str]) -> pd.DataFrame:
        t0 = time.monotonic()
        r = self.client.post(self.url, data=sql)
        dt = time.monotonic() - t0
        code = detect_exception(r.text) if r.text else None
        if r.status != 200 or code is not None:
            self._evict(sql)
            # non-200 bodies are server/proxy error text; a 200 body with an exception starts with data rows
            raise ClickHouseError(code, exception_text(r.text) if code is not None else (r.text or "")[:300],
                                  r.status)
        try:
            df = parse_tsv(r.text, expected_columns)
        except GHArchiveError:
            self._evict(sql)
            raise
        sha8 = hashlib.sha256(sql.encode()).hexdigest()[:8]
        raw_path = self.raw_dir / f"{name}_{sha8}.tsv.gz"
        if self.write_raw:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            sql_path = raw_path.with_name(f"{name}_{sha8}.sql")
            sql_path.write_text(sql + "\n")
            raw_path.write_bytes(_gz_bytes(r.text))
            params = {"backend": "clickhouse", "url": self.url, "sql_file": sql_path.name, "fetched_at": r.fetched_at}
            write_meta(raw_path, script=SCRIPT, params=params, rows=len(df), sources=[self.url],
                       notes="Raw ClickHouse TSVWithNames response for the SQL in the .sql file (never edited).")
            write_meta(sql_path, script=SCRIPT, params={"backend": "clickhouse"}, rows=None, sources=[self.url])
        self.log.append(QueryLog(name, sha8, len(df), round(dt, 3), r.from_cache, str(raw_path)))
        log.info("query %s_%s rows=%d %.2fs cache=%s", name, sha8, len(df), dt, r.from_cache)
        return df

    def run_chunked(self, name: str, build: Callable[[list[str]], str], items: list[str], columns: list[str],
                    chunk_size: int = CHUNK, max_rows: int = MAX_ROWS) -> pd.DataFrame:
        """Run build(chunk) per chunk; a chunk that hits the LIMIT guard or a splittable server error is halved."""
        frames = []
        stack = list(reversed(chunked(items, chunk_size)))
        while stack:
            ch = stack.pop()
            try:
                df = self.query(name, build(ch), columns)
                if len(df) > max_rows:
                    self.log.pop()        # truncated by the LIMIT guard: superseded by the halves, not an input
                    raise ResultTooLarge(f"{name}: {len(df)} rows > {max_rows}")
            except (ResultTooLarge, ClickHouseError) as e:
                splittable = isinstance(e, ResultTooLarge) or e.splittable
                if not splittable or len(ch) == 1:
                    raise
                log.warning("%s: splitting chunk of %d (%s)", name, len(ch), e)
                mid = len(ch) // 2
                stack.extend([ch[mid:], ch[:mid]])
                continue
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=columns, dtype=object)
        return pd.concat(frames, ignore_index=True)

    # -- the four feature queries -------------------------------------------------------------------------------
    def org_activity_span(self, orgs, chunk_size: int = CHUNK) -> pd.DataFrame:
        orgs, _ = normalize_logins(orgs, "org")
        raw = self.run_chunked("span", sql_org_span, orgs,
                               ["owner", "first_event_ts", "last_event_ts", "n_events"], chunk_size)
        return frame_span(raw) if len(raw) else _empty(SPAN_COLUMNS)

    def team_first_push(self, orgs, chunk_size: int = CHUNK) -> pd.DataFrame:
        orgs, _ = normalize_logins(orgs, "org")
        raw = self.run_chunked("team", sql_team_first_push, orgs, ["owner", "actor", "first_push_ts"], chunk_size)
        return frame_team(raw) if len(raw) else _empty(["owner", "actor_login", "first_push_ts"])

    def org_activity_daily(self, orgs, chunk_size: int = CHUNK) -> pd.DataFrame:
        orgs, _ = normalize_logins(orgs, "org")
        raw = self.run_chunked("daily", sql_org_activity_daily, orgs,
                               ["owner", "day", "push", "pr", "issues", "release"], chunk_size)
        return frame_daily(raw) if len(raw) else _empty(DAILY_COLUMNS)

    def external_monthly(self, actors, orgs, chunk_size: int = CHUNK) -> pd.DataFrame:
        actors, _ = normalize_logins(actors, "actor")
        orgs, _ = normalize_logins(orgs, "org")
        raw = self.run_chunked("ext", lambda ch: sql_external_monthly(ch, orgs), actors,
                               ["actor", "month", "owner_bucket", "pr_opened", "issues_opened", "pushes"], chunk_size)
        return frame_external(raw) if len(raw) else _empty(["actor_login", "month", "owner_bucket", "pr_opened",
                                                            "issues_opened", "pushes"])


def _empty(cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in cols})


_BACKEND: ClickHouse | None = None


def backend() -> ClickHouse:
    global _BACKEND
    if cfg()["gharchive"]["backend"] != "clickhouse":
        raise GHArchiveError("only the clickhouse backend runs in this session; use BigQueryBackend explicitly")
    if _BACKEND is None:
        _BACKEND = ClickHouse()
    return _BACKEND


def org_activity_span(orgs) -> pd.DataFrame:
    return backend().org_activity_span(orgs)


def team_first_push(orgs) -> pd.DataFrame:
    return backend().team_first_push(orgs)


def org_activity_daily(orgs) -> pd.DataFrame:
    return backend().org_activity_daily(orgs)


def external_monthly(actors, orgs) -> pd.DataFrame:
    return backend().external_monthly(actors, orgs)


# ----------------------------------------------------------------------------------------------------------------
# schema check (pre-2015 Timeline era)
# ----------------------------------------------------------------------------------------------------------------
SCHEMA_RAW_COLUMNS = ["year", "event_type", "n_events", "n_action_set", "n_action_opened", "n_author_association_set",
                      "n_repo_slash", "n_actor_empty", "n_actor_nonstandard", "n_bot"]


def schema_coverage_frame(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({"year": _to_int(raw["year"], "year"), "event_type": raw["event_type"].astype(str)})
    for c in SCHEMA_RAW_COLUMNS[2:]:
        df[c] = _to_int(raw[c], c)
    n = df["n_events"].where(df["n_events"] > 0)
    has_action = df["event_type"].isin(["PullRequestEvent", "IssuesEvent"])
    df["share_action_set"] = (df["n_action_set"] / n).where(has_action)
    df["share_action_opened"] = (df["n_action_opened"] / n).where(has_action)
    df["share_author_association_set"] = df["n_author_association_set"] / n
    df["share_repo_slash"] = df["n_repo_slash"] / n
    df["share_actor_empty"] = df["n_actor_empty"] / n
    df["share_actor_nonstandard_nonbot"] = df["n_actor_nonstandard"] / n
    df["share_bot"] = df["n_bot"] / n
    flags = []
    for r in df.itertuples():
        f = []
        if r.event_type in FEATURE_TYPES and r.share_repo_slash < 0.99:
            f.append("repo_name_without_owner")
        if r.event_type in ("PullRequestEvent", "IssuesEvent") and r.share_action_set < 0.99:
            f.append("action_missing")
        if r.event_type in FEATURE_TYPES and r.share_actor_empty > 0.01:
            f.append("actor_missing")
        flags.append(";".join(f))
    df["flag"] = flags
    return df.sort_values(["year", "event_type"]).reset_index(drop=True)


def schema_check(ch: ClickHouse | None = None, event_types: list[str] | None = None,
                 out_path: Path = SCHEMA_PATH) -> pd.DataFrame:
    ch = ch or backend()
    frames = [ch.query(f"schema_{et}", sql_schema_coverage(et), SCHEMA_RAW_COLUMNS)
              for et in (event_types or ALL_EVENT_TYPES)]
    raw = pd.concat(frames, ignore_index=True)
    df = schema_coverage_frame(raw)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, float_format="%.6g")
    write_meta(out_path, script=SCRIPT + " schema-check", rows=len(df), sources=[ch.url],
               inputs=[Path(q.raw_path) for q in ch.log if q.name.startswith("schema_")],
               params={"event_types": event_types or ALL_EVENT_TYPES},
               notes="Per year x event_type field coverage of github_events. flag marks years where a feature "
                     "query would be affected (repo_name without owner, PR/Issues action unset, empty actor). "
                     "author_association is not used by any feature query ('NONE' is both a value and the default).")
    return df


# ----------------------------------------------------------------------------------------------------------------
# fidelity check: ClickHouse vs raw GH Archive hourly files
# ----------------------------------------------------------------------------------------------------------------
def iter_json_objects(lines: Iterable[str]) -> Iterator[tuple[dict | None, bool]]:
    """Yield (obj, ok) for every JSON object in newline-delimited text; tolerates several objects on one line
    (seen in early Timeline-era files). Unparseable remainders yield (None, False)."""
    dec = json.JSONDecoder()
    for line in lines:
        s = line.strip()
        i = 0
        while i < len(s):
            try:
                obj, j = dec.raw_decode(s, i)
            except json.JSONDecodeError:
                yield None, False
                break
            yield (obj if isinstance(obj, dict) else None), isinstance(obj, dict)
            i = j
            while i < len(s) and s[i] in " \t\r,":
                i += 1


def parse_event_ts(v) -> datetime | None:
    """GH Archive created_at: '2016-05-11T15:00:00Z' (2015+) or '2013-05-08T08:00:00-07:00' (Timeline era)."""
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00").replace("/", "-"))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None              # never guess a timezone
    return t.astimezone(timezone.utc)


def count_hour_file(path: Path, hour: str) -> dict:
    """Count events per type in one hourly file.

    The file's effective UTC hour is the modal floor((created_at_utc - label) / 1h) over its events: 0 from 2015,
    +7/+8 h for Timeline-era files (labelled in US/Pacific local time; their created_at carries the offset).
    Returns per-type counts of all events, of events with created_at in the effective UTC hour, and of distinct ids
    (2015+ events have an `id`; Timeline-era events do not -> no unique counts).
    """
    h0 = parse_hour(hour)
    recs: list[tuple[str, datetime | None, str | None]] = []
    stats = {"objects": 0, "parse_errors": 0, "no_type": 0, "ts_unparsed": 0, "no_id": 0}
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for obj, ok in iter_json_objects(f):
            if not ok:
                stats["parse_errors"] += 1
                continue
            stats["objects"] += 1
            t = obj.get("type")
            if not isinstance(t, str) or not t:
                stats["no_type"] += 1
                continue
            ts = parse_event_ts(obj.get("created_at"))
            if ts is None:
                stats["ts_unparsed"] += 1
            i = obj.get("id")
            if i is None:
                stats["no_id"] += 1
            recs.append((t, ts, None if i is None else str(i)))
    offs: dict[int, int] = {}
    for _, ts, _ in recs:
        if ts is not None:
            k = int((ts - h0).total_seconds() // 3600)
            offs[k] = offs.get(k, 0) + 1
    off = min(offs, key=lambda k: (-offs[k], abs(k), k)) if offs else 0
    w0, w1 = h0 + timedelta(hours=off), h0 + timedelta(hours=off + 1)
    all_c: dict[str, int] = {}
    in_c: dict[str, int] = {}
    ids: dict[str, set] = {}
    for t, ts, i in recs:
        all_c[t] = all_c.get(t, 0) + 1
        if ts is not None and w0 <= ts < w1:
            in_c[t] = in_c.get(t, 0) + 1
        if i is not None:
            ids.setdefault(t, set()).add(i)
    stats["offset_hist_h"] = {str(k): v for k, v in sorted(offs.items())}
    stats["ts_outside_window"] = sum(v for k, v in offs.items() if k != off)
    return {"all": all_c, "in_hour": in_c, "unique_ids": {t: len(s) for t, s in ids.items()}, "offset_h": off,
            "window_start": w0, "stats": stats}


def rel_diff(a, b) -> float:
    """(a - b) / b, NaN when b == 0 and a == 0, inf when only b == 0."""
    if b == 0:
        return float("nan") if a == 0 else float("inf")
    return (a - b) / b


FIDELITY_COLUMNS = ["hour", "utc_offset_h", "window_start_utc", "event_type", "raw_file_events", "raw_unique_ids",
                    "ch_file_time", "rel_diff_file", "rel_diff_file_unique", "raw_created_in_window",
                    "ch_created_in_window_same_file", "rel_diff_created", "ch_created_in_window_all_files"]


def fidelity_frame(hour: str, raw_counts: dict, ch_same_file: dict, ch_file: dict,
                   ch_all_files: dict | None = None) -> pd.DataFrame:
    ch_all_files = ch_all_files or {}
    has_ids = bool(raw_counts.get("unique_ids"))
    types = sorted(set(raw_counts["all"]) | set(raw_counts["in_hour"]) | set(ch_same_file) | set(ch_file)
                   | set(ch_all_files))
    rows = []
    for t in types + ["_total"]:
        pick = (lambda d: sum(d.values())) if t == "_total" else (lambda d: d.get(t, 0))
        ra, ri, cs, cf, call = (pick(raw_counts["all"]), pick(raw_counts["in_hour"]), pick(ch_same_file),
                                pick(ch_file), pick(ch_all_files))
        ru = pick(raw_counts.get("unique_ids", {})) if has_ids else np.nan
        rows.append({"hour": hour, "utc_offset_h": raw_counts.get("offset_h", 0),
                     "window_start_utc": pd.Timestamp(raw_counts["window_start"]).strftime("%Y-%m-%d %H:%M:%S")
                     if raw_counts.get("window_start") is not None else "",
                     "event_type": t, "raw_file_events": ra, "raw_unique_ids": ru,
                     "ch_file_time": cf, "rel_diff_file": rel_diff(cf, ra),
                     "rel_diff_file_unique": rel_diff(cf, ru) if has_ids else np.nan,
                     "raw_created_in_window": ri, "ch_created_in_window_same_file": cs,
                     "rel_diff_created": rel_diff(cs, ri), "ch_created_in_window_all_files": call})
    return pd.DataFrame(rows, columns=FIDELITY_COLUMNS)


HOURLY_PII_NOTE = ("Raw GH Archive hours contain personal e-mail fields (actor_attributes.email in Timeline-era files, "
                   "payload.commits[].author.email from 2015). They are only counted (event type + created_at + id) "
                   "and deleted after counting unless --keep-hourly; re-download from url and verify sha256.")


def fetch_hour(hour: str, client: Client | None = None, cache_dir: Path = HOURLY_CACHE_DIR,
               manifest_dir: Path = HOURLY_MANIFEST_DIR, keep: bool = False) -> Path:
    parse_hour(hour)
    url = GHARCHIVE_URL.format(hour=hour)
    dest = cache_dir / f"{hour}.json.gz"
    man = manifest_dir / f"{hour}.json.gz.manifest.json"
    cache_dir.mkdir(parents=True, exist_ok=True)
    r = (client or Client()).get(url, stream_to=dest)
    if not dest.exists():
        raise GHArchiveError(f"download failed {url} status={r.status}")
    sha = sha256_file(dest)
    old = json.loads(man.read_text()) if man.exists() else None
    if old is None or old.get("sha256") != sha or old.get("retained") != bool(keep):
        fetched_at = r.fetched_at or datetime.fromtimestamp(dest.stat().st_mtime, timezone.utc).isoformat()
        if old is not None and old.get("sha256") == sha and old.get("fetched_at"):
            fetched_at = old["fetched_at"]          # same bytes: keep the original download time
        manifest_dir.mkdir(parents=True, exist_ok=True)
        man.write_text(json.dumps({"url": url, "sha256": sha, "bytes": dest.stat().st_size, "fetched_at": fetched_at,
                                   "retained": bool(keep),
                                   "local_path": (str(dest.relative_to(ROOT)) if ROOT in dest.parents else str(dest))
                                   if keep else None,
                                   "note": "untrusted raw GH Archive hour. " + HOURLY_PII_NOTE},
                                  indent=2, sort_keys=True))
        write_meta(man, script=SCRIPT + " fidelity", sources=[url], params={"hour": hour, "retained": bool(keep)},
                   notes="Manifest of a raw GH Archive hourly file. " + HOURLY_PII_NOTE)
    return dest


def fidelity_check(hours: list[str] | None = None, ch: ClickHouse | None = None,
                   out_path: Path = FIDELITY_PATH, keep_hourly: bool = False, client: Client | None = None,
                   cache_dir: Path = HOURLY_CACHE_DIR, manifest_dir: Path = HOURLY_MANIFEST_DIR) -> pd.DataFrame:
    ch = ch or backend()
    hours = hours or DEFAULT_FIDELITY_HOURS
    frames, notes = [], {}
    dl = client or Client()
    for hour in hours:
        path = fetch_hour(hour, client=dl, cache_dir=cache_dir, manifest_dir=manifest_dir, keep=keep_hourly)
        try:
            rc = count_hour_file(path, hour)
        finally:
            if not keep_hourly:
                path.unlink(missing_ok=True)       # personal e-mails inside: keep only the manifest (sha256)
        notes[hour] = rc["stats"]
        off = rc["offset_h"]
        q = lambda by: ch.query(f"fidelity_{by}_{hour}", sql_fidelity_hour(hour, by, off), ["event_type", "n"])
        to_d = lambda d: dict(zip(d["event_type"], _to_int(d["n"], "n"))) if len(d) else {}
        frames.append(fidelity_frame(hour, rc, to_d(q("created_same_file")), to_d(q("file_time")),
                                     to_d(q("created_at"))))
    df = pd.concat(frames, ignore_index=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, float_format="%.6g")
    write_meta(out_path, script=SCRIPT + " fidelity", rows=len(df),
               sources=[GHARCHIVE_URL.format(hour=h) for h in hours] + [ch.url],
               inputs=[manifest_dir / f"{h}.json.gz.manifest.json" for h in hours]
               + [Path(q.raw_path) for q in ch.log if q.name.startswith("fidelity_")],
               params={"hours": hours, "raw_parse_stats": notes, "hourly_files_retained": bool(keep_hourly)},
               notes="utc_offset_h = modal (created_at UTC - file label) in hours: Timeline-era (pre-2015) files are "
                     "labelled in US/Pacific local time, so their effective UTC window is label + 7/8 h. "
                     "raw_file_events / raw_unique_ids = objects / distinct ids in the file; ch_file_time = ClickHouse "
                     "rows with file_time = label; raw_created_in_window = file events with created_at in the "
                     "effective UTC hour; ch_created_in_window_same_file = same on ClickHouse (tests created_at "
                     "timezone handling); ch_created_in_window_all_files = ClickHouse created_at in the window from "
                     "any file (informative). rel_diff_* = (ClickHouse - raw) / raw.")
    return df


# ----------------------------------------------------------------------------------------------------------------
# BigQuery alternative backend (NOT run in this session: no GCP credentials)
# ----------------------------------------------------------------------------------------------------------------
BQ_OWNER = "LOWER(REGEXP_EXTRACT(repo.name, r'^([^/]+)/'))"


def _bq_in(logins: list[str]) -> str:
    return "(" + ", ".join(f"'{validate_login(x)}'" for x in logins) + ")"


def _bq_bot(regex: str | None = None) -> str:
    rx = (regex or bots_regex()).replace("\\", "\\\\").replace("'", "\\'")
    return f"NOT REGEXP_CONTAINS(LOWER(actor.login), '{rx}')"


def _bq_suffix(start: str = "201102", end: str | None = None) -> str:
    end = end or data_cutoff().strftime("%Y%m")
    if not (re.fullmatch(r"\d{6}", start) and re.fullmatch(r"\d{6}", end)):
        raise ValueError("table suffixes must be YYYYMM")
    return f"_TABLE_SUFFIX BETWEEN '{start}' AND '{end}'"


def bq_sql(kind: str, orgs: list[str], actors: list[str] | None = None, start: str = "201102",
           end: str | None = None) -> str:
    """BigQuery Standard SQL for the four feature queries against githubarchive.month.* (only needed columns)."""
    T = "`githubarchive.month.*`"
    W = f"{_bq_suffix(start, end)} AND {_bq_bot()}"
    if kind == "span":
        return (f"SELECT {BQ_OWNER} AS owner, MIN(created_at) AS first_event_ts, MAX(created_at) AS last_event_ts, "
                f"COUNT(*) AS n_events FROM {T} WHERE {W} AND {BQ_OWNER} IN {_bq_in(orgs)} GROUP BY owner")
    if kind == "team":
        return (f"SELECT {BQ_OWNER} AS owner, LOWER(actor.login) AS actor, MIN(created_at) AS first_push_ts FROM {T} "
                f"WHERE {W} AND type = 'PushEvent' AND {BQ_OWNER} IN {_bq_in(orgs)} GROUP BY owner, actor")
    if kind == "daily":
        return (f"SELECT {BQ_OWNER} AS owner, DATE(created_at) AS day, COUNTIF(type = 'PushEvent') AS push, "
                f"COUNTIF(type = 'PullRequestEvent') AS pr, COUNTIF(type = 'IssuesEvent') AS issues, "
                f"COUNTIF(type = 'ReleaseEvent') AS release FROM {T} WHERE {W} AND type IN ('PushEvent', "
                f"'PullRequestEvent', 'IssuesEvent', 'ReleaseEvent') AND {BQ_OWNER} IN {_bq_in(orgs)} "
                f"GROUP BY owner, day")
    if kind == "external":
        if not actors:
            raise ValueError("external needs actors")
        act = "JSON_EXTRACT_SCALAR(payload, '$.action')"
        return (f"SELECT LOWER(actor.login) AS actor, DATE_TRUNC(DATE(created_at), MONTH) AS month, "
                f"IF({BQ_OWNER} IN {_bq_in(orgs)}, {BQ_OWNER}, '{EXT_BUCKET}') AS owner_bucket, "
                f"COUNTIF(type = 'PullRequestEvent' AND {act} = 'opened') AS pr_opened, "
                f"COUNTIF(type = 'IssuesEvent' AND {act} = 'opened') AS issues_opened, "
                f"COUNTIF(type = 'PushEvent') AS pushes FROM {T} WHERE {W} AND type IN ('PushEvent', "
                f"'PullRequestEvent', 'IssuesEvent') AND LOWER(actor.login) IN {_bq_in(actors)} "
                f"GROUP BY actor, month, owner_bucket")
    raise ValueError(f"unknown kind {kind!r}")


def bq_guard(total_bytes: int, max_gb: float | None = None) -> int:
    """Hard refusal above the configured budget (GB = 1e9 bytes)."""
    max_gb = float(cfg()["gharchive"]["bigquery_max_gb"] if max_gb is None else max_gb)
    if total_bytes is None or int(total_bytes) < 0:
        raise BigQueryBudgetError("dry run returned no totalBytesProcessed; refusing")
    if int(total_bytes) > max_gb * 1e9:
        raise BigQueryBudgetError(f"query would process {int(total_bytes) / 1e9:.1f} GB > limit {max_gb:g} GB; "
                                  "refusing without explicit approval")
    return int(total_bytes)


def _bq_dry_run_library(sql: str) -> int:
    from google.cloud import bigquery  # lazy: not installed in this session

    job = bigquery.Client().query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    return int(job.total_bytes_processed)


def _bq_dry_run_cli(sql: str) -> int:
    if shutil.which("bq") is None:
        raise GHArchiveError("neither google-cloud-bigquery nor the bq CLI is available")
    out = subprocess.run(["bq", "query", "--use_legacy_sql=false", "--dry_run", "--format=json", sql],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise GHArchiveError(f"bq dry run failed: {out.stderr.strip()[:300]}")
    return int(json.loads(out.stdout)["statistics"]["totalBytesProcessed"])


def _bq_dry_run_default(sql: str) -> int:
    try:
        return _bq_dry_run_library(sql)
    except ImportError:
        return _bq_dry_run_cli(sql)


@dataclass
class BigQueryBackend:
    """Same four queries on BigQuery. Every run() is preceded by a dry run and refused above the GB limit."""
    max_gb: float | None = None
    dry_run_fn: Callable[[str], int] = _bq_dry_run_default
    run_fn: Callable[[str], pd.DataFrame] | None = None

    def dry_run(self, sql: str) -> int:
        n = int(self.dry_run_fn(sql))
        print(f"totalBytesProcessed={n} ({n / 1e9:.2f} GB)")
        return n

    def run(self, sql: str) -> pd.DataFrame:
        bq_guard(self.dry_run(sql), self.max_gb)
        if self.run_fn is not None:
            return self.run_fn(sql)
        from google.cloud import bigquery  # lazy

        # server-side cap as well: the job fails instead of billing more than the configured limit
        max_gb = float(cfg()["gharchive"]["bigquery_max_gb"] if self.max_gb is None else self.max_gb)
        cfg_job = bigquery.QueryJobConfig(maximum_bytes_billed=int(max_gb * 1e9))
        return bigquery.Client().query(sql, job_config=cfg_job).to_dataframe()

    def org_activity_span(self, orgs):
        return self.run(bq_sql("span", normalize_logins(orgs, "org")[0]))

    def team_first_push(self, orgs):
        return self.run(bq_sql("team", normalize_logins(orgs, "org")[0]))

    def org_activity_daily(self, orgs):
        return self.run(bq_sql("daily", normalize_logins(orgs, "org")[0]))

    def external_monthly(self, actors, orgs):
        return self.run(bq_sql("external", normalize_logins(orgs, "org")[0], normalize_logins(actors, "actor")[0]))


# ----------------------------------------------------------------------------------------------------------------
# collection steps (processed outputs)
# ----------------------------------------------------------------------------------------------------------------
def read_orgs_file(path: Path) -> list[str]:
    ok, bad = normalize_logins(Path(path).read_text().splitlines(), "org")
    if bad:
        log.warning("orgs file %s: %d invalid line(s) skipped", path, len(bad))
    return ok


def default_orgs() -> list[str]:
    logins = []
    p = PROCESSED / "entity_github_org.parquet"
    if p.exists():
        logins += pd.read_parquet(p, columns=["org_login"])["org_login"].dropna().tolist()
    p = PROCESSED / "github_org_sample.parquet"
    if p.exists():
        s = pd.read_parquet(p)
        if "in_pool" in s.columns:
            s = s[s["in_pool"].fillna(False).astype(bool)]
        logins += s["login"].dropna().tolist()
    if not logins:
        raise GHArchiveError("no orgs: pass --orgs FILE or build entity_github_org / github_org_sample first")
    return normalize_logins(logins, "org")[0]


def _raw_inputs(ch: ClickHouse, name: str, since: int) -> list[Path]:
    return sorted({Path(q.raw_path) for q in ch.log[since:] if q.name == name})


def _params(orgs: list[str], **kw) -> dict:
    return {"n_orgs": len(orgs), "orgs_sha256": hashlib.sha256("\n".join(orgs).encode()).hexdigest(),
            "bots_regex": bots_regex(), "chunk": CHUNK, "max_rows": MAX_ROWS, **kw}


def _save(df: pd.DataFrame, path: Path, step: str, inputs: list[Path], params: dict, notes: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    write_meta(path, script=f"{SCRIPT} {step}", inputs=inputs, params=params, rows=len(df),
               sources=[cfg()["gharchive"]["clickhouse_url"]], notes=notes)
    log.info("wrote %s rows=%d", path, len(df))


def run_span(orgs: list[str], ch: ClickHouse | None = None, out: Path = SPAN_PATH) -> pd.DataFrame:
    ch = ch or backend()
    k = len(ch.log)
    df = ch.org_activity_span(orgs)[SPAN_COLUMNS]
    _save(df, out, "span", _raw_inputs(ch, "span", k), _params(orgs),
          "All event types on repos owned by the org (owner = repo_name before '/', lower-case), bots excluded. "
          "Orgs without any event are absent. No repo/org id in github_events: renamed orgs lose history.")
    return df


def run_team(orgs: list[str], ch: ClickHouse | None = None, out: Path = TEAM_PATH) -> pd.DataFrame:
    ch = ch or backend()
    k = len(ch.log)
    team = ch.team_first_push(orgs)
    df = team_to_processed(team) if len(team) else _empty(TEAM_COLUMNS)
    _save(df, out, "team", _raw_inputs(ch, "team", k), _params(orgs),
          "Team = actors with >= 1 PushEvent to an org-owned repo; first_push_ts = min(created_at). "
          "actor_hash = sha256(lower(login))[:16]; raw logins only in data/raw/gharchive/team_*.tsv.gz (inputs).")
    return df


def run_activity(orgs: list[str], ch: ClickHouse | None = None, out: Path = DAILY_PATH) -> pd.DataFrame:
    ch = ch or backend()
    k = len(ch.log)
    df = ch.org_activity_daily(orgs)[DAILY_COLUMNS]
    _save(df, out, "activity", _raw_inputs(ch, "daily", k), _params(orgs),
          "Daily (UTC) counts of PushEvent, PullRequestEvent, IssuesEvent, ReleaseEvent rows (all actions) on "
          "org-owned repos, bots excluded. Days without events are absent (= 0).")
    return df


def team_logins_from_raw(team_path: Path = TEAM_PATH) -> pd.DataFrame:
    """Resolve gh_team actor hashes to logins via the raw team TSVs recorded as inputs in gh_team's meta."""
    meta_p = team_path.with_name(team_path.name + ".meta.json")
    if not team_path.exists() or not meta_p.exists():
        raise GHArchiveError(f"{team_path} (+ meta) not found: run `team` first")
    team = pd.read_parquet(team_path)
    raws = [ROOT / i["path"] if not Path(i["path"]).is_absolute() else Path(i["path"])
            for i in json.loads(meta_p.read_text())["inputs"]]
    frames = []
    for p in raws:
        with gzip.open(p, "rt") as f:
            frames.append(frame_team(parse_tsv(f.read(), ["owner", "actor", "first_push_ts"])))
    lg = (pd.concat(frames, ignore_index=True)[["owner", "actor_login"]].drop_duplicates() if frames
          else pd.DataFrame({"owner": pd.Series(dtype=object), "actor_login": pd.Series(dtype=object)}))
    lg["actor_hash"] = lg["actor_login"].map(actor_hash)
    keep = set(zip(team["owner"], team["actor_hash"]))
    lg = lg[pd.Series([(o, h) in keep for o, h in zip(lg["owner"], lg["actor_hash"])], index=lg.index, dtype=bool)]
    missing = len(keep) - len(set(zip(lg["owner"], lg["actor_hash"])))
    if missing:
        raise GHArchiveError(f"{missing} gh_team rows could not be resolved from the raw team TSVs")
    return lg.sort_values(["owner", "actor_login"]).reset_index(drop=True)


def run_external(orgs: list[str] | None = None, ch: ClickHouse | None = None, team_path: Path = TEAM_PATH,
                 out: Path = EXT_PATH) -> pd.DataFrame:
    ch = ch or backend()
    k = len(ch.log)
    lg = team_logins_from_raw(team_path)
    if orgs:
        lg = lg[lg["owner"].isin(set(orgs))]
    bucket_orgs = sorted(lg["owner"].unique())
    actors = sorted(lg["actor_login"].unique())
    ext = ch.external_monthly(actors, bucket_orgs) if actors else _empty(["actor_login"])
    df = external_to_processed(ext) if len(ext) else _empty(EXT_COLUMNS)
    meta_in = [team_path] + _raw_inputs(ch, "ext", k)
    _save(df, out, "external", meta_in, _params(bucket_orgs, n_actors=len(actors)),
          "Per team actor x UTC month x owner_bucket: PullRequestEvent(opened), IssuesEvent(opened), PushEvent rows. "
          "owner_bucket = owner if owner is an org with a team in gh_team, else '_ext' (any other owner is external "
          "to every team actor). Downstream excludes the actor's own org. All months kept; windows are applied "
          "in snapshots (12 full calendar months ending at the month-start as_of).")
    return df


# ----------------------------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------------------------
def _print_log(ch: ClickHouse):
    if not ch.log:
        return
    df = pd.DataFrame([q.__dict__ for q in ch.log])
    print(f"queries={len(df)} rows={int(df['rows'].sum())} seconds={df['seconds'].sum():.1f} "
          f"cache_hits={int(df['from_cache'].sum())}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.sources.gharchive", description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", default="all",
                    choices=["span", "team", "activity", "external", "all", "schema-check", "fidelity", "bq-dry-run"])
    ap.add_argument("--orgs", type=Path, help="file with one org login per line")
    ap.add_argument("--hours", nargs="+", default=None, help="fidelity hours YYYY-MM-DD-H (UTC)")
    ap.add_argument("--keep-hourly", action="store_true",
                    help="fidelity: keep the raw hourly files (they contain personal e-mails; default: delete)")
    a = ap.parse_args(argv)

    if a.cmd == "schema-check":
        df = schema_check()
        flagged = df[df["flag"] != ""]
        print(f"wrote {SCHEMA_PATH} rows={len(df)} flagged={len(flagged)}")
        _print_log(backend())
        return 0
    if a.cmd == "fidelity":
        df = fidelity_check(a.hours, keep_hourly=a.keep_hourly)
        print(df.to_string(index=False))
        return 0
    if a.cmd == "bq-dry-run":
        orgs = read_orgs_file(a.orgs) if a.orgs else default_orgs()
        bq = BigQueryBackend()
        for kind in ("span", "team", "daily"):
            n = bq.dry_run(bq_sql(kind, orgs[:CHUNK]))
            print(kind, "within budget" if n <= float(cfg()["gharchive"]["bigquery_max_gb"]) * 1e9 else "REFUSED")
        return 0

    try:
        orgs = read_orgs_file(a.orgs) if a.orgs else (None if a.cmd == "external" else default_orgs())
    except GHArchiveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ch = backend()
    t0 = time.monotonic()
    if a.cmd in ("span", "all"):
        df = run_span(orgs, ch)
        print(f"span: {SPAN_PATH} rows={len(df)}")
    if a.cmd in ("team", "all"):
        df = run_team(orgs, ch)
        print(f"team: {TEAM_PATH} rows={len(df)} orgs_with_team={df['owner'].nunique() if len(df) else 0}")
    if a.cmd in ("activity", "all"):
        df = run_activity(orgs, ch)
        print(f"activity: {DAILY_PATH} rows={len(df)}")
    if a.cmd in ("external", "all"):
        df = run_external(orgs if a.cmd == "external" else None, ch)
        print(f"external: {EXT_PATH} rows={len(df)}")
    _print_log(ch)
    print(f"elapsed={time.monotonic() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
