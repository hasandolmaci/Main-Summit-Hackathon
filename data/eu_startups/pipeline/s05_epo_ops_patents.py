"""Fill pat_any / pat_count from EPO OPS (free; register at https://developers.epo.org to get a key + secret).
Usage:  OPS_KEY=... OPS_SECRET=... python s05_epo_ops_patents.py eu_startups_2021_2023_all.csv
Counts publications where the applicant matches the company name and the publication date is before asof_date.
pat_count = distinct patent families. Stays within the free fair-use tier (about 1 request/second)."""
import os, sys, re, time, base64, requests, pandas as pd, xml.etree.ElementTree as ET
KEY, SEC = os.environ['OPS_KEY'], os.environ['OPS_SECRET']
LEGAL = r'\b(gmbh|ug|ag|ltd|limited|sas|sarl|sa|bv|nv|ab|oy|aps|as|asa|srl|spa|sl|inc|llc|plc|kg|se|oü)\b\.?'
def token():
    r = requests.post('https://ops.epo.org/3.2/auth/accesstoken', data={'grant_type': 'client_credentials'},
                      headers={'Authorization': 'Basic ' + base64.b64encode(f'{KEY}:{SEC}'.encode()).decode()})
    return r.json()['access_token']
TOK = token(); T0 = time.time()
NS = {'ops': 'http://ops.epo.org', 'ex': 'http://www.epo.org/exchange'}
def search(name, asof):
    global TOK, T0
    if time.time() - T0 > 1100: TOK = token(); T0 = time.time()
    q = f'pa="{name}" and pd<={asof.replace("-", "")}'
    fams, total, start = set(), 0, 1
    while True:
        r = requests.get('https://ops.epo.org/3.2/rest-services/published-data/search/biblio',
                         params={'q': q, 'Range': f'{start}-{start+99}'}, headers={'Authorization': f'Bearer {TOK}'})
        time.sleep(1.0)
        if r.status_code == 404: return 0, 0
        if r.status_code != 200: return None, None
        root = ET.fromstring(r.content)
        total = int(root.find('.//ops:biblio-search', NS).get('total-result-count'))
        for d in root.iter('{http://www.epo.org/exchange}exchange-document'): fams.add(d.get('family-id'))
        start += 100
        if start > min(total, 500): break
    return total, len(fams)
df = pd.read_csv(sys.argv[1], dtype=str)
out = []
for i, r in df.iterrows():
    n = re.sub(r'\s+', ' ', re.sub(LEGAL, '', r['name'], flags=re.I)).strip(' ,.-')
    pubs, fams = search(n, r['asof_date'])
    out.append((r['id'], None if pubs is None else int(pubs > 0), fams))
    if i % 200 == 0: print(i, len(df), flush=True)
res = pd.DataFrame(out, columns=['id', 'pat_any', 'pat_count'])
df = df.drop(columns=['pat_any', 'pat_count']).merge(res, on='id', how='left')
tm = pd.to_numeric(df.tm_any, errors='coerce'); pa = pd.to_numeric(df.pat_any, errors='coerce')
df['ip_bundle'] = (tm.fillna(0) + 2 * pa.fillna(0)).where(tm.notna() | pa.notna())   # 0 none, 1 TM only, 2 patent only, 3 both
df.to_csv(sys.argv[1].replace('.csv', '_with_patents.csv'), index=False)
