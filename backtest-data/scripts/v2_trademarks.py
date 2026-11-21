#!/usr/bin/env python3
"""Trademark research for companies_all.csv. Python standard library only.

Run: python -B scripts/v2_trademarks.py
Only an explicit, complete search may establish a zero. A historical filing
is counted regardless of its current status; filing dates must precede asof.
Caches include request metadata, raw response bodies and all retry responses.
POST query parameters repeat the search criteria so sha1(URL) stays unique.
"""
import base64
import csv
import datetime as dt
import hashlib
import html
import http.cookiejar
import io
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from email.message import Message

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw/cache/v2_trademarks'
UA = 'research script'
FIELDS = ['cid','name','tm_count_before_asof','tm_first_filing_date','tm_count_total','tm_offices','tm_evidence_url','tm_note']
HOST_LOCK = threading.Lock()
HOSTS = {}


def throttle(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    with HOST_LOCK:
        lock = HOSTS.setdefault(host, threading.Lock())
    # Hold the host lock until dispatch so actual request starts stay separated.
    with lock:
        delay = max(0, getattr(throttle, 'last', {}).get(host, 0) + 0.55 - time.monotonic())
        time.sleep(delay)
        if not hasattr(throttle, 'last'):
            throttle.last = {}
        throttle.last[host] = time.monotonic()


class CachedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raw = fp.read()
        directory = CACHE / 'redirects'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (hashlib.sha1(req.full_url.encode()).hexdigest() + '.json')
        path.write_text(json.dumps({'url': req.full_url, 'status': code,
                                   'headers_items': list(headers.items()),
                                   'body_base64': base64.b64encode(raw).decode('ascii'),
                                   'body': raw.decode('utf-8', 'replace')}, ensure_ascii=False), encoding='utf-8')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            throttle(redirected.full_url)
        return redirected


def fetch(url, data=None, opener=None, content_type='application/json'):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode()).hexdigest()
    path = CACHE / (key + '.json')
    if path.exists():
        cached = json.loads(path.read_text(encoding='utf-8'))
        if cached.get('request_body') == data:
            if opener:
                headers = Message()
                for k, v in cached.get('headers_items', cached.get('headers', {}).items()):
                    headers.add_header(k, v)
                response = urllib.response.addinfourl(io.BytesIO(b''), headers, url)
                for handler in opener.handlers:
                    if isinstance(handler, urllib.request.HTTPCookieProcessor):
                        handler.cookiejar.extract_cookies(response, urllib.request.Request(url))
            return cached
    opener = opener or urllib.request.build_opener(CachedRedirect())
    result = None
    for attempt in range(3):
        throttle(url)
        req = urllib.request.Request(
            url, data=data.encode('utf-8') if data is not None else None,
            headers={'User-Agent': UA, 'Accept': 'application/json, text/html;q=0.9',
                     'Content-Type': content_type, 'Referer': 'https://www.tmdn.org/tmview/'})
        try:
            try:
                response = opener.open(req, timeout=15)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                raw = response.read()
                result = {'url': url, 'final_url': response.geturl(),
                          'status': response.code, 'headers': dict(response.headers),
                          'headers_items': list(response.headers.items()),
                          'request_body': data,
                          'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                          'body_base64': base64.b64encode(raw).decode('ascii'),
                          'body': raw.decode('utf-8', 'replace')}
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            result = {'url': url, 'status': None, 'request_body': data,
                      'error': str(exc), 'body': ''}
        # Preserve each response, even errors and retry responses.
        attempt_dir = CACHE / ('attempt_' + str(attempt + 1))
        attempt_dir.mkdir(exist_ok=True)
        (attempt_dir / (key + '.json')).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        if result['status'] is not None and result['status'] not in (429, 500, 502, 503, 504):
            break
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


def text(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', value))).strip()


def clean_name(value):
    value = unicodedata.normalize('NFKC', value).strip()
    suffix = r'(?:gmbh\s*&\s*co\.?\s*kg|ug\s*\(haftungsbeschr[aä]nkt\)|gmbh|ug|ag|se|kg|gbr|ohg|ltd\.?|limited|inc\.?)'
    return re.sub(r'(?:[\s,]+)' + suffix + r'\s*$', '', value, flags=re.I).strip()


def norm(value):
    return re.sub(r'\s+', ' ', clean_name(value).casefold()).strip()


def contains_company(applicant, name):
    return bool(name) and re.search(r'(?<!\w)' + re.escape(norm(name)) + r'(?!\w)', norm(applicant)) is not None


def date_value(value):
    if not isinstance(value, str):
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%b %d, %Y', '%B %d, %Y'):
        try:
            return dt.datetime.strptime(value[:10] if fmt == '%Y-%m-%d' else value.strip(), fmt).date()
        except ValueError:
            pass
    return None


def json_search(name):
    """Current TMview client route, discovered in index.e1860496.js.

    Do not interpret HTML rejection pages, unknown schemas, missing totals or
    incomplete pagination as an empty search. The endpoint is optional when
    its global preflight fails; all rows still receive a DPMA search.
    """
    records, seen = [], set()
    for page in range(1, 101):
        payload = {'basicSearch': name, 'page': page, 'pageSize': 100}
        url = 'https://www.tmdn.org/tmview/api/search/results?' + urllib.parse.urlencode(
            {'translate': 'true', 'basicSearch': name, 'page': page, 'pageSize': 100})
        response = fetch(url, json.dumps(payload, sort_keys=True))
        try:
            data = json.loads(response['body'])
        except (ValueError, TypeError):
            return records, False, url
        if response['status'] != 200 or not isinstance(data, dict):
            return records, False, url
        rows = data.get('trademarks')
        total = data.get('totalResults', data.get('total'))
        if not isinstance(rows, list) or not isinstance(total, int):
            return records, False, url
        for row in rows:
            identity = row.get('st13', row.get('id'))
            if not identity or identity in seen:
                return records, False, url
            seen.add(identity)
            records.append({'id': str(identity), 'mark': row.get('tmName', row.get('markName', '')),
                            'applicant': row.get('applicantName', ''),
                            'date': date_value(row.get('applicationDate', '')),
                            'office': row.get('office', ''), 'url': url})
        if len(seen) == total:
            return records, True, url
        if not rows:
            return records, False, url
    return records, False, url


def detail_record(url, opener):
    response = fetch(url, opener=opener)
    if response['status'] != 200:
        return None
    values = {}
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', response['body'], re.S | re.I):
        cells = [text(x) for x in re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S | re.I)]
        if len(cells) == 4:
            key = re.sub(r'\s+', '', cells[2])
            value = cells[3]
            if key == 'MD' and not value:
                alt = re.search(r'alt="[^"]*?\((.*?)\)"', row, re.S)
                if alt:
                    value = html.unescape(alt.group(1)).strip()
            values.setdefault(key, value)
    identity = values.get('AKZ', values.get('RN/AKZ'))
    if not identity and values.get('DB') == 'IR':
        identity = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('AKZ', [None])[0]
    if not identity or not values.get('DB'):
        return None
    return {'id': identity, 'mark': values.get('MD', ''),
            # INH is the present proprietor, which may have changed after asof.
            # Only ANM establishes an applicant-only match without a name match.
            'applicant': values.get('ANM', ''), 'owner': values.get('INH', ''),
            'date': date_value(values.get('AT', '')),
            'office': 'WO' if values['DB'] == 'IR' else values['DB'], 'url': url}


class ResultForm(HTMLParser):
    """Successful form controls needed for DPMA's ordinary next-page POST."""
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.action = None
        self.fields = {}
        self.select = None
        self.options = []
        self.can_next = False
        self.feed(body)

    def handle_starttag(self, tag, attributes):
        a = dict(attributes)
        if tag == 'form' and a.get('id') == 'form':
            self.active, self.action = True, a.get('action')
        if not self.active:
            return
        if tag == 'input' and a.get('name') and 'disabled' not in a:
            name, kind = a['name'], a.get('type', 'text')
            if kind in ('checkbox', 'radio') and 'checked' not in a:
                return
            if kind in ('submit', 'button', 'image'):
                if name != 'blaetter_eins_vor':
                    return
                self.can_next = True
            self.fields[name] = a.get('value', 'on' if kind == 'checkbox' else '')
        if tag == 'select':
            self.select, self.options = a.get('name'), []
        if tag == 'option' and self.select:
            self.options.append((a.get('value', ''), 'selected' in a))

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active = False
        if tag == 'select' and self.select:
            selected = next((v for v, chosen in self.options if chosen),
                            self.options[0][0] if self.options else '')
            self.fields[self.select] = selected
            self.select = None


def large_dpma_search(name, cutoff=None):
    """Export broad searches instead of fetching thousands of detail pages.

    The export contains dates and word marks, but represents figurative marks
    by image URLs and gives the present proprietor rather than the applicant.
    It can therefore prove positives, but is deliberately not used for zeros,
    exact counts or earliest dates when unverified candidates remain.
    """
    opener = urllib.request.build_opener(CachedRedirect(),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    query = urllib.parse.urlencode({'queryString': name, 'lang': 'en', 'view': 'full'})
    response = fetch('https://register.dpma.de/DPMAregister/smartsearch?' + query, opener=opener)
    try:
        data = json.loads(response['body'])
        if data.get('marErrors') or not data.get('marLink'):
            return [], False, response['url']
    except (ValueError, AttributeError):
        return [], False, response['url']
    url = urllib.parse.urljoin(response['url'], data['marLink']) + '&view=full'
    response = fetch(url, opener=opener)
    form = ResultForm(response['body'])
    if not form.action:
        return [], False, url
    form.fields.pop('blaetter_eins_vor', None)
    form.fields['a_trefferliste_uebernehmen'] = 'Save result list configuration'
    for index in range(2, 13):
        form.fields['checkbox_' + str(index)] = 'on'
    fetch(form.action + '?' + query, urllib.parse.urlencode(form.fields), opener,
          'application/x-www-form-urlencoded')
    url = 'https://register.dpma.de/DPMAregister/marke/trefferliste.navigationleiste.aktionsmenue:herunterladencsv?' + query
    response = fetch(url, opener=opener)
    if response['status'] != 200:
        return [], False, url
    rows = list(csv.reader(io.StringIO(response['body']), delimiter=';'))
    header_index = next((i for i, row in enumerate(rows) if row and row[0] == 'Data file'), None)
    if header_index is None:
        return [], False, url
    headers = rows[header_index]
    records, fully_verified, owner_candidates = [], True, []
    for row in rows[header_index + 1:]:
        if not row:
            continue
        item = dict(zip(headers, row))
        identity = item.get('File number/register number')
        office = item.get('Data file')
        if not identity or not office:
            fully_verified = False
            continue
        mark = item.get('Reproduction of the mark', '')
        owner = item.get('Applicant/proprietor', '')
        endpoint = {'DE': 'register/' + identity + '/DE',
                    'EM': 'registerHABM?AKZ=' + identity,
                    'IR': 'registerIR?AKZ=' + identity}.get(office)
        detail_url = 'https://register.dpma.de/DPMAregister/marke/' + endpoint if endpoint else url
        if norm(mark) == norm(name) and not mark.startswith('http'):
            records.append({'id': identity, 'office': 'WO' if office == 'IR' else office,
                            'mark': mark, 'applicant': '', 'owner': owner,
                            'date': date_value(item.get('Application date', '')), 'url': detail_url})
        elif contains_company(owner, name) and endpoint:
            owner_candidates.append(detail_url)
        else:
            fully_verified = False
    # No exact count can be recovered from this export if other candidates
    # remain unverified. Once presence before cutoff is proved, further
    # present-owner detail lookups cannot improve that binary feature.
    def positive():
        return cutoff is not None and any(r['date'] and r['date'] < cutoff and
            (norm(r['mark']) == norm(name) or contains_company(r.get('applicant', ''), name))
            for r in records)
    if len(owner_candidates) > 30:
        fully_verified = False
    for detail_url in owner_candidates[:30]:
        record = detail_record(detail_url, opener)
        if record:
            records.append(record)
        else:
            fully_verified = False
    complete = fully_verified and len(records) == int(data['marHits'])
    return records, complete, url


def dpma_search(name, cutoff=None):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(CachedRedirect(), urllib.request.HTTPCookieProcessor(jar))
    query = urllib.parse.urlencode({'queryString': name, 'lang': 'en'})
    search_url = 'https://register.dpma.de/DPMAregister/smartsearch?' + query
    response = fetch(search_url, opener=opener)
    try:
        data = json.loads(response['body'])
        if response['status'] != 200 or data.get('marErrors') or 'marHits' not in data:
            return [], False, search_url
        total = int(data['marHits'])
    except (ValueError, TypeError, AttributeError):
        return [], False, search_url
    if total == 0:
        return [], True, search_url
    if total < 0 or not data.get('marLink'):
        return [], False, search_url
    if total > 40:
        return large_dpma_search(name, cutoff)
    url = urllib.parse.urljoin(search_url, data['marLink']) + '&lang=en'
    result = fetch(url, opener=opener)
    records = []
    if result['status'] != 200 or 'Search result' not in result['body']:
        return [], False, search_url
    links = []
    for page in range(1, 1001):
        page_links = [html.unescape(x) for x in re.findall(
            r'href="([^"]*(?:register(?:HABM|IR)?\?[^\"]*AKZ=[^\"]+|/marke/register/[0-9]+/[A-Z]+))"',
            result['body'], re.I)]
        if not page_links or any(x in links for x in page_links):
            break
        links.extend(page_links)
        if len(links) >= total:
            break
        form = ResultForm(result['body'])
        if not form.can_next or not form.action:
            break
        page_url = form.action + '?' + urllib.parse.urlencode(
            {'queryString': name, 'lang': 'en', 'page': page + 1})
        result = fetch(page_url, urllib.parse.urlencode(form.fields), opener,
                       'application/x-www-form-urlencoded')
        if result['status'] != 200:
            break
    complete = len(links) == total
    for link in links:
        detail_url = urllib.parse.urljoin('https://register.dpma.de/DPMAregister/marke/', link)
        record = detail_record(detail_url, opener)
        if record is None:
            complete = False
        else:
            records.append(record)
    return records, complete, search_url


# DPMA's INH is the present proprietor. It may establish the country for
# an exact-mark match, but never substitutes for ANM in applicant-name matching.
def matched(record, company):
    applicant = record.get('applicant', '')
    if contains_company(applicant, company):
        return 'applicant_name'
    # Do not substitute today's proprietor for the original applicant.
    country = re.search(r'(?:,|\s)\s*(DE|Deutschland|Germany)\s*$', applicant, re.I)
    if norm(record.get('mark', '')) == norm(company) and country:
        # An explicitly conflicting business name is not the same company.
        business = applicant.split(',')[0].strip()
        if contains_company(business, company):
            return 'exact_mark_DE_applicant_name'
    return ''


def probe_sources():
    status = {}
    for source, urls in [
        ('TMview', ['https://www.tmdn.org/tmview/api/search/results?translate=true'] * 3),
        ('EUIPO', ['https://euipo.europa.eu/eSearch/'] * 3),
    ]:
        attempts = []
        for i, url in enumerate(urls):
            # Distinct cache keys preserve all three independent failures.
            url += ('&' if '?' in url else '?') + 'research_probe=' + str(i + 1)
            payload = json.dumps({'page':'1','pageSize':'100','criteria':'C',
                'basicSearch':'Holloid','newPage':True}) if source == 'TMview' else None
            response = fetch(url, payload)
            try:
                data = json.loads(response['body'])
                usable = isinstance(data, dict) and isinstance(data.get('tradeMarks'), list)
            except (ValueError, TypeError):
                usable = False
            attempts.append({'status': response['status'], 'usable': usable})
            if usable:
                break
        status[source] = {'attempts': attempts, 'worked': any(x['usable'] for x in attempts)}
        print(source, status[source], flush=True)
    return status


def probe_wipo():
    attempts = []
    for i in range(3):
        response = fetch('https://branddb.wipo.int/?research_probe=' + str(i + 1))
        blocked = any(x in response['body'].lower() for x in ('altcha', 'captcha', 'request rejected'))
        attempts.append({'status':response['status'], 'verification_page':blocked})
        if not blocked and response['status'] == 200:
            break
    return {'attempts':attempts, 'worked':False,
            'reason':'No usable automated search; verification page or frontend only'}


def process_v2(row):
    output = dict.fromkeys(FIELDS, '')
    output.update(cid=row['cid'], name=row['name'])
    # Two display labels contain a tagline or two explicitly supplied brands.
    name = clean_name(row['name']).split(' - ',1)[0].split(' / ')[-1].rstrip('.')
    cutoff = date_value(row['asof_date'])
    try:
        records, complete, url = dpma_search(name, cutoff)
        valid, undated, uncertain = {}, 0, 0
        for r in records:
            basis = matched(r, name)
            if basis:
                r['match_basis'] = basis
                valid[(r['office'], r['id'])] = r
            elif not r.get('applicant') and (contains_company(r.get('owner',''),name)
                    or norm(r.get('mark','')) == norm(name)):
                uncertain += 1
        dated = [r for r in valid.values() if r['date']]
        before = [r for r in dated if r['date'] < cutoff]
        undated = len(valid) - len(dated)
        # Counts are observed matched filings, not an inferred global portfolio.
        # Incomplete searches only yield positive lower bounds, never zeros.
        count_complete = complete and not uncertain
        if valid or count_complete:
            output['tm_count_total'] = str(len(valid))
        if before or (count_complete and not undated):
            output['tm_count_before_asof'] = str(len(before))
        if dated:
            output['tm_first_filing_date'] = min(r['date'] for r in dated).isoformat()
        output['tm_offices'] = ';'.join(sorted({r['office'] for r in valid.values()}))
        output['tm_evidence_url'] = ';'.join(dict.fromkeys(r['url'] for r in valid.values())) if valid else url
        evidence = [{'office':r['office'], 'application_number':r['id'],
            'filing_date':r['date'].isoformat() if r['date'] else None,
            'mark':r['mark'], 'match_basis':r['match_basis'], 'url':r['url']}
            for r in sorted(valid.values(), key=lambda r:(str(r['date']),r['id']))]
        output['tm_note'] = json.dumps({'source':'DPMAregister',
            'asof_date':row['asof_date'], 'date_rule':'filing_date < asof_date',
            'query':name, 'search_complete':complete, 'count_scope':'observed matched filings in DE/EM/IR company-name search',
            'counts_are_lower_bounds':not count_complete, 'undated_matches':undated,
            'unresolved_original_applicant_matches':uncertain,
            'first_date_scope':'earliest observed matched filing, including after asof',
            'current_proprietor_caveat':'INH country/name is current; original applicant unavailable unless ANM present',
            'evidence':evidence},ensure_ascii=False,separators=(',',':'))
        return output, complete or bool(records)
    except Exception as exc:
        output['tm_note'] = json.dumps({'source':'DPMAregister','error':type(exc).__name__,
            'asof_date':row['asof_date']},separators=(',',':'))
        return output, False


def write_rows(rows):
    # One writer in the main thread, flushed after every finished company.
    with (ROOT / 'raw/v2_trademarks.csv').open('w',encoding='utf-8',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        f.flush()


def main():
    rows = list(csv.DictReader((ROOT/'raw/companies_all.csv').open(encoding='utf-8-sig',newline='')))
    outputs = [{key:row.get(key,'') if key in ('cid','name') else '' for key in FIELDS} for row in rows]
    output_path = ROOT/'raw/v2_trademarks.csv'
    if output_path.exists():
        saved = {x['cid']:x for x in csv.DictReader(output_path.open(encoding='utf-8',newline=''))}
        for i,row in enumerate(rows):
            if saved.get(row['cid'],{}).get('tm_note') and saved[row['cid']]['name'] == row['name']:
                outputs[i] = saved[row['cid']]
    write_rows(outputs)
    sources = probe_sources()
    remaining = [i for i,x in enumerate(outputs) if not x['tm_note']]
    failures, done, worked = 0, len(rows)-len(remaining), 0
    # A small submission window prevents further dispatch after three failures.
    with ThreadPoolExecutor(max_workers=24) as pool:
        pending, cursor = {}, 0
        while pending or cursor < len(remaining):
            while len(pending) < 24 and cursor < len(remaining) and failures < 3:
                index = remaining[cursor]
                pending[pool.submit(process_v2,rows[index])] = index
                cursor += 1
            if not pending:
                break
            future = next(as_completed(pending))
            index = pending.pop(future)
            outputs[index], ok = future.result()
            failures = 0 if ok else failures + 1
            worked += int(ok)
            done += 1
            write_rows(outputs)
            if done % 10 == 0 or not ok:
                print(f'Processed {done}/{len(rows)}; source failures in succession {failures}; positive companies '+
                    str(sum(bool(x['tm_count_total']) and x['tm_count_total'] != '0' for x in outputs)),flush=True)
    sources['DPMAregister'] = {'worked':bool(worked) or done > len(remaining),'completed_companies':done,'usable_searches_this_run':worked,
                              'stopped_after_three_failures':failures >= 3}
    sources['WIPO'] = probe_wipo()
    (CACHE/'source_status.json').write_text(json.dumps(sources,indent=2),encoding='utf-8')
    write_rows(outputs)
    print('Rows written:',len(outputs),flush=True)
    for field in FIELDS:
        print(field, sum(x[field] != '' for x in outputs), '/',len(outputs),flush=True)
    print('Sources:',json.dumps(sources),flush=True)


if __name__ == '__main__':
    main()

