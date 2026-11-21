"""Build eu_leads.csv for newly discovered European startups (live, today's view) and score with v2.

Inputs : raw/cohort_eu_live.csv, raw/features_cohort_eu_live.csv, raw/v3_disc_all.csv,
         raw/v3_metrics_A1.csv, raw/v3_metrics_A2.csv, api/met1/sol_out/*.jsonl (+ api/met2 if present)
Outputs: eu_leads.csv (backtest_v2.csv schema), reports/scored_eu.csv
User rule for this list: outside funding up to USD 500k passes the 'not funded' gate.
Shared scripts are not modified; sector lookup and source list are patched in memory only.
"""
import csv, glob, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import eligibility, peer_scoring, score_startups_v2 as ss  # noqa: E402

rd = lambda p: list(csv.DictReader(open(ROOT / p, encoding='utf-8')))
nm = lambda s: re.sub(r'[^a-z0-9]', '', (s or '').lower())
header = next(csv.reader(open(ROOT / 'backtest_v2.csv', encoding='utf-8')))
feats = {nm(r['name']): r for r in rd('raw/features_cohort_eu_live.csv')}
disc = {nm(r['name']): r for r in rd('raw/v3_disc_all.csv')}
mets = {}
for p in ('raw/v3_metrics_A1.csv', 'raw/v3_metrics_A2.csv'):
    if (ROOT / p).exists():
        for r in rd(p):
            mets[nm(r['name'])] = r
for p in sorted(glob.glob(str(ROOT / 'api/met*/sol_out/*.jsonl'))):
    for line in open(p, encoding='utf-8'):
        try:
            j = json.loads(line)
        except ValueError:
            continue
        mets.setdefault(nm(j.get('name')), {k: ('' if v is None else ';'.join(map(str, v)) if isinstance(v, list) else str(v)) for k, v in j.items()})

def v(m, k):
    x = str(m.get(k, '') or '').strip()
    return '' if x.lower() in ('null', 'none', 'nan', 'unknown') else x

def b(m, k):
    x = v(m, k).lower()
    return '1' if x in ('1', '1.0', 'true', 'yes') else '0' if x in ('0', '0.0', 'false', 'no') else ''

SECTOR = {'software/ai': 'digital_tech', 'hardware/deeptech': 'deep_tech', 'health': 'life_science',
          'energy/climate': 'deep_tech', 'other': 'unclear'}
EUROPE = {'germany', 'austria', 'switzerland', 'netherlands', 'belgium', 'denmark', 'sweden', 'finland', 'norway',
          'france', 'spain', 'italy', 'portugal', 'poland', 'lithuania', 'latvia', 'estonia', 'ireland', 'czechia',
          'czech republic', 'luxembourg', 'de', 'at', 'ch', 'nl', 'pl', 'lt', 'it', 'fr', 'es'}

rows, sectors, missing = [], {}, []
for c in rd('raw/cohort_eu_live.csv'):
    k = nm(c['name']); f = feats.get(k, {}); d = disc.get(k, {}); m = mets.get(k, {})
    if not m:
        missing.append(c['name'])
    r = {h: '' for h in header}
    fs = v(m, 'funding_status').upper()
    usd = v(d, 'funding_total_usd') or v(m, 'funding_total_usd')
    try:
        big = float(usd) > 500000
    except ValueError:
        big = False
    fund = 'FUNDED_BEFORE' if (fs.startswith('INSTITUTIONAL') or big) else \
           'NO_FUNDING_FOUND' if fs in ('NONE_FOUND', 'ANGEL_SMALL', 'PRESEED_LE_500K') or usd else 'UNKNOWN'
    country = (v(d, 'country') or v(m, 'country')).lower()
    r.update(id_company=k, id_domain=f.get('f_domain', ''), id_name=c['name'],
             meta_cohort_source='eu_disc_europe' if country in EUROPE else 'eu_disc_other',
             meta_cohort=f"EU live 2026-11 ({v(d, 'country') or v(m, 'country')})", meta_asof_date='21/11/2026',
             meta_sampling='live', y_funding_status=fund, y_dead='0')
    for kk, vv in f.items():
        if kk.startswith('f_') and kk in r:
            r[kk] = vv
    # website-observed facts from the metric pass fill gaps left by the archive-based feature script
    for col, src in (('f_pricing_page', 'pricing_page'), ('f_impressum', 'impressum_or_imprint'),
                     ('f_customer_logos', 'customer_logos_or_named_customers'), ('f_jobs_page', 'jobs_open'),
                     ('f_waitlist', 'waitlist'), ('f_product_live', 'product_live')):
        if col in r and b(m, src) == '1':
            r[col] = '1'
        elif col in r and r[col] == '' and b(m, src) == '0':
            r[col] = '0'
    founders = v(m, 'founders') or v(d, 'founders')
    cnt = v(m, 'founder_count') or (str(len([x for x in founders.split(';') if x.strip()])) if founders else '')
    lf = v(m, 'legal_form')
    put = {'s_founder_names': founders, 'm_founder_count': cnt, 'm_founder_technical': b(m, 'founder_technical'),
           'm_founder_phd': b(m, 'founder_phd'), 'm_founder_industry_exp': b(m, 'founder_industry_years_3plus'),
           'm_serial_score': b(m, 'founder_prior_vc_backed_or_exit'), 'm_star_academic': b(m, 'star_academic'),
           'm_grant_score': v(m, 'grant_score'), 'm_gh_has_open_source': b(m, 'github_open_source'),
           'm_ph_score_before_asof': '0.33' if b(m, 'product_hunt') == '1' else b(m, 'product_hunt'),
           'm_trademark_before_asof': b(m, 'trademark_filed'), 'm_patent_verified_before_asof': b(m, 'patent_filed'),
           's_legal_form': lf, 'f_gmbh_ig': '1' if 'i.g' in lf.lower() else ('0' if lf else ''),
           'f_legal_entity': lf if lf and lf.lower() not in ('other',) else ''}
    for col, val in put.items():
        if col in r and val != '':
            r[col] = val
    if 'f_description_text' in r and not r['f_description_text']:
        r['f_description_text'] = v(d, 'one_line') or v(m, 'one_line')
    consulting = b(m, 'is_consulting_or_agency') == '1'
    sectors[peer_scoring.normalise_name(c['name'])] = 'non_tech' if consulting else SECTOR.get(v(m, 'sector') or v(d, 'sector'), 'unclear')
    rows.append(r)

with open(ROOT / 'eu_leads.csv', 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=header); w.writeheader(); w.writerows(rows)
print(f'eu_leads.csv: {len(rows)} rows; without metric row: {len(missing)} {missing[:10]}')

eligibility.GERMAN_PROGRAMMES = eligibility.GERMAN_PROGRAMMES | {'eu_disc_europe'}
peer_scoring.sector_lookup = lambda *a, **k: sectors
sys.argv = ['score_startups_v2.py', '--in', str(ROOT / 'eu_leads.csv'), '--out', str(ROOT / 'reports/scored_eu.csv')]
ss.main()
