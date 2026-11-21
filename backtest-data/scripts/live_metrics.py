#!/usr/bin/env python3
"""Current public metrics for the 2026-11-21 live cohort; Python stdlib only.

Run --collect to cache websites/search/GitHub; run without flags to assemble.
Unknowns stay blank. Research decisions are explicit in REVIEW below.
"""
import argparse
import csv
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse as up
import urllib.request as ur
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'raw/cache/live_metrics'
UA = 'Mozilla/5.0 (research script)'
LOCK = threading.Lock()
HOSTS = {}
LAST = {}
TOKEN = os.environ.get('GITHUB_TOKEN', '').strip()

def fetch(url):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + '.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    host = up.urlsplit(url).hostname
    with LOCK:
        lock = HOSTS.setdefault(host, threading.Lock())
    result = {}
    for attempt in range(2):
        with lock:
            time.sleep(max(0, LAST.get(host, 0) + 1.1 - time.monotonic()))
            LAST[host] = time.monotonic()
        headers = {'User-Agent': UA, 'Accept-Encoding': 'identity'}
        if host == 'api.github.com':
            headers['Accept'] = 'application/vnd.github+json'
            if TOKEN:
                headers['Authorization'] = 'Bearer ' + TOKEN
        result = {'url': url, 'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'status': None, 'body': ''}
        try:
            with ur.urlopen(ur.Request(url, headers=headers), timeout=20) as r:
                result.update(status=r.status, final_url=r.url,
                              body=r.read(4000000).decode(r.headers.get_content_charset() or 'utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            result.update(status=e.code, body=e.read(1000000).decode('utf-8', 'replace'))
        except (OSError, ValueError) as e:
            result['error'] = type(e).__name__
        # Cache all responses, including errors; never cache request headers/token.
        history = result.get('attempts', [])
        path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        if result['status'] not in (None, 429, 500, 502, 503, 504):
            break
        if attempt == 0:
            time.sleep(2)
    return result

def api(path):
    r = fetch('https://api.github.com' + path)
    if r['status'] == 200:
        try:
            return json.loads(r['body'])
        except ValueError:
            pass
    return None

class Page(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.skip = [], [], 0
        self.feed(body)
        self.text = re.sub(r'\s+', ' ', ' '.join(self.parts)).strip()
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip += 1
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

def domain(url):
    host = (up.urlsplit(url if '://' in url else 'https://' + url).hostname or '').lower()
    return host.removeprefix('www.')

def slug(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')

def read_csv(path):
    with open(ROOT / path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_csv(path, fields, rows):
    with open(ROOT / path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

def github(row, pages):
    dom = domain(row['website'])
    candidates = []
    for url, p in pages:
        for link in p.links:
            m = re.match(r'https?://(?:www\.)?github.com/([^/?#]+)', link)
            if m and m[1] not in ('features', 'login', 'sponsors', 'pricing'):
                candidates.append(m[1])
    queries = [dom + ' in:blog type:org', row['name'] + ' type:org']
    for q in queries:
        js = api('/search/users?' + up.urlencode({'q': q, 'per_page': 5}))
        if js:
            candidates.extend(i['login'] for i in js.get('items', []))
    out = {}
    for login in dict.fromkeys(candidates):
        org = api('/orgs/' + up.quote(login))
        if not org or not org.get('blog') or domain(org['blog']) != dom:
            continue
        out.update(gh_org=login, gh_evidence_url=org['html_url'], gh_match='verified_blog_domain')
        repos = []
        complete = False
        for page in range(1, 4):
            batch = api('/orgs/' + login + '/repos?' + up.urlencode({'per_page': 100, 'page': page}))
            if not isinstance(batch, list):
                break
            repos.extend(batch)
            if len(batch) < 100:
                complete = True
                break
        if not complete:
            return out
        repos = [r for r in repos if not r['fork'] and r.get('created_at', '')[:10] <= row['asof_date']]
        until = dt.datetime.fromisoformat(row['asof_date']).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
        since = until - dt.timedelta(weeks=8)
        weeks, has_commits, all_complete = set(), False, True
        for repo in repos:
            path = '/repos/' + repo['full_name'] + '/commits'
            any_commit = api(path + '?per_page=1&until=' + up.quote(until.isoformat()))
            if isinstance(any_commit, list) and any_commit:
                has_commits = True
            elif any_commit is None:
                # 409 is an empty repository, not evidence of committed source.
                response = fetch('https://api.github.com' + path + '?per_page=1&until=' + up.quote(until.isoformat()))
                if response['status'] != 409:
                    all_complete = False
            done = False
            for page in range(1, 4):
                commits = api(path + '?' + up.urlencode({'since': since.isoformat(), 'until': until.isoformat(), 'per_page': 100, 'page': page}))
                if commits is None:
                    response = fetch('https://api.github.com' + path + '?' + up.urlencode({'since': since.isoformat(), 'until': until.isoformat(), 'per_page': 100, 'page': page}))
                    if response['status'] == 409:
                        done = True
                    break
                for c in commits:
                    date = dt.datetime.fromisoformat(c['commit']['committer']['date'].replace('Z', '+00:00'))
                    if since <= date < until:
                        weeks.add(int((date - since).total_seconds() // (7 * 86400)))
                if len(commits) < 100:
                    done = True
                    break
            if not done:
                all_complete = False
        if has_commits or all_complete:
            out['m_gh_has_open_source'] = int(has_commits)
        if all_complete:
            out['m_gh_commit_weeks_active_8w'] = len(weeks)
        return out
    return out

def collect_company(row):
    home = row['website']
    r = fetch(home)
    pages = []
    if r['status'] == 200:
        p = Page(r['body'])
        pages.append((r.get('final_url', home), p))
        links = [up.urljoin(r.get('final_url', home), l) for l in p.links]
        links = [l.split('#')[0] for l in links if domain(l) == domain(home)]
        targets = []
        for pattern in ('impress|imprint|legal', 'about|ueber|über|team', 'funding|förder|grant', 'press|news|blog'):
            targets.extend([l for l in links if re.search(pattern, l, re.I)][:2])
        if not any(re.search('impress|imprint|legal', l) for l in targets):
            targets.append(up.urljoin(home, '/impressum'))
        for url in dict.fromkeys(targets):
            rr = fetch(url)
            if rr['status'] == 200:
                pages.append((url, Page(rr['body'])))
    queries = [f'"{domain(home)}" (funding OR Finanzierung OR investment OR seed)',
               f'"{domain(home)}" (EXIST OR SPRIND OR EIC OR BMBF OR accelerator OR Gründungsstipendium)',
               f'site:producthunt.com "{row["name"]}"']
    search = []
    for q in queries:
        url = 'https://html.duckduckgo.com/html/?' + up.urlencode({'q': q})
        rr = fetch(url)
        search.append({'url': url, 'status': rr['status'], 'text': Page(rr['body']).text[:15000]})
    gh = github(row, pages)
    record = {'name': row['name'], 'pages': [{'url': u, 'text': p.text, 'links': p.links} for u, p in pages], 'search': search, 'github': gh}
    (CACHE / ('company-' + slug(row['name']) + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(row['name'] + ': pages=' + str(len(pages)) + ', GitHub=' + gh.get('gh_org', 'unverified/unknown'), flush=True)
    return record

# Human-reviewed public evidence. No negative finding is inferred from failed fetches.
REVIEW = {}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--collect', action='store_true')
    args = parser.parse_args()
    rows = read_csv('raw/cohort_linkedin_live.csv')
    if args.collect:
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(collect_company, r) for r in rows]
            for f in as_completed(futures):
                f.result()
        return
    assemble(rows)

if __name__ == '__main__':
    main()
