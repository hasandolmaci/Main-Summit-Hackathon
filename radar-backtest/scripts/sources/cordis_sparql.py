"""CORDIS collector via the public CORDIS SPARQL endpoint (EURIO knowledge graph), open data, CC BY 4.0.

Replacement for scripts/sources/cordis.py when the bulk zips cannot be fetched (cordis.europa.eu/robots.txt disallows
/data/; /datalab/ is allowed and re-checked on every networked run). Same output contract as cordis.py:
    data/processed/cordis_participations.parquet  one row per (programme, project_id, organisation_id)
    data/processed/cordis_companies.parquet       one row per PRC company domain
built with cordis.py's own normalisation (cordis.build_participations / build_companies / summarize).

EURIO structure used (found by inspecting classes / predicates / datatypes only, never data rows):
    eurio:OrganisationRole  isRoleOf -> Organisation, isInvolvedIn -> Project, roleLabel, order,
                            isRecipientOf -> GrantPayment hasPaymentAmount -> MonetaryAmount (value, currency)
    eurio:Organisation      identifier (= PIC, the bulk 'organisationID'), legalName, url,
                            hasAcronym -> Acronym shortForm, hasSite -> Site hasAddress -> PostalAddress addressCountry
                            rdf:type ForProfitOrganisation (= activityType PRC), HigherOrSecondaryEducation (HES),
                            ResearchOrganisation (REC), PublicBody (PUB), SME (SME flag). No class = OTH or unknown.
    eurio:Project           identifier (grant agreement id), hasAcronym, startDate, signatureDate (= ecSignatureDate;
                            absent for FP7), isFundedBy -> Grant
    eurio:Grant             hasFundingSchemeTopic / Call (identifier 'H2020_...', 'FP7-...', 'HORIZON-...'),
                            hasFundingSchemeType (code 'RIA', 'MC-IEF', ...), hasFundingSchemeProgramme (code)
The graph has no frameworkProgramme field; the programme is derived deterministically per project (derive_programmes).
Projects are keyed by their identifier literal, never by node IRI: the endpoint persistently fails to sort or STR()
some Project IRIs (Virtuoso SR477 / SR014), and the identifier is the bulk 'projectID' anyway. Intermittent 5xx on a
page are retried a bounded number of times after a pause (Sparql.outer_retries / retry_pause_s).
Activity type and SME are organisation-level classes in the graph (per participation in the bulk CSV).
EURIO v2.5 removed person classes and contact details; no contact field is ever selected (guarded) and e-mail-like
url values are filtered out server-side, so neither raw nor processed files hold e-mail addresses.

Collection: each dataset is one SELECT, paginated with ORDER BY <every selected variable> + LIMIT/OFFSET (<= 10 000
rows: the server caps result sets at 10 000 rows), through scripts.lib.http.Client (cached, >= 1 s per request). A COUNT
query of the same pattern runs first; pagination stops at the first short page (or when COUNT is reached) and the row
total must equal COUNT. Every response is stored verbatim (gz) in data/raw/cordis_sparql/<dataset>/ with a .meta.json.

Point in time: public_ts = signatureDate if present, else startDate (00:00 UTC). Nothing observed later is used.

CLI
    .venv/bin/python -m scripts.sources.cordis_sparql                      # robots check, fetch (cached), parse, write
    .venv/bin/python -m scripts.sources.cordis_sparql --cache-only         # re-parse from the HTTP cache, no network
    .venv/bin/python -m scripts.sources.cordis_sparql --refresh            # re-fetch every page (new graph release)
    .venv/bin/python -m scripts.sources.cordis_sparql --max-pages 1 --out-dir /tmp/x   # smoke (partial, no COUNT check)
    .venv/bin/python -m scripts.sources.cordis_sparql --programmes H2020 HORIZON
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import requests

from scripts.lib.config import PROCESSED, RAW, get_logger
from scripts.lib.http import CachedResponse, Client, _key
from scripts.lib.meta import write_meta
from scripts.sources import cordis

log = get_logger("cordis_sparql")
SCRIPT = "scripts/sources/cordis_sparql.py"
ENDPOINT = "https://cordis.europa.eu/datalab/sparql"
EURIO = "http://data.europa.eu/s66#"
RAW_DIR = RAW / "cordis_sparql"
LICENSE = ("CC BY 4.0 (CORDIS EURIO knowledge graph, European Union; reuse per Commission Decision 2011/833/EU; "
           "https://creativecommons.org/licenses/by/4.0/)")
FORMAT = "text/csv"
MAX_PAGE_SIZE = 10_000      # server-side cap (ResultSetMaxRows): LIMIT 50000 returned 10 000 rows
PROGRAMMES = ["FP7", "H2020", "HORIZON"]

# EURIO organisation classes -> CORDIS bulk activityType codes. No class: OTH or not reported (indistinguishable).
TYPE_TO_ACTIVITY = {"ForProfitOrganisation": "PRC", "HigherOrSecondaryEducation": "HES",
                    "ResearchOrganisation": "REC", "PublicBody": "PUB"}
SME_CLASS = "SME"
# Same pattern as cordis.EMAIL_IN_URL_RE, applied in the query so e-mail-like url values never leave the server.
EMAIL_REGEX_SPARQL = r"[^\\s@:/]+@[^\\s@/]+\\.[^\\s@/]+"
PHONE_RE = re.compile(r"(?:\+|00)\d[\d\s/().-]{7,}\d")
PROGRAMME_PREFIX_RE = re.compile(r"^(FP7|H2020|HORIZON)(?=[-_.\s]|$)", re.I)


class SparqlError(RuntimeError):
    pass


class PaginationError(SparqlError):
    pass


class RobotsDisallowed(RuntimeError):
    pass


# ======================================================================================================================
# Query builders
@dataclass(frozen=True)
class Dataset:
    name: str
    columns: tuple[str, ...]     # CSV header = projected variables, in order
    select: str                  # SELECT clause body (expressions allowed)
    where: str                   # WHERE body
    order_by: str                # underlying variables: every value that can differ between rows (total order)


def _last_segment(var: str, alias: str) -> str:
    return f'(REPLACE(STR(?{var}), "^.*/", "") AS ?{alias})'


DATASETS: dict[str, Dataset] = {
    "roles": Dataset(
        "roles", ("role_id", "org_id", "project_id", "role_label", "order", "amount", "currency"),
        " ".join([_last_segment("role", "role_id"), _last_segment("org", "org_id"),
                  "?project_id ?role_label ?order ?amount ?currency"]),
        """?role a eurio:OrganisationRole ; eurio:isRoleOf ?org ; eurio:isInvolvedIn ?project .
  OPTIONAL { ?project eurio:identifier ?project_id }
  OPTIONAL { ?role eurio:roleLabel ?role_label }
  OPTIONAL { ?role eurio:order ?order }
  OPTIONAL { ?role eurio:isRecipientOf ?pay . ?pay eurio:hasPaymentAmount ?amt . ?amt eurio:value ?amount .
             OPTIONAL { ?amt eurio:currency ?currency } }""",
        "?role ?org ?project_id ?role_label ?order ?amount ?currency"),
    "organisations": Dataset(
        "organisations", ("org_id", "pic", "name", "short_name", "url", "country"),
        " ".join([_last_segment("org", "org_id"), "?pic ?name ?short_name (STR(?url0) AS ?url) ?country"]),
        f"""?org a eurio:Organisation .
  OPTIONAL {{ ?org eurio:identifier ?pic }}
  OPTIONAL {{ ?org eurio:legalName ?name }}
  OPTIONAL {{ ?org eurio:hasAcronym ?acr . ?acr eurio:shortForm ?short_name }}
  OPTIONAL {{ ?org eurio:url ?url0 . FILTER(!REGEX(STR(?url0), "{EMAIL_REGEX_SPARQL}")) }}
  OPTIONAL {{ ?org eurio:hasSite ?site . ?site eurio:hasAddress ?adr . ?adr eurio:addressCountry ?country }}""",
        "?org ?pic ?name ?short_name ?url0 ?country"),
    "organisation_types": Dataset(
        "organisation_types", ("org_id", "type"),
        " ".join([_last_segment("org", "org_id"), '(REPLACE(STR(?t), "^.*[#/]", "") AS ?type)']),
        "?org a eurio:Organisation ; a ?t . FILTER(?t != eurio:Organisation)",
        "?org ?t"),
    "projects": Dataset(
        "projects", ("project_id", "acronym", "start_date", "signature_date", "grant_uuid"),
        " ".join(["?project_id ?acronym (STR(?sd) AS ?start_date)", "(STR(?sig) AS ?signature_date)",
                  _last_segment("grant", "grant_uuid")]),
        """?project a eurio:Project ; eurio:identifier ?project_id .
  OPTIONAL { ?project eurio:hasAcronym ?acr . ?acr eurio:shortForm ?acronym }
  OPTIONAL { ?project eurio:startDate ?sd }
  OPTIONAL { ?project eurio:signatureDate ?sig }
  OPTIONAL { ?project eurio:isFundedBy ?grant }""",
        "?project_id ?acronym ?sd ?sig ?grant"),
}
# Funding-scheme evidence per grant: one dataset per kind (a UNION with constant kinds overflows Virtuoso temp columns).
SCHEME_KINDS = {"topic": ("hasFundingSchemeTopic", "identifier"), "call": ("hasFundingSchemeCall", "identifier"),
                "type": ("hasFundingSchemeType", "code"), "programme": ("hasFundingSchemeProgramme", "code")}
for _kind, (_pred, _attr) in SCHEME_KINDS.items():
    DATASETS[f"grant_{_kind}"] = Dataset(
        f"grant_{_kind}", ("grant_uuid", "value"), _last_segment("grant", "grant_uuid") + " ?value",
        f"?grant a eurio:Grant ; eurio:{_pred} ?s . ?s eurio:{_attr} ?value .", "?grant ?value")


def _check_no_contact(text: str) -> str:
    """Refuse any query that names a contact-like variable or predicate (contact data are never selected)."""
    for tok in re.findall(r"[?:]([A-Za-z_][\w-]*)", text):
        if cordis.CONTACT_RE.search(tok):
            raise ValueError(f"query references a contact-like field: {tok}")
    return text


def _header(ds: Dataset, what: str) -> str:
    return f"# radar-backtest cordis_sparql dataset={ds.name} {what}\nPREFIX eurio: <{EURIO}>\n"


def build_page_query(ds: Dataset, limit: int, offset: int) -> str:
    if not 0 < limit <= MAX_PAGE_SIZE:
        raise ValueError(f"page size must be in 1..{MAX_PAGE_SIZE} (server cap), got {limit}")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    return _check_no_contact(_header(ds, "page") + f"SELECT {ds.select}\nWHERE {{\n  {ds.where}\n}}\n"
                             f"ORDER BY {ds.order_by}\nLIMIT {int(limit)} OFFSET {int(offset)}")


def build_count_query(ds: Dataset) -> str:
    return _check_no_contact(_header(ds, "count") + f"SELECT (COUNT(*) AS ?n)\nWHERE {{\n  {ds.where}\n}}")


def query_sha8(ds: Dataset) -> str:
    """Version of a dataset's query (without LIMIT/OFFSET): raw files of different query versions never mix."""
    return hashlib.sha256(build_count_query(ds).encode()).hexdigest()[:8]


# ======================================================================================================================
# robots.txt (RFC 9309 with '*' / '$' wildcards; urllib.robotparser ignores wildcards)
def _rule_regex(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    return re.compile("".join(".*" if ch == "*" else re.escape(ch) for ch in body) + ("$" if anchored else ""))


def parse_robots(text: str) -> list[tuple[set[str], list[tuple[bool, str]]]]:
    groups: list[tuple[set[str], list[tuple[bool, str]]]] = []
    agents: set[str] = set()
    rules: list[tuple[bool, str]] = []
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = set(), []
            agents.add(v.lower())
            last_was_agent = True
        elif k in ("allow", "disallow"):
            last_was_agent = False
            if agents and v:
                rules.append((k == "allow", v))
    if agents or rules:
        groups.append((agents, rules))
    return groups


def robots_decision(text: str, user_agent: str, url: str) -> tuple[bool, str | None]:
    """(allowed, deciding rule) for url. Longest match wins; on a tie Allow wins. Both the URL as sent and its
    percent-decoded form are tested, and the stricter outcome is returned."""
    token = user_agent.split("/")[0].strip().lower()
    groups = parse_robots(text)
    rules = [r for a, rs in groups if token in a for r in rs]
    if not rules:
        rules = [r for a, rs in groups if "*" in a for r in rs]
    p = urlparse(url)
    paths = {p.path + (f"?{p.query}" if p.query else ""), unquote(p.path + (f"?{p.query}" if p.query else ""))}
    verdict, why = True, None
    for path in sorted(paths):
        best = None
        for allow, pat in rules:
            if _rule_regex(pat).match(path):
                cand = (len(pat), allow, pat)
                if best is None or cand[:2] > best[:2]:
                    best = cand
        if best is not None and not best[1]:
            verdict, why = False, f"Disallow: {best[2]}"
        elif best is not None and why is None:
            why = f"Allow: {best[2]}"
    return verdict, why


def check_robots(client: Client, urls: list[str]) -> dict:
    """Fetch robots.txt once, fresh (never from cache), and test every URL that will be requested.

    Unreachable (5xx / connection error) = disallow (RFC 9309); 4xx = no restrictions. Raises RobotsDisallowed if any
    URL is disallowed. All URLs must be on one host.
    """
    hosts = {urlparse(u).netloc for u in urls}
    if len(hosts) != 1:
        raise ValueError(f"check_robots expects URLs of one host, got {sorted(hosts)}")
    p = urlparse(urls[0])
    r = client.get(f"{p.scheme}://{p.netloc}/robots.txt", use_cache=False)
    rec = {"robots_url": f"{p.scheme}://{p.netloc}/robots.txt", "status": r.status, "fetched_at": r.fetched_at,
           "sha256": hashlib.sha256(r.text.encode()).hexdigest() if r.text else None, "urls_checked": len(urls)}
    if r.status == -1 or r.status >= 500:
        raise RobotsDisallowed(f"robots.txt of {p.netloc} unreachable (status={r.status}): treated as disallow")
    if r.status != 200:
        return {**rec, "allowed": True, "rules": [f"no robots.txt (status {r.status})"]}
    ua = client.session.headers.get("User-Agent", "*")
    rules = set()
    for u in urls:
        allowed, rule = robots_decision(r.text, ua, u)
        if not allowed:
            raise RobotsDisallowed(f"{p.netloc}/robots.txt disallows {u[:200]}... ({rule})")
        rules.add(rule or "no matching rule (allowed)")
    return {**rec, "allowed": True, "rules": sorted(rules)}


# ======================================================================================================================
# Fetching + parsing pages
def parse_csv_page(text: str, columns: tuple[str, ...] | list[str], label: str = "") -> pd.DataFrame:
    """Virtuoso text/csv result -> frame of str|None (empty field = unbound). The header must equal the projected
    variables and every row must have that many fields; anything else raises (a machine-produced file that does not
    parse is an error page or a format change, never silently skipped)."""
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', doublequote=True, strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise SparqlError(f"{label}: empty response") from None
    except csv.Error as e:
        raise SparqlError(f"{label}: unparseable header ({e})") from None
    if [h.strip() for h in header] != list(columns):
        raise SparqlError(f"{label}: header {header[:10]} != expected {list(columns)} (error page or format change?)")
    data = {c: [] for c in columns}
    try:
        for i, row in enumerate(reader):
            if not row:
                continue
            if len(row) != len(columns):
                raise SparqlError(f"{label}: row {i + 1} has {len(row)} fields, expected {len(columns)}")
            for c, v in zip(columns, row):
                data[c].append(v if v != "" else None)
    except csv.Error as e:
        raise SparqlError(f"{label}: CSV error after {reader.line_num} lines ({e})") from None
    return pd.DataFrame(data, columns=list(columns), dtype=object)


def parse_count(text: str, label: str = "") -> int:
    df = parse_csv_page(text, ("n",), label)
    if len(df) != 1 or df["n"].iloc[0] is None or not re.fullmatch(r"\d+", df["n"].iloc[0]):
        raise SparqlError(f"{label}: bad COUNT result {df.to_dict('list')}")
    return int(df["n"].iloc[0])


def _gz_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as f:  # mtime=0 -> byte-identical re-runs
        f.write(text.encode("utf-8"))
    return buf.getvalue()


@dataclass
class PageLog:
    dataset: str
    kind: str          # 'count' | 'page'
    offset: int | None
    rows: int
    seconds: float
    from_cache: bool
    fetched_at: str
    raw_path: str


@dataclass
class Sparql:
    endpoint: str = ENDPOINT
    client: Client | None = None
    raw_dir: Path = RAW_DIR
    write_raw: bool = True
    refresh: bool = False          # evict cached responses and fetch again
    cache_only: bool = False       # never touch the network; a missing cache entry is an error
    max_retries: int = 3           # Client backoff retries (2-8 s); Virtuoso answers query errors with 500 too
    outer_retries: int = 2         # further attempts after a pause when a page still fails with 5xx
    retry_pause_s: float = 60.0
    log: list = field(default_factory=list)

    def __post_init__(self):
        if self.client is None:
            self.client = Client()
        self.client.max_retries = min(self.client.max_retries, self.max_retries)

    def _params(self, query: str) -> dict:
        return {"query": query, "format": FORMAT}

    def _cache_file(self, query: str) -> Path:
        return self.client._cache_path(urlparse(self.endpoint).netloc, _key("GET", self.endpoint, self._params(query), None))

    def request_url(self, query: str) -> str:
        return requests.Request("GET", self.endpoint, params=self._params(query)).prepare().url

    def _evict(self, query: str):
        p = self._cache_file(query)
        if p.exists():
            p.unlink()

    def fetch(self, query: str, label: str) -> CachedResponse:
        if self.cache_only and not self._cache_file(query).exists():
            raise SparqlError(f"{label}: not in the HTTP cache (--cache-only); run without it to fetch")
        if self.refresh:
            self._evict(query)
        for attempt in range(self.outer_retries + 1):
            r = self.client.get(self.endpoint, params=self._params(query), headers={"Accept": FORMAT})
            if r.status == 200:
                return r
            self._evict(query)
            # Virtuoso sometimes fails a query intermittently (SR477 / SR014 on a page that succeeds minutes later)
            # after the Client's own short backoff: pause, then try again a bounded number of times.
            if (r.status == -1 or r.status >= 500) and attempt < self.outer_retries:
                log.warning("%s: HTTP %s (%s); pausing %ss before retry %d/%d", label, r.status,
                            (r.text or "")[:120].replace("\n", " "), self.retry_pause_s, attempt + 1, self.outer_retries)
                time.sleep(self.retry_pause_s)
                continue
            break
        raise SparqlError(f"{label}: HTTP {r.status}: {(r.text or '')[:500]}")

    def _write_raw(self, ds: Dataset, name: str, r: CachedResponse, query: str, rows: int, extra: dict) -> Path:
        path = self.raw_dir / ds.name / f"{name}.csv.gz"
        if not self.write_raw:
            return path
        blob = _gz_bytes(r.text)
        if not (path.exists() and path.read_bytes() == blob):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
            write_meta(path, script=SCRIPT, rows=rows, sources=[self.endpoint],
                       params={"endpoint": self.endpoint, "format": FORMAT, "fetched_at": r.fetched_at,
                               "query": query, **extra},
                       notes="Raw CORDIS SPARQL text/csv response for 'query' (never edited); " + LICENSE)
        return path

    def count(self, ds: Dataset) -> int:
        q = build_count_query(ds)
        t0 = time.monotonic()
        r = self.fetch(q, f"{ds.name}/count")
        try:
            n = parse_count(r.text, f"{ds.name}/count")
        except SparqlError:
            self._evict(q)
            raise
        path = self._write_raw(ds, f"{ds.name}_{query_sha8(ds)}_count", r, q, 1, {"kind": "count", "count": n})
        self.log.append(PageLog(ds.name, "count", None, n, round(time.monotonic() - t0, 3), r.from_cache,
                                r.fetched_at, str(path)))
        return n

    def page(self, ds: Dataset, limit: int, offset: int) -> pd.DataFrame:
        q = build_page_query(ds, limit, offset)
        label = f"{ds.name}@{offset}"
        t0 = time.monotonic()
        r = self.fetch(q, label)
        try:
            df = parse_csv_page(r.text, ds.columns, label)
            if len(df) > limit:
                raise SparqlError(f"{label}: {len(df)} rows > LIMIT {limit}")
        except SparqlError:
            self._evict(q)
            raise
        path = self._write_raw(ds, f"{ds.name}_{query_sha8(ds)}_o{offset:07d}", r, q, len(df),
                               {"kind": "page", "limit": limit, "offset": offset})
        self.log.append(PageLog(ds.name, "page", offset, len(df), round(time.monotonic() - t0, 3), r.from_cache,
                                r.fetched_at, str(path)))
        log.info("%s rows=%d %.2fs cache=%s", label, len(df), time.monotonic() - t0, r.from_cache)
        return df


def paginate(fetch_page, page_size: int, expected: int | None = None, max_pages: int | None = None
             ) -> tuple[list[pd.DataFrame], dict]:
    """Call fetch_page(offset) for offset = 0, page_size, 2*page_size, ...

    Stops at the first page with fewer than page_size rows, when `expected` (COUNT) rows have been read (no extra
    empty request for an exact multiple), or after max_pages (partial: no total check). A complete run must return
    exactly `expected` rows: a server cap below page_size, a timeout-truncated page or a graph update between pages
    shows up as a PaginationError instead of a silently short table.
    """
    if not 0 < page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page size must be in 1..{MAX_PAGE_SIZE}")
    pages, offset, partial = [], 0, False
    while True:
        df = fetch_page(offset)
        if len(df) > page_size:
            raise PaginationError(f"page at offset {offset} has {len(df)} rows > page size {page_size}")
        pages.append(df)
        offset += len(df)
        if len(df) < page_size:
            break
        if expected is not None and offset >= expected:
            break
        if max_pages is not None and len(pages) >= max_pages:
            partial = True
            break
    total = sum(len(p) for p in pages)
    if expected is not None and not partial and total != expected:
        raise PaginationError(f"read {total} rows in {len(pages)} pages but COUNT = {expected} (server row cap below "
                              f"the page size, truncated page, or the graph changed between pages: rerun with --refresh)")
    return pages, {"pages": len(pages), "rows": total, "expected": expected, "partial": partial}


def collect(sp: Sparql, page_size: int = MAX_PAGE_SIZE, max_pages: int | None = None,
            datasets: list[str] | None = None) -> tuple[dict[str, pd.DataFrame], dict]:
    frames, stats = {}, {}
    for name in datasets or list(DATASETS):
        ds = DATASETS[name]
        t0 = time.monotonic()
        n0 = len(sp.log)
        expected = sp.count(ds)
        pages, st = paginate(lambda off, ds=ds: sp.page(ds, page_size, off), page_size, expected, max_pages)
        df = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame(columns=list(ds.columns), dtype=object)
        entries = sp.log[n0:]
        st.update(requests=len(entries), from_cache=sum(e.from_cache for e in entries),
                  network_requests=sum(not e.from_cache for e in entries),
                  seconds=round(time.monotonic() - t0, 1), query_sha8=query_sha8(ds),
                  fetched_at_min=min(e.fetched_at for e in entries), fetched_at_max=max(e.fetched_at for e in entries))
        stats[name] = st
        frames[name] = df
        log.info("%s: %d rows, %d pages (%d from cache) %.1fs", name, len(df), st["pages"], st["from_cache"],
                 st["seconds"])
    return frames, stats


# ======================================================================================================================
# Transform
def _first_sorted(df: pd.DataFrame, key: str, col: str, index: pd.Index) -> tuple[pd.Series, int]:
    """Deterministic pick of one value per key (alphabetically first non-null) + number of keys with > 1 value."""
    d = df[[key, col]].dropna().drop_duplicates()
    multi = int(d.groupby(key).size().gt(1).sum())
    first = d.sort_values([key, col], kind="mergesort").drop_duplicates(key).set_index(key)[col]
    return first.reindex(index), multi


def build_org_table(orgs: pd.DataFrame, types: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per organisation node: organisation_id (PIC; 'kg:<uuid>' if the node has none), name, short_name,
    organization_url, country, sme ('true'/'false'), activity_type (None if untyped or conflicting classes)."""
    idx = pd.Index(sorted(set(orgs["org_id"].dropna())), name="org_id")
    st: dict = {"nodes": len(idx), "empty_org_id_rows": int(orgs["org_id"].isna().sum())}
    out = pd.DataFrame(index=idx)
    for col, src in [("pic", "pic"), ("name", "name"), ("short_name", "short_name"), ("organization_url", "url"),
                     ("country", "country")]:
        out[col], st[f"multi_{src}"] = _first_sorted(orgs, "org_id", src, idx)
    st["missing_pic"] = int(out["pic"].isna().sum())
    out["organisation_id"] = out["pic"].where(out["pic"].notna(), "kg:" + out.index.to_series())
    t = types.dropna().drop_duplicates()
    tsets = t.groupby("org_id")["type"].agg(lambda s: frozenset(s)).reindex(idx)
    tsets = tsets.map(lambda s: s if isinstance(s, frozenset) else frozenset())
    acts = tsets.map(lambda s: sorted(TYPE_TO_ACTIVITY[c] for c in s if c in TYPE_TO_ACTIVITY))
    out["activity_type"] = acts.map(lambda a: a[0] if len(a) == 1 else None)
    out["sme"] = tsets.map(lambda s: "true" if SME_CLASS in s else "false")
    st["activity_type_conflict"] = int(acts.map(len).gt(1).sum())
    st["untyped"] = int(acts.map(len).eq(0).sum())
    st["activity_types"] = {k: int(v) for k, v in out["activity_type"].value_counts().sort_index().items()}
    st["sme_true"] = int((out["sme"] == "true").sum())
    st["types_not_in_organisations"] = int((~t["org_id"].isin(idx)).sum())
    return out, st


def build_project_table(projects: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per project identifier (grant agreement id = bulk 'projectID'). Projects are keyed by the identifier
    literal, not the node IRI: the endpoint fails to sort / STR() some Project IRIs (Virtuoso SR477 / SR014). Several
    nodes with one identifier are merged (alphabetically first value per field; counted as multi_*)."""
    idx = pd.Index(sorted(set(projects["project_id"].dropna())), name="project_id")
    out = pd.DataFrame(index=idx)
    st: dict = {"project_ids": len(idx), "rows": int(len(projects))}
    for col in ("acronym", "start_date", "signature_date"):
        out[col], st[f"multi_{col}"] = _first_sorted(projects, "project_id", col, idx)
    g = projects[["project_id", "grant_uuid"]].dropna().drop_duplicates()
    st["multi_grant"] = int(g.groupby("project_id").size().gt(1).sum())
    st["no_grant"] = int((~idx.isin(g["project_id"])).sum())
    return out, st


def programme_of(value: str | None) -> str | None:
    m = PROGRAMME_PREFIX_RE.match(str(value or "").strip())
    return m.group(1).upper() if m else None


def derive_programmes(projects: pd.DataFrame, schemes: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Framework programme per project (FP7 / H2020 / HORIZON), deterministic cascade over its grant(s):

    1. topic identifier prefix ('H2020_...', 'FP7-...', 'HORIZON-...'), 2. call identifier prefix, 3. programme code
       prefix; the first step with evidence decides, two programmes at that step = 'conflict' (None).
    4. funding-scheme type code, mapped through the codes whose step-1..3 projects all have one programme
       ('MC-IEF' -> FP7, 'RIA' -> H2020; 'CSA' is used by FP7 and H2020 and so maps to nothing).
    5. numeric project id inside exactly one programme's [min, max] id range of step-1..3 projects.
    Otherwise None ('unresolved'); such projects are excluded from the outputs (FP5/FP6 leftovers in the graph).

    projects: the raw 'projects' dataset (project_id, grant_uuid, ...); schemes: kind -> raw grant_<kind> dataset
    (grant_uuid, value). Returns frame [project_id, programme, programme_step] and stats.
    """
    pg = projects[["project_id", "grant_uuid"]].dropna().drop_duplicates()
    ev = {}
    for kind in ("topic", "call", "programme", "type"):
        s = schemes.get(kind)
        if s is None or s.empty:
            ev[kind] = pd.DataFrame(columns=["project_id", "value"])
            continue
        ev[kind] = pg.merge(s.dropna().drop_duplicates(), on="grant_uuid")[["project_id", "value"]]
    ids = pd.Index(sorted(set(projects["project_id"].dropna())), name="project_id")
    prog = pd.Series([None] * len(ids), index=ids, dtype=object)
    step = pd.Series(["unresolved"] * len(ids), index=ids, dtype=object)

    for kind, label in (("topic", "topic"), ("call", "call"), ("programme", "programme_code")):
        e = ev[kind].assign(p=ev[kind]["value"].map(programme_of)).dropna(subset=["p"])
        sets = e.groupby("project_id")["p"].agg(lambda s: sorted(set(s)))
        todo = sets.index[sets.index.isin(ids) & (step.reindex(sets.index) == "unresolved").values]
        for u in todo:
            ps = sets[u]
            prog[u], step[u] = (ps[0], label) if len(ps) == 1 else (None, "conflict")
    direct = step.isin(["topic", "call", "programme_code"])

    t = ev["type"].merge(prog[direct].rename("p"), left_on="project_id", right_index=True)
    code_sets = t.groupby("value")["p"].agg(lambda s: sorted(set(s)))
    code_map = {c: ps[0] for c, ps in code_sets.items() if len(ps) == 1}
    tt = ev["type"][ev["type"]["project_id"].isin(ids[(step == "unresolved").values])]
    tt = tt.assign(p=tt["value"].map(code_map)).dropna(subset=["p"])
    for u, ps in tt.groupby("project_id")["p"].agg(lambda s: sorted(set(s))).items():
        if len(ps) == 1:
            prog[u], step[u] = ps[0], "type_code"

    num = pd.Series(pd.to_numeric([v if re.fullmatch(r"\d+", v) else None for v in ids], errors="coerce"), index=ids)
    ranges = {p: (int(num[direct & (prog == p)].min()), int(num[direct & (prog == p)].max()))
              for p in sorted(set(prog[direct].dropna())) if num[direct & (prog == p)].notna().any()}
    for u in ids[(step == "unresolved").values]:
        v = num[u]
        if pd.isna(v):
            continue
        hits = [p for p, (lo, hi) in ranges.items() if lo <= v <= hi]
        if len(hits) == 1:
            prog[u], step[u] = hits[0], "id_range"

    out = pd.DataFrame({"project_id": ids, "programme": [p if isinstance(p, str) else None for p in prog],
                        "programme_step": step.values}, dtype=object)
    st = {"by_step": {k: int(v) for k, v in step.value_counts().sort_index().items()},
          "by_programme": {k: int(v) for k, v in prog.value_counts().sort_index().items()},
          "type_code_map_size": len(code_map),
          "type_codes_ambiguous": sorted(c for c, ps in code_sets.items() if len(ps) > 1),
          "id_ranges": {p: list(r) for p, r in ranges.items()}}
    return out, st


def build_role_table(roles: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per (OrganisationRole node, project_id). EC contribution = the largest EUR amount paid to the role (a
    role with two payments / amounts cannot be told apart as ecContribution vs netEcContribution; counted)."""
    r = roles.dropna(subset=["role_id"]).copy()
    st: dict = {"rows": int(len(roles)), "roles": int(r["role_id"].nunique()),
                "empty_role_id_rows": int(roles["role_id"].isna().sum())}
    amt = r["amount"].map(cordis.parse_amount)
    non_eur = r["currency"].notna() & (r["currency"].str.upper() != "EUR")
    st["non_eur_amounts"] = int(non_eur.sum())
    r["_amt"] = amt.where(~non_eur)
    r["project_id"] = r["project_id"].where(r["project_id"].notna(), "")    # '' = role of a node without identifier
    r["_key"] = r["role_id"] + "\t" + r["project_id"]
    st["roles_multi_project"] = int(r.drop_duplicates("_key").groupby("role_id").size().gt(1).sum())
    idx = pd.Index(sorted(r["_key"].unique()), name="_key")
    out = pd.DataFrame(index=idx)
    for col in ("role_id", "project_id", "org_id", "role_label", "order"):
        out[col], st[f"multi_{col}"] = _first_sorted(r, "_key", col, idx)
    n_amt = r.dropna(subset=["_amt"]).groupby("_key")["_amt"].nunique()
    st["roles_multi_amount"] = int(n_amt.gt(1).sum())
    best = r.dropna(subset=["_amt"]).sort_values(["_key", "_amt"], ascending=[True, False], kind="mergesort")
    best = best.drop_duplicates("_key").set_index("_key")
    out["ec_contribution"] = best["amount"].reindex(idx)
    st["roles_without_amount"] = int(out["ec_contribution"].isna().sum())
    out["project_id"] = out["project_id"].replace("", None)
    st["roles_without_project_id"] = int(out["project_id"].isna().sum())
    return out.reset_index(drop=True), st


def build_participations_sparql(frames: dict[str, pd.DataFrame], programmes: list[str] | None = None
                                ) -> tuple[pd.DataFrame, dict]:
    """Join roles x organisations x projects (+ derived programme) and normalise per programme with
    cordis.build_participations (same parsing, dedupe, public_ts and domain rules as the bulk collector)."""
    programmes = programmes or PROGRAMMES
    orgs, org_st = build_org_table(frames["organisations"], frames["organisation_types"])
    proj, proj_st = build_project_table(frames["projects"])
    schemes = {k: frames.get(f"grant_{k}") for k in SCHEME_KINDS}
    progs, prog_st = derive_programmes(frames["projects"], schemes)
    roles, role_st = build_role_table(frames["roles"])

    proj = proj.join(progs.set_index("project_id"))
    df = roles.merge(orgs, left_on="org_id", right_index=True, how="left", indicator="_org")
    df = df.merge(proj, left_on="project_id", right_index=True, how="left", indicator="_proj")
    role_st["org_join_rate"] = float((df["_org"] == "both").mean()) if len(df) else 0.0
    role_st["project_join_rate"] = float((df["_proj"] == "both").mean()) if len(df) else 0.0
    # role whose organisation node is missing: keep it, identified by the graph uuid
    df["organisation_id"] = df["organisation_id"].where(df["organisation_id"].notna(), "kg:" + df["org_id"].astype(str))
    role_st["roles_by_programme_step"] = {k: int(v) for k, v in df["programme_step"].fillna("no_project")
                                          .value_counts().sort_index().items()}
    role_st["roles_without_programme"] = int(df["programme"].isna().sum())

    parts, part_st = [], {}
    for p in programmes:
        d = df[df["programme"] == p].sort_values(["project_id", "organisation_id", "role_id"], kind="mergesort")
        org_frame = pd.DataFrame({
            "project_id": d["project_id"], "organisation_id": d["organisation_id"], "name": d["name"],
            "short_name": d["short_name"], "sme": d["sme"], "activity_type": d["activity_type"],
            "country": d["country"], "organization_url": d["organization_url"], "role": d["role_label"],
            "ec_contribution": d["ec_contribution"], "order": d["order"]}, dtype=object).reset_index(drop=True)
        pr = proj[proj["programme"] == p].reset_index()
        proj_frame = pd.DataFrame({"project_id": pr["project_id"], "start_date": pr["start_date"],
                                   "ec_signature_date": pr["signature_date"], "framework": p},
                                  dtype=object).reset_index(drop=True)
        # left-merge gaps are NaN; cordis.build_participations expects str|None (str(nan) would read as a value)
        org_frame, proj_frame = (f.where(f.notna(), None) for f in (org_frame, proj_frame))
        part, st = cordis.build_participations(org_frame, proj_frame, p)
        parts.append(part)
        part_st[p] = st
    part = (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cordis.PART_COLUMNS))
    part = part.sort_values(["programme", "project_id", "organisation_id"], kind="mergesort").reset_index(drop=True)
    pii = {c: int(part[c].astype("string").str.contains(cordis.EMAIL_IN_URL_RE.pattern, regex=True, na=False).sum()
                  + part[c].astype("string").str.contains(PHONE_RE.pattern, regex=True, na=False).sum())
           for c in ("name", "short_name", "organization_url")}
    for c, n in pii.items():   # scrub (expected 0): no e-mail / phone-like strings in processed text columns
        if n:
            bad = (part[c].astype("string").str.contains(cordis.EMAIL_IN_URL_RE.pattern, regex=True, na=False)
                   | part[c].astype("string").str.contains(PHONE_RE.pattern, regex=True, na=False))
            part.loc[bad, c] = None
            if c == "organization_url":
                part.loc[bad, "domain"] = None
    stats = {"organisations": org_st, "projects": proj_st, "framework": prog_st, "roles": role_st,
             "participations": part_st, "email_or_phone_like_scrubbed": pii}
    return part[cordis.PART_COLUMNS], stats


# ======================================================================================================================
def coverage(part: pd.DataFrame, comp: pd.DataFrame) -> dict:
    """Numbers for the report: per programme rows / projects / organisations, % url, % PRC, PRC domains,
    % missing signature date; first_public_ts histogram of PRC company domains."""
    out = {}
    for p, d in list(part.groupby("programme", sort=True)) + [("ALL", part)]:
        prc = d["activity_type"] == "PRC"
        out[p] = {"participations": int(len(d)), "projects": int(d["project_id"].nunique()),
                  "organisations": int(d["organisation_id"].nunique()),
                  "pct_url": round(100 * d["organization_url"].notna().mean(), 1),
                  "pct_domain": round(100 * d["domain"].notna().mean(), 1),
                  "pct_PRC": round(100 * prc.mean(), 1),
                  "pct_PRC_with_domain": round(100 * d.loc[prc, "domain"].notna().mean(), 1) if prc.any() else None,
                  "PRC_domains": int(d.loc[prc, "domain"].nunique()),
                  "pct_missing_signature": round(100 * d["ec_signature_date"].isna().mean(), 1),
                  "pct_missing_public_ts": round(100 * d["public_ts"].isna().mean(), 2)}
    hist = comp["first_public_ts"].dt.year.value_counts().sort_index()
    out["first_public_ts_by_year"] = {int(y): int(n) for y, n in hist.items()}
    out["companies"] = int(len(comp))
    return out


def run(sp: Sparql, out_dir: Path, page_size: int = MAX_PAGE_SIZE, max_pages: int | None = None,
        programmes: list[str] | None = None, robots: dict | None = None, write: bool = True
        ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    t0 = time.monotonic()
    frames, fetch_st = collect(sp, page_size, max_pages)
    part, st = build_participations_sparql(frames, programmes)
    comp = cordis.build_companies(part)
    stats = {"fetch": fetch_st, **st, "coverage": coverage(part, comp), "robots": robots,
             "requests_total": len(sp.log), "network_requests": sum(not e.from_cache for e in sp.log),
             "seconds_total": round(time.monotonic() - t0, 1)}
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        p_out, c_out = out_dir / "cordis_participations.parquet", out_dir / "cordis_companies.parquet"
        part.to_parquet(p_out, index=False)
        comp.to_parquet(c_out, index=False)
        partial = any(s["partial"] for s in fetch_st.values())
        raw_inputs = sorted({Path(e.raw_path) for e in sp.log if Path(e.raw_path).exists()})
        params = {"endpoint": sp.endpoint, "programmes": programmes or PROGRAMMES, "page_size": page_size,
                  "max_pages": max_pages, "partial": partial, "robots": robots,
                  "datasets": {k: {kk: v[kk] for kk in ("rows", "expected", "pages", "query_sha8", "fetched_at_min",
                                                          "fetched_at_max")} for k, v in fetch_st.items()},
                  "framework": st["framework"], "organisations": st["organisations"], "roles": st["roles"]}
        common = dict(script=SCRIPT, inputs=raw_inputs, sources=[sp.endpoint])
        write_meta(p_out, rows=len(part), params={**params, "parse_stats": st["participations"]},
                   notes=("PARTIAL (smoke) " if partial else "") +
                   "one row per (programme, project_id, organisation_id) from the CORDIS SPARQL endpoint; programme "
                   "derived per project (topic/call/programme-code prefix, type code, id range); activity_type and sme "
                   "are organisation-level classes; public_ts = signatureDate else startDate (00:00 UTC); no contact "
                   "field selected; e-mail-like url values filtered server-side; country UK->GB, EL->GR; " + LICENSE,
                   **common)
        write_meta(c_out, rows=len(comp), params={"programmes": programmes or PROGRAMMES, "partial": partial,
                                                  "filter": "activity_type == 'PRC' and domain"},
                   notes=("PARTIAL (smoke) " if partial else "") + "one row per company domain; country = mode over "
                   "participations (ties alphabetical); multi_country flags domains seen with >1 country; " + LICENSE,
                   **common)
    return part, comp, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--programmes", nargs="+", default=PROGRAMMES, choices=PROGRAMMES)
    ap.add_argument("--page-size", type=int, default=MAX_PAGE_SIZE, help=f"rows per request (max {MAX_PAGE_SIZE})")
    ap.add_argument("--max-pages", type=int, help="smoke test: at most N pages per dataset (partial; no COUNT check)")
    ap.add_argument("--refresh", action="store_true", help="evict cached responses and fetch every page again")
    ap.add_argument("--cache-only", action="store_true", help="no network: parse from the HTTP cache only")
    ap.add_argument("--out-dir", type=Path, help=f"default {PROCESSED}; required with --max-pages")
    ap.add_argument("--raw-dir", type=Path, default=RAW_DIR, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.max_pages is not None and args.out_dir is None:
        ap.error("--max-pages produces partial tables: pass --out-dir (they never overwrite data/processed)")
    if args.refresh and args.cache_only:
        ap.error("--refresh and --cache-only exclude each other")
    out_dir = args.out_dir or PROCESSED

    sp = Sparql(raw_dir=args.raw_dir, refresh=args.refresh, cache_only=args.cache_only)
    robots = None
    if not args.cache_only:   # every query URL that can be sent (count + page URLs differ only in LIMIT/OFFSET digits)
        urls = [sp.request_url(q) for ds in DATASETS.values()
                for q in (build_count_query(ds), build_page_query(ds, args.page_size, 0))]
        robots = check_robots(sp.client, urls)
    part, comp, stats = run(sp, out_dir, args.page_size, args.max_pages, args.programmes, robots)
    print(json.dumps({k: stats[k] for k in ("robots", "fetch", "framework", "organisations", "projects", "roles",
                                             "email_or_phone_like_scrubbed")}, indent=1, default=str))
    print(json.dumps({p: {k: s[k] for k in ("org_rows", "project_rows", "join_rate", "unparsed_dates",
                                            "url_email_like_dropped", "duplicate_key_rows", "unknown_activity_type",
                                            "dropped_no_ids", "rows")} for p, s in stats["participations"].items()},
                     indent=1))
    print(json.dumps(stats["coverage"], indent=1))
    print(cordis.summarize(part, comp))
    print(f"\nrequests: {stats['requests_total']} ({stats['network_requests']} network), {stats['seconds_total']} s")
    print(f"wrote {out_dir / 'cordis_participations.parquet'} ({len(part)} rows), "
          f"{out_dir / 'cordis_companies.parquet'} ({len(comp)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
