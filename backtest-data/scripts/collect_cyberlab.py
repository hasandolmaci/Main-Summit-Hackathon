"""CyberLab alumni cohorts from the page's year tabs (2019 through 2025).

All tabs are server-rendered; no JS pagination or embedded startup JSON is used.
Company identity is in image alt text, logo filenames, or Content-Disposition
on TYPO3 dumpFile images. HEAD requests recover those names without downloading
image bodies. Logo-derived names retain their evidence in name_basis. Company
URLs exclude social and magazine links. Descriptions exist only for highlighted
startups on this source; unknown descriptions remain empty. Do not substitute a
current company homepage description for historical cohort evidence.
The year-tab label determines asof_date; quarter-like logo filenames do not.
Run: python -B scripts/collect_cyberlab.py [--no-disk-cache]
"""
import sys
sys.dont_write_bytecode = True
from collect_hightechxl import Fetcher, Tree, first, own_url, row, output
import json
import re
import urllib.parse

BASE = 'https://www.cyberlab-karlsruhe.de/en/our-startups'


def host(url):
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix('www.')


def logo_name(filename):
    name = urllib.parse.unquote(filename)
    name = re.sub(r'\.(svg|png|jpe?g|webp)$', '', name, flags=re.I)
    name = re.sub(r'^csm_', '', name, flags=re.I)
    name = re.sub(r'_[0-9a-f]{10}$', '', name, flags=re.I)
    name = re.sub(r'^20\d{2}[_ -]+(?:Q[1-4][_ -]+)?', '', name, flags=re.I)
    name = re.sub(r'^(?:Startups?[_ -]+)?(?:Logo[_ -]+)', '', name, flags=re.I)
    name = re.sub(r'^CBL[_ -]+Logo[_ -]+', '', name, flags=re.I)
    name = re.sub(r'^png[_ -]+(?:blue[_ -]+)?', '', name, flags=re.I)
    name = re.sub(r'LogoMitSchriftzug.*$', '', name, flags=re.I)
    name = re.sub(r'[_ -]+Logo.*$', '', name, flags=re.I)
    name = re.split(r'[_ -]+(?:farbig\w*|neu\d*|bunt|png|RGB\w*|weiss\w*|black|schwarz\w*|color\w*|ohneSL|Wortmarke\w*|sr_version\d*|SVG|bildmarke\w*|alpha|sw)(?=[_ -]|$)', name, flags=re.I)[0]
    name = re.split(r'_+-_+|\s+-\s+', name)[0]
    name = name.strip('_ -')
    name = re.sub(r'[_ -]+\d+$', '', name)
    name = re.sub(r'[_ -]+', ' ', name).strip()
    name = re.sub(r'\s+final$', '', name, flags=re.I)
    if name.lower() == 'logosdac':
        name = 'SDAC'
    if name.lower() in ('logo', 'image', 'download', 'file', 'brand', ''):
        return ''
    return name


def collect():
    fetch = Fetcher('collect_cyberlab')
    tree = Tree(fetch.get(BASE)).root
    highlights = {}
    for block in tree.nodes(cls='cyberlab-ce-imagetext-startup'):
        heading = first(block, 'h3')
        paragraphs = [p.text() for p in block.nodes('p') if p.text() and p.text().lower() != 'learn more']
        # The first paragraph is an industry label; the subsequent ones describe the company.
        description = ' '.join(paragraphs[1:])
        for a in block.nodes('a'):
            url = own_url(a.attrs.get('href', ''))
            if url and heading:
                highlights[host(url)] = (heading.text(), description)
    rows, seen, unknown = [], set(), []
    tabs = [b for b in tree.nodes('button') if re.fullmatch(r'20(?:19|2[0-5])', b.text()) and b.attrs.get('role') == 'tab']
    if len(tabs) != 7:
        raise RuntimeError(f'Expected 7 year tabs, found {len(tabs)}')
    for button in tabs:
        year = button.text()
        pane = next((n for n in tree.nodes() if n.attrs.get('id') == button.attrs.get('aria-controls')), None)
        if pane is None:
            raise RuntimeError(f'No content for {year}')
        before = len(rows)
        for anchor in pane.nodes('a'):
            image = first(anchor, 'img')
            if image is None:
                continue
            image_url = image.attrs.get('src', '')
            alt = image.attrs.get('alt', '').strip()
            alt = re.sub(r'^Logo\s+(?:des\s+)?Startups?\s+', '', alt, flags=re.I)
            alt = re.sub(r'^Logo\s+(?:von\s+)?', '', alt, flags=re.I)
            alt = re.sub(r'\s+virstuelle Telefonassistenten$', '', alt, flags=re.I)
            website = own_url(anchor.attrs.get('href', ''))
            description = ''
            name, basis = '', ''
            if alt and alt.lower() not in ('logo', 'image'):
                name, basis = alt, 'logo alt text'
            filename = urllib.parse.urlsplit(image_url).path.rsplit('/', 1)[-1]
            if not name and 'dumpFile' in image_url:
                # TYPO3 HTML sometimes incorrectly prepends '/' to an absolute URL.
                url = urllib.parse.urljoin(BASE, image_url[1:] if image_url.startswith('/https://') else image_url)
                headers = json.loads(fetch.get(url, headers_only=True))
                disposition = next((v for k, v in headers.items() if k.lower() == 'content-disposition'), '')
                match = re.search(r'filename="([^"]+)"', disposition)
                filename = match[1] if match else ''
                name = logo_name(filename)
                basis = f'logo Content-Disposition filename: {filename}'
            elif not name:
                name = logo_name(filename)
                basis = f'logo filename: {filename}'
            if website and host(website) in highlights:
                name, description = highlights[host(website)]
                basis = 'highlighted startup heading (matched by company domain)'
            if not name and website:
                name = host(website).split('.')[0]
                basis = 'company domain (brand spelling unavailable)'
            if not name:
                unknown.append((year, image_url))
                continue
            key = (year, name.casefold(), website)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row(name=name, website=website, description=description,
                            source='cyberlab_karlsruhe', cohort=f'CyberLab alumni {year}',
                            asof_date=f'{year}-07-01', evidence_url=BASE + '#' + pane.attrs['id'],
                            date_basis='alumni year-tab label; year only', name_basis=basis))
        print(f'CyberLab {year}: {len(rows) - before}', flush=True)
    if unknown:
        raise RuntimeError(f'Unidentified logos require inspection: {unknown}')
    output('cyberlab', rows)


if __name__ == '__main__':
    collect()
