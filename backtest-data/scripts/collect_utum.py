"""Cohorts from UnternehmerTUM Prototyping Grants (batches with years). as-of date = approximate batch date."""
import re, html, json, time, urllib.request, csv
UA = {'User-Agent': 'Mozilla/5.0'}
BATCHES = {17246: ('#1', '2022-03-01'), 17382: ('#2', '2022-07-01'), 17268: ('#3', '2022-11-01'),
           17377: ('#4', '2023-03-01'), 17452: ('#5', '2023-09-01'), 17578: ('#6', '2024-03-01'),
           17740: ('#7', '2024-09-01'), 17899: ('#8', '2025-03-01'), 109513: ('#9', '2025-09-01')}
rows = []
for bid, (label, asof) in BATCHES.items():
    u = f'https://funding.unternehmertum.de/supportedteams-2/?filter-block-53207%5B%5D={bid}'
    s = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read().decode('utf-8')
    for tile in s.split('<utum-tile')[1:]:
        n = re.search(r'<h2 class="fs-20">(.*?)</h2>', tile, re.S)
        if not n: continue
        d = re.search(r'<div class="fs-15 mt-15">(.*?)</div>', tile, re.S)
        link = re.search(r'href="(https?://[^"]+)"', tile)
        rows.append({'name': html.unescape(n.group(1)).strip(),
                     'website': link.group(1) if link else '',
                     'description': html.unescape(re.sub(r'<[^>]+>', ' ', d.group(1))).strip() if d else '',
                     'source': 'unternehmertum_prototyping_grant', 'cohort': 'Batch ' + label, 'asof_date': asof})
    time.sleep(1)
w = csv.DictWriter(open('raw/cohort_utum.csv', 'w', newline='', encoding='utf-8'), fieldnames=list(rows[0]))
w.writeheader(); w.writerows(rows)
print(len(rows), 'rows;', sum(1 for r in rows if r['website']), 'with website')
