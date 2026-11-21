"""Conservative patent application research; Python standard library only.

All network responses are cached below raw/cache/v2_patents. Unknown counts
remain blank. Applicant country must be DE, not merely publication authority.
Counts are distinct application numbers, never publications or search families.
Priority/filing dates and publication dates are retained in pat_note; historic
counts require public evidence strictly before the company's cutoff as well.
"""
import argparse
import base64
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import html
import http.cookiejar
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse as up
import urllib.request as ur
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw/cache/v2_patents'
FIELDS = ['cid', 'name', 'pat_apps_before_asof', 'pat_first_priority_date',
          'pat_apps_total', 'pat_founder_inventor_before_asof', 'pat_evidence_url', 'pat_note']
LOCK = threading.Lock()
HOSTS = {}
STATS = {}
LOCAL = threading.local()


def fetch(url, data=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode() + (data or b'')).hexdigest()
    path = CACHE / (key + '.json')
    host = up.urlsplit(url).netloc
    with LOCK:
        state = HOSTS.setdefault(host, {'lock': threading.Lock(), 'last': 0, 'failures': 0,
                                       'ok': 0, 'skipped': 0, 'disabled': False})
    if path.exists():
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['status'] == 200 and not record.get('blocked'):
            return base64.b64decode(record['body_base64'])
        return None
    for attempt in range(3):
        with state['lock']:
            if state['disabled']:
                state['skipped'] += 1
                return None
            time.sleep(max(0, .65 - (time.monotonic() - state['last'])))
            state['last'] = time.monotonic()
        record = {'url': url, 'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'status': 0, 'body_base64': ''}
        try:
            if not hasattr(LOCAL, 'opener'):
                LOCAL.opener = ur.build_opener(ur.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            request = ur.Request(url, data=data, headers={'User-Agent': 'research script',
                'Content-Type': 'application/json' if data else 'text/plain',
                'Accept': 'application/json, application/xml, text/html, */*'})
            try:
                response = LOCAL.opener.open(request, timeout=22)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read()
                record.update(status=response.code, headers=dict(response.headers),
                              body_base64=base64.b64encode(body).decode())
            lowered = body[:30000].lower()
            record['blocked'] = record['status'] in (401, 403, 429) or any(
                marker in lowered for marker in (b'sending automated queries',
                b'unusual traffic', b'<title>just a moment', b'cf-chl-', b'access denied'))
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            record['error'] = type(error).__name__
        (CACHE / (key + '.attempt' + str(attempt + 1) + '.json')).write_text(
            json.dumps(record), encoding='utf-8')
        path.write_text(json.dumps(record), encoding='utf-8')
        if record['status'] == 200 and not record.get('blocked'):
            state['ok'] += 1
            state['failures'] = 0
            return body
        state['failures'] += 1
        if state['failures'] >= 3:
            state['disabled'] = True
        print('Source failure', host, record['status'], flush=True)
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    return None


def google_url(name, extra=None):
    params = {'assignee': name, 'num': '100', 'groupby': 'none'}
    params.update(extra or {})
    return 'https://patents.google.com/xhr/query?url=' + up.quote(up.urlencode(params), safe='') + '&exp='


def probes():
    urls = [
        google_url('Tubulis'),
        'https://patents.google.com/patent/WO2022179698A1/en',
        'https://worldwide.espacenet.com/3.2/rest-services/search?q=' + up.quote('pa="Tubulis"'),
        'https://worldwide.espacenet.com/3.2/rest-services/published-data/search?q=' + up.quote('pa="Tubulis"'),
        'https://ops.epo.org/3.2/rest-services/published-data/search?q=' + up.quote('pa="Tubulis"'),
        'https://www.lens.org/lens/search/patent/list?q=' + up.quote('applicant.name:"Tubulis"'),
        'https://register.dpma.de/DPMAregister/pat/trefferliste?AKZ=&APN=Tubulis',
    ]
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        for url, body in zip(urls, pool.map(fetch, urls)):
            print(url, 'OK' if body else 'FAILED', len(body or b''),
                  repr((body or b'')[:180]), flush=True)


def plain(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', value))).strip()


def norm(value):
    value = unicodedata.normalize('NFKD', value.casefold().replace('ÃŸ', 'ss'))
    return re.sub('[^a-z0-9]', '', value.encode('ascii', 'ignore').decode())


def company_core(value):
    value = re.sub(r'\b(?:gmbh|ug|ag|haftungsbeschrÃ¤nkt|solutions)\b', '', value, flags=re.I)
    return value.strip(' .,-()')


def dates(value):
    result = []
    for day, month, year in re.findall(r'\b(\d{2})\.(\d{2})\.(\d{4})\b', value):
        try:
            result.append(dt.date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            pass
    return result


def parse_detail(body, company, url):
    """Extract INID fields only. Never copy addresses into derived output."""
    source = body.decode('utf-8', 'replace')
    fields = {}
    for row in re.findall(r'<tr\b[^>]*>.*?</tr>', source, re.S):
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)
        if len(cells) == 4:
            code = plain(cells[2])
            fields.setdefault(code, []).append(plain(cells[3]))
    if fields.get('SART') != ['Patent']:
        return None, 'not a patent or detail unavailable'
    applicant_names = []
    uncertain = []
    for applicant in fields.get('INH', [''])[0].split(';'):
        parts = [p.strip() for p in applicant.split(',')]
        name = parts[0]
        # Full company core equality after legal suffix removal. No substring
        # counting: short/common brands and false foreign matches are rejected.
        if norm(company_core(name)) == norm(company_core(company)):
            if parts[-1] == 'DE':
                applicant_names.append(name)
            else:
                uncertain.append('applicant country not DE')
    if not applicant_names:
        return None, '; '.join(uncertain) or 'applicant name mismatch'
    filing = sorted(d for k, values in fields.items() if k in ('AT', 'EAT', 'WAT')
                    for value in values for d in dates(value))
    priority = sorted(d for k, values in fields.items() if 'PRDA' in k or k in ('IAT',)
                      for value in values for d in dates(value))
    published = sorted(d for k in ('EVT', 'OT', 'PT')
                       for value in fields.get(k, []) for d in dates(value))
    inventors = []
    for inventor in fields.get('IN', [''])[0].split(';'):
        parts = [p.strip() for p in inventor.split(',')]
        # DPMA: SURNAME, Given names, postal locality, country.
        if len(parts) >= 2 and not re.search(r'\d', parts[0] + parts[1]):
            inventors.append(parts[1] + ' ' + parts[0])
    return {'url': url, 'applicants': applicant_names,
            'filing': filing[0] if filing else '',
            'priority': priority[0] if priority else '',
            'published': published[0] if published else '',
            'inventors': inventors, 'inventors_checked': bool(fields.get('IN')),
            'akz': up.parse_qs(up.urlsplit(url).query)['AKZ'][0]}, ''


def founder_match(founders, inventors):
    for founder in founders.split(';'):
        founder = re.sub(r'\b(?:prof|dr)\.?', '', founder, flags=re.I).strip()
        tokens = set(re.findall('[a-z]+', unicodedata.normalize('NFKD', founder.casefold())
                                .encode('ascii', 'ignore').decode()))
        for inventor in inventors:
            itokens = set(re.findall('[a-z]+', unicodedata.normalize('NFKD', inventor.casefold())
                                     .encode('ascii', 'ignore').decode()))
            if len(tokens) >= 2 and tokens <= itokens:
                return inventor
    return ''


def blank_row(row):
    result = dict.fromkeys(FIELDS, '')
    result.update(cid=row['cid'], name=row['name'], pat_note='pending / not checked')
    return result


def process(row):
    result = blank_row(row)
    note = ['DPMAregister search coverage only; not a worldwide total',
            'Google Patents HTTP503; Espacenet/OPS HTTP403; Lens API HTTP403 (stopped after 3 failures)',
            'historic count requires priority/filing AND first publication strictly before ' + row['asof_date']]
    name = company_core(row['name'])
    url = 'https://register.dpma.de/DPMAregister/smartsearch?' + up.urlencode({'queryString': name})
    body = fetch(url)
    if not body:
        result['pat_note'] = '; '.join(note + ['DPMA search unavailable; unknown, not zero'])
        return result
    try:
        data = json.loads(body)
        hits = int(data['patHits'])
        if data.get('patErrors'):
            raise ValueError('search error')
    except (ValueError, KeyError, TypeError):
        result['pat_note'] = '; '.join(note + ['DPMA search response invalid; unknown'])
        return result
    result['pat_evidence_url'] = url
    note.append('query=' + name)
    if not hits:
        result.update(pat_apps_before_asof=0, pat_apps_total=0)
        if row['known_founders']:
            result['pat_founder_inventor_before_asof'] = 0
            note.append('founder indicator scoped to matched company applications; no company search hits')
        result['pat_note'] = '; '.join(note + ['checked, no DPMA name-search hits; aliases/unpublished filings not covered'])
        return result
    # Search pages need a session; use a fresh initialization on each worker.
    if not getattr(LOCAL, 'dpma_initialized', False):
        init = 'https://register.dpma.de/DPMAregister/pat/basis?research_session=' + str(time.time_ns())
        fetch(init)
        LOCAL.dpma_initialized = True
    search_url = 'https://register.dpma.de' + data['patLink'] + '&lang=de'
    listing = fetch(search_url)
    source = listing.decode('utf-8', 'replace') if listing else ''
    identifiers = list(dict.fromkeys(re.findall(r'href="register\?AKZ=([^&"<>]+)', source)))
    # Do not mistake session-expired/error HTML or missing pagination for zero.
    if not identifiers:
        result['pat_note'] = '; '.join(note + [f'DPMA raw hits={hits}; listing unavailable; counts unknown'])
        return result
    records, rejected, failures = [], [], 0
    for identifier in identifiers:
        detail_url = 'https://register.dpma.de/DPMAregister/pat/register?AKZ=' + identifier
        detail = fetch(detail_url)
        if not detail:
            failures += 1
            continue
        record, reason = parse_detail(detail, row['name'], detail_url)
        if record:
            records.append(record)
        else:
            rejected.append(identifier + ': ' + reason)
    complete = len(identifiers) >= hits and not failures
    note.append(f'DPMA raw hits={hits}; inspected={len(identifiers)}; verified DE applicant applications={len(records)}')
    if not complete:
        note.append('INCOMPLETE retrieval/pagination: populated positive counts are observed lower bounds; blank if none verified')
    if rejected:
        note.append('excluded uncertain/nonmatching: ' + ' | '.join(rejected))
    if records or complete:
        result['pat_apps_total'] = len(records)
    priorities = [r['priority'] for r in records if r['priority']]
    if priorities:
        result['pat_first_priority_date'] = min(priorities)
    historic = [r for r in records if (r['priority'] or r['filing']) and
                (r['priority'] or r['filing']) < row['asof_date'] and
                r['published'] and r['published'] < row['asof_date']]
    unknown_dates = any(not (r['priority'] or r['filing']) or not r['published'] for r in records)
    if historic or (complete and not unknown_dates):
        result['pat_apps_before_asof'] = len(historic)
    if row['known_founders']:
        matched = [founder_match(row['known_founders'], r['inventors']) for r in historic]
        if any(matched):
            result['pat_founder_inventor_before_asof'] = 1
            note.append('matched founder inventors: ' + ' | '.join(m for m in matched if m))
        elif complete and not unknown_dates and all(r['inventors_checked'] for r in historic):
            result['pat_founder_inventor_before_asof'] = 0
        note.append('founder indicator scoped to verified company applications, full-name match')
    if records:
        result['pat_evidence_url'] = ' | '.join(r['url'] for r in records)
        for r in records:
            note.append('application=' + r['akz'] + ', applicant=' + '+'.join(r['applicants']) +
                        ', priority=' + (r['priority'] or 'unknown') + ', filing=' + (r['filing'] or 'unknown') +
                        ', first_publication=' + (r['published'] or 'unknown'))
    if unknown_dates:
        note.append('undated evidence excluded from before-asof count')
    result['pat_note'] = '; '.join(note)
    return result


def save(rows):
    with (ROOT / 'raw/v2_patents.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()


def iso_date(value):
    if not value or not re.fullmatch(r'\d{8}', value):
        return ''
    try:
        return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()
    except ValueError:
        return ''


def parse_epo(body, url):
    root = ET.fromstring(body)
    if root.tag != 'ep-patent-document':
        return None
    bib = root.find('SDOBI')
    if bib is None:
        return None
    applicants = []
    for path in ('.//B710/B711', './/B730/B731'):
        for party in bib.findall(path):
            name = party.findtext('snm', '').strip()
            country = party.findtext('adr/ctry', '').strip()
            if name:
                applicants.append({'name': name, 'country': country})
    inventors = []
    for party in bib.findall('.//B720/B721'):
        name = party.findtext('snm', '').strip()
        parts = name.split(',', 1)
        inventors.append(' '.join(reversed(parts)).strip())
    priority = sorted(filter(None, (iso_date(e.text) for e in bib.findall('.//B300/B320/date'))))
    return {'url': url, 'applicants': applicants, 'inventors': inventors,
            'priority': priority[0] if priority else '',
            'filing': iso_date(bib.findtext('.//B200/B220/date', '')),
            'published': iso_date(root.get('date-publ', '')),
            'first_a_date': iso_date(bib.findtext('.//B400/B430/date', '')),
            'application': bib.findtext('.//B200/B210', ''),
            'publication': 'EP' + root.get('doc-number', '') + root.get('kind', ''),
            'kind': root.get('kind', '')}


def fetch_epo(url):
    body = fetch(url)
    if not body:
        return []
    try:
        record = parse_epo(body, url)
    except ET.ParseError:
        return []
    if not record:
        return []
    records = [record]
    # A dated application publication verifies the then-applicant, whereas a
    # later grant can name a new proprietor. Download the A publication too.
    if record['kind'].startswith('B') and record['first_a_date']:
        number = re.search(r'/patents/(EP\d+)NW', url)
        if number:
            a_url = 'https://data.epo.org/publication-server/rest/v1.0/publication-dates/' + \
                record['first_a_date'].replace('-', '') + '/patents/' + number.group(1) + 'NWA1/document.xml'
            a_body = fetch(a_url)
            if a_body:
                try:
                    a_record = parse_epo(a_body, a_url)
                    if a_record:
                        records.append(a_record)
                except ET.ParseError:
                    pass
    return records


def epo_index():
    """Use cached external search discovery, validate every hit in official XML.

    Discovery snapshots are raw web search tool responses, not accepted facts.
    A search miss never means no patents. All positive counts are lower bounds
    of verified distinct EP applications (A/B publications deduplicated).
    """
    with (ROOT / 'raw/companies_all.csv').open(encoding='utf-8-sig', newline='') as stream:
        companies = list(csv.DictReader(stream))
    with (ROOT / 'raw/v2_patents.csv').open(encoding='utf-8', newline='') as stream:
        output = list(csv.DictReader(stream))
    urls, checked = set(), set()
    for path in sorted(CACHE.glob('*index*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            checked.update(q['cid'] for q in data.get('queries', []))
            response = str(data.get('response', ''))
        else:
            response = str(data)
        urls.update(re.sub(r'document\.(?:pdf|html)$', 'document.xml', u) for u in re.findall(
            r'https://data\.epo\.org/publication-server/rest/v[\d.]+/publication-dates/'
            r'[\d-]+/patents/EP\d+NW[A-Z]\d/document\.(?:pdf|xml|html)', response))
    records = []
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        for count, results in enumerate(pool.map(fetch_epo, sorted(urls)), 1):
            records.extend(results)
            if count % 10 == 0:
                print('EPO XML documents processed', count, '/', len(urls), flush=True)
    # Include any previously cached EPO XML (including directly probed records).
    for path in CACHE.glob('*.json'):
        if '.attempt' in path.name or 'index' in path.name:
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('status') == 200 and 'data.epo.org/' in data.get('url', '') and data['url'].endswith('.xml'):
            try:
                record = parse_epo(base64.b64decode(data['body_base64']), data['url'])
                if record:
                    records.append(record)
            except ET.ParseError:
                pass
    for company, result in zip(companies, output):
        # Idempotently replace the prior enrichment annotation on repeat passes.
        result['pat_note'] = result['pat_note'].split('; EPO_INDEX:')[0]
        matches = {}
        uncertain = set()
        core = norm(company_core(company['name']))
        for record in records:
            same = [p for p in record['applicants'] if norm(company_core(p['name'])) == core]
            german = [p for p in same if p['country'] == 'DE']
            if same and not german:
                uncertain.add(record['publication'] + ' applicant country not DE')
            if not german or not record['application']:
                continue
            key = norm(record['application'])
            # Use earliest actually inspected matching publication of each app.
            if key not in matches or record['published'] < matches[key]['published']:
                matches[key] = record
        if company['cid'] not in checked and not matches:
            continue
        notes = ['EPO_INDEX: exact company name searched in indexed official EPO documents; search misses are unknown']
        if uncertain:
            notes.append('excluded: ' + ' | '.join(sorted(uncertain)))
        if matches:
            values = list(matches.values())
            before = [r for r in values if (r['priority'] or r['filing']) and
                      (r['priority'] or r['filing']) < company['asof_date'] and
                      r['published'] and r['published'] < company['asof_date']]
            # EP-only lower bounds replace weaker/failed search results; DPMA
            # has no positive verified rows in this run, avoiding mixed scopes.
            result['pat_apps_total'] = len(values)
            # A positive lower bound is valid; zero would falsely imply an
            # exhaustive historic search, so leave it blank if no dated match.
            result['pat_apps_before_asof'] = len(before) if before else ''
            priorities = [r['priority'] for r in values if r['priority']]
            result['pat_first_priority_date'] = min(priorities) if priorities else ''
            result['pat_evidence_url'] = ' | '.join(r['url'] for r in values)
            notes.append('counts are OBSERVED LOWER BOUNDS of distinct EP applications, not worldwide/exhaustive totals; A/B publications deduplicated')
            if company['known_founders']:
                found = [founder_match(company['known_founders'], r['inventors']) for r in before]
                result['pat_founder_inventor_before_asof'] = 1 if any(found) else ''
                notes.append('founder full names checked on dated company filings; incomplete search, negatives unknown')
                if any(found):
                    notes.append('verified founder inventors: ' + ' | '.join(x for x in found if x))
            for r in values:
                notes.append('application=EP' + r['application'] + ', publication=' + r['publication'] +
                             ', applicant=' + '+'.join(p['name'] for p in r['applicants'] if p['country'] == 'DE') +
                             ', country=DE, priority=' + (r['priority'] or 'unknown') +
                             ', filing=' + (r['filing'] or 'unknown') + ', public_document_date=' + (r['published'] or 'unknown'))
        result['pat_note'] += '; ' + '; '.join(notes)
        save(output)
    print('Rows written:', len(output), flush=True)
    for field in FIELDS:
        print(field + ': fill ' + str(sum(r[field] != '' for r in output)) + '/' + str(len(output)), flush=True)
    print('Sources worked: DPMA smart search (partial, then timeouts); official EPO publication XML; web index discovery.', flush=True)
    print('Sources failed/stopped: Google Patents 503; Espacenet/OPS/EPO Register 403; Lens API 403; DPMA repeated timeouts.', flush=True)
    print('EPO candidate URLs:', len(urls), 'parsed records:', len(records), 'companies searched:', len(checked), flush=True)
    print('HTTP host summary:', {h: {k: v for k, v in s.items() if k != 'lock'} for h, s in HOSTS.items()}, flush=True)


def main(limit=None, resume=False):
    with (ROOT / 'raw/companies_all.csv').open(encoding='utf-8-sig', newline='') as stream:
        companies = list(csv.DictReader(stream))
    output = [blank_row(row) for row in companies]
    if resume and (ROOT / 'raw/v2_patents.csv').exists():
        with (ROOT / 'raw/v2_patents.csv').open(encoding='utf-8', newline='') as stream:
            previous = {r['cid']: r for r in csv.DictReader(stream)}
        output = [previous.get(r['cid'], r) for r in output]
    save(output)
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(process, row): index for index, row in enumerate(companies[:limit])
                   if not resume or output[index]['pat_note'] == 'pending / not checked'}
        for count, future in enumerate(cf.as_completed(futures), 1):
            index = futures[future]
            try:
                output[index] = future.result()
            except Exception as error:
                output[index]['pat_note'] = 'processing failed: ' + type(error).__name__ + '; unknown'
            save(output)
            print('Completed', count, '/', len(futures), output[index]['cid'], output[index]['name'],
                  'before=', output[index]['pat_apps_before_asof'], 'total=', output[index]['pat_apps_total'], flush=True)
    print('Rows written:', len(output), flush=True)
    for field in FIELDS:
        print(field + ': fill ' + str(sum(r[field] != '' for r in output)) + '/' + str(len(output)), flush=True)
    print('Source status: Google Patents failed HTTP503; Espacenet/OPS failed HTTP403; Lens HTML shell only/API failed HTTP403; DPMAregister used.', flush=True)
    print('HTTP host summary:', {host: {k: v for k, v in state.items() if k != 'lock'} for host, state in HOSTS.items()}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--epo-index', action='store_true')
    args = parser.parse_args()
    if args.probe:
        probes()
    elif args.epo_index:
        epo_index()
    else:
        main(args.limit, args.resume)
