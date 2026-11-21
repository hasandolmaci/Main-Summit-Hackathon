#!/usr/bin/env python3
"""Free-source enrichment. Python stdlib only; run from any directory.

Unknown values are empty. Dates are exclusive of asof_date (UTC midnight).
Devpost is explicitly an ALL-TIME, page-1 count despite its requested column
name: project dates are not consistently available. News totals/first dates
refer only to the returned RSS sample, not the complete historical press.
HTGF matches are dated using the portfolio's investment year: a year is
unambiguously before asof only when December 31 is before asof. Undated and
same-year matches remain unknown; future investments do not become features.
Nonmatches are 0 only after the complete portfolio and its domains are fetched,
as requested (a current-directory lookup, not proof of historical absence).
Patents require a publication before asof as well as the priority-date query;
otherwise the total would leak applications first published after asof.
sector_news_growth is intentionally skipped.
"""

import calendar
import concurrent.futures
import csv
import datetime as dt
import email.utils
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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw/cache/enrich_free'
COLUMNS = ['name', 'website', 'asof_date', 'in_htgf_portfolio',
           'htgf_match_evidence', 'devpost_projects_before', 'devpost_evidence',
           'news_mentions_before_12m', 'news_mentions_before_total',
           'news_first_mention_date', 'patents_before', 'patent_evidence',
           'sector_news_growth']
UA = 'Mozilla/5.0 (research script)'
PORTFOLIO = 'https://www.htgf.de/en/portfolio/'
NOTES = Counter()
NOTE_LOCK = threading.Lock()


def note(message):
    with NOTE_LOCK:
        NOTES[message] += 1


class HTTP:
    """Cache response envelopes (including failures) by SHA1(URL).

    POST parameters are also encoded into the URL, so pagination has distinct
    URL keys. A per-host lock covers each request, including all redirects;
    requests to a host start at least one second apart. These sources redirect
    within their own host; cross-host redirects are refused to protect limits.
    """

    def __init__(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.locks = defaultdict(threading.Lock)
        self.last = defaultdict(float)
        self.guard = threading.Lock()
        self.url_locks = defaultdict(threading.Lock)
        self.rate_blocked = set()

    def get(self, url, post=None):
        with self.guard:
            lock = self.url_locks[url]
        with lock:
            return self._get(url, post)

    def _get(self, url, post):
        path = CACHE / hashlib.sha1(url.encode('utf-8')).hexdigest()
        method = 'POST' if post is not None else 'GET'
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                if saved.get('method', 'GET') == method:
                    return saved
            except (OSError, ValueError):
                pass
        host = urllib.parse.urlsplit(url).hostname
        with self.guard:
            lock = self.locks[host]
        attempts = []
        for attempt in range(3):
            headers = {'User-Agent': UA, 'Accept': '*/*'}
            data = None
            if post is not None:
                headers['Content-Type'] = 'application/json; charset=utf-8'
                data = json.dumps(post).encode('utf-8')
            with lock:
                if host in self.rate_blocked:
                    return {'url': url, 'status': 0, 'headers': {}, 'body': '',
                            'error': 'Host paused after repeated HTTP 429 responses'}
                time.sleep(max(0, 1.01 - (time.monotonic() - self.last[host])))
                self.last[host] = time.monotonic()
                try:
                    class SameHost(urllib.request.HTTPRedirectHandler):
                        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                            if urllib.parse.urlsplit(newurl).hostname != host:
                                raise urllib.error.URLError('Cross-host redirect refused')
                            # Automatic redirects could otherwise violate 1 req/s.
                            time.sleep(max(0, 1.01 - (time.monotonic() - client.last[host])))
                            client.last[host] = time.monotonic()
                            return super().redirect_request(req, fp, code, msg, hdrs, newurl)
                    client = self
                    opener = urllib.request.build_opener(SameHost())
                    with opener.open(urllib.request.Request(url, data=data, headers=headers),
                                     timeout=25) as response:
                        status, hdrs = response.status, dict(response.headers)
                        body = response.read().decode('utf-8', errors='replace')
                    error = ''
                except urllib.error.HTTPError as exc:
                    status, hdrs = exc.code, dict(exc.headers)
                    body, error = exc.read().decode('utf-8', errors='replace'), str(exc)
                except (OSError, urllib.error.URLError, TimeoutError) as exc:
                    status, hdrs, body, error = 0, {}, '', str(exc)
            record = {'url': url, 'method': method, 'status': status,
                      'headers': hdrs, 'body': body, 'error': error,
                      'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat()}
            attempts.append(record.copy())
            record['attempts'] = attempts.copy()
            path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            blocked = self.blocked(record)
            if (blocked and status != 429) or status not in (0, 408, 429, 500, 502, 503, 504):
                return record
            if attempt < 2:
                retry = next((v for k, v in hdrs.items() if k.lower() == 'retry-after'), '')
                try:
                    delay = float(retry)
                except ValueError:
                    try:
                        delay = (email.utils.parsedate_to_datetime(retry)
                                 - dt.datetime.now(dt.timezone.utc)).total_seconds()
                    except (ValueError, TypeError):
                        delay = 0
                # Do not wait less than Retry-After; leave the source unknown
                # when the server requests a long pause.
                if delay > 60:
                    with lock:
                        self.rate_blocked.add(host)
                    note(host + ' paused: Retry-After exceeds 60 seconds')
                    return record
                # Hold the host lock during a rate-limit cooldown so other
                # workers cannot continue requests while this worker backs off.
                if status == 429:
                    with lock:
                        time.sleep(max(10 * 2 ** attempt, delay))
                else:
                    time.sleep(max(2 ** (attempt + 1), delay))
            elif status == 429:
                with lock:
                    self.rate_blocked.add(host)
                note(host + ' paused after repeated HTTP 429 responses')
        return record

    @staticmethod
    def blocked(record):
        text = record['body'].lower()
        return record['status'] in (401, 403, 429) or any(
            marker in text for marker in ('just a moment...', 'automated queries',
                                         'unusual traffic', 'cf-chl-'))


def text(value):
    return html.unescape(re.sub(r'<[^>]*>', ' ', str(value or ''))).strip()


def clean_name(name, portfolio=False):
    name = unicodedata.normalize('NFKC', name)
    name = re.sub(r'\(\s*haftungsbeschr[äa]nkt\s*\)', '', name, flags=re.I)
    words = re.findall(r'[^\W_]+', name, flags=re.UNICODE)
    remove = {'gmbh', 'ug', 'ag'} | ({'ai', 'labs'} if portfolio else set())
    return ' '.join(word for word in words if word.casefold() not in remove)


def normalized(name):
    return re.sub(r'\W|_', '', clean_name(name, True)).casefold()


def domain(url):
    try:
        host = urllib.parse.urlsplit(url if '://' in url else 'https://' + url).hostname or ''
        host = host.strip().lower().removeprefix('www.').encode('idna').decode('ascii')
        social = ('linkedin.com', 'facebook.com', 'instagram.com', 'twitter.com',
                  'x.com', 'youtube.com', 'youtu.be', 'tiktok.com', 'crunchbase.com')
        if any(host == site or host.endswith('.' + site) for site in social):
            return ''
        return host
    except (ValueError, UnicodeError):
        return ''


def contains_name(name, value):
    return bool(name.strip()) and bool(re.search(r'(?<!\w)' + re.escape(name.strip())
                                                + r'(?!\w)', value, re.I))


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.active = [attrs.get('href', ''), attrs, '']

    def handle_data(self, data):
        if self.active is not None:
            self.active[2] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.active is not None:
            self.links.append(self.active)
            self.active = None


def portfolio_cards(markup):
    chunks = re.split(r'<div\b(?=[^>]*\bclass="[^"]*\bportfolio-item\b)', markup)
    cards = []
    for chunk in chunks[1:]:
        title = re.search(r'<h3[^>]*>(.*?)</h3>', chunk, re.S)
        url = re.search(r'href="(https://www\.htgf\.de/en/portfolio/htgffamily/[^"?#]+)"', chunk)
        year = re.search(r'data-year="(\d{4})"', chunk)
        if title and url:
            cards.append({'name': text(title.group(1)), 'url': html.unescape(url.group(1)),
                          'year': int(year.group(1)) if year else None,
                          'domains': set(), 'domain_ok': False})
    return cards


def build_portfolio(client):
    page = client.get(PORTFOLIO)
    if page['status'] != 200:
        note('HTGF portfolio could not be fetched; all HTGF cells unknown')
        return [], False
    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', page['body'])
    ajax = next((html.unescape(u) for u in scripts if 'list-portfolio-ajax' in u), '')
    if not ajax:
        note('HTGF portfolio API script not found; HTGF unknown')
        return [], False
    js = client.get(ajax)
    if js['status'] != 200 or '/htgf/v1/mq_portfolio' not in js['body']:
        note('HTGF portfolio API unavailable; HTGF unknown')
        return [], False
    cards, seen = [], set()
    complete = False
    for offset in range(0, 10000, 100):
        params = dict(posts_per_page=100, offset=offset, lang='en', orderby='date', order='DESC')
        url = 'https://www.htgf.de/wp-json/htgf/v1/mq_portfolio?' + urllib.parse.urlencode(params)
        response = client.get(url, post=params)
        try:
            payload = json.loads(response['body'])
            markup = payload['html'] if isinstance(payload, dict) else payload
            if response['status'] != 200 or not isinstance(markup, str):
                raise ValueError('Invalid portfolio response')
            batch = portfolio_cards(markup)
        except (ValueError, TypeError, KeyError):
            note('HTGF pagination failed; nonmatches unknown')
            break
        if not batch:
            complete = not re.search(r'\bportfolio-item\b', markup)
            break
        fresh = [card for card in batch if card['url'] not in seen]
        if not fresh:
            note('HTGF pagination repeated; nonmatches unknown')
            break
        cards.extend(fresh)
        seen.update(card['url'] for card in fresh)
        print('HTGF portfolio entries:', len(cards), flush=True)
        # Fetch an additional page even after a short response: some APIs cap
        # page sizes, so a short page alone is not evidence of completion.
        if len(batch) < 100:
            # The next offset must use the actual number returned.
            if len(batch) != len(fresh):
                break
            # This API honors posts_per_page=100; verify with an empty page.
            params['offset'] = offset + len(batch)
            tail_url = 'https://www.htgf.de/wp-json/htgf/v1/mq_portfolio?' + urllib.parse.urlencode(params)
            tail = client.get(tail_url, post=params)
            try:
                tail_html = json.loads(tail['body'])['html']
                complete = tail['status'] == 200 and not portfolio_cards(tail_html) and 'portfolio-item' not in tail_html
            except (ValueError, KeyError, TypeError):
                complete = False
            break
    return cards, complete


def portfolio_detail(client, card):
    response = client.get(card['url'])
    if response['status'] != 200:
        note('HTGF company detail unavailable')
        return card
    parser = Links()
    parser.feed(response['body'])
    # The company CTA is in main content; generic footer/social links must not
    # be treated as company domains. Its label is "To the website" / domain.
    for url, attrs, label in parser.links:
        host = domain(url)
        if not url.startswith(('https://', 'http://')) or host in ('htgf.de', ''):
            continue
        if 'wp-block-button__link' not in attrs.get('class', '').split():
            continue
        if (re.search(r'(?:website|webseite|webpage|homepage)', label, re.I)
                or ('.' in label.strip() and domain(label.strip()) == host)):
            card['domains'].add(host)
    # A successfully fetched entry can legitimately list no company website.
    # That is a complete inventory of the domains provided by this source;
    # an HTTP failure, in contrast, leaves domain_ok false.
    card['domain_ok'] = True
    return card


class OptionalSources:
    """Stop contacting a source after an explicit access block this run."""
    def __init__(self):
        self.dev_lock = threading.Lock()
        self.pat_lock = threading.Lock()
        self.dev_blocked = False
        self.pat_blocked = False

    def devpost(self, client, name):
        with self.dev_lock:
            if self.dev_blocked:
                return {}
            url = 'https://devpost.com/software/search?' + urllib.parse.urlencode({'query': name, 'page': 1})
            response = client.get(url)
            if client.blocked(response):
                self.dev_blocked = True
                note('Devpost access blocked; remaining Devpost values unknown')
                return {}
        if response['status'] != 200:
            note('Devpost HTTP failure')
            return {}
        try:
            payload = json.loads(response['body'])
            if isinstance(payload, list):
                projects = payload
            elif isinstance(payload, dict):
                projects = next((payload[k] for k in ('software', 'projects', 'results') if isinstance(payload.get(k), list)), None)
            else:
                projects = None
            if projects is None or any(not isinstance(p, dict) for p in projects):
                raise ValueError('Unrecognized Devpost schema')
        except ValueError:
            note('Devpost response was not recognized JSON; values unknown')
            return {}
        matches = [p for p in projects if contains_name(name, text(p.get('name') or p.get('title')))
                   or contains_name(name, text(p.get('tagline')))]
        evidence = ''
        if matches:
            evidence = matches[0].get('url') or matches[0].get('software_url') or ''
            if evidence:
                evidence = urllib.parse.urljoin('https://devpost.com', evidence)
        return {'devpost_projects_before': len(matches), 'devpost_evidence': evidence}

    def patents(self, client, name, asof):
        cleaned = clean_name(name)
        if len(re.sub(r'\W', '', cleaned)) < 4:
            note('Patent query skipped: cleaned name shorter than four characters')
            return {}
        inner = urllib.parse.urlencode({'assignee': cleaned, 'before': 'priority:' + asof.strftime('%Y%m%d')})
        url = 'https://patents.google.com/xhr/query?' + urllib.parse.urlencode({'url': inner, 'exp': ''})
        with self.pat_lock:
            if self.pat_blocked:
                return {}
            response = client.get(url)
            if client.blocked(response):
                fallback = client.get('https://patents.google.com/?' + urllib.parse.urlencode({'assignee': cleaned}))
                note('Google Patents query blocked; HTML fallback cannot supply a verified count')
                self.pat_blocked = True
                return {}
        if response['status'] != 200:
            note('Google Patents HTTP failure')
            return {}
        try:
            payload = json.loads(response['body'])
            results = payload['results']
            total = results['total_num_results']
            if isinstance(total, bool) or not re.fullmatch(r'\d[\d,]*', str(total)):
                raise ValueError('Missing exact patent total')
            total = int(str(total).replace(',', ''))
            if total == 0:
                return {'patents_before': 0}
            # Requery with a publication cutoff to exclude information that
            # was still confidential at asof_date.
            pub_inner = inner + '&before=publication:' + asof.strftime('%Y%m%d')
            pub_url = 'https://patents.google.com/xhr/query?' + urllib.parse.urlencode({'url': pub_inner, 'exp': ''})
            safe = client.get(pub_url)
            if safe['status'] != 200:
                return {}
            results = json.loads(safe['body'])['results']
            count = results['total_num_results']
            if not re.fullmatch(r'\d[\d,]*', str(count)):
                return {}
            count = int(str(count).replace(',', ''))
            hits = [hit['patent'] for group in results.get('cluster', []) for hit in group.get('result', []) if 'patent' in hit]
            evidence = hits[0].get('publication_number', '') if hits else ''
            if count and not evidence:
                return {}
            return {'patents_before': count, 'patent_evidence': evidence}
        except (ValueError, KeyError, TypeError, IndexError):
            note('Google Patents JSON schema unrecognized; values unknown')
            return {}


def news(client, name, asof):
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode(
        {'q': '"' + name + '"', 'hl': 'de', 'gl': 'DE', 'ceid': 'DE:de'})
    response = client.get(url)
    if response['status'] != 200:
        note('Google News HTTP failure')
        return {}
    try:
        root = ET.fromstring(response['body'])
        if root.tag != 'rss' or root.find('channel') is None:
            raise ValueError('Not RSS')
    except (ET.ParseError, ValueError):
        note('Google News invalid RSS')
        return {}
    cutoff = dt.datetime.combine(asof, dt.time(), dt.timezone.utc)
    lower = cutoff.replace(year=cutoff.year - 1,
                           day=min(cutoff.day, calendar.monthrange(cutoff.year - 1, cutoff.month)[1]))
    dates, exact_dates, seen = [], [], set()
    for item in root.findall('./channel/item'):
        title = html.unescape(item.findtext('title', ''))
        try:
            published = email.utils.parsedate_to_datetime(item.findtext('pubDate', ''))
            if published.tzinfo is None:
                raise ValueError('Undated timezone')
            published = published.astimezone(dt.timezone.utc)
        except (TypeError, ValueError, OverflowError):
            note('Google News item has invalid date; row counts unknown')
            return {}
        key = item.findtext('guid') or item.findtext('link') or (title, published.isoformat())
        if key not in seen and published < cutoff:
            dates.append(published)
            if contains_name(name, title):
                exact_dates.append(published)
            seen.add(key)
    return {'news_mentions_before_12m': sum(d >= lower for d in exact_dates),
            'news_mentions_before_total': len(dates),
            'news_first_mention_date': min(dates).date().isoformat() if dates else ''}


def enrich(client, sources, row):
    result = {key: '' for key in COLUMNS}
    result.update({key: row[key] for key in ('name', 'website', 'asof_date')})
    asof = dt.date.fromisoformat(row['asof_date'])
    for label, function in (
        ('News', lambda: news(client, row['name'], asof)),
        ('Devpost', lambda: sources.devpost(client, row['name'])),
        ('Patents', lambda: sources.patents(client, row['name'], asof)),
    ):
        try:
            result.update(function())
        except Exception as exc:
            note(label + ' unexpected failure: ' + type(exc).__name__)
    return result


def apply_portfolio(result, cards, complete):
    name, host = normalized(result['name']), domain(result['website'])
    matches = [card for card in cards if (name and normalized(card['name']) == name)
               or (host and host in card['domains'])]
    asof = dt.date.fromisoformat(result['asof_date'])
    before = [card for card in matches if card['year'] and dt.date(card['year'], 12, 31) < asof]
    if before:
        card = before[0]
        result['in_htgf_portfolio'] = 1
        result['htgf_match_evidence'] = '{} | {} | investment_year={}'.format(card['name'], card['url'], card['year'])
    elif matches:
        note('HTGF match lacks an unambiguous investment date before asof; unknown')
    elif complete and (not host or all(card['domain_ok'] for card in cards)):
        result['in_htgf_portfolio'] = 0
    else:
        note('HTGF nonmatch unknown: portfolio/domain inventory incomplete')


def main():
    with (ROOT / 'raw/cohort_short.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        dt.date.fromisoformat(row['asof_date'])
    client, sources = HTTP(), OptionalSources()
    cards, complete = build_portfolio(client)
    print('HTGF listing:', len(cards), 'entries; pagination complete:', complete, flush=True)
    results = [None] * len(rows)
    # Six workers shared across company enrichments and portfolio details. News
    # and portfolio hosts can make progress concurrently at 1 request/s each.
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {}
        for index in range(max(len(rows), len(cards))):
            if index < len(rows):
                futures[pool.submit(enrich, client, sources, rows[index])] = ('row', index)
            if index < len(cards):
                futures[pool.submit(portfolio_detail, client, cards[index])] = ('card', index)
        done_rows = done_cards = 0
        for future in concurrent.futures.as_completed(futures):
            kind, index = futures[future]
            if kind == 'row':
                results[index] = future.result()
                done_rows += 1
                if done_rows % 20 == 0 or done_rows == len(rows):
                    print('Enriched rows: {}/{}'.format(done_rows, len(rows)), flush=True)
            else:
                try:
                    cards[index] = future.result()
                except Exception as exc:
                    note('HTGF detail unexpected failure: ' + type(exc).__name__)
                done_cards += 1
                if done_cards % 25 == 0 or done_cards == len(cards):
                    print('HTGF company domains: {}/{}'.format(done_cards, len(cards)), flush=True)
    for result in results:
        apply_portfolio(result, cards, complete)
    output = ROOT / 'raw/enrich_free.csv'
    with output.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(results)
    print('\nWrote {} rows: {}'.format(len(results), output))
    print('Column fill rates (zeros count as filled):')
    for column in COLUMNS:
        filled = sum(row[column] != '' for row in results)
        rate = 100 * filled / len(results) if results else 0
        print('  {}: {}/{} ({:.1f}% filled; {:.1f}% missing)'.format(column, filled, len(results), rate, 100-rate))
    print('\nFive example rows:')
    for row in results[:5]:
        print(json.dumps(row, ensure_ascii=False))
    print('\nColumn comments:')
    print('Devpost projects/evidence: all-time PAGE-1 matches; dates unavailable, explicitly exempt from cutoff.')
    print('News: 12-month count requires exact-name titles; total/first date use all returned pre-asof RSS items. RSS is not exhaustive historical coverage.')
    print('HTGF: investment-year end must precede asof; undated/same-year/future matches unknown.')
    print('HTGF negative lookup requires a complete company and domain inventory; current absence is not historical proof.')
    print('Patents: priority cutoff plus publication cutoff; short names skipped. Counts are Google search result units.')
    print('sector_news_growth: skipped; all cells empty.')
    for message, count in sorted(NOTES.items()):
        print('  {}: {}'.format(message, count))


if __name__ == '__main__':
    main()
