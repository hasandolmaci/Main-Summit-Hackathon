"""Dated SpinLab startup bios, excluding mentor bios.

The /all HTML includes full bodies, company links and publication dates, so
profile requests are needed only when a listing body omits a company link.
Publication is the first-observed date available here, not a batch start date.
Descriptions are current source snapshots, not verified historical features.
Run: python -B scripts/collect_spinlab.py [--no-disk-cache]
"""
import sys
sys.dont_write_bytecode = True
from collect_hightechxl import Fetcher, Tree, Node, first, own_url, row, output
import datetime
import re
import urllib.parse

BASE = 'https://www.spinlab.co/portfolio/all'


def website_from(body):
    for a in body.nodes('a'):
        url = own_url(a.attrs.get('href', ''))
        host = urllib.parse.urlsplit(url).netloc.lower()
        if url and not host.endswith('spinlab.co'):
            return url
    return ''


def description_from(body):
    paragraphs = []
    for p in body.nodes('p'):
        text = p.text()
        # Exclude website calls-to-action, logos and HubSpot tracking markup.
        if not text or re.search(r'click here|visit .*website|^website', text, re.I):
            continue
        if len(text) > 15:
            paragraphs.append(text)
    if paragraphs:
        return ' '.join(paragraphs)
    # Several older bios use div/span text rather than paragraphs.
    return re.split(r'CLICK HERE|VISIT .*WEBSITE', body.text(), flags=re.I)[0].strip()


def collect():
    fetch = Fetcher('collect_spinlab')
    rows, seen, pages = [], set(), set()
    queue = [BASE]
    excluded = 0
    while queue:
        url = queue.pop(0)
        if url in pages:
            continue
        pages.add(url)
        tree = Tree(fetch.get(url)).root
        # HubSpot nests post-item wrappers; only the inner node owns its h2.
        posts = [n for n in tree.nodes('div', 'post-item')
                 if any(isinstance(c, Node) and c.tag == 'h2' for c in n.children)]
        if not posts:
            raise RuntimeError(f'No profile cards at {url}')
        for post in posts:
            topics = [a.attrs.get('href', '') for a in post.nodes('a', 'topic-link')]
            if any('mentor' in t.lower() for t in topics) or not any('startup-bios' in t for t in topics):
                excluded += 1
                continue
            heading = first(post, 'h2')
            profile = first(heading, 'a').attrs['href']
            if profile in seen:
                continue
            seen.add(profile)
            header = first(post, cls='post-header')
            stamp = re.search(r'\b([A-Z][a-z]{2} \d{1,2}, 20\d{2})\b', header.text() if header else '')
            date = datetime.datetime.strptime(stamp[1], '%b %d, %Y').date().isoformat() if stamp else ''
            if date and not 2019 <= int(date[:4]) <= 2025:
                excluded += 1
                continue
            body = first(post, cls='post-body')
            if body is None:
                raise RuntimeError(f'No body: {profile}')
            website = website_from(body)
            description = description_from(body)
            if not website:
                detail = Tree(fetch.get(profile)).root
                detail_body = first(detail, cls='post-body')
                if detail_body:
                    website = website_from(detail_body)
                    description = description_from(detail_body) or description
            rows.append(row(name=heading.text(), website=website, description=description,
                            source='spinlab_leipzig', cohort=f'SpinLab startup profile {date[:4]}' if date else 'SpinLab startup profile (undated)',
                            asof_date=date, evidence_url=profile,
                            date_basis='startup profile publication date' if date else '',
                            name_basis='startup profile heading'))
        # /all is unpaginated today; follow a real next-page link if that changes.
        for a in tree.nodes('a'):
            if 'next' in a.attrs.get('rel', '').split() or a.has('next-posts-link'):
                nxt = urllib.parse.urljoin(url, a.attrs.get('href', ''))
                if urllib.parse.urlsplit(nxt).netloc == urllib.parse.urlsplit(BASE).netloc and nxt not in pages:
                    queue.append(nxt)
    print(f'SpinLab: {len(pages)} listing page(s); {excluded} mentors/out-of-range entries excluded', flush=True)
    output('spinlab', rows)


if __name__ == '__main__':
    collect()
