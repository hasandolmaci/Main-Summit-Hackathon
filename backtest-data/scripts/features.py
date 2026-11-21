#!/usr/bin/env python3
"""Point-in-time features for a cohort CSV.

Usage: python scripts/features.py raw/cohort_utum.csv [--workers 5] [--no-github] [--no-crtsh]

Writes raw/features_<stem>.csv and raw/evidence_<stem>.csv.
Every feature uses only evidence dated strictly BEFORE the row's asof_date:
  - Wayback CDX captures / snapshots with timestamp < asof
  - crt.sh certificates with entry_timestamp (CT log time) < asof
  - HN stories with created_at_i < asof epoch
  - GitHub org created_at < asof, repos created_at < asof, commits in the 8 weeks before asof
Unknown / failed lookups = empty cell. HTTP responses are cached under raw/cache/features/.
Stdlib only.
"""
import csv
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "raw", "cache", "features")
UA = "Mozilla/5.0 (research script)"

# ----------------------------------------------------------------------------- HTTP


class HostLimiter:
    """Global minimum interval between requests to one host (shared by all threads)."""

    def __init__(self, interval):
        self.interval = interval
        self.lock = threading.Lock()
        self.next_t = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next_t)
            self.next_t = t + self.interval
        if t > now:
            time.sleep(t - now)


LIMITERS = {
    "web.archive.org": HostLimiter(1.0),
    "crt.sh": HostLimiter(2.0),
    "hn.algolia.com": HostLimiter(1.0),
    "api.github.com": HostLimiter(1.0),
}
GH_LOCK = threading.Lock()
GH_STATE = {"core": None, "search": None}  # (remaining, reset_epoch)
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()


def cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")


def gh_bucket(url):
    return "search" if "/search/" in url else "core"


def gh_pre_wait(url):
    """Sleep until reset if the relevant GitHub bucket is (nearly) exhausted."""
    b = gh_bucket(url)
    while True:
        with GH_LOCK:
            st = GH_STATE[b]
        if not st or st[0] > 1:
            return
        wait = st[1] - time.time() + 2
        if wait <= 0:
            with GH_LOCK:
                GH_STATE[b] = None
            return
        print(f"  [github] {b} rate limit low, sleeping {int(wait)} s", flush=True)
        time.sleep(min(wait, 300))


def gh_update(url, headers):
    rem = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    if rem is not None and reset is not None:
        with GH_LOCK:
            GH_STATE[gh_bucket(url)] = (int(rem), int(reset))


def fetch(url, timeout=30, retries=3, cache_404=True):
    """Return (status, body_text, from_cache). status None = network failure.
    Successful (200) and 404 responses are cached; errors are not (so reruns retry)."""
    p = cache_path(url)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return d["status"], d["body"], True
        except Exception:
            pass
    host = urllib.parse.urlsplit(url).hostname or ""
    lim = LIMITERS.get(host, HostLimiter(1.0))
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
    if host == "api.github.com":
        headers["Accept"] = "application/vnd.github+json"
        if GH_TOKEN:
            headers["Authorization"] = "Bearer " + GH_TOKEN
    status, body = None, None
    for attempt in range(retries + 1):
        if host == "api.github.com":
            gh_pre_wait(url)
        lim.wait()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
                hdrs = {k.lower(): v for k, v in r.headers.items()}
                status = r.status
            if enc == "gzip":
                raw = gzip.decompress(raw)
            elif enc == "deflate":
                raw = zlib.decompress(raw)
            if host == "api.github.com":
                gh_update(url, hdrs)
            body = decode(raw, hdrs.get("content-type", ""))
            break
        except urllib.error.HTTPError as e:
            status = e.code
            hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
            if host == "api.github.com":
                gh_update(url, hdrs)
                if e.code in (403, 429) and hdrs.get("x-ratelimit-remaining") == "0":
                    continue  # pre_wait will sleep until reset
            if e.code == 404 or e.code == 422:
                body = ""
                break
            if e.code in (429, 500, 502, 503, 504, 520, 522, 524, 403):
                time.sleep(min(60, 5 * (2 ** attempt)))
                continue
            body = None
            break
        except Exception:
            status = None
            time.sleep(min(60, 3 * (2 ** attempt)))
    if status == 200 or (status in (404, 422) and cache_404):
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = p + ".tmp%d" % threading.get_ident()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"url": url, "status": status, "body": body or "",
                       "retrieved_at": now_iso()}, f)
        os.replace(tmp, p)
    return status, body, False


def cached_retrieved_at(url):
    try:
        with open(cache_path(url), encoding="utf-8") as f:
            return json.load(f).get("retrieved_at", "")
    except Exception:
        return now_iso()


def decode(raw, ctype):
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    cands = []
    if m:
        cands.append(m.group(1))
    m2 = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:3000], re.I)
    if m2:
        cands.append(m2.group(1).decode("ascii", "ignore"))
    cands += ["utf-8"]
    for c in cands:
        try:
            return raw.decode(c)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_json(url, **kw):
    st, body, _ = fetch(url, **kw)
    if st != 200 or body is None:
        return st, None
    try:
        return st, json.loads(body)
    except Exception:
        return st, None


# ----------------------------------------------------------------------------- helpers

SOCIAL = ("linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
          "xing.com", "medium.com", "github.com", "crunchbase.com", "unternehmertum.de", "tum.de",
          "google.com", "bit.ly", "linktr.ee")
MULTI_SUFFIX = ("co.uk", "org.uk", "ac.uk", "com.au", "co.at", "or.at", "co.jp", "com.br", "co.nz",
                "com.tr", "co.il", "com.cn")
HOSTED = ("webflow.io", "wixsite.com", "notion.site", "carrd.co", "github.io", "framer.website",
          "framer.ai", "vercel.app", "netlify.app", "squarespace.com", "wordpress.com", "jimdofree.com",
          "jimdosite.com", "herokuapp.com", "my.canva.site", "super.site", "firebaseapp.com",
          "web.app", "pages.dev", "azurewebsites.net", "onrender.com", "glitch.me", "replit.app",
          "streamlit.app", "wixstudio.io", "myshopify.com", "weebly.com", "strikingly.com")


def registrable_domain(website):
    w = (website or "").strip()
    if not w:
        return ""
    if "://" not in w:
        w = "http://" + w
    host = (urllib.parse.urlsplit(w).hostname or "").lower().strip(".")
    if not host or "." not in host:
        return ""
    for h in HOSTED:
        if host == h or host.endswith("." + h):
            return host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    n = 3 if ".".join(parts[-2:]) in MULTI_SUFFIX else 2
    dom = ".".join(parts[-n:])
    if any(dom == s or dom.endswith("." + s) for s in SOCIAL):
        return ""
    return dom


def parse_date(s):
    return dt.date.fromisoformat(s.strip()[:10])


def ts_to_date(ts):
    return dt.date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))


def months_between(a, b):
    m = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        m -= 1
    return m


def norm_name(s):
    s = (s or "").lower()
    s = re.sub(r"\b(gmbh|ug|e\.?\s?v\.?|ag|inc|ltd|haftungsbeschr\w*|i\.?\s?g\.?)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


class TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.text = []
        self.links = []  # (href, anchor text)
        self._a = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        elif tag == "a":
            self._a = [dict(attrs).get("href") or "", ""]
        elif tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "section", "tr"):
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.depth = max(0, self.depth - 1)
        elif tag == "a" and self._a is not None:
            self.links.append(tuple(self._a))
            self._a = None

    def handle_data(self, data):
        if self.depth:
            return
        self.text.append(data)
        if self._a is not None:
            self._a[1] += data


RX = {
    "f_impressum": re.compile(r"impressum|imprint", re.I),
    "f_pricing_page": re.compile(r"pricing|preise|\bplans\b|tarife", re.I),
    "f_waitlist": re.compile(r"waitlist|wait list|warteliste|early access|\bbeta\b", re.I),
    "f_customer_logos": re.compile(r"trusted by|our customers|our clients|\bkunden\b|used by|vertrauen uns", re.I),
    "f_jobs_page": re.compile(r"careers|\bjobs\b|karriere|join our team|we.re hiring", re.I),
}
RX_LEGAL = re.compile(r"\b(GmbH|UG\s*\(haftungsbeschränkt\)|UG|AG|GbR|e\.\s?V\.|Ltd|Inc)(?![\w])")
RX_IG = re.compile(r"\bi\.\s?G\.|in Gründung", re.I)


def analyze_html(page):
    p = TextExtractor()
    try:
        p.feed(page)
        p.close()
    except Exception:
        pass
    text = re.sub(r"\s+", " ", html.unescape(" ".join(p.text))).strip()
    linkblob = " ".join(h + " " + t for h, t in p.links)
    out = {}
    hay = text + " " + linkblob
    for k, rx in RX.items():
        m = rx.search(hay)
        out[k] = (1 if m else 0, m.group(0) if m else "")
    m = RX_LEGAL.search(text)
    out["f_legal_entity"] = (re.sub(r"\s+", " ", m.group(1)) if m else "", m.group(0) if m else "")
    m = RX_IG.search(text)
    out["f_gmbh_ig"] = (1 if m else 0, m.group(0) if m else "")
    out["f_description_text"] = (text[:1500], "")
    return out


# ----------------------------------------------------------------------------- feature groups

CDX = "http://web.archive.org/cdx/search/cdx?"


def wayback_features(dom, asof, ev):
    f = {}
    q = urllib.parse.urlencode({"url": dom, "output": "json", "limit": "1", "fl": "timestamp", "from": "1996"})
    url = CDX + q
    st, js = get_json(url)
    if st == 200 and js is not None:
        if len(js) > 1:
            ts = js[1][0]
            d = ts_to_date(ts)
            ev("wb_first_capture_any", d.isoformat(), url, ts)
            if d < asof:
                f["f_first_capture"] = d.isoformat()
                f["f_age_months"] = months_between(d, asof)
                ev("f_first_capture", f["f_first_capture"], url, ts)
                ev("f_age_months", f["f_age_months"], url, ts)
            # first capture after asof -> no pre-asof evidence of the domain; leave empty
    # closest 200 snapshot strictly before asof
    to = (asof - dt.timedelta(days=1)).strftime("%Y%m%d")
    q = urllib.parse.urlencode({"url": dom, "output": "json", "limit": "-1",
                                "fl": "timestamp,original,statuscode", "filter": "statuscode:200",
                                "to": to, "from": "1996"})
    url = CDX + q
    st, js = get_json(url)
    keys = ["f_snapshot_date", "f_impressum", "f_legal_entity", "f_gmbh_ig", "f_pricing_page",
            "f_waitlist", "f_customer_logos", "f_jobs_page", "f_description_text"]
    if st != 200 or js is None:
        return f  # CDX failed -> unknown
    rows = [r for r in js[1:] if len(r) >= 2 and r[0] <= to + "999999"]
    if not rows:
        f["f_snapshot_missing"] = 1
        ev("f_snapshot_missing", 1, url, "")
        return f
    ts, orig = rows[-1][0], rows[-1][1]
    snap = f"http://web.archive.org/web/{ts}id_/{orig}"
    st, page, _ = fetch(snap, timeout=30)
    if st != 200 or not page:
        return f  # snapshot fetch failed -> unknown (not "missing")
    f["f_snapshot_missing"] = 0
    f["f_snapshot_date"] = ts_to_date(ts).isoformat()
    ev("f_snapshot_date", f["f_snapshot_date"], snap, ts)
    for k, (val, match) in analyze_html(page).items():
        f[k] = val
        if k != "f_description_text":
            ev(k, val if not match or val == match else f"{val} ({match})", snap, ts)
        else:
            ev(k, f"{len(val)} chars", snap, ts)
    return f


def crtsh_features(dom, asof, ev):
    certs = []
    urls = [f"https://crt.sh/?q=%25.{dom}&output=json", f"https://crt.sh/?q={dom}&output=json"]
    for u in urls:
        st, body, _ = fetch(u, timeout=40, retries=2, cache_404=False)
        if st != 200 or body is None:
            return {}, False  # crt.sh failed -> unknown
        try:
            certs.extend(json.loads(body) if body.strip() else [])
        except Exception:
            return {}, False
    f = {}
    before = []
    for c in certs:
        t = c.get("entry_timestamp") or c.get("not_before") or ""
        if not t:
            continue
        d = parse_date(t)
        if d < asof:
            before.append((d, c))
    f["f_certs_before"] = len({c.get("id") for _, c in before})
    ev("f_certs_before", f["f_certs_before"], urls[0], "")
    if before:
        d0, c0 = min(before, key=lambda x: x[0])
        f["f_first_cert_date"] = d0.isoformat()
        ev("f_first_cert_date", f["f_first_cert_date"], f"https://crt.sh/?id={c0.get('id')}", "")
    live = 0
    rx = re.compile(r"^(\*\.)?(app|api|dashboard|platform)\.", re.I)
    for d, c in sorted(before, key=lambda x: x[0]):
        names = (c.get("name_value") or "").split("\n") + [c.get("common_name") or ""]
        hit = [n for n in names if rx.match(n.strip()) and n.strip().lower().endswith(dom)]
        if hit:
            live = 1
            ev("f_product_live", f"1 ({hit[0].strip()} logged {d.isoformat()})",
               f"https://crt.sh/?id={c.get('id')}", "")
            break
    f["f_product_live"] = live
    if not live:
        ev("f_product_live", 0, urls[0], "")
    return f, True


def hn_features(dom, asof, ev):
    epoch = int(dt.datetime(asof.year, asof.month, asof.day, tzinfo=dt.timezone.utc).timestamp())
    q = urllib.parse.urlencode({"query": dom, "restrictSearchableAttributes": "url",
                                "numericFilters": f"created_at_i<{epoch}", "hitsPerPage": "100"})
    url = "https://hn.algolia.com/api/v1/search?" + q
    st, js = get_json(url)
    if st != 200 or js is None:
        return {}
    hits = []
    for h in js.get("hits", []):
        hu = (h.get("url") or "").lower()
        host = urllib.parse.urlsplit(hu).hostname or ""
        if (host == dom or host.endswith("." + dom)) and (h.get("created_at_i") or 0) < epoch:
            hits.append(h)
    f = {"f_hn_posts_before": len(hits),
         "f_hn_max_points": max((h.get("points") or 0) for h in hits) if hits else 0}
    ev("f_hn_posts_before", f["f_hn_posts_before"], url, "")
    if hits:
        top = max(hits, key=lambda h: h.get("points") or 0)
        ev("f_hn_max_points", f["f_hn_max_points"],
           f"https://news.ycombinator.com/item?id={top.get('objectID')}", top.get("created_at", ""))
    return f


GH = "https://api.github.com"


def gh_find_org(name, dom, ev):
    """Return (login, user_json, how) or (None, None, status) where status 'notfound'/'failed'."""
    failed = False
    if dom:
        url = f"{GH}/search/users?" + urllib.parse.urlencode({"q": f"{dom} in:blog type:org"})
        st, js = get_json(url)
        if js is None:
            failed = True
        else:
            for it in js.get("items", [])[:5]:
                st2, u = get_json(f"{GH}/users/{it['login']}")
                if u is None:
                    failed = failed or st2 != 404
                    continue
                if registrable_domain(u.get("blog") or "") == dom:
                    return it["login"], u, "blog", url
    nn = norm_name(name)
    if len(nn) >= 4:
        q = re.sub(r"\b(GmbH|UG|e\.V\.|AG|Inc|Ltd)\b", "", name).strip()
        url = f"{GH}/search/users?" + urllib.parse.urlencode({"q": f"{q} type:org"})
        st, js = get_json(url)
        if js is None:
            failed = True
        else:
            for it in js.get("items", [])[:10]:
                if norm_name(it["login"]) == nn:
                    st2, u = get_json(f"{GH}/users/{it['login']}")
                    if u is None:
                        failed = failed or st2 != 404
                        continue
                    ud = registrable_domain(u.get("blog") or "")
                    if dom and ud and ud != dom:
                        continue  # same name, different company
                    return it["login"], u, "name", url
    return None, None, ("failed" if failed else "notfound"), ""


def github_features(name, dom, asof, ev):
    login, u, how, surl = gh_find_org(name, dom, ev)
    if not login:
        return {}  # not found or failed -> unknown
    f = {}
    created = parse_date(u["created_at"])
    f["f_gh_org"] = login
    f["f_gh_match"] = how  # 'blog' = org blog field equals company domain; 'name' = login == company name
    f["f_gh_created"] = created.isoformat()
    f["f_gh_created_before_asof"] = 1 if created < asof else 0
    ev("f_gh_org", f"{login} (matched by {how})", surl, "")
    ev("f_gh_created", f["f_gh_created"], f"{GH}/users/{login}", u["created_at"])
    if created >= asof:
        # org did not exist at asof: no repos / commits possible before asof
        f["f_gh_public_repos_before"] = 0
        f["f_gh_commit_weeks_active"] = 0
        return f
    repos, page = [], 1
    while page <= 5:
        url = f"{GH}/orgs/{login}/repos?per_page=100&page={page}"
        st, js = get_json(url)
        if js is None:
            return f
        repos += js
        if len(js) < 100:
            break
        page += 1
    rb = [r for r in repos if parse_date(r["created_at"]) < asof]
    f["f_gh_public_repos_before"] = len(rb)
    ev("f_gh_public_repos_before", len(rb), f"{GH}/orgs/{login}/repos?per_page=100", "")
    if not rb:
        f["f_gh_commit_weeks_active"] = 0
        return f
    top = max(rb, key=lambda r: (r.get("stargazers_count") or 0, r.get("pushed_at") or ""))
    until = dt.datetime(asof.year, asof.month, asof.day, tzinfo=dt.timezone.utc)
    since = until - dt.timedelta(weeks=8)
    url = (f"{GH}/repos/{login}/{top['name']}/commits?" + urllib.parse.urlencode(
        {"since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "per_page": "100"}))
    st, js = get_json(url)
    if st == 409:  # empty repository
        js = []
    if js is None and st != 409:
        return f
    weeks = set()
    for c in js or []:
        d = ((c.get("commit") or {}).get("committer") or {}).get("date") or \
            ((c.get("commit") or {}).get("author") or {}).get("date")
        if d:
            dd = parse_date(d)
            if since.date() <= dd < asof:
                weeks.add(dd.isocalendar()[:2])
    f["f_gh_commit_weeks_active"] = len(weeks)
    ev("f_gh_commit_weeks_active", f"{len(weeks)} (repo {top['name']})", url, "")
    return f


# ----------------------------------------------------------------------------- main

FEATURES = ["f_domain", "f_first_capture", "f_age_months", "f_snapshot_missing", "f_snapshot_date",
            "f_impressum", "f_legal_entity", "f_gmbh_ig", "f_pricing_page", "f_waitlist",
            "f_customer_logos", "f_jobs_page", "f_first_cert_date", "f_certs_before", "f_product_live",
            "f_hn_posts_before", "f_hn_max_points", "f_gh_org", "f_gh_match", "f_gh_created",
            "f_gh_created_before_asof", "f_gh_public_repos_before", "f_gh_commit_weeks_active",
            "f_description_text"]


def process(row, opts):
    name, asof_s = row["name"], row["asof_date"]
    asof = parse_date(asof_s)
    evidence = []

    def ev(field, value, url, snap_ts):
        evidence.append({"name": name, "asof_date": asof_s, "field": field, "value": value, "url": url,
                         "snapshot_ts": snap_ts, "retrieved_at": cached_retrieved_at(url) if url else now_iso()})

    dom = registrable_domain(row.get("website", ""))
    f = {"f_domain": dom}
    if dom:
        ev("f_domain", dom, row.get("website", ""), "")
        for fn, label in ((wayback_features, "wayback"), (hn_features, "hn")):
            try:
                f.update(fn(dom, asof, ev))
            except Exception as e:
                print(f"  ! {label} error for {name}: {e!r}", flush=True)
        if opts["crtsh"]:
            try:
                cf, ok = crtsh_features(dom, asof, ev)
                f.update(cf)
            except Exception as e:
                print(f"  ! crt.sh error for {name}: {e!r}", flush=True)
    if opts["github"]:
        try:
            f.update(github_features(name, dom, asof, ev))
        except Exception as e:
            print(f"  ! github error for {name}: {e!r}", flush=True)
    return f, evidence


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    opts = {"github": "--no-github" not in sys.argv, "crtsh": "--no-crtsh" not in sys.argv}
    workers = 5
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
        args = [a for a in args if a != str(workers)]
    inp = args[0]
    if not os.path.isabs(inp):
        inp = os.path.join(ROOT, inp) if not os.path.exists(inp) else os.path.abspath(inp)
    stem = os.path.splitext(os.path.basename(inp))[0]
    out_f = os.path.join(ROOT, "raw", f"features_{stem}.csv")
    out_e = os.path.join(ROOT, "raw", f"evidence_{stem}.csv")
    with open(inp, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    print(f"{len(rows)} rows from {inp}; github={'on' if opts['github'] else 'off'} "
          f"(token={'yes' if GH_TOKEN else 'no'}), crt.sh={'on' if opts['crtsh'] else 'off'}", flush=True)
    t0 = time.time()
    results = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process, r, opts): i for i, r in enumerate(rows)}
        done = 0
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                print(f"  ! row {i} failed: {e!r}", flush=True)
                results[i] = ({}, [])
            done += 1
            if done % 10 == 0 or done == len(rows):
                print(f"  {done}/{len(rows)} done ({time.time() - t0:.0f} s)", flush=True)
    with open(out_f, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "website", "asof_date"] + FEATURES)
        w.writeheader()
        for r, (f, _) in zip(rows, results):
            w.writerow({"name": r["name"], "website": r.get("website", ""), "asof_date": r["asof_date"],
                        **{k: f.get(k, "") for k in FEATURES}})
    with open(out_e, "w", encoding="utf-8", newline="") as fh:
        cols = ["name", "asof_date", "field", "value", "url", "snapshot_ts", "retrieved_at"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        n_ev = 0
        for _, evs in results:
            for e in evs:
                w.writerow(e)
                n_ev += 1
    n = len(rows)
    print(f"\nWrote {out_f} ({n} rows) and {out_e} ({n_ev} evidence rows) in {time.time() - t0:.0f} s")
    print("% filled per feature:")
    for k in FEATURES:
        c = sum(1 for f, _ in results if f.get(k, "") != "")
        print(f"  {k:28s} {100 * c / n:5.1f}%  ({c}/{n})")


if __name__ == "__main__":
    main()
