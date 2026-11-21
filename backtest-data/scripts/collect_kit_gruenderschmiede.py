"""KIT Gruenderschmiede / KIT FORGE portfolio, including all load-more results.

The requested legacy URL now redirects to KIT FORGE. The portfolio has no cohort
dates or dated internal profiles. Years in descriptions (e.g. product launches,
founding or financing) are not list-appearance dates. Keep asof_date empty; these
rows cannot be used as dated backtest cohorts without additional evidence.
Descriptions are current source snapshots, not verified historical features.
Run: python -B scripts/collect_kit_gruenderschmiede.py [--no-disk-cache]
"""
import sys
sys.dont_write_bytecode = True
from collect_hightechxl import Fetcher, Tree, first, own_url, row, output
import re
import urllib.parse

LEGACY = 'https://kit-gruenderschmiede.de/gruendungen/'


def collect():
    fetch = Fetcher('collect_kit_gruenderschmiede')
    tree = Tree(fetch.get(LEGACY)).root
    canonical = next((n.attrs.get('href') for n in tree.nodes('link') if n.attrs.get('rel') == 'canonical'), LEGACY)
    status = first(tree, cls='js-filter-status')
    total = re.search(r'(\d+)\s+Ergebnisse', status.text() if status else '')
    expected = int(total[1]) if total else None
    # The site's JS passes the load-more button name/value as a GET parameter;
    # this is a cumulative result limit, not an offset.
    button = next((b for b in tree.nodes('button') if b.attrs.get('name') == 'more[startups]'), None)
    evidence = canonical
    if button:
        limit = expected or 1000
        evidence = canonical + '?' + urllib.parse.urlencode({'more[startups]': limit})
        tree = Tree(fetch.get(evidence)).root
    cards = list(tree.nodes(cls='cardImageTeaser__item'))
    if expected is not None and len(cards) != expected:
        raise RuntimeError(f'Incomplete KIT pagination: {len(cards)}/{expected}')
    if any(b.attrs.get('name') == 'more[startups]' for b in tree.nodes('button')):
        raise RuntimeError('KIT load-more button remains; extraction incomplete')
    rows, seen = [], set()
    for card in cards:
        heading = first(card, cls='h5')
        if heading is None:
            raise RuntimeError('Unnamed KIT card')
        name = heading.text()
        description = first(card, cls='content')
        website = own_url(card.attrs.get('href', ''), canonical) if card.attrs.get('href') else ''
        if website and urllib.parse.urlsplit(website).netloc == urllib.parse.urlsplit(canonical).netloc:
            raise RuntimeError(f'New internal profile needs inspection: {website}')
        key = (name.casefold(), website)
        if key in seen:
            continue
        seen.add(key)
        rows.append(row(name=name, website=website, description=description.text() if description else '',
                        source='kit_gruenderschmiede', cohort='KIT FORGE portfolio (undated)',
                        evidence_url=evidence, name_basis='portfolio card heading'))
    print(f'KIT: {len(cards)} cards; no usable cohort dates; asof_date is empty throughout', flush=True)
    output('kit_gruenderschmiede', rows)


if __name__ == '__main__':
    collect()
