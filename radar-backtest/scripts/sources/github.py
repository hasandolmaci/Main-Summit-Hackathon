"""GitHub API collector: org lookup (GraphQL), staged entity -> org search, outcome-blind org sample (REST).

A) lookup_orgs(logins)
   Batched GraphQL (POST /graphql) with aliases `o<i>: organization(login: "...") {login databaseId createdAt
   websiteUrl name}`. Nonexistent logins come back as null + a NOT_FOUND error -> exists=False. Batch-level
   failures (no data, path-less errors, rate limiting) are evicted from the HTTP cache, retried, then split in halves.
   Every response is persisted under data/raw/github_api/org_lookup/*.jsonl.gz; the consolidated table is
   data/processed/github_orgs.parquet (merged, dedupe by lower-case login; rows with a lookup error are not kept so
   they are retried next time). Logins already in that table are not looked up again unless refresh=True.

B) find_org(entities)
   Staged deterministic search, identical for every entity (positives and controls):
     tier 1 = exact stem / name slug (with and without hyphens)
     tier 2 = fixed affixes (hq, -hq, app, -app, io, -io, labs, -labs, inc, -inc, ai, -ai, dev, -dev, get, use, try)
     tier 3 = all remaining scripts.lib.domains.login_candidates
   An org is accepted iff scripts.lib.domains.login_rule(...) is True. Search stops at the first tier with >= 1
   accepted org; ties -> earliest createdAt, then login asc. Output data/processed/entity_github_org.parquet
   (unresolved entities absent) + data/processed/entity_github_org_search.parquet (one row per searched entity, so
   "searched, no org" can be told apart from "never searched"). An entity with an unanswered candidate lookup in a
   tier is held there (no match, no lower tier; note lookup_error_tier<N>) so results never depend on transient
   failures; rerunning find retries it.

C) sample()
   Outcome-blind org sample (pool channel a). No label data is read here.
   1. id -> date map: binary search, for every Jan 1 in cfg pool.org_sample_years (+ the following Jan 1), of the
      smallest account id created on/after that date (GET /user/<id>; deleted ids -> GET /users?since=<id-1>
      finds the next existing account). -> data/processed/github_id_year_bounds.csv
      /user/<id> responses are fetched WITHOUT the HTTP cache and only (id, type, created_at) is kept, so no personal
      profile data (emails etc.) is written to disk.
   2. Per creation year Y: draw pages random `since` values uniformly in [lo_Y, hi_Y) with
      numpy.default_rng(seed + Y); fetch GET /organizations?since=<s>&per_page=100 once each.
      A page covers the id interval (s, last id on page] (all listed orgs in it are on the page); a short page
      (< per_page orgs: GitHub hides some accounts) still covers only up to its last id (counted as short_pages).
      covered_Y = |union of page intervals  intersect  [lo_Y, hi_Y)|; density_Y = sampled_orgs_Y / covered_Y;
      est_total_Y = density_Y * (hi_Y - lo_Y); inclusion_prob = sampled_orgs_Y / est_total_Y (= covered_Y/(hi_Y-lo_Y)).
   3. Enrich with lookup_orgs; 4. pool rule: website_domain not None and login_rule(login, website_url,
      website_domain, display_name). -> data/processed/github_org_sample.parquet

Point in time: org created_at is the public timestamp of the org. websiteUrl / name are CURRENT values, observed at
fetched_at (kept in every table) - a known caveat of the design.

Token: env GITHUB_TOKEN only (one token, no rotation). Never written to disk (the HTTP cache does not store headers).

CLI
    .venv/bin/python -m scripts.sources.github sample [--pages N] [--years 2015 2016]
    .venv/bin/python -m scripts.sources.github find --input entities.csv     # columns entity_id[,names]
    .venv/bin/python -m scripts.sources.github lookup LOGIN [LOGIN ...]
    .venv/bin/python -m scripts.sources.github ratelimit
  `names` in the find CSV: "Name A|Name B" or a JSON list. No subcommand = `sample` (Makefile target `orgs`).
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from scripts.lib.config import PROCESSED, RAW, cfg, get_logger, seed as cfg_seed
from scripts.lib.domains import company_domain, domain_stem, login_candidates, login_rule, name_slug
from scripts.lib.http import CachedResponse, Client, _key
from scripts.lib.meta import write_meta

log = get_logger("github")
SCRIPT = "scripts/sources/github.py"

API = "https://api.github.com"
GRAPHQL_URL = API + "/graphql"

RAW_DIR = RAW / "github_api"
LOOKUP_RAW = RAW_DIR / "org_lookup"
PAGES_RAW = RAW_DIR / "org_pages"
PROBES_RAW = RAW_DIR / "id_probes"
ORGS_PATH = PROCESSED / "github_orgs.parquet"
ENTITY_ORG_PATH = PROCESSED / "entity_github_org.parquet"
SEARCH_LOG_PATH = PROCESSED / "entity_github_org_search.parquet"
SAMPLE_PATH = PROCESSED / "github_org_sample.parquet"
BOUNDS_PATH = PROCESSED / "github_id_year_bounds.csv"

BATCH_SIZE = 100          # aliases per GraphQL query (plan: 100; failures split the batch automatically)
PER_PAGE = 100            # REST /organizations page size (max 100)
GQL_ATTEMPTS = 4
ORG_FIELDS = "login databaseId createdAt websiteUrl name"
ORG_COLUMNS = ["query_login", "login", "exists", "database_id", "created_at", "website_url", "website_domain",
               "display_name", "fetched_at", "lookup_error"]
MATCH_METHOD = "website_equals_domain+login_rule"
MATCH_CONFIDENCE = "high"
ENTITY_ORG_COLUMNS = ["entity_id", "org_login", "org_database_id", "org_created_at", "tier", "match_method",
                      "match_confidence", "n_accepted_in_tier"]
SEARCH_LOG_COLUMNS = ["entity_id", "entity_domain", "names", "n_cand_tier1", "n_cand_tier2", "n_cand_tier3",
                      "tiers_searched", "resolved", "n_lookup_errors", "note"]
SAMPLE_COLUMNS = ["login", "database_id", "created_at", "created_year", "website_url", "website_domain",
                  "display_name", "in_pool", "inclusion_prob", "sample_page_since", "sample_year", "fetched_at"]
BOUNDS_COLUMNS = ["year", "boundary_utc", "first_id", "first_id_created_at", "first_id_type", "n_probes"]
TIER2_SUFFIXES = ("hq", "-hq", "app", "-app", "io", "-io", "labs", "-labs", "inc", "-inc", "ai", "-ai", "dev", "-dev")
TIER2_PREFIXES = ("get", "use", "try")


class GitHubAccessError(RuntimeError):
    """The API refused the request for a reason retrying cannot fix (bad token, blocked path, ...)."""


class GitHubUnavailableError(GitHubAccessError):
    """GraphQL kept failing for many consecutive batches (outage); the run stops (CLI exit code 3) and can be rerun."""


MAX_FAILED_STREAK = 20     # consecutive failed GraphQL batches before giving up (one bad login costs ~8 at size 100)
_FAILED_STREAK = [0]


def _sleep(seconds: float):  # indirection so tests never sleep
    time.sleep(seconds)


@dataclass
class RunStats:
    gql_requests: int = 0
    gql_network: int = 0
    logins_requested: int = 0
    logins_looked_up: int = 0
    logins_reused: int = 0
    rest_requests: int = 0
    rest_network: int = 0
    gql_seconds: float = 0.0
    started: float = field(default_factory=time.monotonic)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "started"}
        d["wall_seconds"] = round(time.monotonic() - self.started, 2)
        d["gql_seconds"] = round(self.gql_seconds, 2)
        d["lookups_per_sec"] = round(self.logins_looked_up / self.gql_seconds, 1) if self.gql_seconds else None
        return d


STATS = RunStats()


# ----------------------------------------------------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------------------------------------------------
def make_client() -> Client:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    else:
        log.warning("GITHUB_TOKEN is not set; GitHub GraphQL requires a token")
    return Client(headers=headers)


def _json_or_none(text: str):
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _message(text: str) -> str:
    j = _json_or_none(text)
    if isinstance(j, dict) and j.get("message"):
        return str(j["message"])[:300]
    return (text or "")[:300]


def _check_access(r: CachedResponse, what: str):
    """Raise for refusals that retrying cannot fix (Client already retried rate-limit 403/429 and 5xx)."""
    if r.status in (401, 403) and "rate limit" not in (r.text or "").lower():
        raise GitHubAccessError(f"{what}: HTTP {r.status}: {_message(r.text)}")
    if r.status == -1:
        raise GitHubAccessError(f"{what}: no connection after retries")


def _evict(client, method: str, url: str, params: dict | None = None, json_body: dict | None = None):
    """Drop a cached response (e.g. a 200 GraphQL reply that only carries an error) so a retry hits the network."""
    cache_path = getattr(client, "_cache_path", None)
    if cache_path is None:
        return
    body = json.dumps(json_body, sort_keys=True) if json_body is not None else None
    p = cache_path(urlparse(url).netloc, _key(method, url, params, body))
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def rate_limit(client) -> dict | None:
    """Current core + graphql rate-limit status (GET /rate_limit does not count against the limit)."""
    try:
        r = client.get(API + "/rate_limit", use_cache=False)
    except Exception:  # pragma: no cover - diagnostics only
        return None
    j = _json_or_none(r.text) if r.status == 200 else None
    if not isinstance(j, dict):
        return None
    res = j.get("resources", {})
    return {k: {f: res.get(k, {}).get(f) for f in ("limit", "used", "remaining", "reset")} for k in ("core", "graphql")}


def _wait_for_reset(client, resource: str, fallback: float):
    rl = rate_limit(client)
    info = (rl or {}).get(resource) or {}
    if info.get("remaining") == 0 and info.get("reset"):
        wait = max(1, int(info["reset"]) - int(time.time()) + 2)
        log.warning("%s rate limit exhausted; sleeping %ds until reset", resource, wait)
        _sleep(wait)
    else:
        _sleep(fallback)


class RawLog:
    """Append-only gz JSONL log of raw responses; written to <name>.part and renamed (+ .meta.json) on close."""

    def __init__(self, directory: Path, prefix: str, params: dict | None = None, notes: str | None = None):
        self.dir = Path(directory)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = self.dir / f"{prefix}_{stamp}.jsonl.gz"
        self.tmp = Path(str(self.path) + ".part")
        self.params = params or {}
        self.notes = notes
        self.lines = 0
        self.sources: set[str] = set()
        self._fh = None

    def write(self, rec: dict, source: str | None = None):
        if self._fh is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = gzip.open(self.tmp, "wt", encoding="utf-8")
        self._fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        self.lines += 1
        if source:
            self.sources.add(source)

    def close(self) -> Path | None:
        if self._fh is None:
            return None
        self._fh.close()
        self._fh = None
        self.tmp.rename(self.path)
        write_meta(self.path, script=SCRIPT, params=self.params, rows=self.lines, sources=sorted(self.sources),
                   notes=self.notes)
        return self.path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()  # keep whatever was fetched, also on errors
        return False


def read_raw(directory: Path) -> Iterable[dict]:
    for p in sorted(Path(directory).glob("*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


# ----------------------------------------------------------------------------------------------------------------------
# A) GraphQL org lookup
# ----------------------------------------------------------------------------------------------------------------------
def build_query(logins: list[str]) -> str:
    parts = [f"o{i}: organization(login: {json.dumps(str(l))}) {{ {ORG_FIELDS} }}" for i, l in enumerate(logins)]
    return "query { " + " ".join(parts) + " rateLimit { cost } }"


def classify_payload(payload, n: int) -> str:
    """'ok' when every alias is answered (an org or a per-alias error), else 'retry'."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return "retry"
    data = payload["data"]
    for e in payload.get("errors") or []:
        if not e.get("path"):
            return "retry"
        if e.get("type") in ("RATE_LIMITED", "SERVICE_UNAVAILABLE", "TIMEOUT", "INTERNAL"):
            return "retry"
    if any(f"o{i}" not in data for i in range(n)):
        return "retry"
    return "ok"


def _is_rate_limited(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    return any((e.get("type") == "RATE_LIMITED") or ("rate limit" in str(e.get("message", "")).lower())
               for e in payload.get("errors") or [])


def _ts(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return pd.NaT
    return pd.to_datetime(x, utc=True)


def _org_row(query_login: str, node: dict | None, fetched_at, error: str | None = None) -> dict:
    if node is not None:
        url = node.get("websiteUrl")
        return {"query_login": query_login, "login": node.get("login"), "exists": True,
                "database_id": node.get("databaseId"), "created_at": _ts(node.get("createdAt")),
                "website_url": url, "website_domain": company_domain(url, (node.get("login") or "", node.get("name") or "")), "display_name": node.get("name"),
                "fetched_at": _ts(fetched_at), "lookup_error": None}
    return {"query_login": query_login, "login": query_login, "exists": None if error else False,
            "database_id": None, "created_at": pd.NaT, "website_url": None, "website_domain": None,
            "display_name": None, "fetched_at": _ts(fetched_at), "lookup_error": error}


def parse_org_batch(logins: list[str], payload: dict, fetched_at) -> list[dict]:
    """One row per requested login. NOT_FOUND -> exists=False; other per-alias errors -> lookup_error, exists=NA."""
    data = payload.get("data") or {}
    alias_err: dict[str, str] = {}
    for e in payload.get("errors") or []:
        path = e.get("path") or []
        if path:
            alias_err.setdefault(str(path[0]), str(e.get("type") or "ERROR"))
    rows = []
    for i, q in enumerate(logins):
        a = f"o{i}"
        node = data.get(a)
        if isinstance(node, dict) and node.get("login"):
            rows.append(_org_row(q, node, fetched_at))
        elif alias_err.get(a) == "NOT_FOUND":
            rows.append(_org_row(q, None, fetched_at))
        else:
            rows.append(_org_row(q, None, fetched_at, error=alias_err.get(a) or ("NULL" if a in data else "MISSING")))
    return rows


def org_frame(rows: list[dict] | pd.DataFrame) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame.from_records(rows, columns=ORG_COLUMNS)
    for c in ORG_COLUMNS:
        if c not in df:
            df[c] = None
    df = df[ORG_COLUMNS].copy()
    df["exists"] = df["exists"].astype("boolean")
    df["database_id"] = pd.to_numeric(df["database_id"], errors="coerce").astype("Int64")
    for c in ("created_at", "fetched_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    for c in ("query_login", "login", "website_url", "website_domain", "display_name", "lookup_error"):
        df[c] = df[c].astype(object).where(df[c].notna(), None)
    return df.reset_index(drop=True)


def _dedupe_logins(logins: Iterable) -> list[str]:
    seen, out = set(), []
    for l in logins:
        if not isinstance(l, str):
            continue
        k = l.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def load_orgs(path: Path = ORGS_PATH) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return org_frame([])
    return org_frame(pd.read_parquet(path))


def _graphql_batch(client, batch: list[str], raw: RawLog) -> tuple[CachedResponse | None, dict | None, str]:
    body = {"query": build_query(batch)}
    r, payload = None, None
    for attempt in range(GQL_ATTEMPTS):
        t0 = time.monotonic()
        r = client.post(GRAPHQL_URL, json_body=body)
        STATS.gql_seconds += time.monotonic() - t0
        STATS.gql_requests += 1
        STATS.gql_network += 0 if r.from_cache else 1
        _check_access(r, "GitHub GraphQL")
        payload = _json_or_none(r.text)
        outcome = classify_payload(payload, len(batch)) if r.status == 200 else "retry"
        raw.write({"kind": "graphql_org_lookup", "logins": batch, "status": r.status, "fetched_at": r.fetched_at,
                   "from_cache": r.from_cache, "outcome": outcome, "attempt": attempt,
                   "response": payload if payload is not None else (r.text or "")[:2000]}, source=GRAPHQL_URL)
        if outcome == "ok":
            _FAILED_STREAK[0] = 0
            if any(e.get("type") != "NOT_FOUND" for e in payload.get("errors") or []):
                # per-alias errors (FORBIDDEN, ...) are not persisted; drop the cached reply so a rerun really asks
                # GitHub again instead of replaying the same error (the raw log above keeps this reply)
                _evict(client, "POST", GRAPHQL_URL, json_body=body)
            return r, payload, "ok"
        _evict(client, "POST", GRAPHQL_URL, json_body=body)
        log.warning("GraphQL batch of %d not ok (status=%s, attempt %d)", len(batch), r.status, attempt + 1)
        if _is_rate_limited(payload) or r.status in (403, 429):
            _wait_for_reset(client, "graphql", fallback=60)
        else:
            _sleep(min(60, 2 ** (attempt + 2)))
    _FAILED_STREAK[0] += 1
    if _FAILED_STREAK[0] >= MAX_FAILED_STREAK:
        # splitting isolates a bad login in ~log2(batch) consecutive failures; far more than that means GraphQL
        # itself is down. Stop instead of bisecting every batch for hours (answered batches stay in the HTTP cache).
        _FAILED_STREAK[0] = 0
        raise GitHubUnavailableError(f"GitHub GraphQL: {MAX_FAILED_STREAK} consecutive batches failed after "
                                     f"{GQL_ATTEMPTS} attempts each (last status {r.status if r else None}); rerun later")
    return r, payload, "failed"


def _lookup_batch(client, batch: list[str], raw: RawLog) -> list[dict]:
    r, payload, outcome = _graphql_batch(client, batch, raw)
    if outcome == "ok":
        return parse_org_batch(batch, payload, r.fetched_at)
    if len(batch) == 1:
        log.error("lookup failed for %s", batch[0])
        return [_org_row(batch[0], None, r.fetched_at if r else None, error="BATCH_FAILED")]
    mid = len(batch) // 2
    return _lookup_batch(client, batch[:mid], raw) + _lookup_batch(client, batch[mid:], raw)


def lookup_orgs(logins: Iterable[str], client=None, batch_size: int = BATCH_SIZE, orgs_path: Path | None = ORGS_PATH,
                raw_dir: Path = LOOKUP_RAW, refresh: bool = False, persist: bool = True) -> pd.DataFrame:
    """Look up GitHub orgs by login. Returns one row per distinct (lower-cased) requested login, in input order.

    Columns: query_login, login, exists (nullable bool; NA = lookup error), database_id, created_at (UTC),
    website_url, website_domain (= company_domain), display_name, fetched_at (UTC, when websiteUrl was observed),
    lookup_error.
    """
    wanted = _dedupe_logins(logins)
    STATS.logins_requested += len(wanted)
    known = load_orgs(orgs_path) if (orgs_path is not None and not refresh) else org_frame([])
    known = known[known["lookup_error"].isna()]
    known_keys = set(known["query_login"])
    todo = sorted(k for k in wanted if k not in known_keys)
    STATS.logins_reused += len(wanted) - len(todo)
    rows: list[dict] = []
    if todo:
        client = client or make_client()
        params = {"n_logins": len(todo), "batch_size": batch_size, "fields": ORG_FIELDS}
        with RawLog(raw_dir, "lookup", params=params,
                    notes="GraphQL organization(login) responses, one line per POST (incl. retries).") as raw:
            for i in range(0, len(todo), batch_size):
                rows += _lookup_batch(client, todo[i:i + batch_size], raw)
                STATS.logins_looked_up += min(batch_size, len(todo) - i)
                if (i // batch_size) % 20 == 19:
                    log.info("looked up %d / %d logins", i + batch_size, len(todo))
    new = org_frame(rows)
    n_err = int(new["lookup_error"].notna().sum())
    if n_err:
        log.warning("%d logins had lookup errors (not persisted; retried next run)", n_err)
    if persist and orgs_path is not None and len(new):
        _merge_orgs(new[new["lookup_error"].isna()], Path(orgs_path))
    if len(new) and len(known):
        allrows = pd.concat([known, new], ignore_index=True)
    else:
        allrows = new if len(new) else known
    allrows = org_frame(allrows).drop_duplicates("query_login", keep="last").set_index("query_login", drop=False)
    return org_frame(allrows.reindex(wanted).reset_index(drop=True))


def _merge_orgs(new: pd.DataFrame, path: Path):
    if not len(new):
        return
    old = load_orgs(path)
    df = pd.concat([old, new], ignore_index=True) if len(old) else new
    df = org_frame(df).drop_duplicates("query_login", keep="last").sort_values("query_login").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    write_meta(path, script=SCRIPT, rows=len(df), sources=[GRAPHQL_URL], inputs=[],
               params={"exists": int(df["exists"].fillna(False).sum()), "not_found": int((~df["exists"].fillna(True)).sum()),
                       "with_website_domain": int(df["website_domain"].notna().sum())},
               notes="Consolidated GitHub org lookups (dedupe by lower-case login, newest wins). created_at = org "
                     "creation (point-in-time); website_url/display_name are current values observed at fetched_at.")


# ----------------------------------------------------------------------------------------------------------------------
# B) staged entity -> org search
# ----------------------------------------------------------------------------------------------------------------------
def _names_tuple(x) -> tuple:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ()
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return ()
        if s.startswith("["):
            try:
                x = json.loads(s)
            except ValueError:
                x = [s]
        else:
            x = s.split("|")
    if isinstance(x, np.ndarray):
        x = x.tolist()
    return tuple(sorted({str(n).strip() for n in x if isinstance(n, str) and n.strip()}))


def tier_candidates(entity_domain: str, names: tuple = ()) -> dict[int, list[str]]:
    """Disjoint candidate tiers whose union is the login_candidates set used by login_rule."""
    stem = domain_stem(entity_domain)
    allc = set(login_candidates(stem, None))
    for n in names:
        allc |= set(login_candidates(None, n))
    bases = []
    for b in [stem] + [name_slug(n) for n in names]:
        b = (b or "").lower().strip("-")
        if b:
            bases += [b, b.replace("-", "")]
    t1 = set(bases) & allc
    t2 = ({b + s for b in bases for s in TIER2_SUFFIXES} | {p + b for b in bases for p in TIER2_PREFIXES}) & allc
    t2 -= t1
    t3 = allc - t1 - t2
    return {1: sorted(t1), 2: sorted(t2), 3: sorted(t3)}


def _prep_entities(entities) -> list[tuple[str, tuple]]:
    df = entities if isinstance(entities, pd.DataFrame) else pd.DataFrame(entities)
    if "entity_id" not in df:
        raise ValueError("entities need an entity_id column")
    names = df["names"] if "names" in df else pd.Series([()] * len(df), index=df.index)
    merged: dict[str, set] = {}
    for eid, nm in zip(df["entity_id"], names):
        if not isinstance(eid, str) or not eid.strip():
            continue
        merged.setdefault(eid.strip().lower(), set()).update(_names_tuple(nm))
    return [(e, tuple(sorted(merged[e]))) for e in sorted(merged)]


def _created_key(ts) -> tuple:
    return (1, 0) if ts is None or pd.isna(ts) else (0, pd.Timestamp(ts).value)


def find_org(entities, client=None, lookup: Callable[[list[str]], pd.DataFrame] | None = None,
             out_path: Path | None = ENTITY_ORG_PATH, search_log_path: Path | None = SEARCH_LOG_PATH,
             persist: bool = True, **lookup_kw) -> pd.DataFrame:
    """Staged org search for entities (entity_id = registrable domain, optional names). Returns matched rows only."""
    ents = _prep_entities(entities)
    if lookup is None:
        def lookup(ls):  # noqa: E306
            return lookup_orgs(ls, client=client, **lookup_kw)
    info: dict[str, dict] = {}
    unresolved: dict[str, tuple[str, tuple, dict]] = {}
    for eid, names in ents:
        dom = company_domain(eid, names)
        rec = {"entity_id": eid, "entity_domain": dom, "names": "|".join(names), "n_cand_tier1": 0,
               "n_cand_tier2": 0, "n_cand_tier3": 0, "tiers_searched": 0, "resolved": False,
               "n_lookup_errors": 0, "note": None}
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
        log.info("find_org tier %d: %d unresolved entities, %d logins", tier, len(unresolved), len(need))
        orgs = org_frame(lookup(need))
        by_key = {r["query_login"]: r for r in orgs.to_dict("records")}
        for eid in sorted(unresolved):
            dom, names, tiers = unresolved[eid]
            info[eid]["tiers_searched"] = tier
            accepted, n_err = [], 0
            for l in tiers[tier]:
                o = by_key.get(l)
                if o is None or o["exists"] is pd.NA or o["exists"] is None:
                    n_err += 1
                    continue
                if bool(o["exists"]) and login_rule(o["login"], o["website_url"], dom, o["display_name"],
                                                    extra_names=names):
                    accepted.append(o)
            info[eid]["n_lookup_errors"] += n_err
            if n_err:
                # An unanswered candidate in this tier could be the (earliest) accepted org: emitting a match or
                # descending to a lower tier would give a result that depends on transient failures. Hold the
                # entity (absent from the output, flagged in the search log); a rerun retries it.
                info[eid]["note"] = ";".join(x for x in (info[eid]["note"], f"lookup_error_tier{tier}") if x)
                held.add(eid)
                continue
            if accepted:
                best = min(accepted, key=lambda o: (_created_key(o["created_at"]), str(o["login"]).lower()))
                matches.append({"entity_id": eid, "org_login": best["login"], "org_database_id": best["database_id"],
                                "org_created_at": best["created_at"], "tier": tier, "match_method": MATCH_METHOD,
                                "match_confidence": MATCH_CONFIDENCE, "n_accepted_in_tier": len(accepted)})
                info[eid]["resolved"] = True
        unresolved = {e: v for e, v in unresolved.items() if not info[e]["resolved"] and e not in held}

    out = _entity_frame(matches)
    search = pd.DataFrame.from_records(list(info.values()), columns=SEARCH_LOG_COLUMNS)
    log.info("find_org: %d entities, %d matched (%s)", len(ents), len(out),
             out["tier"].value_counts().sort_index().to_dict() if len(out) else {})
    if held:
        log.warning("find_org: %d entities held back because a candidate lookup failed (note lookup_error_tier<N> in "
                    "the search log); rerun find for them", len(held))
    if persist:
        searched = set(info)
        if out_path is not None:
            _merge_write(out, Path(out_path), searched, ENTITY_ORG_COLUMNS, _entity_frame,
                         notes="Entity -> GitHub org via staged login_rule search (tier 1 exact, 2 affixes, 3 rest); "
                               "unresolved entities are absent. org_created_at is point-in-time; the websiteUrl used "
                               "for matching is the current value.")
        if search_log_path is not None:
            _merge_write(search, Path(search_log_path), searched, SEARCH_LOG_COLUMNS, lambda d: d,
                         notes="One row per entity searched by find_org (resolved or not).")
    return out


def _entity_frame(rows) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame.from_records(rows, columns=ENTITY_ORG_COLUMNS)
    df["org_database_id"] = pd.to_numeric(df["org_database_id"], errors="coerce").astype("Int64")
    df["org_created_at"] = pd.to_datetime(df["org_created_at"], utc=True)
    df["tier"] = df["tier"].astype("int64")
    df["n_accepted_in_tier"] = df["n_accepted_in_tier"].astype("int64")
    return df[ENTITY_ORG_COLUMNS].sort_values("entity_id").reset_index(drop=True)


def _merge_write(new: pd.DataFrame, path: Path, replaced: set, columns: list, norm, notes: str):
    """Keep rows of other entities from an earlier run; rows of the entities searched now are replaced."""
    if path.exists():
        old = pd.read_parquet(path)
        old = old[~old["entity_id"].isin(replaced)]
        df = pd.concat([old, new], ignore_index=True) if len(old) else new
    else:
        df = new
    df = norm(df[columns]).sort_values("entity_id").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    write_meta(path, script=SCRIPT, rows=len(df), sources=[GRAPHQL_URL], inputs=[ORGS_PATH] if ORGS_PATH.exists() else [],
               params={"entities_searched_this_run": len(replaced)}, notes=notes)


# ----------------------------------------------------------------------------------------------------------------------
# C) outcome-blind org sample
# ----------------------------------------------------------------------------------------------------------------------
class IdProber:
    """first_at_or_after(x) -> (id, created_at, type) of the first existing account with id >= x, or None.

    GET /user/<id> is fetched without the HTTP cache; only id/type/created_at are kept (raw log + memo), so no
    personal profile data reaches disk. Deleted / hidden ids (404) -> GET /users?since=<x-1>&per_page=1 gives the next
    existing account id (list items carry no emails).
    """

    def __init__(self, client, raw: RawLog, memo_dir: Path | None = None):
        self.client = client
        self.raw = raw
        self.user: dict[int, tuple | None] = {}
        self.calls = 0
        if memo_dir is not None and Path(memo_dir).exists():
            for rec in read_raw(memo_dir):
                if rec.get("kind") == "user" and rec.get("status") in (200, 404):
                    a = rec.get("account")
                    self.user[int(rec["id"])] = (int(a["id"]), _ts(a["created_at"]), a.get("type")) if a else None

    def _get_user(self, x: int):
        if x in self.user:
            return self.user[x]
        url = f"{API}/user/{x}"
        r = self.client.get(url, use_cache=False)
        self.calls += 1
        STATS.rest_requests += 1
        STATS.rest_network += 1
        _check_access(r, "GitHub REST /user/<id>")
        acc = None
        if r.status == 200:
            j = r.json()
            acc = {"id": j["id"], "type": j.get("type"), "created_at": j["created_at"]}
            self.user[x] = (int(j["id"]), _ts(j["created_at"]), j.get("type"))
        elif r.status == 404:
            self.user[x] = None
        else:
            raise GitHubAccessError(f"GET /user/{x}: HTTP {r.status}: {_message(r.text)}")
        self.raw.write({"kind": "user", "id": x, "status": r.status, "fetched_at": r.fetched_at, "account": acc},
                       source=API + "/user/<id>")
        return self.user[x]

    def _next_existing(self, x: int) -> int | None:
        params = {"since": x - 1, "per_page": 1}
        r = self.client.get(API + "/users", params=params)
        self.calls += 1
        STATS.rest_requests += 1
        STATS.rest_network += 0 if r.from_cache else 1
        _check_access(r, "GitHub REST /users")
        if r.status != 200:
            raise GitHubAccessError(f"GET /users?since={x - 1}: HTTP {r.status}: {_message(r.text)}")
        lst = r.json()
        first = {"id": lst[0]["id"], "type": lst[0].get("type")} if lst else None
        self.raw.write({"kind": "users_since", "since": x - 1, "status": r.status, "fetched_at": r.fetched_at,
                        "from_cache": r.from_cache, "first": first}, source=API + "/users")
        return int(first["id"]) if first else None

    def first_at_or_after(self, x: int):
        x = max(1, int(x))
        for _ in range(50):
            res = self._get_user(x)
            if res is not None:
                return res
            nxt = self._next_existing(x)
            if nxt is None:
                return None
            if nxt == x:  # listed but /user/<id> 404 (hidden account): step over it
                x += 1
                continue
            x = nxt
        raise RuntimeError(f"no resolvable account found after id {x}")


def find_bound(prober: IdProber, target: pd.Timestamp, lo: int = 1, step: int = 1 << 20) -> tuple[tuple, int]:
    """Smallest existing account id created on/after `target` (ids increase with creation time)."""
    n0 = prober.calls
    target = pd.Timestamp(target)
    # exponential search for an upper end: first account >= hi is created on/after target (or there is none)
    hi = lo
    while True:
        hi = lo + step
        res = prober.first_at_or_after(hi)
        if res is None or res[1] >= target:
            break
        lo = res[0] + 1
        step *= 2
    while lo < hi:
        mid = (lo + hi) // 2
        res = prober.first_at_or_after(mid)
        if res is None or res[1] >= target:
            hi = mid
        else:
            lo = res[0] + 1
    res = prober.first_at_or_after(lo)
    if res is None:
        raise RuntimeError(f"no account created on/after {target}")
    return res, prober.calls - n0


def year_bounds(years: Iterable[int], client=None, bounds_path: Path | None = BOUNDS_PATH,
                probes_dir: Path = PROBES_RAW) -> dict[int, int]:
    """{year: smallest account id created on/after Jan 1 of year}; reuses rows already in bounds_path."""
    years = sorted({int(y) for y in years})
    have: dict[int, dict] = {}
    if bounds_path is not None and Path(bounds_path).exists():
        for r in pd.read_csv(bounds_path).to_dict("records"):
            have[int(r["year"])] = r
    todo = [y for y in years if y not in have]
    if todo:
        client = client or make_client()
        with RawLog(probes_dir, "probes", params={"years": todo},
                    notes="Account id probes for the id->date map: GET /user/<id> reduced to id/type/created_at "
                          "(no profile fields stored); GET /users?since reduced to the first id/type.") as raw:
            prober = IdProber(client, raw, memo_dir=probes_dir)
            lo = 1
            for y in years:
                if y in have:
                    lo = int(have[y]["first_id"])
                    continue
                target = pd.Timestamp(datetime(y, 1, 1, tzinfo=timezone.utc))
                (fid, fts, ftype), n = find_bound(prober, target, lo=lo)
                have[y] = {"year": y, "boundary_utc": target.isoformat(), "first_id": fid,
                           "first_id_created_at": pd.Timestamp(fts).isoformat(), "first_id_type": ftype, "n_probes": n}
                log.info("id bound %d: first_id=%d (%s) after %d calls", y, fid, fts, n)
                lo = fid
        if bounds_path is not None:
            df = pd.DataFrame([have[y] for y in sorted(have)], columns=BOUNDS_COLUMNS)
            Path(bounds_path).parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(bounds_path, index=False)
            write_meta(Path(bounds_path), script=SCRIPT, rows=len(df), sources=[API + "/user/<id>", API + "/users"],
                       params={"method": "binary search on first existing account id >= x"},
                       notes="first_id = smallest existing GitHub account id (users and orgs share the id space) "
                             "created on/after boundary_utc. Ids grow with creation time.")
    ids = {y: int(have[y]["first_id"]) for y in years}
    for a, b in zip(years, years[1:]):
        if ids[b] < ids[a]:
            raise RuntimeError(f"non-monotone id bounds {a}:{ids[a]} > {b}:{ids[b]}")
    return ids


def draw_since(lo: int, hi: int, n: int, base_seed: int, year: int) -> list[int]:
    """n page starts uniformly in [lo, hi), reproducible from (seed, year)."""
    if hi <= lo or n <= 0:
        return []
    rng = np.random.default_rng(int(base_seed) + int(year))
    return [int(s) for s in rng.integers(lo, hi, size=int(n))]


def covered_length(intervals: list[tuple[int, float]], lo: int, hi: int) -> int:
    """|union of page intervals (s, e]  intersect  [lo, hi)| in ids. e may be inf (page hit the end of the list)."""
    segs = []
    for s, e in intervals:
        a = max(int(s) + 1, lo)
        b = hi if e == math.inf else min(int(e) + 1, hi)
        if b > a:
            segs.append((a, b))
    segs.sort()
    total, cur_a, cur_b = 0, None, None
    for a, b in segs:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total


def inclusion_stats(n_sampled: int, covered: int, lo: int, hi: int) -> dict:
    span = hi - lo
    density = n_sampled / covered if covered else float("nan")
    est_total = density * span if covered else float("nan")
    if n_sampled and est_total:
        prob = n_sampled / est_total
    else:
        prob = covered / span if span else float("nan")
    return {"lo": lo, "hi": hi, "id_span": span, "covered_ids": covered, "sampled_orgs": n_sampled,
            "density_per_id": density, "est_total_orgs": est_total, "inclusion_prob": prob}


def pool_rule(login, website_url, website_domain, display_name) -> bool:
    if not isinstance(website_domain, str) or not website_domain:
        return False
    return bool(login_rule(login, website_url, website_domain, display_name if isinstance(display_name, str) else None))


def fetch_pages(client, since_values: list[int], raw: RawLog) -> list[dict]:
    out = []
    for s in since_values:
        params = {"since": int(s), "per_page": PER_PAGE}
        r = client.get(API + "/organizations", params=params)
        STATS.rest_requests += 1
        STATS.rest_network += 0 if r.from_cache else 1
        _check_access(r, "GitHub REST /organizations")
        if r.status != 200:
            raise GitHubAccessError(f"GET /organizations?since={s}: HTTP {r.status}: {_message(r.text)}")
        lst = r.json()
        raw.write({"kind": "organizations_page", "since": int(s), "per_page": PER_PAGE, "status": r.status,
                   "fetched_at": r.fetched_at, "from_cache": r.from_cache, "response": lst}, source=API + "/organizations")
        out.append({"since": int(s), "fetched_at": r.fetched_at,
                    "orgs": [{"id": int(o["id"]), "login": o["login"]} for o in lst]})
    return out


def sample(pages: int | None = None, years: Iterable[int] | None = None, client=None, base_seed: int | None = None,
           out_path: Path | None = SAMPLE_PATH, bounds_path: Path | None = BOUNDS_PATH, orgs_path: Path | None = ORGS_PATH,
           pages_raw: Path = PAGES_RAW, probes_raw: Path = PROBES_RAW, lookup_raw: Path = LOOKUP_RAW,
           batch_size: int = BATCH_SIZE) -> tuple[pd.DataFrame, dict]:
    """Outcome-blind sample of GitHub orgs per creation year. Returns (sample rows of these years, per-year stats)."""
    pcfg = cfg()["pool"]
    if years is None:
        y0, y1 = pcfg["org_sample_years"]
        years = range(y0, y1 + 1)
    years = sorted({int(y) for y in years})
    pages = int(pcfg["org_sample_pages_per_year"] if pages is None else pages)
    base_seed = cfg_seed() if base_seed is None else int(base_seed)
    client = client or make_client()

    bounds = year_bounds(set(years) | {y + 1 for y in years}, client=client, bounds_path=bounds_path,
                         probes_dir=probes_raw)
    draws = [(y, k, s) for y in years for k, s in enumerate(draw_since(bounds[y], bounds[y + 1], pages, base_seed, y))]
    log.info("sample: %d pages over years %s", len(draws), years)
    with RawLog(pages_raw, "pages", params={"years": years, "pages_per_year": pages, "seed": base_seed},
                notes="GET /organizations?since=<random id>&per_page=100 responses, one line per page.") as raw:
        fetched = fetch_pages(client, [s for _, _, s in draws], raw)

    intervals, orgs, short_pages = [], {}, {y: 0 for y in years}
    for (draw_year, _, _), page in zip(draws, fetched):
        ids = [o["id"] for o in page["orgs"]]
        # A page covers (since, last id on page]. A short page is NOT treated as "end of the list": every sampled
        # year ends before ids that are still being created, so a page with < per_page orgs means GitHub filtered
        # some (hidden / flagged accounts). Extending its coverage to infinity would inflate inclusion_prob for this
        # and every later year; an empty page covers nothing.
        end = max(ids) if ids else page["since"]
        if len(ids) < PER_PAGE:
            short_pages[draw_year] += 1
        intervals.append((page["since"], end))
        for o in page["orgs"]:
            prev = orgs.get(o["id"])
            if prev is None or page["since"] < prev["since"]:
                orgs[o["id"]] = {"id": o["id"], "login": o["login"], "since": page["since"]}
    # assign each org to the creation year whose id range holds it; keep only requested years
    edges = sorted(bounds.items())
    starts = [b for _, b in edges]
    stats: dict[int, dict] = {}
    for y in years:
        lo, hi = bounds[y], bounds[y + 1]
        n = sum(1 for oid in orgs if lo <= oid < hi)
        stats[y] = inclusion_stats(n, covered_length(intervals, lo, hi), lo, hi)
        stats[y]["pages"] = sum(1 for d in draws if d[0] == y)
        stats[y]["short_pages"] = short_pages[y]
    rows = []
    for oid in sorted(orgs):
        i = bisect.bisect_right(starts, oid) - 1
        if i < 0 or i >= len(edges) - 1:
            continue
        y = edges[i][0]
        if y not in stats:
            continue
        rows.append({**orgs[oid], "sample_year": y})
    frame = pd.DataFrame(rows, columns=["id", "login", "since", "sample_year"])

    looked = lookup_orgs(frame["login"].tolist(), client=client, batch_size=batch_size, orgs_path=orgs_path,
                         raw_dir=lookup_raw) if len(frame) else org_frame([])
    looked = looked.set_index("query_login")
    out_rows, n_missing, n_err, n_id_mismatch = [], 0, 0, 0
    for r in frame.to_dict("records"):
        o = looked.loc[r["login"].lower()] if r["login"].lower() in looked.index else None
        if o is None or o["exists"] is pd.NA:
            n_err += 1
            continue
        if not bool(o["exists"]):
            n_missing += 1
            continue
        if pd.notna(o["database_id"]) and int(o["database_id"]) != int(r["id"]):
            n_id_mismatch += 1
            continue
        y = r["sample_year"]
        out_rows.append({"login": o["login"], "database_id": r["id"], "created_at": o["created_at"],
                         "created_year": o["created_at"].year if pd.notna(o["created_at"]) else None,
                         "website_url": o["website_url"], "website_domain": o["website_domain"],
                         "display_name": o["display_name"],
                         "in_pool": pool_rule(o["login"], o["website_url"], o["website_domain"], o["display_name"]),
                         "inclusion_prob": stats[y]["inclusion_prob"], "sample_page_since": r["since"],
                         "sample_year": y, "fetched_at": o["fetched_at"]})
    df = _sample_frame(out_rows)
    for y in years:
        sub = df[df["sample_year"] == y]
        stats[y].update({"enriched": int(len(sub)), "in_pool": int(sub["in_pool"].sum()),
                         "created_year_agrees": float((sub["created_year"] == y).mean()) if len(sub) else None})
    summary = {"not_found_at_lookup": n_missing, "lookup_errors": n_err, "database_id_mismatch": n_id_mismatch}
    log.info("sample: %d orgs (%d in pool); %s", len(df), int(df["in_pool"].sum()), summary)
    if out_path is not None:
        out_path = Path(out_path)
        if out_path.exists():
            old = pd.read_parquet(out_path)
            old = old[~old["sample_year"].isin(years)]
            full = _sample_frame(pd.concat([old, df], ignore_index=True)) if len(old) else df
        else:
            full = df
        out_path.parent.mkdir(parents=True, exist_ok=True)
        full.to_parquet(out_path, index=False)
        write_meta(out_path, script=SCRIPT, rows=len(full),
                   inputs=[p for p in (Path(bounds_path) if bounds_path else None,) if p is not None and p.exists()],
                   sources=[API + "/organizations", GRAPHQL_URL],
                   params={"seed": base_seed, "pages_per_year": pages, "per_page": PER_PAGE, "years_this_run": years,
                           "per_year": {str(y): stats[y] for y in years}, **summary},
                   notes="Outcome-blind org sample (pool channel a). inclusion_prob = sampled_orgs / "
                         "estimated_total_orgs_in_year (= covered id fraction). in_pool = login_rule with the org's own "
                         "website domain. No label data read. websiteUrl is the current value (observed at fetched_at).")
    return df, {"per_year": stats, **summary}


def _sample_frame(rows) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame.from_records(rows, columns=SAMPLE_COLUMNS)
    df = df[SAMPLE_COLUMNS].copy()
    df["database_id"] = df["database_id"].astype("int64")
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True)
    df["created_year"] = pd.to_numeric(df["created_year"], errors="coerce").astype("Int64")
    df["in_pool"] = df["in_pool"].astype(bool)
    df["inclusion_prob"] = df["inclusion_prob"].astype(float)
    df["sample_page_since"] = df["sample_page_since"].astype("int64")
    df["sample_year"] = df["sample_year"].astype("int64")
    return df.sort_values(["sample_year", "database_id"]).reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------------------------------
def _report(client, before):
    after = rate_limit(client)
    s = STATS.as_dict()
    print("stats:", json.dumps(s, sort_keys=True))
    if before and after:
        used = {k: (after[k]["used"] or 0) - (before[k]["used"] or 0) if after[k]["reset"] == before[k]["reset"]
                else f"window reset; used now {after[k]['used']}" for k in ("core", "graphql")}
        print("rate limit used this run:", json.dumps(used), "remaining:",
              json.dumps({k: after[k]["remaining"] for k in after}))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.sources.github", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    p_s = sub.add_parser("sample", help="outcome-blind org sample (pool channel a)")
    p_s.add_argument("--pages", type=int, default=None, help="pages per creation year (default: config)")
    p_s.add_argument("--years", type=int, nargs="+", default=None, help="creation years (default: config range)")
    p_s.add_argument("--out", type=Path, default=SAMPLE_PATH, help=argparse.SUPPRESS)
    p_f = sub.add_parser("find", help="staged entity -> GitHub org search")
    p_f.add_argument("--input", type=Path, required=True, help="CSV with entity_id[,names]")
    p_f.add_argument("--out", type=Path, default=ENTITY_ORG_PATH, help="output parquet")
    p_f.add_argument("--search-log", type=Path, default=None, help="default: <out stem>_search.parquet")
    for p in (p_s, p_f):
        p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p_l = sub.add_parser("lookup", help="look up org logins (prints a summary)")
    p_l.add_argument("logins", nargs="+")
    sub.add_parser("ratelimit", help="print current rate-limit status")
    args = ap.parse_args(argv)
    cmd = args.cmd or "sample"

    client = make_client()
    before = rate_limit(client)
    try:
        if cmd == "ratelimit":
            print(json.dumps(before, indent=2))
            return 0 if before else 1
        if cmd == "lookup":
            df = lookup_orgs(args.logins, client=client)
            print(df[["login", "exists", "database_id", "created_at", "website_domain"]].to_string(index=False))
        elif cmd == "find":
            ents = pd.read_csv(args.input, dtype=str, keep_default_na=False, encoding="utf-8-sig")
            slog = args.search_log or args.out.with_name(args.out.stem + "_search.parquet")
            if args.out == ENTITY_ORG_PATH and args.search_log is None:
                slog = SEARCH_LOG_PATH
            out = find_org(ents, client=client, out_path=args.out, search_log_path=slog, batch_size=args.batch_size)
            n = ents["entity_id"].nunique()
            held = int(pd.read_parquet(slog)["note"].fillna("").str.contains("lookup_error_tier").sum()) \
                if Path(slog).exists() else 0
            print(f"entities={n} matched={len(out)} ({len(out) / max(n, 1):.1%}) by tier "
                  f"{out['tier'].value_counts().sort_index().to_dict()} -> {args.out}; "
                  f"held back after lookup errors (search-log total; rerun find for them): {held}")
        else:
            pages = getattr(args, "pages", None)
            yrs = getattr(args, "years", None)
            df, st = sample(pages=pages, years=yrs, client=client, out_path=getattr(args, "out", SAMPLE_PATH),
                            batch_size=getattr(args, "batch_size", BATCH_SIZE))
            for y, s in st["per_year"].items():
                print(f"{y}: pages={s['pages']} short_pages={s['short_pages']} ids=[{s['lo']},{s['hi']}) covered={s['covered_ids']} "
                      f"sampled={s['sampled_orgs']} est_total={s['est_total_orgs']:.0f} incl={s['inclusion_prob']:.5f} "
                      f"enriched={s['enriched']} in_pool={s['in_pool']} created_year_agrees={s['created_year_agrees']}")
            print({k: v for k, v in st.items() if k != "per_year"})
    except GitHubAccessError as e:
        log.error("%s", e)
        print(f"ERROR: {e}", file=sys.stderr)
        _report(client, before)
        return 3
    _report(client, before)
    return 0


if __name__ == "__main__":
    sys.exit(main())
