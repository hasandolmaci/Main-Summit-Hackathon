"""Worldwide patent research (stdlib only); unknown is blank, never zero.

Raw source responses, including failures, live in raw/cache/v2_patents_epo.
Run from any directory: python scripts/v2_patents_epo.py [--probe].
"""
import argparse
import base64
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse as up
import urllib.request as ur

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw/cache/v2_patents_epo'
OUTPUT = ROOT / 'raw/v2_patents_epo.csv'
FIELDS = ['cid', 'name', 'epo_apps_before_asof', 'epo_first_priority_date',
          'epo_apps_total', 'epo_founder_inventor_before_asof',
          'epo_evidence_url', 'epo_note']
STATES = {}
LOCK = threading.Lock()
REFRESH_FAILURES = False


def state_for(host):
    with LOCK:
        return STATES.setdefault(host, dict(lock=threading.Lock(), last=0,
            failures=0, successes=0, skipped=0, disabled=False, errors=[]))


def fetch(url):
    """Serialize each host, limit to <2 req/s, stop after three failures."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    path = CACHE / (key + '.json')
    host = up.urlsplit(url).netloc
    state = state_for(host)
    with state['lock']:
        if path.exists():
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('usable'):
                return base64.b64decode(record['body_base64'])
            if not REFRESH_FAILURES:
                # Carry the circuit breaker across interrupted/repeated runs.
                attempts = sorted(CACHE.glob(key + '.attempt*.json'))
                errors = [json.loads(p.read_text(encoding='utf-8')) for p in attempts]
                state['failures'] = max(state['failures'], len(errors))
                state['errors'] = [str(r['status']) + ':' + r.get('error', 'HTTP failure')
                                   for r in errors]
                state['disabled'] = state['failures'] >= 3
                return None
        if state['disabled']:
            state['skipped'] += 1
            return None
        for attempt in range(1, 4):
            if state['disabled']:
                break
            time.sleep(max(0, .6 - (time.monotonic() - state['last'])))
            state['last'] = time.monotonic()
            record = dict(url=url, retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                          status=0, body_base64='', usable=False)
            try:
                request = ur.Request(url, headers={'User-Agent': 'research script',
                    'Accept': 'application/json, application/xml, text/html, */*'})
                try:
                    response = ur.urlopen(request, timeout=20)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    body = response.read()
                    record.update(status=response.code, body_base64=base64.b64encode(body).decode())
                lowered = body[:60000].lower()
                blocked = any(s in lowered for s in (b'cf-chl-', b'<title>just a moment',
                    b'unusual traffic', b'access denied', b'sending automated queries',
                    b'captcha', b'<title>lens - human verification'))
                record['usable'] = record['status'] == 200 and not blocked
                if blocked:
                    record['error'] = 'challenge/block page'
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                record['error'] = type(error).__name__
            payload = json.dumps(record, ensure_ascii=False)
            (CACHE / (key + '.attempt' + str(attempt) + '.json')).write_text(payload, encoding='utf-8')
            path.write_text(payload, encoding='utf-8')
            if record['usable']:
                state['successes'] += 1
                return body
            state['failures'] += 1
            reason = str(record['status']) + ':' + record.get('error', 'HTTP failure')
            state['errors'].append(reason)
            print('Source failure', host, reason, flush=True)
            if state['failures'] >= 3:
                state['disabled'] = True
                break
            time.sleep(2 ** attempt)
    return None


def urls(name):
    query = up.urlencode({'assignee': name, 'num': '100', 'groupby': 'none'})
    return {
        'google': 'https://patents.google.com/xhr/query?url=' + up.quote(query, safe='') + '&exp=',
        'espacenet': 'https://worldwide.espacenet.com/3.2/rest-services/search?q=' + up.quote('pa="' + name + '"'),
        'lens': 'https://www.lens.org/lens/search/patent/list?q=' + up.quote('applicant.name:"' + name + '"'),
    }


def normalize(value):
    value = unicodedata.normalize('NFKD', value.casefold().replace('\u00df', 'ss'))
    return re.sub('[^a-z0-9]', '', value.encode('ascii', 'ignore').decode())


def company_core(value):
    # Strip legal forms, never substantive words like 'technologies'.
    value = re.sub(r'\b(?:gmbh|ug|ag|haftungsbeschr\u00e4nkt|mbh)\b', '', value, flags=re.I)
    value = re.sub(r'\b(?:co|kg)\b', '', value, flags=re.I)
    return value.strip(' .,&()-')


def iso_date(value):
    value = str(value or '').strip()
    if re.fullmatch(r'\d{8}', value):
        value = value[:4] + '-' + value[4:6] + '-' + value[6:]
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return ''


def founder_match(founders, inventors):
    def tokens(value):
        value = re.sub(r'\b(?:prof|dr)\.?', '', value, flags=re.I)
        value = unicodedata.normalize('NFKD', value.casefold().replace('\u00df', 'ss'))
        return set(re.findall('[a-z]+', value.encode('ascii', 'ignore').decode()))
    for founder in founders:
        ft = tokens(founder)
        if len(ft) >= 2 and any(ft == tokens(i) for i in inventors):
            return founder
    return ''


def load_founders(companies):
    result = {c['cid']: {'asof': set(), 'now': set(), 'evidence': []} for c in companies}
    cutoffs = {c['cid']: c['asof_date'] for c in companies}
    for c in companies:
        # Input-supplied founders are authorized identity candidates. Their
        # founder role is not independently inferred to have a historical date.
        result[c['cid']]['now'].update(n.strip() for n in c['known_founders'].split(';') if n.strip())
    for filename in ('v2_founders_A.csv', 'v2_founders_B.csv'):
        with (ROOT / 'raw' / filename).open(encoding='utf-8-sig', newline='') as handle:
            for row in csv.DictReader(handle):
                if row['cid'] not in result:
                    continue
                for key, field in [('asof', 'founder_names_asof'), ('now', 'founder_names_now')]:
                    names = {n.strip() for n in row.get(field, '').split(';') if n.strip()}
                    day = iso_date(row.get('snapshot_date', '')[:10])
                    if key == 'asof' and names:
                        if not day or day >= cutoffs[row['cid']]:
                            result[row['cid']]['now'].update(names)
                            continue
                        result[row['cid']]['evidence'].append('founder evidence date=' + day +
                            ', source=' + row.get('evidence_url', ''))
                    result[row['cid']][key].update(names)
    return result


class PatentHTML(HTMLParser):
    """Only retain public professional names, dates and application IDs."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.items = {}
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name'):
            name = attrs['name']
            if name == 'DC.contributor':
                if attrs.get('scheme') == 'assignee':
                    name = 'citation_assignee'
                elif attrs.get('scheme') != 'inventor':
                    return
            self.meta.setdefault(name, []).append(attrs.get('content', ''))
        prop = attrs.get('itemprop', '')
        if prop:
            if 'content' in attrs:
                self.items.setdefault(prop, []).append(attrs['content'])
            elif tag in ('span', 'dd', 'time'):
                self.stack.append((tag, prop, []))

    def handle_data(self, data):
        for _, _, text in self.stack:
            text.append(data)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            open_tag, prop, parts = self.stack[index]
            if open_tag == tag:
                self.items.setdefault(prop, []).append(' '.join(''.join(parts).split()))
                del self.stack[index]
                break


def parse_google_detail(body, company, founders, url):
    doc = PatentHTML()
    doc.feed(body.decode('utf-8', 'replace'))
    # DC.contributor includes inventors on some pages; require an assignee field
    # explicitly or citation_assignee, rather than treating contributors as owners.
    applicants = doc.items.get('assigneeOriginal', []) + doc.items.get('assigneeCurrent', [])
    applicants += doc.meta.get('citation_assignee', [])
    company_norm = normalize(company_core(company))
    if not any(normalize(company_core(a)) == company_norm for a in applicants):
        return None, 'uncertain/rejected applicant name match: ' + url
    inventors = doc.meta.get('DC.contributor', []) + doc.meta.get('citation_inventor', [])
    inventors += doc.items.get('inventor', [])
    matched = founder_match(founders['asof'] | founders['now'], inventors)
    # Publication jurisdiction and countryStatus are NOT applicant nationality.
    # Google often omits applicant addresses: use the authorized founder identity
    # route, otherwise leave the candidate uncounted.
    if not matched:
        return None, 'uncertain: matching assignee but no DE/EP applicant address or founder inventor match: ' + url
    # DC.date can be publication date; only explicit filingDate/citation_filing_date.
    filing = next((d for v in doc.items.get('filingDate', []) + doc.meta.get('citation_filing_date', [])
                   if (d := iso_date(v))), '')
    priority = next((d for v in doc.items.get('priorityDate', []) + doc.meta.get('citation_priority_date', [])
                     if (d := iso_date(v))), '')
    publication = next((d for v in doc.items.get('publicationDate', []) + doc.meta.get('citation_publication_date', [])
                        if (d := iso_date(v))), '')
    application = next(iter(doc.items.get('applicationNumber', []) + doc.meta.get('citation_application_number', [])), '')
    if not application:
        return None, 'uncertain: no application identifier for deduplication: ' + url
    return dict(application=re.sub(r'\s+', '', application), priority=priority,
                filing=filing, publication=publication, founder=matched,
                founder_dated=bool(founder_match(founders['asof'], inventors)), url=url), ''


def google_candidates(body):
    data = json.loads(body)
    results = data['results']
    total = int(str(results['total_num_results']).replace(',', ''))
    patents = []
    for cluster in results.get('cluster', []):
        for entry in cluster.get('result', []):
            if entry.get('patent', {}).get('publication_number'):
                patents.append(entry['patent']['publication_number'])
    # A capped/ambiguous result set is not an exact count.
    complete = len(patents) >= total
    return list(dict.fromkeys(patents)), complete


def source_summary():
    parts = []
    for host, state in sorted(STATES.items()):
        status = ('stopped after 3 failures' if state['disabled'] else
                  'worked' if state['successes'] else 'unavailable')
        errors = ','.join(sorted(set(state['errors'])))
        parts.append(host + ': ' + status + (' (' + errors + ')' if errors else ''))
    return '; '.join(parts)


def process(company, founders, available):
    row = dict(dict.fromkeys(FIELDS, ''), cid=company['cid'], name=company['name'])
    cutoff = company['asof_date']
    notes = ['cutoff=' + cutoff + ' (strictly earlier)',
             'worldwide applications; distinct application IDs, not families/publications',
             'before-asof requires priority/filing AND public publication date before cutoff',
             source_summary()]
    if 'google' not in available:
        notes.append('no usable worldwide search; company query not executed after source circuit breakers; blank=unknown, not zero')
        row['epo_note'] = '; '.join(notes)
        return row
    query_url = urls(company_core(company['name']))['google']
    body = fetch(query_url)
    if not body:
        row['epo_note'] = '; '.join(notes + ['company search unavailable; counts unknown'])
        return row
    try:
        candidates, complete = google_candidates(body)
    except (ValueError, KeyError, TypeError):
        row['epo_note'] = '; '.join(notes + ['unrecognized Google search JSON; counts unknown'])
        return row
    matches = {}
    uncertain = []
    for number in candidates:
        url = 'https://patents.google.com/patent/' + up.quote(number, safe='') + '/en'
        detail = fetch(url)
        if detail is None:
            uncertain.append('unavailable detail: ' + url)
            continue
        match, reason = parse_google_detail(detail, company['name'], founders, url)
        if match:
            # Multiple publications/grants of one application count once.
            matches.setdefault(match['application'], match)
        else:
            uncertain.append(reason)
    apps = list(matches.values())
    dated = [a for a in apps if (a['priority'] or a['filing']) and
             (a['priority'] or a['filing']) < cutoff and
             a['publication'] and a['publication'] < cutoff]
    undated = [a['application'] for a in apps if not (a['priority'] or a['filing']) or not a['publication']]
    exact = complete and not uncertain
    # Positive partial counts are explicitly marked as lower bounds.
    if apps or exact:
        row['epo_apps_total'] = len(apps)
    if dated or (exact and not undated):
        row['epo_apps_before_asof'] = len(dated)
    priorities = sorted(a['priority'] for a in apps if a['priority'])
    if priorities:
        row['epo_first_priority_date'] = priorities[0]
    # Identity from names_now remains separately documented; founder role must
    # have dated evidence to set the historical founder-inventor indicator.
    if any(a['founder_dated'] for a in dated):
        row['epo_founder_inventor_before_asof'] = 1
    elif exact and not undated and founders['asof'] and not dated:
        row['epo_founder_inventor_before_asof'] = 0
    if apps or exact:
        row['epo_evidence_url'] = ' | '.join([query_url] + [a['url'] for a in apps])
    if not exact:
        notes.append('positive counts are verified lower bounds; incomplete/unverified candidates not counted')
    notes.append('undated applications (not before-asof)=' + ','.join(undated))
    for app in apps:
        notes.append('application={application}, priority={priority}, filing={filing}, '
                     'publication={publication}, founder_identity={founder}, '
                     'founder_role_dated={founder_dated}, evidence={url}'.format(**app))
    if any(a['founder_dated'] for a in dated):
        notes.extend(founders['evidence'])
    notes.extend(uncertain)
    row['epo_note'] = '; '.join(notes)
    return row


def save(rows):
    # Rewrite the small CSV after every completed company; no extra output files.
    with OUTPUT.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    global REFRESH_FAILURES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--refresh-failures', action='store_true',
                        help='Explicitly retry previously cached failed source probes')
    args = parser.parse_args()
    REFRESH_FAILURES = args.refresh_failures
    with (ROOT / 'raw/companies_all.csv').open(encoding='utf-8-sig', newline='') as handle:
        companies = list(csv.DictReader(handle))
    founders = load_founders(companies)
    if not args.probe:
        rows = [dict(dict.fromkeys(FIELDS, ''), cid=c['cid'], name=c['name'],
                     epo_note='pending / not checked') for c in companies]
        save(rows)
    available = set()
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch, url): (source, url) for source, url in urls('Tubulis').items()}
        for future in cf.as_completed(futures):
            source, url = futures[future]
            body = future.result()
            if body:
                available.add(source)
            print(source, 'OK' if body else 'FAILED', len(body or b''), url, flush=True)
    if args.probe:
        print(source_summary())
        return
    # Only Google JSON has an implemented audited parser. Other public sources
    # are access probes; an unfamiliar payload never becomes a spurious zero.
    if available - {'google'}:
        print('Other source reachable but no validated parser: counts remain unknown', flush=True)
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(process, c, founders[c['cid']], available): i
                   for i, c in enumerate(companies)}
        for future in cf.as_completed(futures):
            i = futures[future]
            try:
                rows[i] = future.result()
            except Exception as error:
                rows[i]['epo_note'] = 'processing failed: ' + type(error).__name__ + '; unknown, not zero'
            save(rows)
    print('Rows written:', len(rows))
    for field in FIELDS:
        print('Fill count', field + ':', sum(row[field] != '' for row in rows))
    print('Sources:', source_summary())


if __name__ == '__main__':
    main()
