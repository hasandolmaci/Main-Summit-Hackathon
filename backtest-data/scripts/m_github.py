#!/usr/bin/env python3
"""Point-in-time GitHub metrics for raw/cohort_short.csv -> raw/m_github.csv.

Usage: python scripts/m_github.py [--in raw/cohort_short.csv] [--out raw/m_github.csv] [--workers 4]

Only activity BEFORE each row's asof_date counts (repos created_at < asof, commits in
windows ending at asof, external commits in the 365 days before asof). Exceptions that are
not point-in-time by nature: account matching uses current profile data, gh_stars_top_repo_now
is today's star count, and the member/contributor list is the current one.

Account matching (conservative):
  blog_domain   : /search/users?q=<domain> in:blog, then profile blog domain == company domain
  homepage_link : github.com/<account> link on the company homepage AND (blog domain matches
                  or account login/name matches the company name)
  exact_name    : /search/users?q=<name> type:org; normalized name/login == company name and
                  location in Germany and (no blog, or blog is the same brand on another
                  TLD, e.g. x.com vs x.de). A blog domain equal to the company domain -> blog_domain.
Unknown = empty cell. All HTTP responses cached under raw/cache/m_github/. Stdlib only.
The token is read from env GITHUB_TOKEN (or the Windows user env) and never printed/written.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "raw", "cache", "m_github")
UA = "Mozilla/5.0 (research script)"
API = "https://api.github.com"

COLS = ["name", "website", "asof_date", "gh_account", "gh_account_type", "gh_match_method",
        "gh_created", "gh_public_repos_before", "gh_top_repo", "gh_stars_top_repo_now",
        "gh_has_open_source", "gh_commits_last3m", "gh_commits_prev3m", "gh_momentum_ratio",
        "gh_momentum_score", "gh_members_checked", "gh_external_contribs_12m",
        "gh_external_score"]

SOCIAL = ("linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
          "github.com", "github.io", "xing.com", "medium.com", "t.co", "notion.site", "wixsite.com",
          "crunchbase.com", "linktr.ee", "google.com")
DE_LOC = ("germany", "deutschland", "berlin", "munich", "münchen", "muenchen", "hamburg",
          "cologne", "köln", "koeln", "frankfurt", "stuttgart", "düsseldorf", "duesseldorf",
          "karlsruhe", "darmstadt", "aachen", "dresden", "leipzig", "heidelberg", "mannheim",
          "nuremberg", "nürnberg", "nuernberg", "hannover", "bremen", "bonn", "freiburg",
          "potsdam", "garching", "würzburg", "wuerzburg", "regensburg", "augsburg", "jena",
          "münster", "muenster", "kiel", "ulm", "erlangen", "bavaria", "bayern", ", de")
GH_RESERVED = {"sponsors", "features", "about", "pricing", "login", "join", "orgs", "topics",
               "marketplace", "explore", "site", "contact", "security", "enterprise", "apps",
               "collections", "trending", "readme", "customer-stories", "team", "settings",
               "notifications", "new", "search", "issues", "pulls", "github", "users", "events"}
LEGAL = re.compile(r"\b(gmbh|ug|ag|se|kg|ohg|gbr|inc|ltd|llc|haftungsbeschränkt|"
                   r"haftungsbeschraenkt|co)\b\.?", re.I)

# ----------------------------------------------------------------------------- token


def get_token():
    t = os.environ.get("GITHUB_TOKEN")
    if not t:
        try:
            t = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Environment]::GetEnvironmentVariable('GITHUB_TOKEN','User')"],
                capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            t = ""
    return t or None


TOKEN = None

# ----------------------------------------------------------------------------- HTTP + cache


class Limiter:
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

    def pause(self, secs):
        with self.lock:
            self.next_t = max(self.next_t, time.monotonic() + secs)


SEARCH_LIM = Limiter(2.4)   # ~25 search calls / min
CORE_LIM = Limiter(0.15)
WEB_LIM = Limiter(0.2)
CORE_STATE = {"remaining": 5000, "reset": 0}
STATS = {"net": 0, "cache": 0}
STAT_LOCK = threading.Lock()


def cpath(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")


def gh_get(path, search=False, accept="application/vnd.github+json"):
    """Return (status, json_body, link_header). Cached for 200/404/409/422/451/204/202-less."""
    url = path if path.startswith("http") else API + path
    cp = cpath(url)
    if os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as f:
                d = json.load(f)
            with STAT_LOCK:
                STATS["cache"] += 1
            return d["status"], d["body"], d.get("link", "")
        except Exception:
            pass
    for attempt in range(6):
        if search:
            SEARCH_LIM.wait()
        else:
            CORE_LIM.wait()
            if CORE_STATE["remaining"] < 30 and CORE_STATE["reset"] > time.time():
                time.sleep(CORE_STATE["reset"] - time.time() + 2)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": accept, "Authorization": "Bearer " + TOKEN,
            "X-GitHub-Api-Version": "2022-11-28"})
        status, body, link, hdrs = None, None, "", {}
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status = r.status
                hdrs = r.headers
                raw = r.read()
                body = json.loads(raw) if raw else None
                link = r.headers.get("Link", "") or ""
        except urllib.error.HTTPError as e:
            status = e.code
            hdrs = e.headers
            try:
                body = json.loads(e.read() or b"null")
            except Exception:
                body = None
        except Exception:
            time.sleep(2 ** attempt)
            continue
        with STAT_LOCK:
            STATS["net"] += 1
        rem = hdrs.get("X-RateLimit-Remaining") if hdrs else None
        reset = hdrs.get("X-RateLimit-Reset") if hdrs else None
        if rem is not None and not search:
            try:
                CORE_STATE["remaining"] = int(rem)
                CORE_STATE["reset"] = int(reset)
            except (TypeError, ValueError):
                pass
        if status in (403, 429):
            msg = json.dumps(body or "").lower()
            if "rate limit" in msg or status == 429 or rem == "0":
                ra = hdrs.get("Retry-After") if hdrs else None
                if ra:
                    wait = int(ra) + 1
                elif reset:
                    wait = max(5, int(reset) - time.time() + 2)
                else:
                    wait = 60
                wait = min(wait, 3700)
                if search:
                    SEARCH_LIM.pause(wait)
                else:
                    time.sleep(wait)
                continue
        if status == 202:   # stats being computed (contributors on big repos)
            time.sleep(3)
            continue
        if status is not None and status >= 500:
            time.sleep(2 ** attempt)
            continue
        if status in (200, 204, 404, 409, 410, 422, 451, 403):
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cp, "w", encoding="utf-8") as f:
                json.dump({"url": url, "status": status, "body": body, "link": link}, f)
        return status, body, link
    return None, None, ""


def web_get(url):
    """Fetch a homepage once (cached). Returns (final_url, text) or (None, None)."""
    cp = cpath("WEB " + url)
    if os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("final"), d.get("text")
        except Exception:
            pass
    final, text = None, None
    for attempt in range(2):
        WEB_LIM.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                final = r.geturl()
                text = r.read(3_000_000).decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            final, text = None, None
            if e.code < 500:
                break
        except Exception:
            time.sleep(1 + attempt)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        json.dump({"url": url, "final": final, "text": text}, f)
    return final, text


def paged(path, max_pages=10):
    out = []
    sep = "&" if "?" in path else "?"
    for p in range(1, max_pages + 1):
        st, body, link = gh_get(f"{path}{sep}page={p}")
        if st != 200 or not isinstance(body, list):
            return out if p > 1 else None
        out.extend(body)
        if 'rel="next"' not in (link or "") or len(body) < 100:
            break
    return out

# ----------------------------------------------------------------------------- helpers


def domain_of(url):
    if not url:
        return ""
    u = url.strip()
    if not re.match(r"^[a-z]+://", u, re.I):
        u = "http://" + u
    try:
        host = urllib.parse.urlsplit(u).hostname or ""
    except ValueError:
        return ""
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def company_domain(website):
    d = domain_of(website)
    if not d or "." not in d or any(d == s or d.endswith("." + s) for s in SOCIAL):
        return ""
    return d


def dom_match(a, b):
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def norm(s):
    s = LEGAL.sub(" ", (s or "").lower())
    return re.sub(r"[^a-z0-9äöüß]", "", s)


def clean_name(name):
    s = LEGAL.sub(" ", name or "")
    s = re.sub(r"[^\w\s.-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_de(loc):
    l = (loc or "").lower()
    return any(k in l for k in DE_LOC)


def sig(x):
    return 1.0 / (1.0 + math.exp(-x))


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def user_detail(login):
    st, b, _ = gh_get(f"/users/{urllib.parse.quote(login)}")
    return b if st == 200 and isinstance(b, dict) else None

# ----------------------------------------------------------------------------- matching


def match_blog(domains):
    for d in domains:
        st, b, _ = gh_get("/search/users?q=" + urllib.parse.quote(f"{d} in:blog") + "&per_page=10",
                          search=True)
        if st != 200 or not isinstance(b, dict):
            continue
        hits = []
        for it in (b.get("items") or [])[:8]:
            u = user_detail(it["login"])
            if u and dom_match(domain_of(u.get("blog")), d):
                hits.append(u)
        if hits:
            hits.sort(key=lambda u: (u.get("type") != "Organization", -(u.get("public_repos") or 0)))
            return hits[0]
    return None


def match_homepage(website, domains, name):
    if not website:
        return None
    url = website if re.match(r"^https?://", website, re.I) else "https://" + website
    final, text = web_get(url)
    if not text:
        return None
    nn = norm(name)
    logins = []
    for m in re.finditer(r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?=[/\"'?#\s<]|$)", text):
        lg = m.group(1)
        if lg.lower() not in GH_RESERVED and lg.lower() not in [x.lower() for x in logins]:
            logins.append(lg)
    for lg in logins[:5]:
        u = user_detail(lg)
        if not u:
            continue
        if any(dom_match(domain_of(u.get("blog")), d) for d in domains):
            return u
        ln, un = norm(u.get("login")), norm(u.get("name"))
        if nn and len(nn) >= 4 and (nn == ln or nn == un or (len(ln) >= 4 and (nn in ln or ln in nn))):
            return u
    return None


def match_name(name, domains):
    cn = clean_name(name)
    nn = norm(name)
    if len(nn) < 3:
        return None
    st, b, _ = gh_get("/search/users?q=" + urllib.parse.quote(f"{cn} type:org") + "&per_page=10",
                      search=True)
    if st != 200 or not isinstance(b, dict):
        return None
    for it in (b.get("items") or [])[:6]:
        u = user_detail(it["login"])
        if not u:
            continue
        bd = domain_of(u.get("blog"))
        if domains and any(dom_match(bd, d) for d in domains):
            return u, "blog_domain"
        if nn not in (norm(u.get("login")), norm(u.get("name"))):
            continue
        if bd and domains:
            # same brand on another TLD (e.g. x.com vs x.de) is accepted, any other site is not
            if norm(bd.split(".")[-2] if bd.count(".") else bd) == norm(domains[0].split(".")[-2])                     and len(nn) >= 4 and is_de(u.get("location")):
                return u, "exact_name"
            continue
        if is_de(u.get("location")):
            return u, "exact_name"
    return None

# ----------------------------------------------------------------------------- metrics


def count_commits(owner, repo, since, until):
    total = 0
    for p in range(1, 4):
        st, b, link = gh_get(f"/repos/{owner}/{repo}/commits?since={iso(since)}&until={iso(until)}"
                             f"&per_page=100&page={p}")
        if st in (409, 404):     # empty repo / gone
            return 0 if p == 1 else total
        if st != 200 or not isinstance(b, list):
            return None if p == 1 else total
        total += len(b)
        if len(b) < 100:
            break
    return total


def is_bot(c):
    lg = (c.get("login") or "").lower()
    return c.get("type") == "Bot" or lg.endswith("[bot]") or lg.endswith("-bot")


def process(row):
    out = {k: "" for k in COLS}
    out.update(name=row["name"], website=row["website"], asof_date=row["asof_date"])
    asof = dt.datetime.strptime(row["asof_date"], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    d0 = company_domain(row["website"])
    domains = [d0] if d0 else []

    acct, method = None, ""
    if domains:
        acct = match_blog(domains)
        method = "blog_domain" if acct else ""
    if not acct and domains:
        acct = match_homepage(row["website"], domains, row["name"])
        method = "homepage_link" if acct else ""
    if not acct:
        r = match_name(row["name"], domains)
        if r:
            acct, method = r
    if not acct:
        return out

    login = acct["login"]
    is_org = acct.get("type") == "Organization"
    out.update(gh_account=login, gh_account_type="org" if is_org else "user",
               gh_match_method=method, gh_created=(acct.get("created_at") or "")[:10])

    q = urllib.parse.quote(login)
    repos = paged(f"/orgs/{q}/repos?per_page=100&type=public" if is_org
                  else f"/users/{q}/repos?per_page=100&type=owner")
    if repos is None:
        return out      # repo list unknown -> leave repo metrics empty
    before = [r for r in repos if not r.get("fork") and (r.get("created_at") or "9") < iso(asof)]
    before.sort(key=lambda r: (-(r.get("stargazers_count") or 0), r.get("created_at") or ""))
    out["gh_public_repos_before"] = len(before)
    out["gh_has_open_source"] = 1 if before else 0
    if before:
        out["gh_top_repo"] = before[0]["name"]
        out["gh_stars_top_repo_now"] = before[0].get("stargazers_count", "")
        l3 = p3 = 0
        ok = True
        for r in before[:3]:
            a = count_commits(login, r["name"], asof - dt.timedelta(days=90), asof)
            b = count_commits(login, r["name"], asof - dt.timedelta(days=180),
                              asof - dt.timedelta(days=90))
            if a is None or b is None:
                ok = False
                break
            l3 += a
            p3 += b
        if ok:
            ratio = (l3 + 1) / (p3 + 1)
            out.update(gh_commits_last3m=l3, gh_commits_prev3m=p3,
                       gh_momentum_ratio=round(ratio, 4),
                       gh_momentum_score=round(sig(math.log(ratio)), 4))

    # external contributions
    people = []
    if is_org:
        st, b, _ = gh_get(f"/orgs/{q}/public_members?per_page=100")
        if st == 200 and isinstance(b, list):
            people = [m["login"] for m in b if not is_bot(m)][:5]
    if not people and before:
        st, b, _ = gh_get(f"/repos/{q}/{urllib.parse.quote(before[0]['name'])}/contributors?per_page=30")
        if st == 200 and isinstance(b, list):
            people = [c["login"] for c in b if c.get("login") and not is_bot(c)][:5]
    if not people and not is_org:
        people = [login]
    if people:
        lo = (asof - dt.timedelta(days=365)).strftime("%Y-%m-%d")
        hi = (asof - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        tot, n = 0, 0
        for p in people:
            qs = f"author:{p} committer-date:{lo}..{hi} -user:{login}"
            st, b, _ = gh_get("/search/commits?q=" + urllib.parse.quote(qs) + "&per_page=1",
                              search=True)
            if st == 200 and isinstance(b, dict) and "total_count" in b:
                items = b.get("items") or []
                au = ((items[0].get("author") or {}).get("login") or "") if items else ""
                if items and au.lower() != p.lower():
                    continue     # search silently ignored the author filter -> unknown
                tot += int(b["total_count"])
                n += 1
            elif st == 422:      # unknown user in search -> 0 commits for that login
                n += 1
        if n:
            out.update(gh_members_checked=n, gh_external_contribs_12m=tot,
                       gh_external_score=round(2 * sig(math.log(1 + tot)) - 1, 4))
    return out

# ----------------------------------------------------------------------------- main


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=os.path.join(ROOT, "raw", "cohort_short.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "raw", "m_github.csv"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    TOKEN = get_token()
    if not TOKEN:
        sys.exit("GITHUB_TOKEN not found")
    with open(a.inp, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if a.limit:
        rows = rows[:a.limit]
    results = [None] * len(rows)
    done = 0
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(process, r): i for i, r in enumerate(rows)}
        for fu in as_completed(futs):
            i = futs[fu]
            try:
                results[i] = fu.result()
            except Exception as e:
                print(f"ERROR {rows[i]['name']}: {type(e).__name__}: {e}", file=sys.stderr)
                results[i] = {k: "" for k in COLS}
                results[i].update(name=rows[i]["name"], website=rows[i]["website"],
                                  asof_date=rows[i]["asof_date"])
            done += 1
            if done % 10 == 0:
                print(f"{done}/{len(rows)} net={STATS['net']} cache={STATS['cache']}", flush=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(results)
    n = len(results)
    print(f"wrote {a.out}: {n} rows")
    for c in COLS[3:]:
        filled = sum(1 for r in results if r[c] not in ("", None))
        print(f"  {c:28s} missing {100 * (n - filled) / max(n, 1):5.1f}%")
    from collections import Counter
    print("  gh_has_open_source:", dict(Counter(str(r["gh_has_open_source"]) or "empty" for r in results)))
    print("  gh_match_method:", dict(Counter(r["gh_match_method"] or "none" for r in results)))


if __name__ == "__main__":
    main()
