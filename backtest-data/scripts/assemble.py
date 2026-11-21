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
class Keyed(dict):
    '''lookup by (normalised name, asof_date); falls back to name only when that name occurs once'''
    def __init__(self, rows, namekey='name'):
        super().__init__(); self.byname = {}
        for r in rows:
            n = norm(r.get(namekey)); self[(n, r.get('asof_date', ''))] = r; self.byname.setdefault(n, []).append(r)
    def find(self, n, asof):
        if (n, asof) in self: return self[(n, asof)]
        c = self.byname.get(n, [])
        return c[0] if len(c) == 1 and not c[0].get('asof_date') else ({} if len(c) != 1 else (c[0] if c[0].get('asof_date', '') == asof else {}))

cohort = load(f'{R}/cohort_short.csv')
labels = Keyed(load(f'{R}/labels_cohort_short.csv'))
feats = Keyed(load(f'{R}/features_cohort_short.csv'))
cb = {}
for p in glob.glob(f'{R}/crunchbase_rounds_*.csv'):
    for r in load(p):
        if 'WRONG' in r.get('check', ''): continue
        cb.setdefault(norm(r['company']), []).append(r)

feat_cols = [c for c in (next(iter(feats.values())).keys() if feats else []) if c.startswith('f_')]
EQ_TYPES = ('pre_seed', 'seed', 'series_', 'equity_crowdfunding')
out, seen_n, seen_d = [], set(), set()
for c in cohort:
    n, d = norm(c['name']), dom(c['website'])
    if n in seen_n or (d and d in seen_d): continue
    seen_n.add(n); seen_d.add(d) if d else None
    L, F, rounds = labels.find(n, c['asof_date']), feats.find(n, c['asof_date']), cb.get(n, [])
    eq = [r for r in rounds if any(str(r.get('round_type', '')).startswith(t) for t in EQ_TYPES) or 'accelerator' in r.get('check', '')]
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
    rows_ = load(path)
    if not rows_: continue
    extra = Keyed(rows_)
    ecols = [k for k in rows_[0].keys() if k not in ('name', 'website', 'asof_date')]
    for row in out:
        e = extra.find(row['id_company'], row['meta_asof_date'])
        for k in ecols: row[prefix + k] = e.get(k, '')
# the 7 priority metrics (m_*.csv, one row per company) joined by normalised name
for path, prefix in ((f'{R}/m_github.csv', 'm_'), (f'{R}/m_grants.csv', 'm_'), (f'{R}/m_patents.csv', 'm_'), (f'{R}/m_trademarks.csv', 'm_'),
                     (f'{R}/m_producthunt.csv', 'm_')):
    rows_ = load(path)
    if not rows_: continue
    extra = Keyed(rows_)
    ecols = [k for k in rows_[0].keys() if k not in ('name', 'website', 'asof_date')]
    for row in out:
        e = extra.find(row['id_company'], row['meta_asof_date'])
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
# --- usability fixes ---
LEAKY = ['f_description_cohort', 'e_in_htgf_portfolio', 'e_htgf_match_evidence', 'm_gh_stars_top_repo_now', 'm_gh_momentum_ratio', 'm_gh_momentum_score',
         'm_gh_commits_last3m', 'm_gh_commits_prev3m', 'f_hn_max_points', 'm_ph_launch_date_any', 'm_gh_top_repo']
audit = {(norm(r['name']), r.get('asof_date', '')): r for r in load(f'{R}/label_audit.csv')}
viol = 0
for row in out:
    a = row['meta_asof_date']
    # date checks: anything observed on/after as-of is blanked
    if row.get('f_snapshot_date') and row['f_snapshot_date'][:10] >= a:
        viol += 1
        for k in [k for k in row if k.startswith('f_') and k not in ('f_domain', 'f_first_capture', 'f_age_months', 'f_description_cohort')]: row[k] = ''
    for k in ('s_homepage_snapshot_ts', 's_impressum_snapshot_ts'):
        ts = row.get(k, '')
        if ts and ts[:4].isdigit() and (ts[:4] + '-' + ts[4:6] + '-' + ts[6:8] if len(ts) >= 8 and '-' not in ts[:8] else ts[:10]) >= a:
            viol += 1
            for kk in [kk for kk in row if kk.startswith('s_')]: row[kk] = ''
            break
    # label audit (LLM + rules): exclusions and confidence
    au = audit.get((row['id_company'], a))
    row['y_label_confident'] = ''
    if au:
        cs = au.get('corrected_status', 'keep')
        if cs.startswith('EXCLUDE'):
            row['y_label'], row['y_funding_status'] = '', cs
        elif cs == 'REVIEW':
            row['needs_review'] = '1'
        row['y_label_confident'] = '1' if (cs == 'keep' and au.get('confidence') == 'high') else '0'
    # label basis + dead-company sensitivity (primary label unchanged)
    st = row['y_funding_status']
    row['y_label_basis'] = {'FUNDED_WITHIN': 'news_dated_round', 'NO_FUNDING_FOUND': 'news_and_cb_nothing_found', 'FUNDED_LATER': 'news_round_after_18m'}.get(st, st.lower())
    row['y_label_incl_dead'] = row['y_label']
    if row['y_label'] == '' and st == 'UNKNOWN' and row.get('y_dead') == '1' and row.get('cb_has_equity_round') != '1':
        row['y_label_incl_dead'] = '0'
    for k in LEAKY:
        if k in row: row['info_' + k] = row.pop(k)
print('timestamp violations blanked:', viol)
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
print('feature columns', len(feat_cols), '| fill %:', {k: round(100 * sum(1 for r in out if r.get(k) not in ('', None)) / len(out)) for k in feat_cols if k in out[0]})
print('discovery leads', len(leads))
