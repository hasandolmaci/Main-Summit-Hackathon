"""Point-in-time Wayback enrichment. Python 3.13, standard library only.

Only archived captures strictly before asof_date are eligible. CDX's collapsed
discovery results are refined with exact-URL last-capture queries. At most four
distinct HTML snapshots are fetched per row. Unavailable evidence stays empty;
zero means a successful relevant page was examined and no match was found.
Raw HTTP responses (including errors) are cached, with metadata, by URL SHA1.
Run: python scripts/enrich_snapshots.py
Use --cache-only to repeat extraction without making network requests.
"""
import argparse
import concurrent.futures
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
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw/cache/enrich_snap'
CACHE_ONLY = False
FIELDS = 'name website asof_date homepage_snapshot_ts impressum_snapshot_ts analytics_tools ab_testing_tool founder_names managing_directors founders_named_in_impressum legal_form registered_court_hrb parent_company_mentioned team_page_found team_size_on_page jobs_page_found job_titles contact_email_present careers_engineering_roles careers_sales_roles'.split()
LOCAL = threading.local()
STATS = Counter()
LOCK = threading.Lock()
HOST_LOCK = threading.Lock()
HOST_LAST = {}
URL_LOCKS = {}
UA = 'Mozilla/5.0 (research script)'
SOCIAL = {'linkedin.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'youtube.com'}


def stat(key):
    with LOCK:
        STATS[key] += 1


class ArchiveOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never follow replay redirects to a live company website, or another
        # capture: its timestamp would no longer be the selected evidence.
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def request(url):
    with LOCK:
        url_lock = URL_LOCKS.setdefault(url, threading.Lock())
    with url_lock:
        return cached_request(url)


def cached_request(url):
    digest = hashlib.sha1(url.encode()).hexdigest()
    body_path, meta_path = CACHE / digest, CACHE / (digest + '.json')
    meta = {'url': url, 'status': 0}
    if body_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        # Transient failures are retried on the next run, not frozen forever.
        if meta['status'] == 200:
            stat('cache_hits')
            return body_path.read_bytes(), meta
        if meta['status'] in (400, 404, 410):
            return None, meta
    if CACHE_ONLY:
        stat('cache_unavailable')
        return None, meta
    opener = urllib.request.build_opener(ArchiveOnlyRedirect())
    for attempt in range(3):
        # Four company workers; enforce the contract's >=1s host-wide interval
        # as well as >=1s within each worker. Cache hits do not consume a slot.
        with HOST_LOCK:
            host = urllib.parse.urlsplit(url).hostname
            delay = max(HOST_LAST.get(host, 0), getattr(LOCAL, 'last', 0)) + 1.05 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            LOCAL.last = HOST_LAST[host] = time.monotonic()
        try:
            with opener.open(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=25) as response:
                body = response.read()
                meta = {'url': url, 'status': response.status, 'headers': dict(response.headers), 'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        except urllib.error.HTTPError as exc:
            body = exc.read()
            meta = {'url': url, 'status': exc.code, 'headers': dict(exc.headers), 'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            body = b''
            meta = {'url': url, 'status': 0, 'error': str(exc), 'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        stat('http_' + str(meta['status']))
        if meta['status'] == 200:
            return body, meta
        if meta['status'] not in (0, 429, 500, 502, 503, 504):
            break
        if attempt < 2:
            retry_after = meta.get('headers', {}).get('Retry-After', '')
            time.sleep(max(2 ** (attempt + 1), min(60, int(retry_after)) if str(retry_after).isdigit() else 0))
    return None, meta


def cdx(url, cutoff, exact=False):
    params = {'url': url, 'output': 'json', 'to': cutoff, 'filter': 'statuscode:200', 'fl': 'timestamp,original', 'limit': '-1' if exact else '500'}
    if not exact:
        params['collapse'] = 'urlkey'
    # HTTPS is equivalent to the supplied HTTP endpoints and avoids downgrade
    # blocking; all evidence still comes solely from web.archive.org.
    endpoint = 'https://web.archive.org/cdx/search/cdx?' + urllib.parse.urlencode(params)
    body, _ = request(endpoint)
    if body is None:
        return None
    try:
        data = json.loads(body)
        if not isinstance(data, list):
            raise ValueError('CDX response is not a list')
        return [(r[0], r[1]) for r in data[1:] if len(r) == 2 and re.fullmatch(r'\d{14}', r[0]) and r[0] <= cutoff]
    except (ValueError, TypeError):
        stat('invalid_cdx')
        return None


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.lines, self.assets = [], []
        self.blocks, self.active_blocks = [], []
        self.email_present = False
        self.skip = 0
        self.script_depth = 0
        self.feed(source.replace('\u00ad', '').replace('\u200b', ''))
        self.text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(self.lines))
        self.lines = [x.strip() for x in self.text.splitlines() if x.strip()]
        self.email_present = self.email_present or bool(re.search(r'[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}', self.text))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get('href') or ''
        if href.lower().startswith('mailto:') and re.search(r'[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}', urllib.parse.unquote(href)):
            self.email_present = True
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a'):
            block = {'tag': tag, 'text': ''}
            self.blocks.append(block)
            self.active_blocks.append(block)
        if tag in ('script', 'iframe', 'img', 'link'):
            self.assets.extend(attrs.get(k) or '' for k in ('src', 'href', 'data-src'))
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        if tag == 'script':
            self.script_depth += 1
        if tag in ('p', 'div', 'br', 'li', 'section', 'article', 'h1', 'h2', 'h3', 'h4', 'a', 'dt', 'dd'):
            self.lines.append('\n')

    def handle_endtag(self, tag):
        for i in range(len(self.active_blocks) - 1, -1, -1):
            if self.active_blocks[i]['tag'] == tag:
                del self.active_blocks[i:]
                break
        if tag in ('script', 'style', 'noscript'):
            self.skip = max(0, self.skip - 1)
        if tag == 'script':
            self.script_depth = max(0, self.script_depth - 1)
        if tag in ('p', 'div', 'li', 'section', 'article', 'h1', 'h2', 'h3', 'h4', 'a', 'dt', 'dd'):
            self.lines.append('\n')

    def handle_data(self, data):
        data = data.replace('\u00ad', '').replace('\u200b', '')
        for block in self.active_blocks:
            block['text'] += data
        if self.skip:
            # Inline script configuration counts as script evidence.
            if self.script_depth:
                self.assets.append(data)
        else:
            self.lines.append(data)


ANALYTICS = [
    ('Google Analytics/gtag', r'google-analytics\.com|googletagmanager\.com/gtag|\bgtag\s*\(|\bGoogleAnalyticsObject\b|\b_gaq\b'),
    ('Google Tag Manager', r'googletagmanager\.com/gtm|\bGTM-[A-Z0-9]+'),
    ('Matomo', r'matomo(?:\.js|\.php|\s*\()|\b_paq\b|piwik\.(?:js|php)'),
    ('Plausible', r'plausible\.io|plausible(?:\.min)?\.js'),
    ('Hotjar', r'hotjar\.com|\bhj\s*\('), ('Mixpanel', r'mixpanel\.com|\bmixpanel\.'),
    ('Amplitude', r'amplitude\.com|\bamplitude\.(?:init|getInstance)'),
    ('Segment', r'cdn\.segment\.com|api\.segment\.io|analytics\.load\s*\('),
    ('PostHog', r'posthog\.com|posthog[-.]js|\bposthog\.(?:init|capture|getFeatureFlag|isFeatureEnabled)'),
    ('HubSpot', r'hs-scripts\.com|hs-analytics\.net|hsforms\.(?:net|com)|hs-banner\.com|hscollectedforms\.net|hsadspixel\.net|hsleadflows\.net|hubspot\.com|\b_hsq\b')]
AB = [('Optimizely', r'optimizely'), ('VWO', r'visualwebsiteoptimizer|vwo\.com|\b_vwo_'),
      ('Google Optimize', r'googleoptimize\.com|\bOPT-[A-Z0-9]+|optimize\.activate'),
      ('AB Tasty', r'abtasty'), ('GrowthBook', r'growthbook'),
      ('PostHog experiments', r'posthog\.(?:getFeatureFlag|isFeatureEnabled|getFeatureFlagPayload)|posthog[^\n]{0,80}experiment'),
      ('LaunchDarkly', r'launchdarkly|ldclient'), ('Kameleoon', r'kameleoon')]
ROLE = re.compile(r'\b(?:co[- ]?founder|founder|gründer(?:in)?|mitgründer(?:in)?|CEO|CTO)\b', re.I)
PERSON = re.compile(r'(?<!\w)(?:(?:Dr|Prof)\.\s+)*[A-ZÄÖÜÀ-Ý][a-zäöüßà-ÿ]+(?:[-\u2019\x27][A-ZÄÖÜÀ-Ý]?[a-zäöüßà-ÿ]+)?(?:\s+(?:von|van|de|zu))?(?:\s+[A-ZÄÖÜÀ-Ý][a-zäöüßà-ÿ]+(?:[-\u2019\x27][A-ZÄÖÜÀ-Ý]?[a-zäöüßà-ÿ]+)?){1,3}(?!\w)')
BAD_NAME = re.compile(r'GmbH|Geschäft|Director|Founder|Company|Unternehmen|Register|Amtsgericht|Straße\b|Strasse\b|Privacy|Policy|Contact|Kontakt|Impressum|Managing|Chief|Officer|Executive|Technology|Legal|Notice|Team|Unsere|Unser|Munich|Berlin|Hamburg|München|Datenschutz|Alle Rechte|Verantwortlich|Vertreten|Business|Development|Engineering|Software|Sales|Marketing|Manager|Engineer|Developer|Design|Join|Karriere|Experience|Years|Background|Strong|Robotics|Product|Hardware|Cloud|Embedded|University|Professor|Welcome|Support|Quick Links|All Rights|Institute|Strategic|Advisor|Advisory|Sustainability', re.I)


def names(text):
    if text.isupper():
        text = text.title()
    return [m.group().strip() for m in PERSON.finditer(text) if not BAD_NAME.search(m.group()) and not re.search(r'\b(?:Lead|Medical|Technical|Adviser|Senior|Customer|Science|Learning|Smart Metering|Venture Labs)\b', m.group(), re.I)]


def name_line(text):
    probe = text.title() if text.isupper() else text
    return names(probe) if PERSON.fullmatch(probe) else []


def unique(items):
    return list(dict.fromkeys(items))


def unique_names(items):
    seen, result = set(), []
    for name in items:
        key = re.sub(r'\b(?:Dr|Prof)\.\s*', '', name).casefold()
        if key not in seen:
            seen.add(key)
            result.append(name)
    return result


def snippet(text):
    # Never copy email addresses or phone numbers into derived CSV text fields.
    text = re.sub(r'[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}', '', text)
    text = re.sub(r'(?<!\w)\+?\d[\d ()/.-]{6,}\d', '', text)
    return re.sub(r'\s+', ' ', text).strip(' ,;:|-')[:240]


def job_title(text):
    text = snippet(text).strip('›»→ ')
    text = re.sub(r'^(?:Wir suchen (?:Dich|Sie) als\s+|We are looking for (?:an?\s+)?)', '', text, flags=re.I)
    return re.split(r',?\s+who\s+|,?\s+der\s+|,?\s+die\s+', text, maxsplit=1, flags=re.I)[0].strip(' ,.')


def extract(pages, result):
    all_pages = list(pages.values())
    if all_pages:
        assets = '\n'.join(a for p in all_pages for a in p.assets)
        detected = [label for label, pattern in ANALYTICS if re.search(pattern, assets, re.I)]
        result['analytics_tools'] = ', '.join(detected)
        result['ab_testing_tool'] = next((label for label, pattern in AB if re.search(pattern, assets, re.I)), '0')
        result['contact_email_present'] = int(any(p.email_present for p in all_pages))
    if 'impressum' in pages:
        page = pages['impressum']
        text = '\n'.join(page.lines)
        directors = []
        for match in re.finditer(r'Geschäftsführer(?:in|innen)?|Geschäftsführung|Managing\s+Directors?|vertreten\s+durch|represented\s+by', text, re.I):
            following = text[match.end():match.end() + 260].splitlines()
            for line in following[:8]:
                if re.search(r'Handelsregister|Amtsgericht|Umsatz|Telefon|Registergericht|HRB|HRA|@|^\s*(?:Kontakt|Contact|E-Mail|Email|Tel\b)', line, re.I):
                    break
                found = names(line)
                if not found and directors and line.strip(' :,;-'):
                    break
                directors.extend(found)
        result['managing_directors'] = '; '.join(unique_names(directors))
        result['founders_named_in_impressum'] = int(bool(directors))
        legal = re.search(r'(?<!\w)(?:gGmbH|GmbH|UG\s*\(haftungsbeschränkt\)|AG|GbR|e\.\s*V\.|Einzelunternehmen)(?!\w)', text, re.I)
        if legal:
            result['legal_form'] = legal.group()
        court = re.search(r'(?:Amtsgericht|Registergericht)\s*[:\-]?\s*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß .()/-]{1,65}?)(?:\s*[,;\n]\s*|\s+)(?:Handelsregister\s*[:\-]?\s*)?HRB\s*[:\-]?\s*(\d+(?:[ \t]*[A-Z]\b)?)', text, re.I)
        if court:
            result['registered_court_hrb'] = 'Amtsgericht ' + re.sub(r'\s+', ' ', court[1]).strip() + ' HRB ' + court[2].strip()
        else:
            hrb = re.search(r'\bHRB[ \t:.-]*(\d+(?:[ \t]*[A-Z]\b)?)', text, re.I)
            if hrb:
                result['registered_court_hrb'] = 'HRB ' + hrb[1]
        parent = re.search(r'(?:ein\s+Unternehmen\s+der|a\s+company\s+of|Tochtergesellschaft)[^\n.!?]{0,160}', text, re.I)
        if parent:
            result['parent_company_mentioned'] = snippet(parent.group())
    if 'team' in pages:
        page = pages['team']
        result['team_page_found'] = 1
        founders, people = [], []
        for i, line in enumerate(page.lines):
            if ROLE.search(line) and len(line) <= 160 and not re.search(r'\d|\b(?:ex|former|ehemalig)[-\s]', line, re.I):
                found = names(ROLE.sub('', line))
                if not found and i:
                    found = name_line(page.lines[i - 1])
                if not found and i + 1 < len(page.lines):
                    found = name_line(page.lines[i + 1])
                founders.extend(found)
            if name_line(line) and re.search(r'CEO|CTO|founder|gründer|engineer|developer|manager|head of|director|designer|sales|marketing', ' '.join(page.lines[max(0, i-1):i+2]), re.I):
                people.extend(name_line(line))
        result['founder_names'] = '; '.join(unique_names(founders))
        if people:
            result['team_size_on_page'] = len(unique_names(people))
    if 'jobs' in pages:
        page = pages['jobs']
        result['jobs_page_found'] = 1
        role_words = r'engineer(?:s|ing)?|developers?|entwickler(?:in|innen)?|sales|business development|vertrieb\w*|account|managers?|designers?|intern(?:ship|ships|s)?|praktik\w*|werkstudent\w*|scientists?|specialists?|consultants?|technicians?|techniker\w*'
        def job_candidate(line):
            return 6 <= len(line) <= 150 and re.search(r'\b(?:' + role_words + r')\b', line, re.I) and not re.search(r'cookie|privacy|datenschutz|@|no (?:open|current)|keine offenen', line, re.I)
        headings = [re.sub(r'\s+', ' ', b['text']).strip() for b in page.blocks]
        candidates = [line for line in headings if job_candidate(line)]
        if not candidates:
            candidates = [line for line in page.lines if job_candidate(line)]
        titles = unique(job_title(line) for line in candidates)
        result['job_titles'] = '; '.join(titles[:10])
        result['careers_engineering_roles'] = sum(bool(re.search(r'engineer|developer|entwickler', x, re.I)) for x in titles)
        result['careers_sales_roles'] = sum(bool(re.search(r'sales|business development|vertrieb|account', x, re.I)) for x in titles)


def decode(body, meta):
    content_type = next((v for k, v in meta.get('headers', {}).items() if k.lower() == 'content-type'), '')
    charset = re.search(r'charset=["\x27]?([\w-]+)', content_type)
    encoding = charset[1] if charset else 'utf-8'
    try:
        text = body.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        text = body.decode('windows-1252', errors='replace')
    if 'Ã' in text or 'Â' in text:
        try:
            text = text.encode('latin-1').decode('utf-8')
        except (UnicodeError, LookupError):
            pass
    return text


def enrich(row):
    result = {key: '' for key in FIELDS}
    result.update({key: row.get(key, '') for key in ('name', 'website', 'asof_date')})
    try:
        date = dt.date.fromisoformat(row['asof_date'])
        cutoff = (date - dt.timedelta(days=1)).strftime('%Y%m%d') + '235959'
        parsed = urllib.parse.urlsplit(row['website'] if '://' in row['website'] else 'https://' + row['website'])
        host = (parsed.hostname or '').lower().removeprefix('www.')
        if not host or any(host == s or host.endswith('.' + s) for s in SOCIAL):
            stat('skipped_missing_or_social_website')
            return result
        records = cdx(host + '/*', cutoff)
        if records is None:
            stat('cdx_unavailable_rows')
            return result
        selected, selected_scores = {}, {}
        supplied_path = urllib.parse.unquote(parsed.path).lower().rstrip('/')
        for timestamp, original in records:
            parts = urllib.parse.urlsplit(original)
            if (parts.hostname or '').lower().removeprefix('www.') != host:
                continue
            path = urllib.parse.unquote(parts.path).lower()
            if re.search(r'\.(?:pdf|jpg|jpeg|png|gif|svg|webp|avif|ico|bmp|tiff?|mp4|webm|js|css|xml|json|map|zip|woff2?|ttf|eot)(?:$|/)', path) or '/_next/data/' in path or '/wp-json/' in path:
                continue
            kinds = []
            if (path in ('', '/', '/index.html', '/index.htm') or path.rstrip('/') == supplied_path) and not parts.query:
                kinds.append('homepage')
            for kind, pattern in [('impressum', r'impressum|imprint|legal'), ('team', r'team|about|ueber-uns|uber-uns|über-uns'), ('jobs', r'jobs|careers|karriere')]:
                if re.search(pattern, path):
                    kinds.append(kind)
            for kind in kinds:
                score = (int(kind == 'homepage' and path.rstrip('/') == supplied_path), timestamp)
                if kind not in selected or score > selected_scores[kind]:
                    selected[kind] = (timestamp, original)
                    selected_scores[kind] = score
        pages, fetched = {}, {}
        for kind, (timestamp, original) in selected.items():
            refined = cdx(original, cutoff, exact=True)
            if refined is None:
                continue
            if not refined:
                continue
            timestamp, original = max(refined)
            snapshot = 'https://web.archive.org/web/' + timestamp + 'id_/' + original
            if snapshot not in fetched:
                body, meta = request(snapshot)
                content_type = next((v for k, v in meta.get('headers', {}).items() if k.lower() == 'content-type'), '')
                if body is None or (content_type and not any(x in content_type for x in ('html', 'text/plain', 'xhtml'))):
                    fetched[snapshot] = None
                else:
                    source = decode(body, meta)
                    if re.search(r'<title>\s*(?:Wayback Machine|Internet Archive Wayback Machine)', source, re.I):
                        fetched[snapshot] = None
                    else:
                        fetched[snapshot] = Page(source)
            if fetched[snapshot] is not None:
                pages[kind] = fetched[snapshot]
                if kind in ('homepage', 'impressum'):
                    result[kind + '_snapshot_ts'] = timestamp
        extract(pages, result)
        stat('rows_with_pages' if pages else 'rows_without_pages')
    except Exception as exc:
        stat('row_errors')
        print(f"ERROR {row.get('name', '')}: {type(exc).__name__}: {exc}", flush=True)
    return result


def main():
    global CACHE_ONLY
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'raw/cohort_short.csv')
    parser.add_argument('--cache-only', action='store_true', help='Re-extract cached evidence; leave unavailable responses unknown.')
    args = parser.parse_args()
    CACHE_ONLY = args.cache_only
    CACHE.mkdir(parents=True, exist_ok=True)
    with args.input.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    results = [None] * len(rows)
    destination = ROOT / 'raw/enrich_snapshot.csv'

    def save():
        with destination.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(r for r in results if r is not None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(enrich, row): i for i, row in enumerate(rows)}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            index = futures[future]
            results[index] = future.result()
            save()
            if done % 10 == 0 or done == len(rows):
                print(f'Completed {done}/{len(rows)}; {dict(STATS)}', flush=True)
    print(f'\nRows: input={len(rows)}, output={len(results)}')
    for key in FIELDS:
        filled = sum(r[key] != '' for r in results)
        rate = 100 * filled / len(results) if results else 0
        print(f'{key}: filled={filled}/{len(results)} ({rate:.1f}%), missing={100-rate:.1f}%')
    print('\n5 example rows:')
    examples = sorted(results, key=lambda r: sum(v != '' for v in r.values()), reverse=True)[:5]
    for row in examples:
        print(json.dumps(row, ensure_ascii=False))
    print('Request summary:', json.dumps(dict(STATS), sort_keys=True))


if __name__ == '__main__':
    main()
