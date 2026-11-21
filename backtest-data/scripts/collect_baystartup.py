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


INDEX = 'https://www.baystartup.de/businessplan-wettbewerbe/sieger/ehemalige-sieger'
# Names are selectors for the three annual final-round winners, checked against
# each dated announcement. Do not collect nominees or earlier-phase winners.
NAMES={2019:['presize.ai','m-Bee','Orbem'],2020:['NEUREVO','Manyfolds','qbound'],2021:['Opsyon','Divizend','sewts'],2022:['Dymium','NIMMSTA','Reverion'],2023:['Energyminer','Sedivention','baind'],2024:['EpiCure','Qualiwise','AUDAVIS'],2025:['BioExoTec','Peak Quantum','Versatile Robotics']}

def main():
    index=dom(INDEX)
    urls=[url for label,url in index.links(INDEX) if label=='Details zu den Siegern' and 'muenchener' in url and any(str(y) in url for y in NAMES)]
    assert len(urls)==7, f'Expected seven annual Munich announcements, found {len(urls)}'
    rows=[]
    for url in urls:
        year=next(y for y in NAMES if str(y) in url)
        tree=dom(url)
        dates=[n.text() for n in tree.all({'time'}) if n.attrs.get('itemprop')=='datePublished']
        m=re.fullmatch(r'(\d{2})\.(\d{2})\.(\d{4})',dates[0])
        assert m, dates
        date=f'{m[3]}-{m[2]}-{m[1]}'
        nodes=list(tree.all({'p','h2','h3'}))
        for rank,name in enumerate(NAMES[year],1):
            if year==2024:
                candidates=[(i,n) for i,n in enumerate(nodes) if n.tag=='p' and n.text().lower().startswith(name.lower()) and len(n.text())>90]
            else:
                candidates=[(i,n) for i,n in enumerate(nodes) if re.match(rf'(?:{rank}\.\s*Platz|Platz\s*{rank})\s*:',n.text(),re.I) and name.casefold() in n.text().casefold()]
            assert candidates, (year,name,'winner block not found')
            i,node=max(candidates,key=lambda pair:len(pair[1].text()))
            blocks=[node]
            for nxt in ([] if year==2024 else nodes[i+1:]):
                if nxt.tag.startswith('h') or re.match(r'(?:\d\.\s*Platz|Platz\s*\d)\s*:',nxt.text(),re.I):break
                if nxt.attrs.get('class','') in ('quote','quote-attribution') or any(k in nxt.text() for k in ('LfA','Förderbank')):break
                if nxt.tag=='p' and not nxt.attrs.get('class','').startswith('quote'):blocks.append(nxt)
            description=' '.join(n.text() for n in blocks)
            description=re.sub(r'^(?:\d\.\s*Platz|Platz\s*\d)\s*:\s*','',description)
            links=[link for n in blocks for link in n.links(url)]
            website=company_url(links,'baystartup.de')
            if website and name.casefold().replace(' ','') not in urlparse(website).netloc.casefold().replace('-','') and year==2024:
                website=''
            rows.append(dict(name=name,website=website,description=description,source='baystartup',cohort=f'M\u00fcnchener Businessplan Wettbewerb {year} - Phase 3',asof_date=date,source_url=url,rank=str(rank),date_precision='day',date_basis='announcement publication'))
    assert len(rows)==21
    finish(rows,'baystartup',['source_url','rank','date_precision','date_basis'])

if __name__ == '__main__':
    main()
