#!/usr/bin/env python3
"""Label first institutional funding using free, cached public sources.

Usage: python scripts/label_funding.py raw/cohort_utum.csv [--workers 8]
Publication dates are proxies for round dates; uncertain hits are flagged for review.
An empty label means unknown, not a negative. The maturity cutoff is fixed by the
backtest specification, independently of the day this script runs.
"""
import argparse
import calendar
import csv
import datetime as dt
import email.utils
import gzip
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw' / 'cache' / 'label_funding'
FIELDS = 'name website asof_date y_label y_round_date y_round_type y_amount_eur y_dead funding_status evidence_url evidence_quote checked_sources needs_review'.split()
CUTOFF = dt.date(2025, 5, 20)
INVESTORS = ['HTGF', 'High-Tech Gruenderfonds', 'High-Tech Gründerfonds',
 'Y Combinator', 'Antler', 'Techstars', 'Earlybird', 'Cherry Ventures',
 'Project A', 'Speedinvest', 'La Famiglia', 'Point Nine', 'UVC Partners',
 'Atlantic Labs', 'Picus', '468 Capital', 'Vorwerk Ventures', 'BayernKapital',
 'Seedcamp', 'Entrepreneur First', 'First Momentum', 'Plug and Play',
 'Redstone', 'Visionaries Club', 'xdeck', 'Possible Ventures', 'Amino Collective',
 'IBB Ventures', 'eCAPITAL', 'Matterwave', 'Bayern Kapital']
FUND = re.compile(r'finanzier\w*|finanzspritze|\bfunding\b|\brais(?:e[sd]?|ing)\b|\bpre[- ]?seed\w*|\bseed\w*|investier\w*|investment|investoren|investors|series [a-f]|\bserie [a-f]\b|(?:sichert|sammelt|erhält|bekommt|secures|closes).{0,50}(?:million|mio|euro|€)', re.I)
GRANT = re.compile(r'\bgrant[s]?\b|\bEXIST\b|förder\w*|foerder\w*|\bprize\b|\baward\b|\bpreis\b|wettbewerb|competition|scholarship', re.I)
INSTITUTION = re.compile(r'venture capital|\bVC\b|\bCVC\b|angel fund|equity|convertible|\bSAFE\b|finanzierungsrunde|funding round|investment round|\b(?:pre[- ]?seed|seed)\b|\b(?:pre[- ]?seed|seed)[- ]*(?:round|runde|finanzierung|funding)|series [a-f]|serie [a-f]|investoren|investors|\b[\w-]+ (?:Ventures|Capital|Partners)\b', re.I)
DEAD = re.compile(r'\binsolven\w*|\bshut(?:s)? down\b|\bclosed down\b|\bceased operations\b|\bgeschäftsbetrieb eingestellt\b', re.I)
PARKED = re.compile(r'this domain (?:is |may be )?for sale|buy this domain|domain (?:steht )?zum verkauf|sedo domain parking|website coming soon', re.I)
COMMON = {'antler', 'aware', 'atlas', 'beam', 'bevel', 'bloom', 'circle', 'climate', 'drive', 'flow', 'hey', 'impetus', 'katalyst', 'lemon', 'level', 'light', 'nova', 'octave', 'one', 'orbit', 'pulse', 'root', 'rooted', 'seed', 'sensus', 'shift', 'simple', 'sphere', 'spot', 'still', 'tandem', 'trace', 'triton', 'trust', 'turn', 'vortex', 'wave'}
_lock = threading.Lock()
_host_locks = {}
_last = {}


def throttle(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    with _lock:
        guard = _host_locks.setdefault(host, threading.Lock())
    with guard:
        time.sleep(max(0, 1.05 - (time.monotonic() - _last.get(host, 0))))
        _last[host] = time.monotonic()


class PoliteRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        throttle(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + '.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    result = None
    for attempt in range(3):
        throttle(url)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (research script)', 'Accept-Encoding': 'gzip'})
            with urllib.request.build_opener(PoliteRedirect()).open(req, timeout=25) as response:
                body = response.read()
                if response.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                result = {'url': url, 'final_url': response.url, 'status': response.status,
                          'body': body.decode(response.headers.get_content_charset() or 'utf-8', errors='replace'), 'error': ''}
        except urllib.error.HTTPError as exc:
            result = {'url': url, 'status': exc.code, 'body': exc.read().decode('utf-8', errors='replace'), 'error': str(exc)}
        except Exception as exc:
            result = {'url': url, 'status': 0, 'body': '', 'error': str(exc)}
        result['fetched_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        # Cache each response, including failed attempts, before retrying.
        path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        if result['status'] == 200 or result['status'] in (400, 401, 403, 404, 410):
            break
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    return result


def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', value or ''))).strip()


def normalize(value):
    return re.sub(r'[^\w]+', ' ', value.casefold()).strip()


def has_name(text, name):
    value = normalize(name)
    return bool(value) and re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', normalize(text)) is not None


def investor(text):
    return next((item for item in INVESTORS if has_name(text, item)), '')


def iso_date(value):
    try:
        return dt.date.fromisoformat(value[:10]).isoformat()
    except (ValueError, TypeError):
        try:
            return email.utils.parsedate_to_datetime(value).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            return ''


def rss_items(body):
    root = ET.fromstring(body)
    if root.tag != 'rss':
        raise ValueError('not RSS')
    return [{'title': clean(item.findtext('title')), 'date': iso_date(item.findtext('pubDate')),
             'url': item.findtext('link') or '', 'text': clean(item.findtext('description'))}
            for item in root.findall('.//item')]


class Articles(HTMLParser):
    """WordPress search results; never infer a post date from a site footer."""
    def __init__(self):
        super().__init__()
        self.items = []
        self.current = None
        self.heading = False
        self.skip = 0
        self.visible = []
        self.depth = 0
        self.post_depth = None
        self.paragraphs = []
        self.paragraph = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'p':
            self.paragraph = []
        if tag == 'img' and attrs.get('alt') and not self.skip:
            self.visible.append(attrs['alt'])
        if tag == 'div':
            self.depth += 1
            if 'post' in attrs.get('class', '').split():
                self.current = {'title': '', 'date': '', 'url': '', 'text': ''}
                self.post_depth = self.depth
        if tag in ('script', 'style'):
            self.skip += 1
        if tag == 'article':
            self.current = {'title': '', 'date': '', 'url': '', 'text': ''}
        if self.current is not None:
            if tag in ('h1', 'h2', 'h3'):
                self.heading = True
            if tag == 'a' and (self.heading or self.post_depth is not None) and not self.current['url']:
                self.current['url'] = attrs.get('href', '')
            if tag == 'time':
                self.current['date'] = iso_date(attrs.get('datetime', ''))

    def handle_endtag(self, tag):
        if tag == 'p' and self.paragraph is not None:
            self.paragraphs.append(clean(' '.join(self.paragraph)))
            self.paragraph = None
        if tag == 'div':
            if self.post_depth == self.depth and self.current is not None:
                self.items.append(self.current)
                self.current = None
                self.post_depth = None
            self.depth = max(0, self.depth - 1)
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
        if tag in ('h1', 'h2', 'h3'):
            self.heading = False
        if tag == 'article' and self.current is not None:
            self.items.append(self.current)
            self.current = None

    def handle_data(self, data):
        if self.skip:
            return
        self.visible.append(data)
        if self.paragraph is not None:
            self.paragraph.append(data)
        if self.current is not None:
            self.current['text'] += ' ' + data
            if self.heading:
                self.current['title'] += data


def page(body):
    parser = Articles()
    parser.feed(body)
    return parser, clean(' '.join(parser.visible))


def add_months(day, months):
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def round_type(text):
    for pattern, label in [(r'pre[- ]?seed', 'PRE_SEED'), (r'\bseed\b', 'SEED'),
                           (r'serie[s]? ([a-f])', 'SERIES'), (r'\bSAFE\b', 'SAFE'),
                           (r'convertible|wandeldarlehen', 'CONVERTIBLE')]:
        match = re.search(pattern, text, re.I)
        if match:
            return label + ('_' + match[1].upper() if label == 'SERIES' else '')
    return 'INSTITUTIONAL'


def amount_eur(text):
    # Do not convert other currencies or mistake valuation for money raised.
    number = r'\d+(?:[.,]\d+)*'
    scale = r'million(?:en)?|mio\.?|billion|mrd\.?|tausend|thousand|[mk]'
    pattern = rf'(?:(?:€|\bEUR)\s*({number})\s*({scale})?|({number})\s*({scale})?\s*(?:euros?\b|EUR\b|€))'
    matches = list(re.finditer(pattern, text, re.I))
    if len(matches) != 1:
        return ''
    match = matches[0]
    if re.search(r'valuation|bewertung', text[max(0, match.start()-30):match.end()+30], re.I):
        return ''
    unit = (match[2] or match[4] or '').lower()
    value = match[1] or match[3]
    if re.fullmatch(r'\d{1,3}(?:[.,]\d{3})+', value):
        value = re.sub('[.,]', '', value)
    elif ',' in value and '.' in value:
        decimal = ',' if value.rfind(',') > value.rfind('.') else '.'
        value = value.replace('.' if decimal == ',' else ',', '').replace(decimal, '.')
    else:
        value = value.replace(',', '.')
    factor = 1e9 if unit in ('billion', 'mrd', 'mrd.') else 1e6 if unit in ('million', 'millionen', 'mio', 'mio.', 'm') else 1e3 if unit in ('tausend', 'thousand', 'k') else 1
    return str(round(float(value) * factor))


def label(row):
    out = dict.fromkeys(FIELDS, '')
    out.update({key: row.get(key, '') for key in ('name', 'website', 'asof_date')})
    checks, hits, weak = [], [], []
    ambiguous_name = normalize(row['name']) in COMMON or len(normalize(row['name'])) <= 3
    review = ambiguous_name
    try:
        asof = dt.date.fromisoformat(row['asof_date'])
    except ValueError:
        out.update(funding_status='UNKNOWN', needs_review='1', checked_sources='invalid_asof_date')
        return out
    # Still check immature companies; their label remains censored regardless of evidence.
    query = row['name'] + ' (Finanzierung OR Finanzierungsrunde OR Seed OR Pre-Seed OR investiert OR raises OR funding)'
    all_news_ok = True
    for lang, region, ceid in [('de', 'DE', 'DE:de'), ('en-US', 'US', 'US:en')]:
        url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': query, 'hl': lang, 'gl': region, 'ceid': ceid})
        response = fetch(url)
        try:
            if response['status'] != 200:
                raise ValueError('HTTP_' + str(response['status']))
            items = rss_items(response['body'])
            checks.append('google_news_' + lang + ':OK')
            for item in items:
                if has_name(item['title'], row['name']):
                    if DEAD.search(item['title']):
                        out['y_dead'] = '1'
                    if FUND.search(item['title']):
                        hits.append(item)
        except (ValueError, ET.ParseError) as exc:
            all_news_ok = False
            checks.append('google_news_' + lang + ':ERROR_' + str(exc))
    url = 'https://www.deutsche-startups.de/?' + urllib.parse.urlencode({'s': row['name']})
    response = fetch(url)
    parser, visible = page(response['body'])
    ds_ok = response['status'] == 200 and ('deutsche-startups' in visible.lower() or bool(parser.items)) and not re.search(r'just a moment|verify you are human', visible, re.I)
    checks.append('deutsche_startups:' + ('OK' if ds_ok else 'ERROR_HTTP_' + str(response['status'])))
    if ds_ok:
        article_candidates = []
        for item in parser.items:
            if has_name(item['title'] + ' ' + item['text'], row['name']):
                if DEAD.search(item['title']):
                    out['y_dead'] = '1'
                if FUND.search(item['title']) or FUND.search(item['text']):
                    # WordPress URL dates are publication dates if no time element exists.
                    if not item['date']:
                        match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', item['url'])
                        if match:
                            item['date'] = iso_date('-'.join(match.groups()))
                    # Multi-company deal summaries need the company's own paragraph.
                    article_candidates.append(item)
        for item in article_candidates[:5]:
            article_url = item['url']
            if urllib.parse.urlsplit(article_url).hostname != 'www.deutsche-startups.de':
                continue
            article = fetch(article_url)
            checks.append('ds_article:' + str(article['status']) + ':' + article_url)
            article_parser, _ = page(article['body'])
            found = False
            if article['status'] == 200:
                for paragraph in article_parser.paragraphs:
                    if has_name(paragraph, row['name']) and FUND.search(paragraph):
                        # Summary tickers can mix rounds in one paragraph: split them.
                        for segment in paragraph.split('+++'):
                            if has_name(segment, row['name']) and FUND.search(segment):
                                hits.append(dict(item, title=clean(segment), text=''))
                                found = True
            if not found and has_name(item['title'], row['name']) and FUND.search(item['title']):
                hits.append(dict(item, text=''))
            elif not found:
                # A failed article fetch must not erase a funding-bearing excerpt.
                for segment in clean(item['text']).split('+++'):
                    if has_name(segment, row['name']) and FUND.search(segment):
                        hits.append(dict(item, title=clean(segment), text=''))
    home_ok, home_funding = False, None
    website = row.get('website', '').strip()
    if website and '://' not in website:
        website = 'https://' + website
    host = urllib.parse.urlsplit(website).hostname or ''
    if website and not any(host == domain or host.endswith('.' + domain) for domain in ('linkedin.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com')):
        response = fetch(website)
        _, visible = page(response['body'])
        home_ok = response['status'] == 200 and bool(visible) and not re.search(r'just a moment|verify you are human|access denied', visible, re.I)
        parked = bool(PARKED.search(visible))
        # Unreachability is the requested death proxy, not a legal insolvency claim.
        if response['status'] in (0, 404, 410) or parked:
            out['y_dead'] = '1'
        elif home_ok and not out['y_dead']:
            out['y_dead'] = '0'
        checks.append('homepage:' + ('PARKED' if parked else 'OK' if home_ok else 'ERROR_HTTP_' + str(response['status'])))
        match = re.search(r'backed by|funded by|\binvestors\b|\binvestoren\b', visible, re.I)
        inv = investor(visible)
        if home_ok and (match or inv):
            pos = match.start() if match else visible.casefold().find(inv.casefold())
            context = visible[max(0, pos-100):pos+350]
            if inv or not GRANT.search(context):
                home_funding = {'url': website, 'title': context, 'date': ''}
    else:
        checks.append('homepage:MISSING' if not website else 'homepage:SOCIAL_PROFILE')
    strong = []
    unique = set()
    for hit in hits:
        key = (normalize(hit['title'].rsplit(' - ', 1)[0]), hit['date'])
        if key in unique:
            continue
        unique.add(key)
        # Classify the headline/snippet only; never borrow investor names from sidebars.
        text = clean(hit['title'] + ' ' + hit.get('text', ''))
        # "X raises ... from Y" reports funding of X, not of investor Y.
        name_match = re.search(re.escape(normalize(row['name'])), normalize(hit['title']))
        if name_match:
            prefix = normalize(hit['title'])[:name_match.start()]
            if re.search(r'\braises?\b.{0,100}\bfrom\b', prefix) or re.search(r'\b(?:led|backed) by\s*$', prefix):
                continue
        institutional = bool(INSTITUTION.search(text) or investor(text))
        if GRANT.search(hit['title']) or (GRANT.search(text) and not institutional):
            continue
        if institutional and hit['date'] and not ambiguous_name:
            strong.append(hit)
        else:
            weak.append(hit)
    # A nearby headline with the same explicit EUR amount can be corroborated
    # by an institutional report of that round. Keep its earlier publication date.
    unresolved = []
    corroborated = []
    for hit in weak:
        amount = amount_eur(hit['title'])
        matching = [item for item in strong if hit['date'] and amount and
                    amount == amount_eur(item['title']) and
                    abs((dt.date.fromisoformat(hit['date']) - dt.date.fromisoformat(item['date'])).days) <= 14]
        if matching and not ambiguous_name:
            corroborated.append(dict(hit, title=hit['title'], corroboration=matching[0]))
        else:
            unresolved.append(hit)
    strong.extend(corroborated)
    weak = unresolved
    strong.sort(key=lambda item: item['date'])
    evidence = strong[0] if strong else home_funding or (weak[0] if weak else None)
    status = 'UNKNOWN'
    if strong:
        first = strong[0]
        if first.get('corroboration'):
            # Keep the institutional paragraph as evidence, while documenting
            # the earlier dated headline used to set the candidate round date.
            checks.append('corroborated_round:' + first['corroboration']['url'])
            checks.append('candidate_round:' + first['date'] + ':' + first['url'])
            evidence = first['corroboration']
        day = dt.date.fromisoformat(first['date'])
        out.update(y_round_date=first['date'], y_round_type=round_type(first['title']), y_amount_eur=amount_eur(first['title']))
        # An earlier unresolved hit prevents claiming that a later known round was first.
        earlier_weak = any(not hit['date'] or hit['date'] < first['date'] for hit in weak)
        later_series = out['y_round_type'].startswith('SERIES_') or bool(re.search(r'follow[- ]?on|weitere|erneut|another|growth round', first['title'], re.I))
        if day <= asof:
            status = 'FUNDED_BEFORE'
        elif earlier_weak or later_series:
            review = True
        else:
            out['y_label'] = '1' if day <= add_months(asof, 18) else '0'
            status = 'FUNDED_WITHIN' if out['y_label'] == '1' else 'FUNDED_LATER'
        # One headline remains reviewable even if it explicitly identifies a round.
        review |= len(strong) == 1
        # Publication dates can differ across reporting; retain the earliest.
        review |= len({item['date'] for item in strong if abs((dt.date.fromisoformat(item['date']) - day).days) <= 30}) > 1
    elif home_funding or weak:
        review = True
    elif all_news_ok and ds_ok and home_ok:
        status = 'NO_FUNDING_FOUND'
        out['y_label'] = '0'
    else:
        review = True
    if asof > CUTOFF:
        out['y_label'], status = '', 'IMMATURE'
    if evidence:
        out['evidence_url'] = evidence['url']
        out['evidence_quote'] = clean(evidence['title'])
    out.update(funding_status=status, checked_sources=';'.join(checks), needs_review=str(int(review)))
    return out


def main():
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument('cohort', type=Path)
    arg.add_argument('--workers', type=int, default=8)
    args = arg.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    with args.cohort.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    output = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(label, row): i for i, row in enumerate(rows)}
        for done, future in enumerate(as_completed(futures), 1):
            i = futures[future]
            try:
                output[i] = future.result()
            except Exception as exc:
                output[i] = dict.fromkeys(FIELDS, '')
                output[i].update({key: rows[i].get(key, '') for key in ('name', 'website', 'asof_date')})
                output[i].update(funding_status='IMMATURE' if rows[i].get('asof_date', '') > CUTOFF.isoformat() else 'UNKNOWN', needs_review='1', checked_sources='ERROR:' + str(exc))
            if done % 10 == 0 or done == len(rows):
                print(f'Checked {done}/{len(rows)}', flush=True)
    target = ROOT / 'raw' / ('labels_' + args.cohort.stem + '.csv')
    with target.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output)
    counts = {value or 'empty': sum(row['y_label'] == value for row in output) for value in ('1', '0', '')}
    print(f'Wrote {target}: {len(output)} rows; labels {json.dumps(counts)}')
    for column in FIELDS:
        missing = sum(row[column] == '' for row in output)
        print(f'  {column}: {missing / len(output) * 100 if output else 0:.1f}% missing')
    print('Example positives (up to 10):')
    for row in [item for item in output if item['y_label'] == '1'][:10]:
        print(json.dumps({key: row[key] for key in ('name', 'asof_date', 'y_round_date', 'evidence_quote', 'evidence_url', 'needs_review')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
