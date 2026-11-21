"""Cohorts from Startup Incubator Berlin portfolio (year = cohort year). as-of = July 1 of that year."""
import re, html, time, urllib.request, csv
UA = {'User-Agent': 'Mozilla/5.0'}
s = open('raw/sib.html', encoding='utf-8').read()
cards = re.findall(r'<a href="(https://www\.startup-incubator\.berlin/startup/[^"]+)" class="su-card" data-cat="([^"]*)" data-year="(\d{4})" data-sector="([^"]*)" data-name="([^"]*)".*?data-tag="([^"]*)"', s, re.S)
rows = []
for url, cat, year, sector, name, tag in cards:
    if not (2019 <= int(year) <= 2025): continue
    site = ''
    try:
        p = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode('utf-8', 'ignore')
        links = [l for l in re.findall(r'href="(https?://[^"#]+)"', p)
                 if not re.search(r'startup-incubator\.berlin|hwr-berlin|facebook|instagram|linkedin|twitter|x\.com|youtube|xing|wordpress|w\.org|google|gstatic|yoast|berlin\.de|apple\.com|cookie|elementor|fonts', l)]
        site = links[0] if links else ''
    except Exception:
        pass
    rows.append({'name': html.unescape(name).strip(), 'website': site, 'description': html.unescape(tag).strip(),
                 'source': 'startup_incubator_berlin', 'cohort': f'{html.unescape(cat)} {year}', 'asof_date': f'{year}-07-01',
                 'sector': sector, 'profile_url': url})
    time.sleep(0.7)
w = csv.DictWriter(open('raw/cohort_sib.csv', 'w', newline='', encoding='utf-8'), fieldnames=list(rows[0]))
w.writeheader(); w.writerows(rows)
import collections
print(len(rows), 'rows;', sum(1 for r in rows if r['website']), 'with website;', collections.Counter(r['asof_date'][:4] for r in rows))
