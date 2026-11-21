#!/usr/bin/env python3
"""Extract published founder/director names with a strict historical cutoff.

Stdlib only. Run: python scripts/m_founders.py
Live fallback pages are fetched and marked 'live', but their undated names are
withheld by default. --include-live-names explicitly opts out of that PIT rule.
The three-request company budget includes redirects and retry attempts. Every
HTTP response (including HTTP errors) is saved in a SHA1(URL) JSON cache file.
"""
import argparse
import base64
import csv
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'raw' / 'cache' / 'm_founders'
FIELDS = ['name', 'website', 'asof_date', 'founder_names',
          'managing_directors', 'source_url', 'snapshot_ts']
UA = 'Mozilla/5.0 (research script)'
LOCK = threading.Lock()
HOST_LOCKS = {}
LAST_REQUEST = {}
CACHE_LOCK = threading.Lock()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self):
        self.remaining = 3
        self.opener = urllib.request.build_opener(NoRedirect)

    def get(self, url):
        """Return a cached response or request within the remaining company budget."""
        if not self.remaining:
            return None
        path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + '.json')
        with CACHE_LOCK:
            try:
                entries = json.loads(path.read_text(encoding='utf-8'))['responses']
            except (OSError, ValueError, KeyError):
                entries = []
        # Replay the final result; retry histories are retained for auditing.
        if entries:
            response = entries[-1]
            # Cached attempts use the same budget as the original run, so reruns
            # cannot gradually exceed the three-fetch company limit.
            self.remaining = max(0, self.remaining - len(entries))
        else:
            response = None
            for attempt in range(2):
                if not self.remaining:
                    return None
                if attempt:
                    time.sleep(2 ** attempt)
                self.remaining -= 1
                host = urllib.parse.urlsplit(url).netloc.lower()
                with LOCK:
                    host_lock = HOST_LOCKS.setdefault(host, threading.Lock())
                # Serialize starts, allowing slow responses to overlap across workers.
                with host_lock:
                    time.sleep(max(0, 1.05 - (time.monotonic() - LAST_REQUEST.get(host, 0))))
                    LAST_REQUEST[host] = time.monotonic()
                response = {'url': url, 'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                            'status': None, 'headers': {}, 'body_b64': ''}
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Encoding': 'identity'})
                    with self.opener.open(req, timeout=20) as reply:
                        response.update(status=reply.status, headers=dict(reply.headers),
                                        body_b64=base64.b64encode(reply.read()).decode('ascii'))
                except urllib.error.HTTPError as exc:
                    response.update(status=exc.code, headers=dict(exc.headers),
                                    body_b64=base64.b64encode(exc.read()).decode('ascii'))
                except (OSError, ValueError) as exc:
                    response['error'] = str(exc)
                entries.append(response)
                with CACHE_LOCK:
                    path.write_text(json.dumps({'url': url, 'responses': entries}, ensure_ascii=False), encoding='utf-8')
                status = response['status']
                if status not in (429, 500, 502, 503, 504) and status is not None:
                    break
                # Honor Retry-After without consuming the last slot on a retry
                # when the server asks for a long pause.
                delay = next((v for k, v in response['headers'].items() if k.lower() == 'retry-after'), '')
                try:
                    delay = float(delay)
                except ValueError:
                    try:
                        import email.utils
                        delay = max(0, (email.utils.parsedate_to_datetime(delay) - dt.datetime.now(dt.timezone.utc)).total_seconds())
                    except (ValueError, TypeError):
                        delay = 0
                if delay > 30:
                    break
                if delay:
                    time.sleep(delay)
        if response and response['status'] in (301, 302, 303, 307, 308):
            location = next((v for k, v in response['headers'].items() if k.lower() == 'location'), '')
            if location and self.remaining:
                return self.get(urllib.parse.urljoin(url, location))
        return response


def body(response):
    if not response:
        return ''
    raw = base64.b64decode(response['body_b64'])
    content_type = next((v for k, v in response['headers'].items() if k.lower() == 'content-type'), '')
    charset = re.search(r'charset=["\']?([\w-]+)', content_type)
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
        if tag in ('p', 'div', 'br', 'li', 'section', 'h1', 'h2', 'h3', 'h4', 'tr'):
            self.parts.append('\n')
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))
        if tag == 'img':
            # Founder portraits often publish the name in accessible alt text.
            alt = dict(attrs).get('alt', '')
            if alt:
                self.parts.extend(['\n', alt, '\n'])

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ('p', 'div', 'li', 'section', 'h1', 'h2', 'h3', 'h4', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def lines(self):
        return [re.sub(r'\s+', ' ', s).strip() for s in ''.join(self.parts).splitlines() if s.strip()]


MD = re.compile(r'Gesch(?:ä|ae)ftsf(?:ü|ue)hrer(?:in|innen)?|Gesch(?:ä|ae)ftsf(?:ü|ue)hrung|Managing\s+Directors?|vertreten\s+durch|represented\s+by', re.I)
FOUNDER = re.compile(r'(?<!\w)(?:Co[\s-]?)?Founders?\b|Gr(?:ü|ue)nder(?:in|innen)?\b|\b(?:CEO|CTO)\b', re.I)
WORD = r'(?:[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿĀ-ž]+(?:[’\x27-][A-Za-zÀ-ÖØ-öø-ÿĀ-ž]+)*|[A-Z]\.)'
NAME = re.compile(r'(?<!\w)' + WORD + r'(?:\s+(?:(?:von|van|de|del|der|den|zu|zur)\s+)?' + WORD + r'){1,4}(?!\w)')
DENY = set('gmbh ug ag kg mbh inc ltd limited llc germany deutschland berlin munich hamburg address adresse street straße strasse telefon phone fax email kontakt contact register handelsregister amtsgericht ust id vat tax responsible verantwortlich inhalt impressum imprint legal team about uns unser unsere unternehmen company management director directors managing founder founders co ceo cto chief executive officer technology geschäftsführer geschäftsführung vertreten durch angaben gemäß rechtliche hinweise datenschutz privacy policy linkedin copyright all rights reserved alle rechte vorbehalten die der das den dem herr herrn frau registergericht sitz münchen frankfurt bonn karlsruhe leipzig dresden stuttgart'.split())
DENY.update('university technical institute institut venture ventures lab labs technologies solutions robotics systems represented research services'.split())
DENY.update('tech lead software engineer engineering product manager business development design designer marketing sales finance operations assistant scientist researcher head advisory board president advisor creative customer success human resources learn read more founding'.split())
DENY.update('with phase change during interview portrait picture photo'.split())


def names_in(text):
    text = re.sub(r'\b(?:Prof\.?|Dr\.?|Dipl\.?[- ]?Ing\.?)\s*', '', text)
    text = re.sub(r'\b(?:PhD|MSc|MBA)\b', '', text)
    found = []
    for match in NAME.finditer(text):
        name = match.group().strip()
        tokens = name.lower().split()
        if (2 <= len(tokens) <= 5 and not any(t in DENY for t in tokens)
                and sum(len(t.strip('.')) > 1 for t in name.split()) >= 2
                and not any(t.isupper() and len(t.strip('.')) > 1 for t in name.split())):
            found.append(name)
    return found


def extract(markup):
    lines = Page(markup).lines()
    founders, directors = [], []
    excluded = False
    for i, line in enumerate(lines):
        # Other companies' founders often appear as advisors or testimonials.
        if len(line) < 100 and re.search(r'\b(?:angels|advisors|advisory|investors|testimonials|customers|kunden|beirat|investoren|mentors|mentoren)\b', line, re.I):
            excluded = True
        if re.fullmatch(r'(?:(?:our|the|unser|unsere)\s+)?(?:team|founders|founding team|management team|leadership|gründer|geschäftsführung)', line, re.I):
            excluded = False
        for pattern, target, preceding in ((MD, directors, False), (FOUNDER, founders, True)):
            if excluded and preceding:
                continue
            for match in pattern.finditer(line):
                after = line[match.end():].strip(' :;,|–—-()')
                # Stop before another role, addresses, or sentence-length prose.
                after = re.split(r'[;|]|\s(?:ist|is|war|was|has|hat|founded|gründete)\s', after)[0]
                after = MD.sub('|', FOUNDER.sub('|', after)).split('|')[0]
                candidates = [] if preceding and re.match(r'[a-z]', after) else names_in(after[:150])
                if preceding:
                    before = line[:match.start()].strip(' :;,|–—-()')
                    candidates += names_in(re.split(r'[;|]', before)[-1][-100:])
                # Team cards normally publish name, then role, then the next
                # person's name. Prefer the name above a standalone role.
                if preceding and not candidates and i:
                    candidates += names_in(lines[i - 1][-100:])
                if not candidates and not after:
                    for following in lines[i + 1:i + (3 if preceding else 7)]:
                        if MD.search(following) or FOUNDER.search(following):
                            continue
                        following_names = names_in(following[:100])
                        if not following_names and candidates:
                            break
                        candidates += following_names
                        if candidates and preceding:
                            break
                target.extend(candidates)
    return list(dict.fromkeys(founders)), list(dict.fromkeys(directors))


def kind(url):
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).lower()
    if re.search(r'(?:impressum|imprint|legal(?:-notice)?)(?:[/_.-]|$)', path):
        return 'legal'
    if re.search(r'/(?:team|our-team|the-team|about(?:[-_]us)?|ueber[-_]uns|uber[-_]uns|über[-_]uns|company|people-company|founders?)(?:-\d+)?(?:\.(?:html?|php))?/?$', path):
        return 'team'
    return ''


def process(row, include_live=False):
    result = {key: row.get(key, '') if key in ('name', 'website', 'asof_date') else '' for key in FIELDS}
    website = row.get('website', '').strip()
    if not website:
        return result
    website = website if '://' in website else 'https://' + website
    parsed = urllib.parse.urlsplit(website)
    domain = (parsed.hostname or '').removeprefix('www.')
    if not domain:
        return result
    if domain in ('linkedin.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com'):
        return result
    try:
        cutoff = dt.date.fromisoformat(row['asof_date']).strftime('%Y%m%d') + '000000'
    except (ValueError, KeyError):
        return result
    client = Client()
    # HTTPS is the equivalent CDX endpoint; avoids a transport-only HTTP redirect.
    query = urllib.parse.urlencode({'url': domain + '/*', 'output': 'json',
                                   'to': cutoff[:8], 'filter': 'statuscode:200',
                                   'collapse': 'urlkey', 'fl': 'timestamp,original', 'limit': '500'})
    response = client.get('https://web.archive.org/cdx/search/cdx?' + query)
    selected = {}
    try:
        records = json.loads(body(response)) if response and response['status'] == 200 else []
        for entry in records:
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            ts, original = entry
            if not re.fullmatch(r'\d{14}', str(ts)) or ts >= cutoff:
                continue
            if (urllib.parse.urlsplit(original).hostname or '').removeprefix('www.') != domain:
                continue
            category = kind(original)
            if category and (category not in selected or ts > selected[category][0]):
                selected[category] = (ts, original)
    except (ValueError, TypeError):
        pass
    founders, directors, sources, timestamps = [], [], [], []
    if selected:
        for category in ('legal', 'team'):
            if category not in selected:
                continue
            ts, original = selected[category]
            archive_url = 'https://web.archive.org/web/' + ts + 'id_/' + original
            page = client.get(archive_url)
            if not page or page['status'] != 200:
                continue
            # Do not trust a replay redirected onto another timestamp or live URL.
            if page['url'] != archive_url:
                continue
            f, d = extract(body(page))
            founders.extend(f)
            directors.extend(d)
            sources.append(original)
            timestamps.append(ts)
    else:
        # Two available slots: homepage for link discovery, then live Impressum.
        origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '/', '', ''))
        home = client.get(origin)
        if home and home['status'] == 200 and kind(home['url']) == 'legal':
            live = home
        else:
            links = Page(body(home)).links if home and home['status'] == 200 else []
            candidates = [urllib.parse.urljoin(origin, link) for link in links if kind(link) == 'legal']
            candidates = [url for url in candidates if (urllib.parse.urlsplit(url).hostname or '').removeprefix('www.') == domain]
            live = client.get(candidates[0] if candidates else urllib.parse.urljoin(origin, 'impressum'))
        if live and live['status'] == 200:
            sources.append(live['url'])
            timestamps.append('live')
            if include_live:
                f, d = extract(body(live))
                founders.extend(f)
                directors.extend(d)
    result.update(founder_names='; '.join(dict.fromkeys(founders)),
                  managing_directors='; '.join(dict.fromkeys(directors)),
                  source_url='; '.join(sources), snapshot_ts='; '.join(timestamps))
    return result


def self_test():
    assert extract('<p>Geschäftsführer: Dr. Max Müller und Anna Schmidt</p>')[1] == ['Max Müller', 'Anna Schmidt']
    assert extract('<div>Anna Schmidt</div><div>Co-Founder &amp; CEO</div>')[0] == ['Anna Schmidt']
    assert extract('<p>CTO: Johannes von Braun</p>')[0] == ['Johannes von Braun']
    assert extract('<p>Managing Director: Berlin GmbH</p>')[1] == []
    assert extract('<p>Geschäftsführer:innen Lisa Oberaigner, Florian Fesch.</p>')[1] == ['Lisa Oberaigner', 'Florian Fesch']
    assert extract('<p>represented by</p><p>Manuel Kuder</p><p>Lukas Obkircher</p>')[1] == ['Manuel Kuder', 'Lukas Obkircher']
    assert names_in('TUM Venture Labs') == []
    assert names_in('Technical University of Munich') == []
    assert extract('<img alt="Anna Schmidt"><p>Co-Founder</p>')[0] == ['Anna Schmidt']
    assert extract('<p>Nabil Imran</p><p>Co-Founder, CTO</p><p>Elena Frimmersdorf</p><p>Business Development</p>')[0] == ['Nabil Imran']
    assert kind('https://example.com/magazin/sarina-nowak-fuer-about-you') == ''
    assert kind('https://example.com/en/about-us/') == 'team'
    assert names_in('Mataza A. A. Golzari') == ['Mataza A. A. Golzari']
    assert extract('<h2>Our Business Angels and Advisors</h2><p>David Bellem</p><p>Founder &amp; CTO of Payworks</p>')[0] == []
    assert extract('<script>Founder: Max Müller</script>') == ([], [])
    print('Extraction self-tests passed.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, choices=range(4, 7), default=5)
    parser.add_argument('--include-live-names', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'raw' / 'cohort_short.csv').open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    results = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(process, row, args.include_live_names): i for i, row in enumerate(rows)}
        for n, future in enumerate(as_completed(pending), 1):
            index = pending[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                results[index] = {key: rows[index].get(key, '') if key in ('name', 'website', 'asof_date') else '' for key in FIELDS}
                print(f'Row {index + 1}: {type(exc).__name__}: {exc}', flush=True)
            if n % 10 == 0 or n == len(rows):
                print(f'Processed {n}/{len(rows)}', flush=True)
    output = ROOT / 'raw' / 'm_founders.csv'
    with output.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)
    print(f'\nWrote {len(results)} rows to {output}', flush=True)
    for key in FIELDS:
        filled = sum(bool(row[key]) for row in results)
        percentage = 100 * filled / len(results) if results else 0
        print(f'{key}: {filled}/{len(results)} filled ({percentage:.1f}%); missing {100 - percentage:.1f}%')
    print('\n5 example rows:')
    writer = csv.DictWriter(__import__('sys').stdout, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(sorted(results, key=lambda row: bool(row['founder_names']) or bool(row['managing_directors']), reverse=True)[:5])
    if not args.include_live_names:
        print('Live fallback name cells withheld: current pages do not establish historical roles.')


if __name__ == '__main__':
    main()
