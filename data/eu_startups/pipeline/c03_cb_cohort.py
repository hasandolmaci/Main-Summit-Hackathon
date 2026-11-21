import pandas as pd, numpy as np, sys
sys.path.insert(0,'/home/claude/pipeline'); from lib import *
df=pd.read_parquet('/home/claude/data/cb_europe.parquet')
fl=pd.concat([pd.read_parquet('/home/claude/data/raw/fliar/'+f) for f in ['fliarbi_global-startup-funding.parquet','HarshitKZ_uk-startup-funding-2019-2026.parquet']]).drop_duplicates('round_id')
fl['d']=pd.to_datetime(fl.announced_date)
NONROUND={'Grant','Debt','Venture debt'}
rounds={}
for r in fl.itertuples():
    rounds.setdefault(r.company_domain.lower().replace('www.',''),[]).append((r.d, 'Grant' if r.stage in ('Grant',) else ('Debt' if r.stage in ('Debt','Venture debt') else r.stage), r.amount_usd, r.news_url or r.fliar_profile_url))
df['fy']=pd.to_datetime(df.founded_on,errors='coerce').dt.year
ASOF={2019:'2021-06-30',2020:'2021-06-30',2021:'2022-06-30',2022:'2023-06-30'}
c=df[df.fy.isin(ASOF)].copy()
c['asof_date']=c.fy.map(ASOF)
c=c[pd.to_datetime(c.founded_on)<=pd.to_datetime(c.asof_date)]
c['tech']=c.categories.map(is_tech)
c=c[c.tech]
SMALL={'pre_seed','angel','grant','non_equity_assistance','equity_crowdfunding','convertible_note','product_crowdfunding'}
rows=[]
for r in c.to_dict('records'):
    asof=pd.Timestamp(r['asof_date']); win=asof+pd.DateOffset(months=24)
    f=funding_fields(r,asof,win,extra_rounds=rounds.get(r['domain'] or '~'))
    # eligibility: <=800k EUR raised before asof
    if f['pre_known'] and f['pre_asof_funding_eur']<=CAP_EUR: elig='yes'
    elif f.get('pre_bound_ok'): elig='yes_bounded'
    elif not f['pre_known'] and pd.notna(r['last_funding_at']) and pd.Timestamp(r['last_funding_at'])<=asof and r['last_funding_type'] in SMALL: elig='yes_undisclosed_small_stage'
    elif f['pre_known']: elig='no_over_cap'
    else: elig='unknown'
    r.update(f); r['eligible']=elig; rows.append(r)
o=pd.DataFrame(rows)
print('young tech EU in CB:',len(o)); print(o.eligible.value_counts())
o.to_parquet('/home/claude/data/cohorts/cb_all.parquet',index=False)
e=o[o.eligible.str.startswith('yes')]
print('eligible',len(e)); print(pd.crosstab(e.asof_date,e.label.fillna(-1)))
print(e.label_basis.value_counts())
