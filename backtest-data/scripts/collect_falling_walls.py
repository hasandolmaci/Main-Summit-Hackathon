"""Reproducible cohort collector; Python 3.13 standard library only.

HTTP responses (including HTTP errors) are cached by URL SHA1. Dates and
company descriptions come from the cited cohort pages, not current company sites.
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
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw' / 'cache' / Path(__file__).stem
LAST = {}

def fetch(url):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / hashlib.sha1(url.encode()).hexdigest()
    if path.exists():
        item = json.loads(path.read_text(encoding='utf-8'))
        if item['status'] >= 400:
            raise RuntimeError(f"HTTP {item['status']}: {url} (cached)")
        return item['body']
    host = urlparse(url).netloc
    for attempt in range(3):
        time.sleep(max(0, 1.05 - (time.monotonic() - LAST.get(host, 0))))
        try:
            LAST[host] = time.monotonic()
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 (research script)'}), timeout=25) as response:
                body = response.read().decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                item = {'url': url, 'final_url': response.url, 'status': response.status, 'headers': dict(response.headers), 'body': body}
            path.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
            return body
        except HTTPError as exc:
            item = {'url': url, 'status': exc.code, 'body': exc.read().decode('utf-8', errors='replace')}
            path.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f'HTTP {exc.code}: {url}') from exc
        except (URLError, TimeoutError, OSError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))

def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()

class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []
    def text(self):
        if self.tag in ('script', 'style', 'noscript'):
            return ''
        return clean(' '.join(c if isinstance(c, str) else c.text() for c in self.children))
    def all(self, tags=None):
        for c in self.children:
            if isinstance(c, Node):
                if tags is None or c.tag in tags:
                    yield c
                yield from c.all(tags)
    def links(self, base):
        return [(a.text(), urljoin(base, a.attrs['href'])) for a in self.all({'a'}) if a.attrs.get('href')]

class DOM(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node('root')
        self.feed(source)
    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.current)
        self.current.children.append(n)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.current = n
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)
    def handle_endtag(self, tag):
        n = self.current
        while n.parent:
            if n.tag == tag:
                self.current = n.parent
                return
            n = n.parent
    def handle_data(self, data):
        self.current.children.append(data)

def dom(url):
    return DOM(fetch(url)).root

def company_url(links, source_host):
    for label, url in links:
        host = urlparse(url).netloc.lower()
        if url.startswith(('https://','http://')) and host and not any(x in host for x in (source_host, 'facebook.', 'instagram.', 'linkedin.', 'twitter.', 'youtube.', 'youtu.be', 'xing.', 'google.', 'vimeo.')) and not url.lower().endswith(('.pdf','.jpg','.png')):
            return url
    return ''

def finish(rows, source, extras):
    fields = ['name','website','description','source','cohort','asof_date'] + extras
    rows = sorted(rows, key=lambda r: (r['asof_date'], r['name'].casefold(), r['cohort']))
    assert rows, f'No rows extracted for {source}'
    seen = set()
    for row in rows:
        assert row['name'] and re.fullmatch(r'20(?:19|2[0-5])-\d{2}-\d{2}', row['asof_date']), row
        key = (row['name'].casefold(), row['cohort'])
        assert key not in seen, f'Duplicate cohort member: {key}'
        seen.add(key)
    path = ROOT / 'raw' / f'cohort_{source}.csv'
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f'{source}: {len(rows)} rows; websites={sum(bool(r["website"]) for r in rows)}; dates={sum(bool(r["asof_date"]) for r in rows)}')
    for field in fields:
        print(f'  {field}: {100 * sum(not r.get(field) for r in rows) / len(rows):.1f}% missing')

if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--inspect':
    url = sys.argv[2]
    raw = fetch(url)
    tree = DOM(raw).root
    if len(sys.argv) > 3:
        pattern = re.compile(sys.argv[3], re.I)
        for n in tree.all():
            if n.tag in ('a','h1','h2','h3','h4','p','li','time') and pattern.search(n.text()):
                print(n.tag, n.attrs, n.text()[:1400])
    else:
        print(tree.text())
    print('\nLINKS:', json.dumps(tree.links(url), ensure_ascii=False))
    sys.exit(0)


INDEX='https://falling-walls.com/activities/venture/finalists'
COUNTRY_PAGES={2021:'https://falling-walls.com/falling-walls-engage-and-falling-walls-venture-shortlist-candidates',2022:'https://falling-walls.com/past-science-summits/2022'}
# Older finalist cards omit country. These aliases join finalist names to the
# explicit GERMANY entries in the official contemporary shortlist (never add
# a shortlisted company unless it also appears on the finalist page).
ALIASES={'Carbon One':'C1 GREEN CHEMICALS AG','VIDA':'VILLAGE DATA ANALYTICS'}
# All four appear on the official 2020 winners page, which the archive includes
# under Finalists 2020. Nominators and locations are documented there/profiles.
GERMAN_2020={'Made of Air','FibreCoat','Helio Heat','NanoWired'}

def norm(s):
    return re.sub(r'[^a-z0-9]','',s.casefold().replace('gmbh',''))

def main():
    tree=dom(INDEX)
    country_evidence={}
    for year,url in COUNTRY_PAGES.items():
        t=dom(url)
        country_evidence[year]=[(n.text(),n.parent.text(),url) for n in t.all({'strong'}) if re.search(r'[-\u2013,]\s*GERMANY$',n.text(),re.I)]
    rows=[];year=None;seen=set()
    for n in tree.all({'h2','article'}):
        if n.tag=='h2':
            m=re.search(r'Finalists (20\d{2})',n.text());year=int(m[1]) if m else None
            continue
        if not year or not 2019<=year<=2025:
            continue
        links=n.links(INDEX)
        if not links:continue
        label,url=links[0]
        if (year,url) in seen:continue
        seen.add((year,url))
        name=re.sub(r'^(?:Science Breakthrough of the Year \d{4}|Cluster Winner (?:Sustainability|Engineering of the Future|Health Solutions))\s*','',label)
        german=bool(re.search(r'\bGermany$',name))
        evidence_url=INDEX;evidence='Germany on finalist card' if german else ''
        name=re.sub(r'\s+Germany$','',name)
        if year in COUNTRY_PAGES:
            alias=ALIASES.get(name,name)
            matches=[x for x in country_evidence[year] if norm(alias)==norm(re.split(r'\s*[-\u2013,]\s*GERMANY',x[0],flags=re.I)[0])]
            if matches:
                german=True;evidence=matches[0][0];evidence_url=matches[0][2]
        if year==2020 and name in GERMAN_2020:
            german=True;evidence='German company; official 2020 winner profile / nominator location';evidence_url='https://falling-walls.com/meet-falling-walls-venture-winners-2020'
        if not german:continue
        profile=dom(url)
        bodies=[x for x in profile.all({'div'}) if 'field--name-body' in x.attrs.get('class','')]
        description=bodies[0].text() if bodies else ''
        website=company_url([(label,link) for label,link in profile.links(url) if label.casefold()=='website'],'falling-walls.com')
        rows.append(dict(name=name,website=website,description=description,source='falling_walls',cohort=f'Falling Walls Venture finalists {year}',asof_date=f'{year}-07-01',country='Germany',source_url=INDEX,profile_url=url,date_precision='year',date_basis='cohort year; July 1 contract fallback',country_source_url=evidence_url,country_evidence=evidence))
    print('Coverage: archive lists 2020-2025; 2019 finalist URL returns 404. 2020 archive contains the ten category winners, not all 74 original video finalists.')
    finish(rows,'falling_walls',['country','source_url','profile_url','date_precision','date_basis','country_source_url','country_evidence'])

if __name__ == '__main__':
    main()
