#!/usr/bin/env python3
"""Public R&D grant features, strictly using project starts before asof_date.

Python standard library only. HTTP bodies and metadata (including HTTP errors and
redirects) are cached by SHA1(URL) in raw/cache/m_grants. Cohort programme flags
are reported as requested, but undated cohort labels alone cannot establish a
strictly earlier grant start and therefore do not set grant_score to one.
"""
import argparse
import concurrent.futures
import csv
import datetime as dt
import functools
import hashlib
import html
from html.parser import HTMLParser
import http.cookiejar
import io
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw' / 'cache' / 'm_grants'
URLS = ['https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip',
        'https://cordis.europa.eu/data/cordis-h2020projects-csv.zip']
FOEKAT = 'https://foerderportal.bund.de/foekat/'
FIELDS = ['name', 'website', 'asof_date', 'cordis_projects_before',
          'cordis_first_start_date', 'cordis_evidence', 'foekat_projects_before',
          'foekat_evidence', 'exist_or_programme_grant', 'grant_score']
GUARD = threading.Lock()
HOSTS = {}
URL_LOCKS = {}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

OPENER = urllib.request.build_opener(NoRedirect)

def fetch(url, depth=0):
    """Cache every response; space request starts by >=1 second per host."""
    if depth > 8:
        raise RuntimeError('Too many redirects')
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode('utf-8')).hexdigest()
    with GUARD:
        lock = URL_LOCKS.setdefault(key, threading.Lock())
    with lock:
        meta_path, body_path = CACHE / (key + '.json'), CACHE / (key + '.body')
        if meta_path.exists() and body_path.exists():
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        else:
            for attempt in range(3):
                host = urllib.parse.urlsplit(url).netloc.lower()
                with GUARD:
                    state = HOSTS.setdefault(host, [threading.Lock(), 0.0])
                with state[0]:
                    time.sleep(max(0, state[1] + 1.1 - time.monotonic()))
                    state[1] = time.monotonic()
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (research script)'})
                response = None
                try:
                    try:
                        response = OPENER.open(req, timeout=30)
                    except urllib.error.HTTPError as exc:
                        response = exc
                    status = response.code
                    # Keep each retry response as well as the canonical final response.
                    attempt_path = CACHE / (key + '.attempt' + str(attempt) + '.body')
                    with attempt_path.open('wb') as out:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                    meta = {'url': url, 'status': status, 'headers': dict(response.headers),
                            'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat()}
                    (CACHE / (key + '.attempt' + str(attempt) + '.json')).write_text(json.dumps(meta), encoding='utf-8')
                    if body_path.exists():
                        body_path.replace(CACHE / (key + '.attempt' + str(attempt - 1) + '.body'))
                    attempt_path.replace(body_path)
                    meta_path.write_text(json.dumps(meta), encoding='utf-8')
                    if status not in (429, 500, 502, 503, 504) or attempt == 2:
                        break
                except (OSError, TimeoutError) as exc:
                    if attempt == 2:
                        raise RuntimeError(str(exc)) from exc
                finally:
                    if response is not None:
                        response.close()
                time.sleep(2 ** (attempt + 1))
    status = meta['status']
    if status in (301, 302, 303, 307, 308):
        location = next((v for k, v in meta['headers'].items() if k.lower() == 'location'), '')
        return fetch(urllib.parse.urljoin(url, location), depth + 1)
    if status != 200:
        raise RuntimeError('HTTP %s for %s' % (status, url))
    return body_path

@functools.lru_cache(maxsize=100000)
def normalise(value):
    value = html.unescape(value).casefold().replace('ß', 'ss')
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    value = re.sub(r'[^a-z0-9]+', ' ', value).strip()
    suffix = r'\s+(?:gmbh\s+(?:und\s+|co\s+|and\s+)*kg|gmbh|ggmbh|ug(?:\s+haftungsbeschrankt)?|ag|se|kg|ohg|gbr|mbh|limited|ltd|inc|corporation|co)$'
    while re.search(suffix, value):
        value = re.sub(suffix, '', value).strip()
    return value


def matches(query, organisation):
    a, b = normalise(query), normalise(organisation)
    return bool(a and b and (a == b or a.replace(' ', '') == b.replace(' ', '')
                            or (len(a.replace(' ', '')) >= 4 and b.startswith(a + ' '))))


def iso_date(value):
    try:
        return dt.date.fromisoformat(value.strip()[:10]).isoformat()
    except (ValueError, AttributeError):
        return None


def load_cordis(url):
    """Stream ZIP members without writing extracted files."""
    projects, organisations = {}, {}
    csv.field_size_limit(10 * 1024 * 1024)
    with zipfile.ZipFile(fetch(url)) as archive:
        for filename, required in [('project.csv', {'id', 'startDate'}),
                                   ('organization.csv', {'name', 'country', 'projectID'})]:
            member = next((x for x in archive.namelist() if x.rsplit('/', 1)[-1] == filename), None)
            if member is None:
                raise ValueError('Missing ' + filename)
            with archive.open(member) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig'), delimiter=';')
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError('Unexpected schema in ' + filename)
                for row in reader:
                    if filename == 'project.csv':
                        projects[row['id']] = (iso_date(row['startDate']), row.get('acronym', ''))
                    elif row['country'].upper() == 'DE':
                        # Associated partners with explicitly zero EU contribution
                        # are not grant recipients.
                        amount = row.get('netEcContribution') or row.get('ecContribution')
                        if amount and float(amount.replace(',', '.')) <= 0:
                            continue
                        organisations.setdefault(normalise(row['name']), []).append((row['projectID'], row['name']))
    print('CORDIS loaded: %s; %s projects, %s German recipients' %
          (url.rsplit('/', 1)[-1], len(projects), len(organisations)), flush=True)
    return projects, organisations


class ResultsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cells, self.parts = [], None, None
        self.headers = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'h1':
            self.headers.append(attrs.get('aria-label', ''))
        if tag == 'tr':
            self.cells = []
        elif tag == 'td' and self.cells is not None:
            self.parts = []
        elif tag in ('br', 'div') and self.parts is not None:
            self.parts.append(' ')

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.parts is not None:
            self.cells.append(' '.join(''.join(self.parts).split()))
            self.parts = None
        elif tag == 'tr' and self.cells is not None:
            if self.cells:
                self.rows.append(self.cells)
            self.cells = None


def foekat_url(name, first=1):
    # Full GET queries avoid shared server-side search state between workers.
    return 'https://foerderportal.bund.de/foekat/jsp/SucheAction.do?' + urllib.parse.urlencode({
        'actionMode': 'searchlist', 'suche.detailSuche': 'true', 'suche.ZeSt': 'ZE',
        'suche.nameZeStSuche': name.rstrip('. ') + '%', 'suche.lfdVhb': 'N',
        'suche.nurVerbund': 'N', 'submitAction': 'Detailsuche starten',
        'suche.listrowpersite': '50', 'suche.listrowfrom': str(first)}, encoding='iso-8859-15', errors='replace')


def search_foekat(name, cutoff):
    found = {}
    first, seen = 1, set()
    while True:
        page = fetch(foekat_url(name, first)).read_text(encoding='iso-8859-15')
        parser = ResultsParser()
        parser.feed(page)
        summary = ' '.join(parser.headers)
        if 'Keine Treffer' in summary and 'Die Suche liefert keine Ergebnisse' in page:
            return found, True
        total_match = re.search(r'(\d[\d.]*) Treffer insgesamt', summary)
        if not total_match:
            raise ValueError('Foerderkatalog blocked or unrecognised search response')
        total = int(total_match[1].replace('.', ''))
        result_rows = [r for r in parser.rows if len(r) == 8]
        if not result_rows:
            raise ValueError('Foerderkatalog result table was not parseable')
        keys = set()
        missing_dates = False
        for cells in result_rows:
            fkz = cells[0].split()[0]
            keys.add(fkz)
            if not matches(name, cells[2]):
                continue
            date_match = re.search(r'\b(\d{2}\.\d{2}\.\d{4})\b', cells[5])
            if not date_match:
                missing_dates = True
                continue
            start = dt.datetime.strptime(date_match[1], '%d.%m.%Y').date().isoformat()
            if start < cutoff:
                evidence = ('https://foerderportal.bund.de/foekat/jsp/SucheAction.do?actionMode=view&fkz='
                            + urllib.parse.quote(fkz))
                found[fkz] = {'url': evidence, 'start': start, 'recipient': cells[2]}
        if missing_dates:
            return found, False
        if not keys - seen:
            raise ValueError('Foerderkatalog pagination did not advance')
        seen.update(keys)
        if len(seen) >= total:
            return found, True
        first += len(result_rows)
        if first > 10000:
            raise ValueError('Foerderkatalog result set unexpectedly large')


def programme_flag(row):
    value = (row.get('cohort', '') + ' ' + row.get('source', '')).lower().replace('_', ' ')
    pattern = r'\bexist\b|prototyping[ -]+grant|science\s*4\s*life|grunderstipendium|gründerstipendium|forschungstransfer|go[ -]?bio|\bvip\+|\beurostars\b'
    return 1 if re.search(pattern, value) else ''


def process_row(row, datasets):
    out = {field: '' for field in FIELDS}
    for field in ('name', 'website', 'asof_date'):
        out[field] = row.get(field, '')
    cutoff = iso_date(out['asof_date'])
    out['exist_or_programme_grant'] = programme_flag(row)
    if not cutoff or not normalise(row['name']):
        return out
    found, incomplete = {}, len(datasets) != len(URLS)
    for projects, organisations in datasets:
        for org_name, participations in organisations.items():
            if not matches(row['name'], org_name):
                continue
            for project_id, recipient in participations:
                start, acronym = projects.get(project_id, (None, ''))
                if not start:
                    incomplete = True
                elif start < cutoff:
                    found[project_id] = {'url': 'https://cordis.europa.eu/project/id/' + project_id,
                                         'start': start, 'recipient': recipient, 'acronym': acronym}
    if not incomplete:
        out['cordis_projects_before'] = len(found)
    if found:
        out['cordis_first_start_date'] = min(p['start'] for p in found.values())
        out['cordis_evidence'] = json.dumps(sorted(found.values(), key=lambda p: (p['start'], p['url'])), ensure_ascii=False)
    foekat, foekat_ok = {}, False
    try:
        foekat, foekat_ok = search_foekat(row['name'], cutoff)
        if foekat_ok:
            out['foekat_projects_before'] = len(foekat)
        if foekat:
            out['foekat_evidence'] = json.dumps(sorted(foekat.values(), key=lambda p: (p['start'], p['url'])), ensure_ascii=False)
    except (RuntimeError, OSError, ValueError) as exc:
        print('Foerderkatalog unavailable for %s: %s' % (row['name'], exc), flush=True)
    if found or foekat:
        out['grant_score'] = 1
    elif not incomplete or foekat_ok:
        out['grant_score'] = 0
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, choices=(4, 5, 6), default=5)
    args = parser.parse_args()
    with (ROOT / 'raw' / 'cohort_short.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    datasets = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(load_cordis, url): url for url in URLS}
        for future in concurrent.futures.as_completed(futures):
            try:
                datasets.append(future.result())
            except (RuntimeError, OSError, ValueError, zipfile.BadZipFile, csv.Error) as exc:
                print('CORDIS failed: %s: %s' % (futures[future], exc), flush=True)
        work = {pool.submit(process_row, row, datasets): i for i, row in enumerate(rows)}
        results = [None] * len(rows)
        for n, future in enumerate(concurrent.futures.as_completed(work), 1):
            results[work[future]] = future.result()
            if n % 20 == 0 or n == len(rows):
                print('Processed %s/%s rows' % (n, len(rows)), flush=True)
    output = ROOT / 'raw' / 'm_grants.csv'
    with output.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)
    print('\nWrote %s rows to %s' % (len(results), output))
    print('Column fill rates:')
    for field in FIELDS:
        filled = sum(r[field] != '' for r in results)
        pct = 100 * filled / len(results) if results else 0
        print('  %s: %s/%s (%.2f%% filled; %.2f%% missing)' % (field, filled, len(results), pct, 100 - pct))
    print('grant_score counts:', {str(v): sum(r['grant_score'] == v for r in results) for v in (1, 0, '')})
    print('Programme metadata alone is undated and does not prove a grant start before asof_date.')
    print('\nFive example rows:')
    examples = [r for r in results if r['grant_score'] == 1][:3]
    for row in results:
        if len(examples) >= 5:
            break
        if row not in examples:
            examples.append(row)
    import sys
    writer = csv.DictWriter(sys.stdout, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(examples)


if __name__ == '__main__':
    main()
