"""Range B founder/director research; Python standard library only.

Undated/current directors are never historical founder evidence. The requested
role-based method includes director/CEO proxies, identified in source fields.
Raw input fallback has a file URI as evidence and is explicitly undated.
"""
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw/cache/v2_founders_b'
OUT = ROOT / 'raw/v2_founders_B.csv'
FIELDS = 'cid,name,founder_names_asof,founder_count_asof,source_asof,snapshot_date,founder_names_now,founder_count_now,source_now,legal_form,evidence_url'.split(',')
LOCK = threading.Lock()
HOSTS = {}
STATS = {}
OFFLINE = '--offline' in sys.argv
CACHED_ARCHIVES = []
REVIEWED = {}
PATHS = ['impressum', 'imprint', 'legal', 'about', 'team', 'ueber-uns']
ROLE = re.compile(r'\b(?:Geschäftsführer(?:in|innen)?|Geschäftsführung|Geschaeftsfuehrer|vertreten\s+durch|Managing\s+Directors?|(?:Co[- ]?)?Founders?|(?:Mit[- ]?)?Gründer(?:in|innen)?|CEO)\b', re.I)
BAD = set('managing director directors chief executive officer founder founders ceo cto coo co cfo geschäftsführer geschäftsführung vertreten durch team about contact kontakt impressum legal imprint privacy datenschutz company innovation technology technologies deutschland germany gmbh haftung responsible content copyright address street register handelsregister amtsgericht umsatzsteuer email telefon tel fax business development product management technical officer our your the und and wir unser weitere learn more meet us support board advisors jetzt alle von der die das für for read digital solutions follow linkedin get leadership joint stock president deputy seiteninhalt verantwortlich inhalt verantwortlicher telefonnummer'.split())
BAD.update('at vat number workshop manager general partner senior backend engineer full stack developer registration details vertretungsberechtigte personen erfahrene engineering reach out inc north america medineers ultrasound phd advisers associate associates'.split())


def fetch(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    key = hashlib.sha256(url.encode()).hexdigest()
    bodypath = CACHE / (key + '.body')
    metapath = CACHE / (key + '.json')
    if metapath.exists():
        meta = json.loads(metapath.read_text(encoding='utf-8'))
        if bodypath.exists():
            with LOCK:
                STATS[host] = STATS.get(host, 0) + 1
            return bodypath.read_bytes(), meta
        return None, meta
    if OFFLINE:
        return None, {'url': url, 'error': 'not cached; offline verification'}
    with LOCK:
        state = HOSTS.setdefault(host, {'lock': threading.Lock(), 'last': 0, 'fail': 0})
    with state['lock']:
        if state['fail'] >= 3:
            return None, {'url': url, 'error': 'source skipped after 3 failures'}
        for attempt in range(3):
            time.sleep(max(0, .55 - (time.monotonic() - state['last'])))
            state['last'] = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'research script'})
                with urllib.request.urlopen(req, timeout=12) as response:
                    body = response.read(2500000)
                    meta = {'url': url, 'final_url': response.url, 'status': response.status,
                            'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                            'content_type': response.headers.get('Content-Type', '')}
                bodypath.write_bytes(body)
                metapath.write_text(json.dumps(meta), encoding='utf-8')
                with LOCK:
                    STATS[host] = STATS.get(host, 0) + 1
                return body, meta
            except Exception as exc:
                code = getattr(exc, 'code', None)
                meta = {'url': url, 'status': code, 'error': str(exc), 'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat()}
                if code in (404, 410):
                    break
                state['fail'] += 1
                if state['fail'] >= 3:
                    break
                time.sleep(1.5 * 2 ** attempt)
        metapath.write_text(json.dumps(meta), encoding='utf-8')
        return None, meta


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.parts, self.links, self.skip = [], [], 0
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'section', 'tr'):
            self.parts.append('\n')
        if tag == 'a':
            self.links += [v for k, v in attrs if k == 'href' and v]
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip = max(0, self.skip - 1)
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'section', 'tr'):
            self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def page(body):
    text = body.decode('utf-8', errors='replace')
    if '\ufffd' in text:
        text = body.decode('cp1252', errors='replace')
    return Page(text)


def plausible(value):
    value = re.sub(r'[\u200b-\u200f\ufeff]', '', value)
    value = re.sub(r'\([^)]*\)', '', value)
    value = re.sub(r'^Name\s*:\s*', '', value, flags=re.I)
    value = re.sub(r'\b(?:Dr\.-Ing\.|Prof\.?|Dr\.?|Dipl\.-Ing\.|M\.?Sc\.?|B\.?Sc\.?)\s*', '', value).strip(' \t:;,.–—-()')
    tokens = value.split()
    if not 2 <= len(tokens) <= 4 or any(t.lower().strip('.,') in BAD for t in tokens):
        return ''
    for t in tokens:
        if t in ('von', 'van', 'de', 'den', 'der', 'del', 'zu'):
            continue
        if t.isupper() or re.search(r'[a-zäöü][A-Z]{2,}', t) or not t[0].isupper() or not all(c.isalpha() or c in "-'’" for c in t):
            return ''
    return value


def extract(p, row=None):
    lines = [re.sub(r'\s+', ' ', x).strip() for x in ''.join(p.parts).splitlines()]
    lines = [x for x in lines if x]
    results = []
    for i, line in enumerate(lines):
        matches = list(ROLE.finditer(line))
        if not matches or len(line) > 180 or re.search(r'founders?\W+associate', line, re.I) or any(x in line for x in ('“', '”', '„', '″', '‶')):
            continue
        candidates = [line[:matches[0].start()], line[matches[-1].end():]]
        candidates = [re.sub(r'^\s*(?:die\s+)?Gesellschafter(?:\:innen|innen)?\s*', '', c, flags=re.I) for c in candidates]
        splitnames = lambda c: [plausible(part) for part in re.split(r'\s*(?:;|\||\s/\s|\s&\s|\sund\s|\sand\s|,|\s[–—]\s)\s*', c)]
        if not any(any(splitnames(c)) for c in candidates):
            previous = plausible(lines[i-1]) if i else ''
            if previous:
                candidates = [previous]
            else:
                candidates = []
                for nextline in lines[i+1:i+4]:
                    if any(splitnames(nextline)):
                        candidates.append(nextline)
                    else:
                        break
        for candidate in candidates:
            for part in re.split(r'\s*(?:;|\||\n|\s/&?\s|\s&\s|\sund\s|\sand\s|,|\s[–—]\s)\s*', candidate):
                name = plausible(part)
                if name and name not in [x['name'] for x in results]:
                    results.append({'name': name, 'role': matches[0].group(), 'context': ' | '.join(lines[max(0,i-1):i+4])})
    formtext = '\n'.join(lines)
    if row:
        markers = [x.lower() for x in re.split(r'[^\w]+', row['name']) if len(x) >= 4 and x.lower() not in BAD]
        markers += [row['domain'].split('.')[0].lower()]
        formtext = '\n'.join(line for line in lines if any(marker in line.lower() for marker in markers))
    legal = re.search(r'\b(GmbH\s+i\.\s*G\.|GmbH|UG(?:\s*\(haftungsbeschränkt\))?|GbR|AG|e\.\s*V\.)', formtext)
    return results, legal.group(1) if legal else ''


def cached_archive(row):
    for meta, bodypath in sorted(CACHED_ARCHIVES, key=lambda x: x[0]['url'], reverse=True):
        match = re.search(r'/web/(\d{14})id_/(.*)', meta['url'])
        if not match:
            continue
        timestamp, original = match.groups()
        if urllib.parse.urlsplit(original).netloc.removeprefix('www.') != row['domain'].removeprefix('www.') or timestamp[:8] >= row['asof_date'].replace('-', ''):
            continue
        names, form = extract(page(bodypath.read_bytes()), row)
        if names:
            return names, meta['url'], timestamp, form, True
    return [], '', '', '', False


def archive(row):
    cutoff = dt.date.fromisoformat(row['asof_date']) - dt.timedelta(days=1)
    query = urllib.parse.urlencode([('url', row['domain'] + '/*'), ('output', 'json'), ('filter', 'statuscode:200'), ('filter', 'mimetype:text/html'), ('filter', 'original:.*(?:impressum|imprint|legal|about|team|ueber-uns).*'), ('to', cutoff.strftime('%Y%m%d')), ('limit', '-5000')])
    body, meta = fetch('https://web.archive.org/cdx/search/cdx?' + query)
    if body is None:
        return cached_archive(row)
    try:
        records = json.loads(body)
        header = records[0] if records else []
        records = [dict(zip(header, v)) for v in records[1:]]
    except (ValueError, IndexError):
        return [], '', '', '', False
    records = [r for r in records if any(x in urllib.parse.urlsplit(r.get('original', '')).path.lower() for x in PATHS) and r.get('timestamp', '')[:8] <= cutoff.strftime('%Y%m%d')]
    records.sort(key=lambda r: r['timestamp'], reverse=True)
    checked = not records
    for r in records[:3]:
        url = 'https://web.archive.org/web/' + r['timestamp'] + 'id_/' + r['original']
        b, m = fetch(url)
        if b:
            checked = True
            names, legal = extract(page(b), row)
            if names:
                return names, url, r['timestamp'], legal, True
    cached = cached_archive(row)
    if cached[0]:
        return cached
    return [], '', '', '', checked


def current(row):
    base = 'https://' + row['domain'].strip('/') + '/'
    b, meta = fetch(base)
    links, checked, legal, legalurl = [], bool(b), '', ''
    allnames = []
    if b:
        p = page(b)
        allnames, legal = extract(p, row)
        if legal:
            legalurl = meta.get('final_url', base)
        links = [urllib.parse.urljoin(meta.get('final_url',base), link) for link in p.links
                 if any(x in link.lower() for x in PATHS) and urllib.parse.urlsplit(urllib.parse.urljoin(base,link)).netloc.removeprefix('www.') == urllib.parse.urlsplit(base).netloc.removeprefix('www.')]
    urls = list(dict.fromkeys(links + [base + x + '/' for x in PATHS]))
    urls.sort(key=lambda u: (0 if any(x in u.lower() for x in ('impressum','imprint','legal')) else 1))
    if allnames:
        home_result = (allnames, meta.get('final_url', base), legal, checked)
    else:
        home_result = ([], '', legal, checked)
    for url in urls[:7]:
        b, m = fetch(url)
        if not b:
            continue
        checked = True
        names, form = extract(page(b), row)
        if form:
            legal, legalurl = form, m.get('final_url', url)
        if names:
            return names, m.get('final_url', url), legal, checked, legalurl
    if home_result[0]:
        return home_result[0], home_result[1], legal, checked, legalurl
    return [], '', legal, checked, legalurl


def process(row):
    result = dict.fromkeys(FIELDS, '')
    result.update(cid=row['cid'], name=row['name'])
    old, oldurl, timestamp, oldlegal, oldchecked = archive(row)
    now, nowurl, legal, checked, legalurl = current(row)
    audit = {'input': row, 'historical': [{k:v for k,v in n.items() if k != 'context'} for n in old], 'current': [{k:v for k,v in n.items() if k != 'context'} for n in now], 'archive_checked': oldchecked, 'current_checked': checked}
    (CACHE / (row['cid'] + '_extraction.json')).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    if old:
        result.update(founder_names_asof='; '.join(n['name'] for n in old), founder_count_asof=len(old), source_asof='wayback (founder/director/CEO role)', snapshot_date=dt.datetime.strptime(timestamp, '%Y%m%d%H%M%S').isoformat() + 'Z', evidence_url=oldurl)
    elif oldchecked:
        result['founder_count_asof'] = 0
    if now:
        result.update(founder_names_now='; '.join(n['name'] for n in now), founder_count_now=len(now), source_now='current site (founder/director/CEO role); checked ' + dt.date.today().isoformat(), evidence_url='; '.join(x for x in (oldurl, nowurl) if x))
    elif not old and row['known_founders'].strip():
        names = [n.strip() for n in row['known_founders'].split(';') if n.strip()]
        result.update(founder_names_now='; '.join(names), founder_count_now=len(names), source_now='known_founders (undated input; current status unverified)', evidence_url=(ROOT / 'raw/companies_all.csv').as_uri())
    elif checked:
        result['founder_count_now'] = 0
    result['legal_form'] = legal or oldlegal
    if legal and legalurl and legalurl not in result['evidence_url']:
        result['evidence_url'] = '; '.join(x for x in (result['evidence_url'], legalurl) if x)
    if result['legal_form'] and not result['evidence_url']:
        # A form without its exact source is not emitted.
        result['legal_form'] = ''
    reviewed = REVIEWED.get(row['cid'])
    if reviewed:
        cid, names, url, date, basis = reviewed
        if not result['founder_names_now'] or result['source_now'].startswith('known_founders') or cid in ('bt185', 'lv015'):
            result.update(founder_names_now=names, founder_count_now=len(names.split('; ')), source_now='web (' + basis + ('; date=' + date if date else '') + '; current status unverified)')
            result['evidence_url'] = '; '.join(dict.fromkeys(x for x in (result['evidence_url'], url) if x))
        if not result['founder_names_asof'] and date and date < row['asof_date']:
            result.update(founder_names_asof=names, founder_count_asof=len(names.split('; ')), source_asof='web (' + basis + ')', snapshot_date=date)
            if url not in result['evidence_url']:
                result['evidence_url'] = '; '.join(x for x in (result['evidence_url'], url) if x)
    return result


def write(rows):
    temporary = CACHE / 'partial_output.csv'
    with temporary.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda x: x['cid']))
    temporary.replace(OUT)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    evidence = CACHE / 'reviewed_web_evidence.json'
    if evidence.exists():
        REVIEWED.update({r[0]: r for r in json.loads(evidence.read_text(encoding='utf-8'))})
    statuspath = CACHE / 'source_status.json'
    if statuspath.exists():
        for host in json.loads(statuspath.read_text(encoding='utf-8')).get('skipped_after_three_failures', []):
            HOSTS[host] = {'lock': threading.Lock(), 'last': 0, 'fail': 3}
    for metapath in CACHE.glob('*.json'):
        try:
            meta = json.loads(metapath.read_text(encoding='utf-8'))
            if isinstance(meta, dict) and '/web/' in meta.get('url', '') and meta.get('status') == 200 and metapath.with_suffix('.body').exists():
                CACHED_ARCHIVES.append((meta, metapath.with_suffix('.body')))
        except (ValueError, OSError):
            continue
    with (ROOT / 'raw/companies_all.csv').open(encoding='utf-8-sig', newline='') as f:
        inputs = [r for r in csv.DictReader(f) if (r['cid'].startswith('bt') and 111 <= int(r['cid'][2:]) <= 205) or (r['cid'].startswith('lv') and 1 <= int(r['cid'][2:]) <= 20)]
    rows = {r['cid']: dict(dict.fromkeys(FIELDS, ''), cid=r['cid'], name=r['name']) for r in inputs}
    write(rows)
    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(process, r): r for r in inputs}
        for future in cf.as_completed(futures):
            r = futures[future]
            try:
                rows[r['cid']] = future.result()
            except Exception as exc:
                print('ERROR', r['cid'], str(exc), flush=True)
            write(rows)
            print('PROGRESS', r['cid'], sum(bool(v['founder_names_now']) for v in rows.values()), flush=True)
    print('ROWS WRITTEN', len(rows))
    print('FILL COUNTS', json.dumps({c: sum(v[c] != '' for v in rows.values()) for c in FIELDS}))
    print('SOURCES WORKED', json.dumps(STATS, sort_keys=True))
    print('SOURCES SKIPPED AFTER 3 FAILURES', json.dumps([h for h,s in HOSTS.items() if s['fail'] >= 3]))
    statuspath.write_text(json.dumps({'rows_written': len(rows), 'fill_counts': {c: sum(v[c] != '' for v in rows.values()) for c in FIELDS}, 'sources_worked': STATS, 'skipped_after_three_failures': [h for h,s in HOSTS.items() if s['fail'] >= 3]}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
