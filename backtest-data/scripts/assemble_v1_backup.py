"""Merge cohort + labels + features + Crunchbase into backtest.csv; LinkedIn finds into discovery_leads.csv.
Dedupe by normalised name and domain. Unknown = empty."""
import csv, re, os, glob, json
from urllib.parse import urlparse
R = 'raw'
def norm(n): return re.sub(r'(gmbh|ug|ag|haftungsbeschrankt|haftungsbeschränkt|ev|inc|ltd|[^a-z0-9])', '', (n or '').lower())
def dom(u):
    if not u: return ''
    u = u if '://' in u else 'http://' + u
    d = urlparse(u).netloc.lower().split(':')[0]
    return d[4:] if d.startswith('www.') else d
def load(p): return list(csv.DictReader(open(p, encoding='utf-8'))) if os.path.exists(p) else []

cohort = load(f'{R}/cohort_short.csv')
labels = {norm(r['name']): r for r in load(f'{R}/labels_cohort_short.csv')}
feats = {norm(r.get('name')): r for r in load(f'{R}/features_cohort_short.csv')}
cb = {}
for p in glob.glob(f'{R}/crunchbase_rounds_*.csv'):
    for r in load(p):
        if 'WRONG' in r.get('check', ''): continue
        cb.setdefault(norm(r['company']), []).append(r)

feat_cols = [c for c in (next(iter(feats.values())).keys() if feats else []) if c.startswith('f_')]
out, seen_n, seen_d = [], set(), set()
for c in cohort:
    n, d = norm(c['name']), dom(c['website'])
    if n in seen_n or (d and d in seen_d): continue
    seen_n.add(n); seen_d.add(d) if d else None
    L, F, rounds = labels.get(n, {}), feats.get(n, {}), cb.get(n, [])
    eq = [r for r in rounds if str(r.get('is_equity')).lower() == 'true' or 'accelerator' in r.get('check', '')]
    y = L.get('y_label', '')
    status = L.get('funding_status', '')
    review = L.get('needs_review', '')
    if eq and y == '0':          # Crunchbase shows a round the news labeller missed -> date unknown -> exclude from training
        y, status, review = '', 'CB_ROUND_NO_DATE', '1'
    row = {'id_company': n, 'id_domain': d, 'id_name': c['name'], 'meta_cohort_source': c['source'], 'meta_cohort': c['cohort'],
           'meta_asof_date': c['asof_date'], 'meta_sampling': 'cohort', 'f_description_cohort': c.get('description', '')[:300]}
    for k in feat_cols: row[k] = F.get(k, '')
    row.update({'y_label': y, 'y_round_date': L.get('y_round_date', ''), 'y_round_type': L.get('y_round_type', ''),
                'y_amount_eur': L.get('y_amount_eur', ''), 'y_dead': L.get('y_dead', ''), 'y_funding_status': status,
                'cb_rounds': ' ; '.join(f"{r['round_type']}:{r.get('investors','')}" for r in rounds),
                'cb_has_equity_round': '1' if eq else ('0' if n in cb else ''),
                'needs_review': review, 'evidence_url': L.get('evidence_url', '')})
    out.append(row)
# optional enrichment files (joined by normalised name); prefix e_ to keep them apart from point-in-time features
for path, prefix in ((f'{R}/enrich_free.csv', 'e_'), (f'{R}/enrich_snapshot.csv', 's_')):
    extra = {norm(r.get('name')): r for r in load(path)}
    if not extra: continue
    ecols = [k for k in next(iter(extra.values())).keys() if k not in ('name', 'website', 'asof_date')]
    for row in out:
        e = extra.get(row['id_company'], {})
        for k in ecols: row[prefix + k] = e.get(k, '')
# the 7 priority metrics (m_*.csv, one row per company) joined by normalised name
for path, prefix in ((f'{R}/m_github.csv', 'm_'), (f'{R}/m_grants.csv', 'm_'), (f'{R}/m_patents.csv', 'm_'), (f'{R}/m_trademarks.csv', 'm_'),
                     (f'{R}/m_producthunt.csv', 'm_')):
    extra = {norm(r.get('name')): r for r in load(path)}
    if not extra: continue
    ecols = [k for k in next(iter(extra.values())).keys() if k not in ('name', 'website', 'asof_date')]
    for row in out:
        e = extra.get(row['id_company'], {})
        for k in ecols: row[prefix + k] = e.get(k, '')
# verified patents (Apify): patent before as-of = 1 only if first priority date < asof
pat = {norm(r['name']): r for r in load(f'{R}/m_patents_apify.csv')}
for row in out:
    p = pat.get(row['id_company'])
    if p and p['first_priority_date']:
        row['m_patent_verified_before_asof'] = '1' if p['first_priority_date'] < row['meta_asof_date'] else '0'
        row['m_patent_verified_first_priority'] = p['first_priority_date']
    else:
        row['m_patent_verified_before_asof'] = ''; row['m_patent_verified_first_priority'] = ''
# Product Hunt: 0 no launch, 0.33 launched, 0.66 top 10 of day, 1 top 3 - only launches before asof count
ph = {norm(r['name']): r for r in load(f'{R}/m_producthunt_raw.csv')}
for row in out:
    p = ph.get(row['id_company'])
    if p and p['launch_date'] and p['launch_date'] < row['meta_asof_date']:
        rk = int(p['day_rank']) if p['day_rank'] else 999
        row['m_ph_score_before_asof'] = 1 if rk <= 3 else 0.66 if rk <= 10 else 0.33
    else:
        row['m_ph_score_before_asof'] = 0
    row['m_ph_launch_date_any'] = p['launch_date'] if p else ''
# LinkedIn employees (TODAY's value, identity-verified by domain only) - NOT a training feature
emp = {norm(r['name']): r for r in load(f'{R}/m_employees.csv')}
for row in out:
    e = emp.get(row['id_company'], {})
    row['info_li_employees_now'] = e.get('li_employees_now', '') if e.get('li_match') == 'domain' else ''
    row['info_li_founded_year'] = e.get('li_founded_year', '') if e.get('li_match') == 'domain' else ''
# serial entrepreneur: per person -> company max
ser = {}
for r in load(f'{R}/m_serial.csv'):
    if r.get('match') != '1' or r.get('serial_score', '') == '': continue
    k = norm(r['company']); s = float(r['serial_score'])
    cur = ser.get(k, {'serial': -1, 'ind': 0, 'tech': 0})
    cur['serial'] = max(cur['serial'], s)
    cur['ind'] = max(cur['ind'], int(r.get('industry_experience') or 0)); cur['tech'] = max(cur['tech'], int(r.get('technical') or 0))
    ser[k] = cur
for row in out:
    s = ser.get(row['id_company'])
    row['m_serial_score'] = s['serial'] if s else ''
    row['m_founder_industry_exp'] = s['ind'] if s else ''
    row['m_founder_technical'] = s['tech'] if s else ''
cols = []
for r in out:
    for k in r:
        if k not in cols: cols.append(k)
w = csv.DictWriter(open('backtest.csv', 'w', newline='', encoding='utf-8'), fieldnames=cols); w.writeheader(); w.writerows(out)

# evidence: copy feature evidence + label evidence
ev = load(f'{R}/evidence_cohort_short.csv')
for r in out:
    if r['evidence_url']: ev.append({'name': r['id_name'], 'asof_date': r['meta_asof_date'], 'field': 'y_label', 'value': r['y_label'], 'url': r['evidence_url']})
if ev:
    keys = sorted({k for e in ev for k in e})
    w = csv.DictWriter(open('evidence.csv', 'w', newline='', encoding='utf-8'), fieldnames=keys); w.writeheader(); w.writerows(ev)

# discovery leads from LinkedIn (today's data, not for backtest), deduped against cohort + each other
allc = set()
for p in glob.glob(f'{R}/cohort_*.csv'):
    for r in load(p): allc.add(norm(r['name'])); allc.add(dom(r.get('website')))
leads, seen = [], set()
for r in load(f'{R}/linkedin_companies_startups.csv'):
    k = norm(r['name'])
    if k in allc or dom(r['website']) in allc or k in seen: continue
    seen.add(k); leads.append({'name': r['name'], 'website': r['website'], 'founded_year': r['founded_year'], 'employees': r['employee_count'],
                               'note': r['note'], 'founder_title': '', 'source': 'linkedin_company_search'})
for r in load(f'{R}/linkedin_founders.csv'):
    k = norm(r['company'])
    if r['startup_relevant'] == 'no' or k in allc or k in seen: continue
    seen.add(k); leads.append({'name': r['company'], 'website': '', 'founded_year': r['started_on'][:4], 'employees': '1-10',
                               'note': f"{r['industry']}; {r['city']}; {r['note']}", 'founder_title': r['founder_title'], 'source': 'linkedin_profile_search'})
w = csv.DictWriter(open('discovery_leads.csv', 'w', newline='', encoding='utf-8'), fieldnames=list(leads[0])); w.writeheader(); w.writerows(leads)

from collections import Counter
print('backtest rows', len(out), '| y_label', dict(Counter(r['y_label'] for r in out)), '| status', dict(Counter(r['y_funding_status'] for r in out)))
print('feature columns', len(feat_cols), '| fill %:', {k: round(100 * sum(1 for r in out if r[k] not in ('', None)) / len(out)) for k in feat_cols})
print('discovery leads', len(leads))
