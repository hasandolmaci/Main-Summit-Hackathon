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


# Contemporary press release mirrors are used because science4life.de now
# serves a suspension notice and the preserved welabs site requires HTTP auth.
# Names below are extraction selectors, asserted against downloaded HTML.
PAGES={
2019:'https://www.presseportal.de/pm/36685/4300591',
2020:'https://www.fair-news.de/2797653/science4life-zeichnet-aus-innovationen-fr-die-gesellschaft',
2021:'https://www.starthub-hessen.de/de/aktuelles/science4life-das-sind-die-innovativsten-start-ups-2021/',
2022:'https://healthcare-mittelhessen.eu/so-wollen-die-gewinner-von-science4life-die-zukunft-veraendern',
2023:'https://www.technologieland-hessen.de/news/45457',
2024:'https://www.unitednetworker.com/science4life-abschlusspraemierung-2/',
2025:'https://station-frankfurt.de/2025/07/15/rekordjahr-fuer-science4life/'}
NAMES={
2019:([ 'Mediaire GmbH','Phytoprove Pflanzenanalytik UG','YCOR','Ebenbuild','Freemotion Systems','Actome GmbH','implacit GmbH','PerioTrap Pharmaceuticals GmbH','PolyGlyco Biotech GmbH i.Gr.','QYOBO'],['KOENA tec GmbH']),
2020:([ 'YURI GmbH','Prime Vector Technologies GmbH','DEOXY','Nia Health GmbH','Fibragg Diagnostics GmbH','CellUnite GmbH','DERMAGNOSTIX','Green Elephant Biotech','InnoSurge AC','Senodis Technologies GmbH'],['Pionierkraft GmbH','Smela','greenventory GmbH']),
2021:([ 'Nephrolytix','RECTECH','GOETHE CVI','LIDROTEC GmbH','EpiBlock','InContAlert','NanoStruct GmbH','NEUREVO GmbH','PRAMOMOLECULAR','ProSion GmbH'],['Airstier Technology GmbH','suena GmbH','innocept']),
2022:([ 'CatSper','DNTOX GmbH','FUSIX BIOTECH GmbH','HBOX Therapies GmbH','Revoltech GmbH','Advylop','Dymium','n-biotics','OPSYON Therapeutics GmbH','Shards'],['Reverion','PV2+','plugX']),
2023:([ 'Sedivention','Akribion Genomics','Octo Germany','MAKROPHAGEN 2.0','DeepEn','ArtifiCell','FLIPoQ','LigniLabs','MYTRA','SAFIA Technologies'],['xemX','retoflow','\u00d6koVision']),
2024:([ 'ResCure','fiberior','CeraSleeve','TENTA VISION','QUASAR Therapeutics'],['ILLUTHERM','Hydrogenea','AdaptX Systems']),
2025:([ 'HeartGate','CiX','Blueprint Biomed','X-ECD','Plantman'],['WeldNova','Radiant Solar','TwinWatt'])}
# Award dates, not press publication dates. 2019/2023 programme dates and
# 2024 institute account corroborate the dates that the mirrored texts omit.
DATES={2019:'2019-06-17',2020:'2020-09-21',2021:'2021-06-28',2022:'2022-07-18',2023:'2023-06-26',2024:'2024-07-01',2025:'2025-06-30'}
DATE_SOURCES={
2019:'https://www.adlershof.de/fileadmin/user_upload/S4L-Flyer-VC19_WEB.pdf',
2020:PAGES[2020],2021:PAGES[2021],
2022:'https://cris.uni-muenster.de/portal/de/prize/82225424',
2023:'https://groups.google.com/a/go.gdch.de/g/vcw-frankfurt/c/JVFga5_uWeQ',
2024:'https://www.ipfdd.de/en/communication/news/news-detail/newsitem/1942/',
2025:'https://www.uniklinik-freiburg.de/herz-thorax-gefaesschirurgie-aortenmedizin/forschung/forschungsprojekte/ycor-heartgate-minimal-invasive-kunstherzimplantation/default-4f3c8e3bc4-1-1.html'}

def alias(name):
    name=re.sub(r'\s+GmbH(?:\s+i.Gr.)?$|\s+UG$', '',name,flags=re.I)
    return {'OPSYON Therapeutics':'OPSYON','Hydrogenea':'Hydrogena','FUSIX BIOTECH':'F USIX BIOTECH'}.get(name,name)

def extract_description(nodes,name,names):
    key=alias(name)
    candidates=[]
    for node in nodes:
        if node.tag!='p':continue
        text=node.text()
        if re.search(r'Platz,\s*dotiert|Preisgeld:|^[-–]',text):continue
        match=re.search(re.escape(key),text,re.I)
        if not match:continue
        text=text[match.start():]
        stops=[]
        for other in names:
            if other==name:continue
            found=re.search(re.escape(alias(other)),text[len(key):],re.I)
            if found:stops.append(len(key)+found.start())
        if stops:text=text[:min(stops)]
        text=re.sub(r'\s+(?:Platz [1-5] (?:geht an|sicherte sich)|Den dritten Platz belegt|Die Plätze).*$', '',text)
        text=re.sub(r'\s+(?:Die|Der|Das|Mit|Bei|und|über|Die Firma)\s*$','',text).strip()
        if len(text)>60 and re.search(r'entwick|bietet|ermöglicht|produziert|arbeitet|stellt|vertreibt|ersetzt|senkt|reduziert|nutzt|will|möchte|forscht|macht|optimier|erhöh|erkenn|löst|rüstet|identifiz|auskommt|wandelt|setzt',text,re.I):
            candidates.append(text[:2000])
    return max(candidates,key=len) if candidates else ''

def main():
    rows=[]
    for year,url in PAGES.items():
        tree=dom(url)
        text=tree.text()
        # Isolate release from menus, related articles, contemporary site footer.
        start=text.find('Science4Life')
        text=text[start:]
        assert str(year) in text
        nodes=list(tree.all({'p','li','pre'}))
        all_links=tree.links(url)
        for cup,names in zip(('Venture Cup','Energy Award' if year==2019 else 'Energy Cup'),NAMES[year]):
            for index,name in enumerate(names):
                assert name.casefold() in text.casefold(),(year,name,'not found in source')
                # Prefer descriptive paragraphs, exclude ranking-only blocks.
                description=extract_description(nodes,name,NAMES[year][0]+NAMES[year][1])
                # Some releases describe a product before the parenthesised name.
                if not description and year in (2019,2020):
                    patterns={
                        'Actome GmbH':r'Analyseverfahren, das die Medikamentenentwicklung revolutioniert',
                        'implacit GmbH':r'Behandlung inoperabler Tumoren',
                        'PerioTrap Pharmaceuticals GmbH':r'Parodontitis',
                        'PolyGlyco Biotech GmbH i.Gr.':r'Behandlung der häufigsten Erblindungsursache[^()]+',
                        'QYOBO':r'Plattform für Chemikalien und pharmazeutische Wirkstoffe',
                        'CellUnite GmbH':r'personalisierte Zelltherapie für Jedermann',
                        'DERMAGNOSTIX':r'molekulardiagnostisches Point-of-Care Gerät[^()]+',
                        'InnoSurge AC':r'medizinische Software zur automatisierten Chirurgieplanung'}
                    m=re.search(patterns.get(name,r'(?!)'),text,re.I)
                    if m:description=m[0].strip()
                # Company URLs only when actually linked by name in the release.
                links=[(label,link) for label,link in all_links if alias(label).casefold()==alias(name).casefold()]
                website=company_url(links,urlparse(url).netloc.removeprefix('www.'))
                if 'list-manage.com' in urlparse(website).netloc:
                    try:
                        fetch(website)
                        cached=json.loads((CACHE/hashlib.sha1(website.encode()).hexdigest()).read_text(encoding='utf-8'))
                        website=cached.get('final_url','')
                    except (RuntimeError, URLError, TimeoutError, OSError):
                        website=''
                if any(host in urlparse(website).netloc for host in ('list-manage.com','go-bio.de','presseportal.de','tum.de')):
                    website=''
                rows.append(dict(name=name,website=website,description=description,source='science4life',cohort=f'Science4Life {cup} {year} - Businessplanphase',asof_date=DATES[year],source_url=url,rank=str(index+1) if index<5 or cup!='Venture Cup' else '',rank_group='6-10 (alphabetical)' if cup=='Venture Cup' and index>=5 else '',date_precision='day',date_basis='award date',date_source_url=DATE_SOURCES[year]))
    assert len(rows)==79,len(rows)
    finish(rows,'science4life',['source_url','rank','rank_group','date_precision','date_basis','date_source_url'])

if __name__ == '__main__':
    main()
