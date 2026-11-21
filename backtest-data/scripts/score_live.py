"""Build live_leads.csv from the LinkedIn live cohort and score it (today's view).

Inputs : raw/cohort_linkedin_live.csv, raw/features_cohort_linkedin_live.csv, raw/live_metrics.csv
Outputs: live_leads.csv (backtest.csv schema), reports/scored_live.csv
Shared scripts are not modified: sector lookup and German-source list are patched in memory only.
"""
import csv, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import eligibility, peer_scoring, score_startups  # noqa: E402

rd = lambda p: list(csv.DictReader(open(ROOT / p, encoding='utf-8')))
header = next(csv.reader(open(ROOT / 'backtest.csv', encoding='utf-8')))
cohort = rd('raw/cohort_linkedin_live.csv')
feats = {r['name']: r for r in rd('raw/features_cohort_linkedin_live.csv')}
mets = {r['name']: r for r in rd('raw/live_metrics.csv')}

# Not German per evidence (foreign entity) -> german gate stays unknown/fail.
NOT_GERMAN = {'AI Data Defense', 'phablo.ai'}
SECTOR = {'software/ai': 'digital_tech', 'hardware/deeptech': 'deep_tech', 'health': 'life_science',
          'energy/climate': 'deep_tech', 'other': 'unclear'}
FUND = {'INSTITUTIONAL': 'FUNDED_BEFORE', 'NONE_FOUND': 'NO_FUNDING_FOUND', 'ANGEL_SMALL': 'NO_FUNDING_FOUND'}

rows, sectors, unmapped = [], {}, set()
for c in cohort:
    n, f, m = c['name'], feats.get(c['name'], {}), mets.get(c['name'], {})
    r = {k: '' for k in header}
    y, mo, d = c['asof_date'].split('-')
    r.update(id_company=peer_scoring.normalise_name(n), id_domain=f.get('f_domain', ''), id_name=n,
             meta_cohort_source='linkedin_live_de' if n not in NOT_GERMAN else 'linkedin_live_foreign',
             meta_cohort=c['cohort'], meta_asof_date=f'{d}/{mo}/{y}', meta_sampling='live',
             y_funding_status=FUND.get(m.get('funding_status', ''), 'UNKNOWN'), y_dead='0')
    for k, v in f.items():
        if k.startswith('f_'):
            (r.__setitem__(k, v) if k in r else unmapped.add(k))
    pairs = {'m_gh_has_open_source': 'gh_has_open_source', 'm_grant_score': 'grant_score',
             'm_serial_score': 'founder_serial_vc', 'm_founder_technical': 'founder_technical',
             'm_founder_industry_exp': 'founder_industry_exp', 's_founder_names': 'founder_names',
             'm_patent_verified_before_asof': 'patent_found', 'm_ph_score_before_asof': 'ph_launched',
             's_legal_form': 'legal_form'}
    for col, src in pairs.items():
        if col in r:
            r[col] = m.get(src, '')
        elif m.get(src, ''):
            unmapped.add(f'{col}<-{src}')
    sec = 'non_tech' if m.get('is_consulting_or_agency') == '1' else SECTOR.get(m.get('sector', ''), 'unclear')
    sectors[peer_scoring.normalise_name(n)] = sec
    rows.append(r)

with open(ROOT / 'live_leads.csv', 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=header)
    w.writeheader(); w.writerows(rows)
print(f'live_leads.csv: {len(rows)} rows; unmapped: {sorted(unmapped)}')

# In-memory patches only.
eligibility.GERMAN_PROGRAMMES = eligibility.GERMAN_PROGRAMMES | {'linkedin_live_de'}
peer_scoring.sector_lookup = lambda *a, **k: sectors
sys.argv = ['score_startups.py', '--in', str(ROOT / 'live_leads.csv'), '--out', str(ROOT / 'reports/scored_live.csv')]
score_startups.main()
