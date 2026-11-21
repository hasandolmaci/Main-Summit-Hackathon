#!/usr/bin/env python3
"""Collect public founder/director names, stdlib only.

Usage: python scripts/v2_founders.py --cid-range bt001-bt110
Writes each finished row atomically. Source labels distinguish founders from
CEO/managing-director proxies; counts describe extracted names, not a certified
complete founding team. Unknown/failed checks stay blank; a successful relevant
page with no names is recorded as 0. known_founders is a search hint, never dated
historical evidence. Current pages and undated web results never enter *_asof.
Raw HTTP responses and run diagnostics are retained in raw/cache/v2_founders/.
"""
import argparse
import base64
import calendar
import csv
import datetime as dt
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'raw/cache/v2_founders'
FIELDS = 'cid,name,founder_names_asof,founder_count_asof,source_asof,snapshot_date,founder_names_now,founder_count_now,source_now,legal_form,evidence_url'.split(',')
LOCK = threading.Lock()
HOSTS = {}
STATS = Counter()
PATH = re.compile(r'impressum|imprint|legal|about|team|ueber[-_]uns|über[-_]uns|founders?', re.I)
ROLE = re.compile(r'Gesch(?:ä|ae)ftsf(?:ü|ue)hrer(?:in|innen)?(?:[:*]innen)?|Gesch(?:ä|ae)ftsf(?:ü|ue)hrung|vertreten\s+durch|represented\s+by|Managing\s+Directors?|(?<!\w)(?:co[- ]?)?founders?\b|Gr(?:ü|ue)nder(?:in|innen)?\b|\bCEO\b', re.I)
FOUNDER = re.compile(r'founder|gr(?:ü|ue)nder', re.I)
WORD = r'[A-ZÀ-ÖØ-ÞĀ-Ž][a-zA-ZÀ-ÖØ-öø-ÿĀ-ž]+(?:[’\x27-][a-zA-ZÀ-ÖØ-öø-ÿĀ-ž]+)*'
NAME = re.compile(r'(?<!\w)' + WORD + r'(?:\s+(?:(?:von|van|de|der|zu)\s+)?' + WORD + r'){1,3}(?!\w)')
DENY = set(('gmbh ug ag kg mbh inc ltd limited llc germany deutschland berlin munich hamburg address adresse street straße strasse telefon phone fax email kontakt contact register handelsregister amtsgericht ust id vat tax responsible verantwortlich inhalt impressum imprint legal team about uns unser unsere unternehmen company management director directors managing founder founders co ceo cto chief executive officer technology geschäftsführer geschäftsführung vertreten durch angaben gemäß rechtliche hinweise datenschutz privacy policy linkedin copyright all rights reserved alle rechte vorbehalten die der das den dem herr herrn frau registergericht sitz münchen frankfurt bonn karlsruhe leipzig dresden stuttgart university technical institute institut venture ventures lab labs technologies solutions robotics systems represented research services tech lead software engineer engineering product manager business development design designer marketing sales finance operations assistant scientist researcher head advisory board president advisor creative customer success human resources learn read more founding with phase change during interview portrait picture photo powered by welcome our based germany registered german austria austrian austria österreich responsible content executive officer artificial intelligence machine learning follow us cookie settings cookie policy cookie consent terms conditions commercial register contact information general partner limited liability this website european union').split())
DENY.update('registry entry court district process dev investor investors years senior expert consulting physical chemistry'.split())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, redirects=0):
    """Cache all responses; throttle actual requests (including redirects)."""
    if redirects > 6:
        return None
    path = CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        response = cached['attempts'][-1]
    except (OSError, ValueError, KeyError, IndexError):
        host = urllib.parse.urlsplit(url).netloc.lower()
        with LOCK:
            state = HOSTS.setdefault(host, {'lock': threading.Lock(), 'last': 0, 'failures': 0})
        attempts = []
        response = None
        # Serial per-host request handling enforces both rate and failure limit.
        with state['lock']:
            for attempt in range(3):
                if state['failures'] >= 3:
                    with LOCK:
                        STATS['skipped:' + host] += 1
                    return None
                time.sleep(max(0, .55 - (time.monotonic() - state['last'])))
                if attempt:
                    time.sleep(2 ** attempt)
                state['last'] = time.monotonic()
                response = {'url': url, 'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'status': None, 'headers': {}, 'body_b64': ''}
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'research script', 'Accept-Encoding': 'identity'})
                    with urllib.request.build_opener(NoRedirect).open(req, timeout=14) as r:
                        response.update(status=r.status, headers=dict(r.headers), body_b64=base64.b64encode(r.read(5_000_000)).decode())
                except urllib.error.HTTPError as e:
                    response.update(status=e.code, headers=dict(e.headers), body_b64=base64.b64encode(e.read(500_000)).decode())
                except (OSError, ValueError) as e:
                    response['error'] = str(e)
                attempts.append(response)
                path.write_text(json.dumps({'url': url, 'attempts': attempts}, ensure_ascii=False), encoding='utf-8')
                status = response['status']
                with LOCK:
                    STATS[host + ':' + str(status)] += 1
                # A missing individual URL is not a host/source failure.
                failed = status is None or status in (401, 403, 408, 429) or status >= 500
                if failed:
                    state['failures'] += 1
                if not failed:
                    break
                if status in (401, 403):
                    break
    if response and response['status'] in (301, 302, 303, 307, 308):
        loc = next((v for k, v in response['headers'].items() if k.lower() == 'location'), '')
        if loc:
            return fetch(urllib.parse.urljoin(url, loc), redirects + 1)
    return response


def body(response):
    if not response or response['status'] != 200:
        return ''
    raw = base64.b64decode(response['body_b64'])
    header = next((v for k, v in response['headers'].items() if k.lower() == 'content-type'), '')
    charset = re.search(r'charset=[\"\x27]?([\w-]+)', header)
    try:
        return raw.decode(charset.group(1) if charset else 'utf-8', errors='replace')
    except LookupError:
        return raw.decode('utf-8', errors='replace')


class Page(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.hidden = [], [], 0
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.hidden += 1
        if self.hidden:
            return
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))
        if tag in ('br', 'p', 'div', 'li', 'section', 'h1', 'h2', 'h3', 'h4', 'h5', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ('p', 'div', 'li', 'section', 'h1', 'h2', 'h3', 'h4', 'h5', 'tr'):
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text + ' ')

    def lines(self):
        return [re.sub(r'\s+', ' ', x).strip() for x in ''.join(self.parts).splitlines() if x.strip()]


def names_in(text):
    text = re.sub(r'\b(?:Prof\.?|Dr\.?|Dipl\.?[- ]?Ing\.?)\s*', '', text)
    out = []
    for m in NAME.finditer(text):
        name = m.group().strip()
        tokens = name.split()
        if 2 <= len(tokens) <= 4 and not any(t.lower() in DENY or t.isupper() for t in tokens):
            out.append(name)
    return out


def extract(markup):
    lines = Page(markup).lines()
    found = {}
    excluded = False
    for i, line in enumerate(lines):
        if len(line) < 100 and re.search(r'\b(?:advisors|advisory|investors|testimonials|customers|beirat|investoren|mentors)\b', line, re.I):
            excluded = True
        if re.fullmatch(r'(?:(?:our|the|unser|unsere)\s+)?(?:team|founders|founding team|management team|leadership|gründer|geschäftsführung)', line, re.I):
            excluded = False
        for match in ROLE.finditer(line):
            is_founder = bool(FOUNDER.search(match.group()))
            if re.search(r'\b(?:former|serial)\b', line, re.I):
                continue
            if is_founder and re.match(r"(?:['’]s)?\s+associate\b", line[match.end():], re.I):
                continue
            if excluded and (is_founder or match.group().upper() == 'CEO'):
                continue
            after = line[match.end():].strip(' :;,|–—-()')
            after = ROLE.split(after)[0].strip(' :;,|&–—-()')
            after = re.split(r'\b(?:CTO|COO|CFO|CSO|CPO|CDO)\b', after)[0].strip(' :;,|&–—-()')
            # Prose is not a name list. Only short role/name cards qualify.
            candidates = names_in(after) if len(after) < 180 and not re.match(r'[a-z]', after) else []
            before = line[:match.start()].strip(' :;,|–—-()')
            if len(before) < 100:
                candidates += names_in(before)
            if not candidates and not after:
                if (is_founder or match.group().upper() == 'CEO') and i and len(lines[i - 1]) < 70:
                    candidates += names_in(lines[i - 1])
                if not candidates:
                    for following in lines[i + 1:i + 6]:
                        if ROLE.search(following):
                            break
                        if len(following) > 100 or re.search(r'@|\d{3}|register|telefon|phone|address|adresse', following, re.I):
                            break
                        ns = names_in(following)
                        if not ns:
                            break
                        candidates += ns
                        if is_founder or match.group().upper() == 'CEO':
                            break
            for name in candidates:
                found[name] = 'founder' if is_founder else found.get(name, 'director_or_ceo_proxy')
    text = '\n'.join(lines)
    legal = re.search(r'\b(?:GmbH\s+i\.?\s*G\.?|gGmbH|GmbH|UG(?:\s*\(haftungsbeschränkt\))?|GbR|AG|e\.\s*V\.)(?!\w)', text)
    return found, legal.group() if legal else ''


def role_label(names):
    return '+'.join(sorted(set(names.values())))


def historical(row):
    domain = row['domain'].strip().removeprefix('https://').removeprefix('http://').rstrip('/')
    cutoff = dt.date.fromisoformat(row['asof_date'])
    to = (cutoff - dt.timedelta(days=1)).strftime('%Y%m%d')
    query = urllib.parse.urlencode([('url', domain + '/*'), ('from', '1996'), ('to', to), ('output', 'json'), ('filter', 'statuscode:200'), ('filter', 'mimetype:text/html'), ('filter', 'urlkey:.*(impressum|imprint|legal|about|team|ueber-uns).*'), ('fl', 'timestamp,original'), ('limit', '10000')])
    response = fetch('https://web.archive.org/cdx/search/cdx?' + query)
    try:
        records = json.loads(body(response))
    except (ValueError, TypeError):
        return {}, []
    selected = {}
    for entry in records:
        if len(entry) != 2:
            continue
        ts, original = entry
        if not re.fullmatch(r'\d{14}', str(ts)) or ts[:8] > to:
            continue
        host = (urllib.parse.urlsplit(original).hostname or '').removeprefix('www.')
        if host != domain.removeprefix('www.'):
            continue
        if not PATH.search(urllib.parse.urlsplit(original).path):
            continue
        key = urllib.parse.urlsplit(original).path.rstrip('/').lower()
        if key not in selected or ts > selected[key][0]:
            selected[key] = (ts, original)
    names, evidence = {}, []
    # Latest relevant snapshots first; never fall through to live page redirects.
    for ts, original in sorted(selected.values(), reverse=True)[:4]:
        url = 'https://web.archive.org/web/' + ts + 'id_/' + original
        page = fetch(url)
        if not page or page['url'] != url or page['status'] != 200:
            continue
        extracted, _ = extract(body(page))
        evidence.append((url, ts, role_label(extracted)))
        names.update(extracted)
        if names:
            break
    return names, evidence


def current(row):
    origin = 'https://' + row['domain'].strip().rstrip('/') + '/'
    home = fetch(origin)
    markup = body(home)
    discovered = []
    if markup:
        origin = home['url']
        for link in Page(markup).links:
            url = urllib.parse.urljoin(origin, link).split('#')[0]
            host = (urllib.parse.urlsplit(url).hostname or '').removeprefix('www.')
            original_host = (urllib.parse.urlsplit(origin).hostname or '').removeprefix('www.')
            if host == original_host and PATH.search(urllib.parse.urlsplit(url).path):
                discovered.append(url)
    discovered = sorted(dict.fromkeys(discovered), key=lambda x: (not bool(re.search('impressum|imprint|legal', x, re.I)), len(x)))
    targets = discovered[:5] if discovered else [urllib.parse.urljoin(origin, x) for x in ('impressum', 'imprint', 'about', 'team')]
    names, evidence, legal = {}, [], ''
    for url in targets:
        response = fetch(url)
        text = body(response)
        if not text:
            continue
        ns, lf = extract(text)
        if re.search(r'impressum|imprint|legal', urllib.parse.urlsplit(response['url']).path, re.I):
            legal = legal or lf
        evidence.append((response['url'], role_label(ns)))
        names.update(ns)
        if ns and any(v == 'founder' for v in ns.values()):
            break
        if names and len(evidence) >= 2:
            break
    if not names and markup:
        ns, lf = extract(markup)
        if ns:
            names.update(ns)
            evidence.append((home['url'], role_label(ns)))
    return names, evidence, legal


def web_fallback(row):
    with LOCK:
        if STATS['web_search_irrelevant'] >= 3:
            STATS['skipped:web_search'] += 1
            return {}, []
    hint = row.get('known_founders', '').split(';')[0].strip()
    query = row['name'] + ' ' + row['domain'] + ' Gründer founders' + (' ' + hint if hint else '')
    url = 'https://www.bing.com/search?' + urllib.parse.urlencode({'q': query})
    response = fetch(url)
    markup = body(response)
    # Search result snippets are discovery only. Fetch the linked source page.
    if not markup or re.search('captcha|verify you are human', markup, re.I):
        return {}, []
    results = re.findall(r'<li\b[^>]*class="b_algo"[^>]*>(.*?)</li>', markup, re.S)
    # Some search responses return unrelated results despite HTTP 200.
    if row['name'].casefold() not in ' '.join(Page(' '.join(results)).lines()).casefold():
        with LOCK:
            STATS['web_search_irrelevant'] += 1
        return {}, []
    for item in results[:3]:
        links = Page(item).links
        for target in links[:1]:
            if 'bing.com/ck/a' in target:
                encoded = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query).get('u', [''])[0]
                if encoded.startswith('a1'):
                    try:
                        target = base64.urlsafe_b64decode(encoded[2:] + '=' * (-len(encoded[2:]) % 4)).decode()
                    except (ValueError, UnicodeError):
                        continue
            if not target.startswith('http') or 'bing.com' in target:
                continue
            page = fetch(target)
            text = body(page)
            visible = ' '.join(Page(text).lines())
            if row['name'].casefold() not in visible.casefold():
                continue
            names, _ = extract(text)
            # Only explicit founder roles on third-party fallback pages.
            names = {k: v for k, v in names.items() if v == 'founder'}
            if names:
                return names, [(page['url'], role_label(names))]
    return {}, []


def process(row):
    out = dict.fromkeys(FIELDS, '')
    out.update(cid=row['cid'], name=row['name'])
    if not row['domain'] or row['domain'] in ('linkedin.com', 'facebook.com', 'instagram.com'):
        return out
    old, old_ev = historical(row)
    now, now_ev, legal = current(row)
    if not old and not now:
        now, web_ev = web_fallback(row)
        if web_ev:
            now_ev = web_ev
            out['source_now'] = 'web:' + role_label(now)
    # Prefer explicit founder roles; retain directors/CEO only as a fallback.
    if any(v == 'founder' for v in old.values()):
        old = {k: v for k, v in old.items() if v == 'founder'}
    if any(v == 'founder' for v in now.values()):
        now = {k: v for k, v in now.items() if v == 'founder'}
    # The same page may print a full name and a shortened middle-name variant.
    for collection in (old, now):
        for name in list(collection):
            tokens = name.casefold().split()
            if any(name != other and len(other.split()) > len(tokens)
                   and other.split()[0].casefold() == tokens[0]
                   and other.split()[-1].casefold() == tokens[-1]
                   and all(t in other.casefold().split() for t in tokens)
                   for other in collection):
                del collection[name]
    if old_ev:
        out.update(founder_names_asof='; '.join(old), founder_count_asof=len(old), source_asof='wayback:' + (role_label(old) or 'checked_no_names'), snapshot_date='; '.join(dt.datetime.strptime(t, '%Y%m%d%H%M%S').isoformat() + 'Z' for _, t, _ in old_ev))
    if now_ev:
        out.update(founder_names_now='; '.join(now), founder_count_now=len(now), source_now=out['source_now'] or 'current_site:' + (role_label(now) or 'checked_no_names'))
    out['legal_form'] = legal
    out['evidence_url'] = ' | '.join(dict.fromkeys([x[0] for x in old_ev] + [x[0] for x in now_ev]))
    return out


def save(results, output):
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(results[k] for k in sorted(results))
    # Temporary file stays in the explicitly permitted cache directory.
    tmp = CACHE / (output.name + '.tmp')
    tmp.write_text(buf.getvalue(), encoding='utf-8-sig', newline='')
    tmp.replace(output)


def supplement(out, row, records):
    """Apply manually reviewed web evidence saved in this script's cache.

    A publication month is compared using its last possible day, while its
    original month granularity is preserved in snapshot_date. No founding year
    or current crawl date is used as a historical publication date.
    """
    for record in records:
        if record['cid'] != row['cid']:
            continue
        names = record['names']
        if not names or not record.get('url', '').startswith('https://'):
            continue
        label = 'web:' + record['role']
        date = record.get('date', '')
        if date:
            label += ';' + record.get('date_type', 'publication_date') + '=' + date
        else:
            label += ';undated'
        if record.get('scope') != 'asof':
            out.update(founder_names_now='; '.join(names), founder_count_now=len(names), source_now=label)
        if date and record.get('scope') != 'now':
            if re.fullmatch(r'\d{4}-\d{2}', date):
                year, month = map(int, date.split('-'))
                latest_date = dt.date(year, month, calendar.monthrange(year, month)[1])
            else:
                latest_date = dt.date.fromisoformat(date)
            if latest_date < dt.date.fromisoformat(row['asof_date']):
                out.update(founder_names_asof='; '.join(names), founder_count_asof=len(names), source_asof=label, snapshot_date=date)
        if record.get('legal_form'):
            out['legal_form'] = record['legal_form']
        existing = out['evidence_url'].split(' | ') if out['evidence_url'] else []
        out['evidence_url'] = ' | '.join(dict.fromkeys(existing + [record['url']]))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cid-range', default='bt001-bt110')
    parser.add_argument('--output', default='raw/v2_founders_A.csv')
    parser.add_argument('--workers', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    match = re.fullmatch(r'(bt\d+)-(bt\d+)', args.cid_range)
    if not match:
        parser.error('--cid-range must look like bt001-bt110')
    lo, hi = (int(x[2:]) for x in match.groups())
    CACHE.mkdir(parents=True, exist_ok=True)
    # Circuit breakers survive interrupted/restarted runs.
    for cached_path in CACHE.glob('*.json'):
        try:
            cached = json.loads(cached_path.read_text(encoding='utf-8'))
            if not isinstance(cached, dict):
                continue
            for response in cached.get('attempts', []):
                host = urllib.parse.urlsplit(response['url']).netloc.lower()
                state = HOSTS.setdefault(host, {'lock': threading.Lock(), 'last': 0, 'failures': 0})
                status = response['status']
                STATS[host + ':' + str(status)] += 1
                if status is None or status in (401, 403, 408, 429) or status >= 500:
                    state['failures'] += 1
        except (OSError, ValueError, KeyError):
            continue
    with (ROOT / 'raw/companies_all.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['cid'].startswith('bt') and lo <= int(r['cid'][2:]) <= hi]
    output = ROOT / args.output
    results = {}
    try:
        reviewed = json.loads((CACHE / 'web_verified.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        reviewed = []
    if args.resume and output.exists():
        with output.open(encoding='utf-8-sig', newline='') as f:
            results = {r['cid']: r for r in csv.DictReader(f)}
    save(results, output)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, row): row for row in rows if row['cid'] not in results}
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = dict.fromkeys(FIELDS, '')
                result.update(cid=row['cid'], name=row['name'])
                with LOCK:
                    STATS['exception:' + type(exc).__name__] += 1
                print(row['cid'], type(exc).__name__, str(exc), flush=True)
            result = supplement(result, row, reviewed)
            results[row['cid']] = result
            save(results, output)
            print('saved', row['cid'], 'asof=' + str(result['founder_count_asof']), 'now=' + str(result['founder_count_now']), 'total=' + str(len(results)), flush=True)
    fills = {k: sum(str(r.get(k, '')).strip() != '' for r in results.values()) for k in FIELDS}
    summary = {'rows_written': len(results), 'fill_count': fills, 'positive_name_rows': {k: sum(bool(r[k]) for r in results.values()) for k in ('founder_names_asof', 'founder_names_now')}, 'sources': dict(sorted(STATS.items())), 'source_failures': {h: s['failures'] for h, s in HOSTS.items() if s['failures']}, 'reviewed_web_records': len(reviewed), 'notes': ['Current legal_form is undated.', 'Director/CEO names are role proxies, not verified founders.', 'Counts describe documented names and may be incomplete.', 'known_founders is never assigned an as-of date or published without corroboration.']}
    (CACHE / 'run_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
