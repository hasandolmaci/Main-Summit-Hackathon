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


SOURCE_URL = 'https://www.uni-kassel.de/einrichtung/ukt/unikat-von-der-idee-zur-gruendung/gruendungsprojekte.html'
MONTHS = {m: i for i,m in enumerate('Januar Februar M\u00e4rz April Mai Juni Juli August September Oktober November Dezember'.split(),1)}

def main():
    rows = []
    tree = dom(SOURCE_URL)
    for card in tree.all({'div'}):
        if 'card' not in card.attrs.get('class','').split():
            continue
        heads = list(card.all({'h2','h3'}))
        if len(heads) != 1 or 'EXIST' not in card.text():
            continue
        name = heads[0].text().replace('\u00ad','')
        paragraphs = [p.text() for p in card.all({'p'})]
        evidence = ' '.join(p for p in paragraphs if 'EXIST' in p)
        # Anchor to the EXIST clause: earlier Hessen Ideen dates are a different cohort.
        matches = list(re.finditer(r'(?:seit|ab|Ab|Seit)\s+(?:(\d{2})\.(\d{2})\.(20\d{2})|(Januar|Februar|M\u00e4rz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+(20\d{2}))', evidence))
        date = precision = basis = ''
        for m in matches:
            if 'EXIST' in evidence[max(0,m.start()-80):m.end()+130]:
                if m[3]:
                    date=f'{m[3]}-{m[2]}-{m[1]}';precision='day'
                else:
                    date=f'{m[5]}-{MONTHS[m[4]]:02d}-01';precision='month'
                basis='EXIST funding start';break
        if not date:
            # No inferred start from a funding end. A dated end is still a documented
            # programme membership observation; label it explicitly as such.
            ends=list(re.finditer(r'bis\s+(Januar|Februar|M\u00e4rz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+(20\d{2})(?:(?!\bbis\b)[^.])*EXIST',evidence))
            m=ends[-1] if ends else None
            if m:
                date=f'{m[2]}-{MONTHS[m[1]]:02d}-01';precision='month';basis='EXIST funding end (start not stated)'
        if not date or not '2019' <= date[:4] <= '2025':
            continue
        description = ' '.join(paragraphs)
        # Exclude subsequent achievements, mentors and funding history from feature text.
        description = re.split(r'(?:Das Team (?:erhielt|wird|wurde|bekam)|Das Gr\u00fcndungsprojekt wurde|Ab Februar|Seit Dezember|F\u00fcr ein halbes Jahr|Fachbereich:)',description)[0].strip()
        rows.append(dict(name=name,website=company_url(card.links(SOURCE_URL),'uni-kassel.de'),description=description,source='unikat',cohort=f'UNIKAT EXIST {date[:4]}',asof_date=date,source_url=SOURCE_URL+'#'+card.attrs.get('id',''),date_precision=precision,date_basis=basis,date_evidence=evidence))
    finish(rows,'unikat',['source_url','date_precision','date_basis','date_evidence'])

if __name__ == '__main__':
    main()
