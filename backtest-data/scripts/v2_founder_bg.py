"""Conservative, resumable founder background research (Python stdlib only).

Run: python scripts/v2_founder_bg.py --minutes 25
Reviewed web facts live in raw/cache/v2_founder_bg/reviewed_facts.json.
Each fact has cid, founder, field, value, url, date (optional), date_kind,
and note. A year-only date is conservatively bounded at December 31.
No inference of a doctorate from Dr., or of technical skill from founder/CEO.
Undated facts never populate the requested point-in-time feature columns.
OpenAlex identities require a manually corroborated institution and exact name.
Current citation counts are deliberately never exported as historical counts.
"""
import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw/cache/v2_founder_bg'
OUT = ROOT / 'raw/v2_founder_bg.csv'
FIELDS = 'cid company founder role has_phd technical_background industry_years_before_asof prior_company prior_company_vc_backed_or_exit prior_evidence_url is_professor star_academic openalex_id works_before_asof citations_note top_employer evidence_url confidence'.split()
FIELDS += ['asof_date', 'evidence_dates', 'undated_facts', 'post_asof_facts', 'check_notes']
FEATURES = set(FIELDS[3:17]) - {'prior_evidence_url', 'evidence_url', 'citations_note', 'openalex_id', 'works_before_asof'}

def norm(s):
    s = re.sub(r'\b(prof|dr|med|phd)\b\.?', '', s, flags=re.I)
    return ''.join(c for c in unicodedata.normalize('NFKD', s.casefold()) if c.isalnum())

def split_names(value):
    value = (value or '').strip()
    if value.startswith('['):
        try:
            return [str(x).strip() for x in json.loads(value) if x]
        except (ValueError, TypeError):
            pass
    result = []
    for x in re.split(r';|\s*\|\s*', value):
        x = re.sub(r'^(?:(?:Prof|Dr)\.?\s*)+', '', x.strip(), flags=re.I).strip()
        x = re.sub(r'^(?:Co-?Founder|Founder|Gründer)\s*', '', x, flags=re.I)
        x = re.sub(r'(?:Ultrasound PhD|\s+Investor)$', '', x).strip()
        if not x or len(x.split()) < 2:
            continue
        if re.search(r'\b(registry|entry|court|consulting|years|expert|process|dev|ventures|partner|manager|number|engineer|reach out|inc|medineers)\b', x, re.I) or x == 'At INFRASOLID':
            continue
        result.append(x)
    return result

def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def inputs():
    companies = {r['cid']: r for r in read_csv(ROOT / 'raw/companies_all.csv')}
    positive = {norm(r.get('id_name', '')) for r in read_csv(ROOT / 'backtest.csv') if r.get('y_label') == '1'}
    people = {}
    for c in companies.values():
        for name in split_names(c.get('known_founders', '')):
            people[(c['cid'], norm(name))] = (c, name)
    for filename in ('v2_founders_A.csv', 'v2_founders_B.csv'):
        for r in read_csv(ROOT / 'raw' / filename):
            c = companies.get(r.get('cid', ''))
            if not c:
                continue
            value = ';'.join(filter(None, [r.get('founder_names_asof'), r.get('founder_names_now')])) or r.get('founder') or r.get('founder_name') or r.get('founder_names') or r.get('founders') or r.get('known_founders') or ''
            for name in split_names(value):
                # A supplemental encoding loss duplicates the canonical known name.
                if c['cid'] == 'bt152' and name == 'Jasper B?ckel':
                    name = 'Jasper Böckel'
                if name.lower() not in ('unknown', 'not found', 'none', 'n/a', '0'):
                    people.setdefault((c['cid'], norm(name)), (c, name))
    return sorted(people.values(), key=lambda p: (0 if p[0]['set'] == 'live' else 1 if norm(p[0]['name']) in positive else 2, p[0]['cid'], norm(p[1])))

class Client:
    def __init__(self):
        self.lock = threading.Lock()
        self.host_locks = {}
        self.next_at = {}
        self.failures = Counter()
        self.status = Counter()
        # The source circuit breaker survives reruns during this research session.
        for path in CACHE.glob('*.json'):
            if not re.fullmatch(r'[0-9a-f]{40}\.json', path.name):
                continue
            cached = json.loads(path.read_text(encoding='utf-8'))
            if cached.get('ok') is False:
                self.failures[urllib.parse.urlsplit(cached['url']).netloc] += 1

    def get(self, url):
        key = hashlib.sha1(url.encode()).hexdigest()
        path = CACHE / (key + '.json')
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('ok'):
                self.status['cache_hit'] += 1
                return data['body']
            return None
        host = urllib.parse.urlsplit(url).netloc
        with self.lock:
            host_lock = self.host_locks.setdefault(host, threading.Lock())
        for attempt in range(3):
            with host_lock:
                if self.failures[host] >= 3:
                    self.status[host + ':skipped_after_3_failures'] += 1
                    return None
                time.sleep(max(0, self.next_at.get(host, 0) - time.monotonic()))
                self.next_at[host] = time.monotonic() + 1.05
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'research script'})
                    with urllib.request.urlopen(req, timeout=25) as response:
                        body = response.read().decode('utf-8', errors='replace')
                    data = {'url': url, 'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'ok': True, 'body': body}
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                    self.status[host + ':success'] += 1
                    return body
                except (urllib.error.URLError, OSError, TimeoutError) as exc:
                    self.failures[host] += 1
                    self.status[host + ':failure'] += 1
                    data = {'url': url, 'ok': False, 'error': str(exc), 'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat()}
                    if isinstance(exc, urllib.error.HTTPError):
                        data['body'] = exc.read().decode('utf-8', errors='replace')
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            time.sleep(2 ** attempt)
        return None

def reviewed():
    path = CACHE / 'reviewed_facts.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return []

def date_bound(value):
    if re.fullmatch(r'\d{4}', value):
        return value + '-12-31'
    if re.fullmatch(r'\d{4}-\d{2}', value):
        import calendar
        y, m = map(int, value.split('-'))
        return '%s-%02d' % (value, calendar.monthrange(y, m)[1])
    dt.date.fromisoformat(value)
    return value

def collect_oa(person, client):
    company, name = person
    url = 'https://api.openalex.org/authors?' + urllib.parse.urlencode({'search': name, 'per-page': 25})
    client.get(url)
    return company['cid'], name

def make_row(company, name, facts, client):
    row = dict.fromkeys(FIELDS, '')
    row.update(cid=company['cid'], company=company['name'], founder=name, asof_date=company['asof_date'], confidence='low')
    evidence, dates, undated, later, notes = [], [], [], [], []
    mine = [f for f in facts if f['cid'] == company['cid'] and norm(f['founder']) == norm(name)]
    if not mine:
        notes.append('No manually reviewed web evidence yet; input founder identity and company relationship not independently verified.')
    anchors = []
    for f in mine:
        field, value, url = f['field'], str(f['value']), f.get('url', '')
        if field == 'affiliation_anchor':
            anchors.append(value.casefold())
            continue
        if field == 'check_note':
            notes.append(value)
            continue
        if field not in FEATURES or not url:
            continue
        item = {'field': field, 'value': value, 'url': url}
        if f.get('note'):
            item['note'] = f['note']
        date = f.get('date', '')
        if not date:
            undated.append(item)
            continue
        item.update(date=date, date_kind=f.get('date_kind', 'publication'))
        if date_bound(date) >= company['asof_date']:
            later.append(item)
            continue
        if field == 'star_academic' and value == '1' and f.get('certification') not in ('ERC', 'Leibniz', 'Highly Cited Researcher', 'academy_member', 'full_professor_top_research_university'):
            notes.append('Rejected star_academic without qualifying certification')
            continue
        row[field] = value
        evidence.append(url)
        dates.append(item)
        if field in ('prior_company', 'prior_company_vc_backed_or_exit'):
            row['prior_evidence_url'] = ' | '.join(dict.fromkeys(filter(None, [row['prior_evidence_url'], url])))
        row['confidence'] = 'high' if f.get('confidence') == 'high' else ('medium' if row['confidence'] != 'high' else 'high')
    oa_url = 'https://api.openalex.org/authors?' + urllib.parse.urlencode({'search': name, 'per-page': 25})
    path = CACHE / (hashlib.sha1(oa_url.encode()).hexdigest() + '.json')
    if path.exists():
        raw = json.loads(path.read_text(encoding='utf-8'))
        if raw.get('ok'):
            candidates = json.loads(raw['body']).get('results', [])
            exact = []
            for a in candidates:
                if norm(a.get('display_name', '')) != norm(name):
                    continue
                institutions = [x.get('display_name', '') for x in (a.get('last_known_institutions') or [])]
                institutions += [x.get('institution', {}).get('display_name', '') for x in (a.get('affiliations') or [])]
                if anchors and any(norm(anchor) in norm(inst) for anchor in anchors for inst in institutions):
                    exact.append(a)
            if len(exact) == 1:
                a = exact[0]
                author_id = a['id']
                year = int(company['asof_date'][:4])
                works_url = 'https://api.openalex.org/works?' + urllib.parse.urlencode({'filter': 'author.id:%s,to_publication_date:%d-12-31' % (author_id.rsplit('/', 1)[-1], year - 1), 'per-page': 1})
                body = client.get(works_url)
                row['openalex_id'] = author_id
                evidence += [oa_url, author_id]
                notes.append('OpenAlex exact name and externally corroborated affiliation: ' + ', '.join(anchors))
                row['confidence'] = 'medium' if row['confidence'] == 'low' else row['confidence']
                if body:
                    row['works_before_asof'] = str(json.loads(body)['meta']['count'])
                    evidence.append(works_url)
                    dates.append({'field': 'works_before_asof', 'date': str(year - 1) + '-12-31', 'date_kind': 'publication_year_upper_bound', 'url': works_url})
                elif sum(y.get('works_count', 0) for y in a.get('counts_by_year', [])) == a.get('works_count') and len({y['year'] for y in a.get('counts_by_year', [])}) == len(a.get('counts_by_year', [])):
                    # Safe cached fallback only when annual counts cover the entire
                    # author total. Never subtract an incomplete recent-year window.
                    row['works_before_asof'] = str(sum(y['works_count'] for y in a['counts_by_year'] if y['year'] < year))
                    dates.append({'field': 'works_before_asof', 'date': str(year - 1) + '-12-31', 'date_kind': 'publication_year_upper_bound', 'url': oa_url})
                    notes.append('Works counted from cached counts_by_year; sum of all annual counts exactly equals author works_count.')
                row['citations_note'] = 'Current citation counts excluded: OpenAlex does not supply historical citation totals here. Works count uses publication_year < ' + str(year) + '; present-day index, not an archived author record.'
            else:
                notes.append('OpenAlex checked; identity unresolved (no unique exact name plus corroborated affiliation). Flags left blank where unsupported.')
        else:
            notes.append('OpenAlex unavailable: ' + raw.get('error', 'request failed'))
    else:
        notes.append('OpenAlex not checked or source stopped after three failures.')
    if undated and row['confidence'] == 'low':
        notes.append('Public identity corroborated only by undated material; dated features remain blank.')
    row['evidence_url'] = ' | '.join(dict.fromkeys(evidence))
    for field, value in [('evidence_dates', dates), ('undated_facts', undated), ('post_asof_facts', later)]:
        row[field] = json.dumps(value, ensure_ascii=False, separators=(',', ':')) if value else ''
    row['check_notes'] = ' | '.join(dict.fromkeys(notes))
    return row

def save(people, client):
    facts = reviewed()
    rows = [make_row(c, n, facts, client) for c, n in people]
    tmp = CACHE / 'output.tmp.csv'
    with tmp.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        f.flush()
    tmp.replace(OUT)
    return rows

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=float, default=25)
    parser.add_argument('--refresh-only', action='store_true')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    client = Client()
    people = inputs()
    rows = save(people, client)
    started = time.monotonic()
    seen = set()
    if not args.refresh_only:
        while True:
            people = inputs()
            pending = [p for p in people if (p[0]['cid'], norm(p[1])) not in seen]
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(collect_oa, p, client): p for p in pending}
                for future in concurrent.futures.as_completed(futures):
                    p = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        print('Research error:', p[0]['cid'], type(exc).__name__, flush=True)
                    seen.add((p[0]['cid'], norm(p[1])))
                    if len(seen) % 10 == 0:
                        rows = save(people, client)
                        print('Partial rows:', len(rows), 'OpenAlex searches processed:', len(seen), flush=True)
            rows = save(people, client)
            if time.monotonic() - started >= args.minutes * 60:
                break
            # Re-read supplemental founder inputs at most 10 minutes apart.
            time.sleep(min(30, max(0, args.minutes * 60 - (time.monotonic() - started))))
    rows = save(inputs(), client)
    cached_sources = Counter()
    for path in CACHE.glob('*.json'):
        if re.fullmatch(r'[0-9a-f]{40}\.json', path.name):
            raw = json.loads(path.read_text(encoding='utf-8'))
            host = urllib.parse.urlsplit(raw.get('url', '')).netloc
            cached_sources[host + (':cached_success' if raw.get('ok') else ':cached_failure')] += 1
    summary = {'rows_written': len(rows), 'fill_count_per_column': {k: sum(r[k] != '' for r in rows) for k in FIELDS}, 'sources': dict(client.status), 'failed_hosts': dict(client.failures), 'reviewed_web_facts': len(reviewed()), 'web_search_batches_cached': len(list(CACHE.glob('web_search_*.json'))), 'reviewed_web_domains': sorted({urllib.parse.urlsplit(f.get('url', '')).netloc for f in reviewed() if f.get('url')})}
    summary['cached_source_results'] = dict(cached_sources)
    summary['coverage'] = {'with_dated_evidence': sum(bool(r['evidence_dates']) for r in rows), 'with_any_reviewed_evidence': sum(bool(r['evidence_dates'] or r['undated_facts'] or r['post_asof_facts']) for r in rows), 'unknown_features_remain_blank': True}
    (CACHE / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

if __name__ == '__main__':
    main()
