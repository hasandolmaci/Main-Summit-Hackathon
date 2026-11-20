"""HTGF (High-Tech Gruenderfonds) portfolio -> label records with dated evidence of the HTGF investment.

Discovery (structure only, see the module tests):
- robots.txt allows everything except /xmlrpc.php and sets `Content-Signal: ai-input=no`, so every step here is
  deterministic parsing; no model reads page text.
- sitemap_index.xml -> portfolio-sitemap*.xml lists every portfolio page (German + /en/ translations).
- The WordPress REST API exposes the `portfolio` post type (/wp-json/wp/v2/portfolio): title, link, publish date,
  language + translation ids and `class_list` (taxonomy term slugs, e.g. `sectors-<slug>`). The rendered content
  is empty, so the facts come from the portfolio page itself.
- Portfolio page sections used (by element id / class):
    #key-facts                 table rows "Invested since | YYYY", "Based in | <city>", "Stage | ..."
    #main-info-desktop         buttons: sector(s), focus areas, German federal state
    #company-info              external link(s): the company website
    #portfolio-connected-news  HTGF news posts linked to this company (date "D Month YYYY" + post URL)

Evidence of the HTGF investment (earliest wins, `date_precision` recorded):
    invested_since  key-facts year         -> YYYY-01-01, precision "year", source_url = portfolio page
    news            earliest linked post   -> that day,  precision "day",  source_url = the news post
    entry           REST publish date (min over the DE/EN pair) unless it falls on a bulk-import day
                    (>= BULK_DAY_MIN portfolio posts of one language published the same day, i.e. a CMS migration)
                                           -> that day,  precision "day",  source_url = portfolio page
A company with no dated evidence is kept in the provenance with an empty date and never enters companies.csv.

Raw files (data/raw/htgf/, gzip, mtime=0, each with .meta.json):
    robots.txt, sitemaps.jsonl.gz, portfolio_rest.jsonl.gz (REST pages, byte-identical response text),
    portfolio_pages.jsonl.gz: per page only the four sections above, re-serialised from the parsed HTML.
    The page's manager-contact block, description text, mailto:/tel: links, e-mail addresses and phone numbers are
    NOT stored (no personal contact data on disk; the HTTP cache under data/cache is gitignored).
"""
from __future__ import annotations

import gzip
import html as htmllib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib import robotparser

from scripts.lib.config import RAW, cfg, get_logger
from scripts.lib.domains import company_domain
from scripts.lib.http import Client
from scripts.lib.meta import write_meta

log = get_logger("htgf")

BASE = "https://www.htgf.de"
ROBOTS_URL = BASE + "/robots.txt"
SITEMAP_INDEX = BASE + "/sitemap_index.xml"
REST_URL = BASE + "/wp-json/wp/v2/portfolio"
PER_PAGE = 100
SCRIPT = "scripts/sources/htgf.py"
RAW_DIR = RAW / "htgf"

HTGF_FOUNDED = 2005
BULK_DAY_MIN = 10           # >= this many portfolio posts (one language) on one day = migration, not evidence
STATE_IMPLIES_DE = True     # a German federal state button counts as the page saying "Germany"
SECTOR_TAXONOMIES = ("sectors-", "branchen22-")   # class_list prefixes, in order of preference

SECTION_IDS = ("key-facts", "main-info-desktop", "company-info", "portfolio-connected-news")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

_LABEL_INVESTED = {"invested since", "investiert seit", "beteiligt seit", "investment seit", "investment since",
                   "in portfolio since", "im portfolio seit"}
_LABEL_BASED = {"based in", "sitz", "standort", "location", "headquarters"}
_LABEL_STAGE = {"stage", "phase", "status"}

GERMAN_STATES = {
    "baden-württemberg", "baden-wuerttemberg", "bayern", "bavaria", "berlin", "brandenburg", "bremen", "hamburg",
    "hessen", "hesse", "mecklenburg-vorpommern", "mecklenburg-western pomerania", "niedersachsen", "lower saxony",
    "nordrhein-westfalen", "north rhine-westphalia", "rheinland-pfalz", "rhineland-palatinate", "saarland",
    "sachsen", "saxony", "sachsen-anhalt", "saxony-anhalt", "schleswig-holstein", "thüringen", "thueringen",
    "thuringia",
}

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], 1)}
_MONTHS.update({m: i for i, m in enumerate(
    ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober", "november",
     "dezember"], 1)})
_MONTHS.update({"maerz": 3, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
                "sept": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12})

_COMPANY_PAGE_RE = re.compile(r"/portfolio/htgffamily/[^/?#]+/?$")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d ()/.-]{7,}\d")


# ---------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------
def slugify(text: str) -> str:
    """WordPress-like slug used only to match a button label to a taxonomy term slug."""
    s = unicodedata.normalize("NFKD", (text or "").replace("&", "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def parse_news_date(text: str) -> date | None:
    """'7 March 2016' / '07. März 2016' / 'March 7, 2016' / '2016-03-07' -> date. None if not parseable."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.strip().lower())
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})\.? ([a-zä]+)\.? (\d{4})", t)
        if m:
            d, mon, y = int(m.group(1)), m.group(2), int(m.group(3))
        else:
            m = re.fullmatch(r"([a-zä]+)\.? (\d{1,2}),? (\d{4})", t)
            if not m:
                return None
            mon, d, y = m.group(1), int(m.group(2)), int(m.group(3))
        mo = _MONTHS.get(mon)
        if mo is None:
            return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _gz_jsonl(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fb, gzip.GzipFile(filename=path.name[:-3], mode="wb", fileobj=fb, mtime=0) as f:
        for r in records:
            f.write((json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def read_gz_jsonl(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------------------------------------------
# robots + sitemaps
# ---------------------------------------------------------------------------------------------------------------
def parse_robots(text: str, user_agent: str) -> tuple[robotparser.RobotFileParser, dict]:
    rp = robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    signals = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if k.strip().lower() == "content-signal":
            for part in v.split(","):
                kk, _, vv = part.strip().partition("=")
                if kk:
                    signals[kk.strip()] = vv.strip()
    return rp, signals


def parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (child sitemap locs, url locs) of a sitemap index or urlset."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    sitemaps = [s.findtext(ns + "loc").strip() for s in root.findall(ns + "sitemap") if s.findtext(ns + "loc")]
    urls = [u.findtext(ns + "loc").strip() for u in root.findall(ns + "url") if u.findtext(ns + "loc")]
    return sitemaps, urls


# ---------------------------------------------------------------------------------------------------------------
# page parsing
# ---------------------------------------------------------------------------------------------------------------
class _PageParser(HTMLParser):
    """Collects the four sections by id; ignores everything else (e.g. other companies' cards, manager contact).

    Also re-serialises the sections (company-info: links only) for the raw fragment file, dropping mailto:/tel:
    links and e-mail / phone-number strings.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sec = None
        self.depth = 0
        self.frag: dict[str, list[str]] = {s: [] for s in SECTION_IDS}
        self.seen = set()
        # extracted
        self.kf_rows: list[list[str]] = []
        self._cells: list[str] | None = None
        self._cell: str | None = None
        self.buttons: list[str] = []
        self._btn: str | None = None
        self.links: list[str] = []
        self._ci_link: bool = False
        self.n_results: int | None = None
        self.news: list[dict] = []
        self._post: dict | None = None
        self._date: str | None = None

    # -- serialisation ------------------------------------------------------------------------------------------
    def _emit_start(self, tag, attrs):
        keep = []
        for k, v in attrs:
            if k in ("class", "id", "href", "datetime") and v is not None:
                if k == "href" and re.match(r"^(mailto|tel):", v.strip(), re.I):
                    continue
                keep.append(f' {k}="{htmllib.escape(v, quote=True)}"')
        self.frag[self.sec].append(f"<{tag}{''.join(keep)}>")

    def _emit_text(self, data):
        data = _PHONE_RE.sub("[removed]", _EMAIL_RE.sub("[removed]", data))
        self.frag[self.sec].append(htmllib.escape(data, quote=False))

    # -- parser callbacks ---------------------------------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if self.sec is None:
            if a.get("id") in SECTION_IDS and a.get("id") not in self.seen:
                self.sec, self.depth = a["id"], 0
                self.seen.add(self.sec)
                self.frag[self.sec].append(f'<div id="{self.sec}">')
            return
        if tag not in _VOID:
            self.depth += 1
        sec = self.sec
        if sec != "company-info":
            self._emit_start(tag, attrs)
        if sec == "key-facts":
            if tag == "tr":
                self._cells = []
            elif tag in ("td", "th") and self._cells is not None:
                self._cell = ""
        elif sec == "main-info-desktop":
            if tag == "a" and "wp-block-button__link" in cls:
                self._btn = ""
        elif sec == "company-info":
            href = (a.get("href") or "").strip()
            if tag == "a" and re.match(r"^https?://", href, re.I):
                self.links.append(href)
                self._ci_link = True
                self._emit_start(tag, [("href", href)])
        elif sec == "portfolio-connected-news":
            if tag == "section":
                for c in cls:
                    m = re.fullmatch(r"has-(\d+)-results", c)
                    if m:
                        self.n_results = int(m.group(1))
            if tag == "div" and "htgf-block-list-posts__post" in cls:
                self._post = {"href": None, "date_text": None}
                self.news.append(self._post)
            elif tag == "a" and "htgf-block-list-posts__link" in cls and self._post is not None:
                if self._post["href"] is None:
                    self._post["href"] = a.get("href")
            elif tag == "p" and "htgf-block-list-posts__date" in cls and self._post is not None:
                self._date = ""

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.sec is None:
            return
        sec = self.sec
        if sec == "key-facts":
            if tag in ("td", "th") and self._cell is not None:
                self._cells.append(re.sub(r"\s+", " ", self._cell).strip())
                self._cell = None
            elif tag == "tr" and self._cells is not None:
                self.kf_rows.append(self._cells)
                self._cells = None
        elif sec == "main-info-desktop":
            if tag == "a" and self._btn is not None:
                self.buttons.append(re.sub(r"\s+", " ", self._btn).strip())
                self._btn = None
        elif sec == "company-info":
            if tag == "a" and self._ci_link:
                self.frag[sec].append("</a>")
                self._ci_link = False
        elif sec == "portfolio-connected-news":
            if tag == "p" and self._date is not None:
                if self._post is not None and self._post["date_text"] is None:
                    self._post["date_text"] = re.sub(r"\s+", " ", self._date).strip()
                self._date = None
        if self.depth == 0:
            self.frag[sec].append("</div>")
            self.sec = None
            return
        if tag not in _VOID:
            self.depth -= 1
        if sec != "company-info":
            self.frag[sec].append(f"</{tag}>")

    def handle_data(self, data):
        if self.sec is None:
            return
        if self.sec == "company-info":
            if self._ci_link:
                self._emit_text(data)
            return
        self._emit_text(data)
        if self._cell is not None:
            self._cell += data
        if self._btn is not None:
            self._btn += data
        if self._date is not None:
            self._date += data


def parse_page(html: str) -> dict:
    """Deterministic extraction from a portfolio page (full page or stored fragments)."""
    p = _PageParser()
    p.feed(html or "")
    p.close()
    facts = {}
    for row in p.kf_rows:
        if len(row) >= 2:
            facts.setdefault(row[0].strip().rstrip(":").lower(), row[1])
    invested_raw = next((v for k, v in facts.items() if k in _LABEL_INVESTED), None)
    invested = None
    if invested_raw is not None:
        m = re.fullmatch(r"\s*(\d{4})\s*", invested_raw)
        invested = int(m.group(1)) if m else None
    based_in = next((v for k, v in facts.items() if k in _LABEL_BASED), "")
    stage = next((v for k, v in facts.items() if k in _LABEL_STAGE), "")
    website = None
    for href in p.links:
        d = company_domain(href)
        if d and d != "htgf.de":
            website = href
            break
    news = []
    for n in p.news:
        d = parse_news_date(n["date_text"] or "")
        news.append({"href": n["href"], "date": d.isoformat() if d else None})
    fragments = "".join("".join(p.frag[s]) for s in SECTION_IDS if p.frag[s])
    return {
        "has_key_facts": "key-facts" in p.seen,
        "key_fact_labels": sorted(facts),
        "invested_since_raw_present": invested_raw is not None,
        "invested_since": invested,
        "based_in": based_in,
        "stage": stage,
        "buttons": p.buttons,
        "website": website,
        "n_links": len(p.links),
        "news": news,
        "news_n_results": p.n_results,
        "fragments": fragments,
    }


# ---------------------------------------------------------------------------------------------------------------
# record assembly
# ---------------------------------------------------------------------------------------------------------------
def page_country(parsed: dict) -> tuple[str, str]:
    """('DE', 'state') etc. Germany only when the page says so (key facts, or a German federal state button)."""
    from scripts.collect_labels import country_iso2
    based = parsed.get("based_in") or ""
    for tok in reversed([t.strip() for t in re.split(r"[,/()]", based) if t.strip()]):
        iso = country_iso2(tok)
        if iso:
            return iso, "based_in"
    if STATE_IMPLIES_DE and any(b.strip().lower() in GERMAN_STATES for b in parsed.get("buttons", [])):
        return "DE", "state"
    return "", "none"


def page_sector(parsed: dict, class_list: list[str]) -> str:
    """Sector label as shown on the page: the first button whose slug is one of the post's `sectors-<slug>` terms,
    else one of its older `branchen22-<slug>` terms ('' if none)."""
    for prefix in SECTOR_TAXONOMIES:
        slugs = {c[len(prefix):] for c in class_list or [] if c.startswith(prefix)}
        for b in parsed.get("buttons", []):
            if slugify(b) in slugs:
                return b
    return ""


def bulk_days(rest_rows: list[dict], threshold: int = BULK_DAY_MIN) -> set[tuple[str, str]]:
    c = Counter((r.get("lang") or "", (r.get("date_gmt") or "")[:10]) for r in rest_rows)
    return {k for k, n in c.items() if n >= threshold}


def translation_groups(rest_rows: list[dict]) -> list[dict]:
    """Group DE/EN translations: {'key', 'rows': {lang: row}, 'page': row whose page is parsed (EN, else DE)}."""
    by_id = {r["id"]: r for r in rest_rows}
    groups: dict[int, dict] = {}
    for r in rest_rows:
        ids = [i for i in (r.get("translations") or {}).values() if isinstance(i, int)] + [r["id"]]
        key = min(ids)
        g = groups.setdefault(key, {"key": key, "rows": {}})
        g["rows"][r.get("lang") or ""] = r
        for i in ids:
            if i in by_id:
                g["rows"].setdefault(by_id[i].get("lang") or "", by_id[i])
    out = []
    for key in sorted(groups):
        g = groups[key]
        g["page"] = g["rows"].get("en") or g["rows"].get("de") or next(iter(g["rows"].values()))
        out.append(g)
    return sorted(out, key=lambda g: g["page"]["link"])


def htgf_evidence(group: dict, parsed: dict, bulk: set, today: date) -> dict:
    """Earliest dated public evidence of the HTGF investment for one company (pure function)."""
    page = group["page"]
    cands, notes = [], []
    y = parsed.get("invested_since")
    if y is not None:
        if HTGF_FOUNDED <= y <= today.year:
            cands.append((date(y, 1, 1), 1, "year", page["link"], "invested_since"))
            notes.append(f"invested_since={y}")
        else:
            notes.append(f"invested_since_out_of_range={y}")
    news = [n for n in parsed.get("news", []) if n.get("date") and n.get("href")]
    if news:
        first = min(news, key=lambda n: (n["date"], n["href"]))
        d = date.fromisoformat(first["date"])
        if date(HTGF_FOUNDED, 1, 1) <= d <= today:
            cands.append((d, 0, "day", first["href"], "news"))
        notes.append(f"news={first['date']}(n={len(news)})")
    entry = []
    for lang, r in sorted(group["rows"].items()):
        day = (r.get("date_gmt") or "")[:10]
        if not day:
            continue
        if (lang, day) in bulk:
            notes.append(f"entry_{lang}={day}(bulk_import_day)")
            continue
        entry.append(date.fromisoformat(day))
    if entry:
        d = min(entry)
        cands.append((d, 0, "day", page["link"], "entry"))
        notes.append(f"entry={d.isoformat()}")
    if not cands:
        return {"htgf_date": "", "date_precision": "", "source_url": page["link"], "evidence": ";".join(notes),
                "kind": ""}
    d, _, prec, url, kind = min(cands)   # earliest; on a tie the day-precision evidence wins
    if y is not None and kind != "invested_since" and d.year < y:
        notes.append("conflict=earlier_than_invested_since")   # kept (earliest evidence), flagged + counted
    return {"htgf_date": d.isoformat(), "date_precision": prec, "source_url": url,
            "evidence": f"chosen={kind};" + ";".join(notes), "kind": kind}


def htgf_records(groups: list[dict], parsed_by_link: dict[str, dict | None], rest_rows: list[dict],
                 today: date, counts: Counter | None = None) -> list[dict]:
    counts = counts if counts is not None else Counter()
    bulk = bulk_days(rest_rows)
    rows = []
    for g in groups:
        page = g["page"]
        counts["htgf_records"] += 1
        parsed = parsed_by_link.get(page["link"])
        name = htmllib.unescape(((page.get("title") or {}).get("rendered") or "")).strip()
        base = {"company": name, "label_source": "htgf", "batch": "", "batch_start": "", "acceptance_proxy": "",
                "yc_status": "", "first_funding_date": "", "round_type": "seed"}
        if parsed is None:
            counts["drop:htgf_page_fetch_failed"] += 1
            rows.append({**base, "domain": "", "htgf_date": "", "date_precision": "", "source_url": page["link"],
                         "sector": "", "country": "", "evidence": "", "excluded_reason": "htgf_page_fetch_failed"})
            continue
        ev = htgf_evidence(g, parsed, bulk, today)
        dom = company_domain(parsed.get("website")) if parsed.get("website") else None
        country, crule = page_country(parsed)
        counts[f"htgf_country_{crule}"] += 1
        counts["htgf_has_invested_since"] += parsed.get("invested_since") is not None
        counts["htgf_has_news_date"] += any(n.get("date") for n in parsed.get("news", []))
        counts["htgf_has_website"] += parsed.get("website") is not None
        if ev["kind"]:
            counts[f"htgf_chosen_{ev['kind']}"] += 1
        counts["htgf_conflict_earlier_than_invested_since"] += "conflict=" in ev["evidence"]
        reason = ""
        if not ev["htgf_date"]:
            reason = "htgf_no_dated_evidence"
        elif dom is None:
            reason = "no_company_domain"
        if reason:
            counts[f"drop:{reason}"] += 1
        rows.append({**base, "domain": dom or "", "htgf_date": ev["htgf_date"],
                     "date_precision": ev["date_precision"], "source_url": ev["source_url"],
                     "sector": page_sector(parsed, page.get("class_list")), "country": country,
                     "evidence": ev["evidence"], "excluded_reason": reason})
    return rows


# ---------------------------------------------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------------------------------------------
def fetch_rest(client: Client) -> tuple[list[dict], list[dict]]:
    """All portfolio posts via the REST API. Returns (rows, raw page records).

    Stops on a short page, or on HTTP 400 past page 1 (WordPress `rest_post_invalid_page_number` when the total is a
    multiple of PER_PAGE). Any other non-200 answer (e.g. a 5xx that survived the client's retries) raises instead of
    silently returning a truncated portfolio; the failed page is not cached, so a rerun resumes from the cache."""
    rows, raw = [], []
    page = 1
    while True:
        params = {"per_page": PER_PAGE, "page": page}
        r = client.get(REST_URL, params=params)
        if r.status == 400 and page > 1:
            break
        if r.status != 200:
            raise RuntimeError(f"HTGF REST {REST_URL} page {page} returned status {r.status}; refusing to continue "
                               "with a truncated portfolio")
        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"HTGF REST page {page} returned {type(data).__name__}, expected a list")
        raw.append({"url": REST_URL, "params": params, "status": r.status, "fetched_at": r.fetched_at,
                    "text": r.text})
        rows += data
        if len(data) < PER_PAGE:
            break
        page += 1
    return rows, raw


def collect(client: Client | None = None, limit: int | None = None,
            counts: Counter | None = None) -> tuple[list[dict], list[Path]]:
    """Fetch (cached, >= 2 s between requests) and parse the HTGF portfolio. Returns (provenance rows, raw paths)."""
    client = client or Client()
    counts = counts if counts is not None else Counter()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ua = client.session.headers["User-Agent"]

    rb = client.get(ROBOTS_URL)
    if rb.status != 200:
        raise RuntimeError(f"robots.txt status {rb.status}; refusing to crawl")
    rp, signals = parse_robots(rb.text, ua)
    robots_path = RAW_DIR / "robots.txt"
    robots_path.write_text(rb.text)
    write_meta(robots_path, script=SCRIPT, params={"url": ROBOTS_URL, "fetched_at": rb.fetched_at},
               sources=[ROBOTS_URL], notes=f"content signals {signals}: deterministic parsing only, no AI input.")
    for u in (SITEMAP_INDEX, REST_URL, BASE + "/en/portfolio/htgffamily/x/"):
        if not rp.can_fetch(ua, u):
            raise RuntimeError(f"robots.txt disallows {u}")

    # sitemaps (discovery + cross-check of the REST listing)
    sm_raw, sitemap_urls = [], set()
    r = client.get(SITEMAP_INDEX)
    if r.status != 200:
        raise RuntimeError(f"{SITEMAP_INDEX} returned status {r.status}")
    sm_raw.append({"url": SITEMAP_INDEX, "status": r.status, "fetched_at": r.fetched_at, "text": r.text})
    children, _ = parse_sitemap(r.text)
    for child in children:
        if "portfolio-sitemap" not in child:
            continue
        rc = client.get(child)
        if rc.status != 200:
            raise RuntimeError(f"{child} returned status {rc.status}")
        sm_raw.append({"url": child, "status": rc.status, "fetched_at": rc.fetched_at, "text": rc.text})
        sitemap_urls.update(parse_sitemap(rc.text)[1])
    sm_path = RAW_DIR / "sitemaps.jsonl.gz"
    _gz_jsonl(sm_path, sm_raw)
    write_meta(sm_path, script=SCRIPT, rows=len(sm_raw), sources=[SITEMAP_INDEX],
               notes="sitemap index + portfolio sitemaps, byte-identical text per line.")

    rest_rows, rest_raw = fetch_rest(client)
    rest_path = RAW_DIR / "portfolio_rest.jsonl.gz"
    _gz_jsonl(rest_path, rest_raw)
    write_meta(rest_path, script=SCRIPT, rows=len(rest_rows), sources=[REST_URL],
               params={"per_page": PER_PAGE, "pages": len(rest_raw)},
               notes="WordPress REST /wp/v2/portfolio, one response page per line (byte-identical text).")
    rest_links = {r["link"] for r in rest_rows}
    counts["htgf_rest_posts"] = len(rest_rows)
    counts["htgf_sitemap_urls"] = len(sitemap_urls)
    counts["htgf_rest_links_in_sitemap"] = len(rest_links & sitemap_urls)
    # truncation detector: company pages the sitemap lists but the REST listing lacks (the archive index is excluded)
    missing = {u for u in sitemap_urls - rest_links if _COMPANY_PAGE_RE.search(u)}
    counts["htgf_sitemap_company_urls_not_in_rest"] = len(missing)
    if missing:
        log.warning("htgf: %d sitemap company pages are missing from the REST listing", len(missing))

    groups = translation_groups(rest_rows)
    if limit is not None:
        groups = groups[:limit]
    parsed_by_link, page_raw = {}, []
    for i, g in enumerate(groups, 1):
        link = g["page"]["link"]
        if not rp.can_fetch(ua, link):
            counts["htgf_robots_disallowed"] += 1
            parsed_by_link[link] = None
            continue
        r = client.get(link)
        if r.status != 200:   # excluded as htgf_page_fetch_failed; only 200/404 are cached, so a rerun retries 5xx
            log.warning("htgf: page fetch failed status=%s %s", r.status, link)
            parsed_by_link[link] = None
            page_raw.append({"url": link, "status": r.status, "fetched_at": r.fetched_at, "fragments": ""})
            continue
        parsed = parse_page(r.text)
        parsed_by_link[link] = parsed
        page_raw.append({"url": link, "status": r.status, "fetched_at": r.fetched_at,
                         "fragments": parsed["fragments"]})
        if i % 100 == 0:
            log.info("htgf pages parsed: %d/%d", i, len(groups))
    pages_path = RAW_DIR / "portfolio_pages.jsonl.gz"
    _gz_jsonl(pages_path, page_raw)
    write_meta(pages_path, script=SCRIPT, rows=len(page_raw), sources=[BASE + "/en/portfolio/htgffamily/"],
               params={"sections": list(SECTION_IDS), "limit": limit},
               notes="Per portfolio page only the sections the parser reads, re-serialised (tags with class/id/href,"
                     " text); company-info keeps external links only. No manager contact block, no description, "
                     "no mailto/tel, e-mails and phone numbers replaced by [removed].")

    # structural validation counts (no text inspection)
    counts["htgf_pages_with_key_facts"] = sum(1 for p in parsed_by_link.values() if p and p["has_key_facts"])
    counts["htgf_pages_invested_since_label"] = sum(1 for p in parsed_by_link.values()
                                                    if p and p["invested_since_raw_present"])
    counts["htgf_pages_invested_since_parsed"] = sum(1 for p in parsed_by_link.values()
                                                     if p and p["invested_since"] is not None)
    n_dates = sum(1 for p in parsed_by_link.values() if p for n in p["news"])
    n_dates_ok = sum(1 for p in parsed_by_link.values() if p for n in p["news"] if n["date"])
    counts["htgf_news_items"] = n_dates
    counts["htgf_news_dates_parsed"] = n_dates_ok
    counts["htgf_news_count_matches_results_class"] = sum(
        1 for p in parsed_by_link.values() if p and p["news_n_results"] is not None
        and p["news_n_results"] == len(p["news"]))
    today = datetime.now().date()
    rows = htgf_records(groups, parsed_by_link, rest_rows, today, counts)
    return rows, [robots_path, sm_path, rest_path, pages_path]


if __name__ == "__main__":
    c = Counter()
    rows, _ = collect(counts=c)
    print(json.dumps(dict(sorted(c.items())), indent=1))
