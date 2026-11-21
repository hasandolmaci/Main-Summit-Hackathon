"""HighTechXL starting-year cohorts, including the Netherlands by request.

Python 3.13 / standard library only. Run with python -B scripts/collect_hightechxl.py.
The other three assigned collectors reuse the HTTP/HTML/CSV helpers in this file.
Default: sha1(URL) response caches under raw/cache/<script_name>/, per CONTRACT.md.
--no-disk-cache honors an explicit restriction to writing scripts and CSVs only;
responses are still memoized in memory during that run.

Descriptions are source snapshots, not verified historical features. Starting
year is taken from the portfolio filter, never a WordPress publication timestamp.
"""
import csv
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['name', 'website', 'description', 'source', 'cohort', 'asof_date',
          'country', 'evidence_url', 'date_basis', 'name_basis']
UA = 'Mozilla/5.0 (research script)'


class Fetcher:
    def __init__(self, script):
        self.directory = ROOT / 'raw' / 'cache' / script
        self.disk = '--no-disk-cache' not in sys.argv
        self.memory = {}
        self.last = {}

    def get(self, url, headers_only=False):
        if url in self.memory:
            return self.memory[url]
        path = self.directory / hashlib.sha1(url.encode()).hexdigest()
        if path.exists():
            payload = json.loads(path.read_text(encoding='utf-8'))
            if payload['status'] < 400:
                self.memory[url] = payload['body']
                return payload['body']
        host = urllib.parse.urlsplit(url).netloc.lower()
        for attempt in range(4):
            time.sleep(max(0, 1.05 - (time.monotonic() - self.last.get(host, 0))))
            self.last[host] = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}, method='HEAD' if headers_only else 'GET'), timeout=30) as r:
                    body = json.dumps(dict(r.headers)) if headers_only else r.read().decode(r.headers.get_content_charset() or 'utf-8', errors='replace')
                    payload = {'url': url, 'final_url': r.url, 'status': r.status,
                               'headers': dict(r.headers), 'body': body}
                # Also throttle the redirected host, if different.
                self.last[urllib.parse.urlsplit(payload['final_url']).netloc.lower()] = time.monotonic()
                if self.disk:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
                self.memory[url] = body
                return body
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    payload = {'url': url, 'final_url': exc.url, 'status': exc.code,
                               'headers': dict(exc.headers), 'body': exc.read().decode('utf-8', errors='replace')}
                    if self.disk:
                        self.directory.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
                if attempt == 3:
                    raise
                print(f'Retry {attempt + 1}: {url}: {exc}', file=sys.stderr, flush=True)
                time.sleep(2 ** (attempt + 1))


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def has(self, cls):
        return cls in self.attrs.get('class', '').split()

    def nodes(self, tag=None, cls=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or child.has(cls)):
                    yield child
                yield from child.nodes(tag, cls)

    def text(self):
        if self.tag in ('script', 'style'):
            return ''
        return ' '.join((' '.join(x.text() if isinstance(x, Node) else x for x in self.children)).split())


class Tree(HTMLParser):
    VOID = set('area base br col embed hr img input link meta param source track wbr'.split())

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        n = self.current
        while n.parent is not None:
            if n.tag == tag:
                self.current = n.parent
                return
            n = n.parent

    def handle_data(self, data):
        self.current.children.append(data)


def first(node, tag=None, cls=None):
    return next(node.nodes(tag, cls), None)


def own_url(url, base=''):
    url = urllib.parse.urljoin(base, html.unescape(url.strip()))
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower().removeprefix('www.')
    blocked = ('linkedin.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com',
               'youtube.com', 'youtu.be', 'hubspot.com', 'hs-sites.com', 'techtag.de',
               'xing.com', 'tiktok.com', 'crunchbase.com', 'linktr.ee', 'angel.co')
    if p.scheme not in ('http', 'https') or not host or any(host == h or host.endswith('.' + h) for h in blocked):
        return ''
    return url


def row(**kwargs):
    result = dict.fromkeys(FIELDS, '')
    result.update(kwargs)
    return result


def output(source, rows):
    if not rows:
        raise RuntimeError(f'{source}: empty extraction; refusing to replace output')
    rows.sort(key=lambda r: (r['asof_date'], r['name'].casefold()))
    for r in rows:
        assert r['name'].strip()
        if r['asof_date']:
            import datetime
            datetime.date.fromisoformat(r['asof_date'])
            assert 2019 <= int(r['asof_date'][:4]) <= 2025
        assert not r['website'] or own_url(r['website'])
    path = ROOT / 'raw' / f'cohort_{source}.csv'
    path.parent.mkdir(exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f'{source}: rows={len(rows)}, websites={sum(bool(r["website"]) for r in rows)}, dates={sum(bool(r["asof_date"]) for r in rows)}', flush=True)
    print('Missing: ' + ', '.join(f'{k}={sum(not r[k] for r in rows)/len(rows):.1%}' for k in FIELDS), flush=True)


def collect():
    fetch = Fetcher('collect_hightechxl')
    base = 'https://www.hightechxl.com/portfolio/'
    initial = Tree(fetch.get(base)).root
    years = sorted({int(n.attrs['value']) for n in initial.nodes('input')
                    if n.attrs.get('name') == '_sfm_year_venture[]'
                    and n.attrs.get('value', '').isdigit()
                    and 2019 <= int(n.attrs['value']) <= 2025})
    if not years:
        raise RuntimeError('Starting-year filter not found')
    rows, seen = [], set()
    for year in years:
        page, count = 1, 0
        while True:
            url = base + '?' + urllib.parse.urlencode({'_sfm_year_venture': year, 'sf_paged': page})
            source = fetch.get(url)
            tree = Tree(source).root
            expected = re.search(r'Total of\s+(\d+)\s+results found', source)
            expected = int(expected[1]) if expected else None
            cards = [a for a in tree.nodes('a') if '/venture/' in a.attrs.get('href', '') and first(a, 'h3')]
            fresh = 0
            for card in cards:
                profile = card.attrs['href']
                key = (profile, year)
                if key in seen:
                    continue
                seen.add(key)
                fresh += 1
                name = first(card, 'h3').text()
                desc = first(card, 'p')
                detail = Tree(fetch.get(profile)).root
                website = ''
                for a in detail.nodes('a'):
                    candidate = own_url(a.attrs.get('href', ''))
                    host = urllib.parse.urlsplit(candidate).netloc.lower()
                    if candidate and 'hightechxl.com' not in host and host not in ('jkc.media', 'www.google.com'):
                        website = candidate
                        break
                rows.append(row(name=name, website=website, description=desc.text() if desc else '',
                                source='hightechxl_portfolio', cohort=f'HighTechXL starting year {year}',
                                asof_date=f'{year}-07-01', country='Netherlands', evidence_url=url,
                                date_basis='portfolio starting-year filter; year only', name_basis='portfolio heading'))
            count += fresh
            if expected is not None and count >= expected:
                break
            if not fresh:
                if expected is not None and count != expected:
                    raise RuntimeError(f'Incomplete year {year}: {count}/{expected}')
                break
            page += 1
            if page > 100:
                raise RuntimeError('Pagination did not terminate')
        print(f'HighTechXL {year}: {count}', flush=True)
    output('hightechxl', rows)


if __name__ == '__main__':
    collect()
