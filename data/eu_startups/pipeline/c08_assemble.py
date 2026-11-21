import pandas as pd, numpy as np, sys, json, os
sys.path.insert(0,'/home/claude/pipeline'); from lib import *
CNAME={'Germany':'DE','United Kingdom':'GB','France':'FR','Spain':'ES','Italy':'IT','Netherlands':'NL','Norway':'NO','Switzerland':'CH','Austria':'AT','Finland':'FI','Sweden':'SE','Belgium':'BE','Denmark':'DK','Ukraine':'UA','Ireland':'IE','Portugal':'PT','Poland':'PL','Estonia':'EE','Lithuania':'LT','Latvia':'LV','Czech Republic':'CZ','Czechia':'CZ','Greece':'GR','Hungary':'HU','Romania':'RO','Bulgaria':'BG','Croatia':'HR','Slovenia':'SI','Slovakia':'SK','Luxembourg':'LU','Iceland':'IS','Malta':'MT','Cyprus':'CY','Turkey':'TR','Serbia':'RS'}
EUROPE=set(CNAME.values())|{'EU_UNKNOWN'}
# ---------- round-level news data (Fliar samples) ----------
fl=pd.concat([pd.read_parquet('/home/claude/data/raw/fliar/'+f) for f in ['fliarbi_global-startup-funding.parquet','HarshitKZ_uk-startup-funding-2019-2026.parquet']]).drop_duplicates('round_id')
fl['d']=pd.to_datetime(fl.announced_date); fl['cc']=fl.country.map(CNAME); fl['nn']=fl.company_name.map(norm_name)
def stg(s): return 'Grant' if s=='Grant' else ('Debt' if s in ('Debt','Venture debt') else s)
R_dom={}; R_nn={}
for r in fl.itertuples():
    t=(r.d,stg(r.stage),r.amount_usd,r.news_url or r.fliar_profile_url)
    R_dom.setdefault(str(r.company_domain).lower().replace('www.',''),[]).append(t)
    if isinstance(r.cc,str): R_nn.setdefault((r.nn,r.cc),[]).append(t)
# ---------- Crunchbase cohort ----------
cb_all=pd.read_parquet('/home/claude/data/cohorts/cb_all.parquet')
cbc=cb_all[cb_all.eligible.str.startswith('yes')].copy()
cbc['cohort_source']='Crunchbase young-startup cohort (founded '+cbc.fy.astype(int).astype(str)+')'
cbc['comp_placement']=np.nan; cbc['program_type']='none'; cbc['program_evidence_url']=None
# ---------- Program cohorts ----------
prog=pd.read_csv('/home/claude/data/cohorts/programs_matched.csv',dtype=str)
fw=pd.read_csv('/home/claude/data/cohorts/fw_raw.csv',dtype=str); fw=fw[fw.name!='Simon Gröblacher']
fw.loc[fw.name.isin(['Qphox','Reyedar','traceless materials']),'comp_placement']='2'
cbe=pd.read_parquet('/home/claude/data/cb_europe.parquet')
cbe_idx=cbe.set_index('id')
cbe['nn']=cbe.name.map(norm_name)
fwm=fw.assign(nn=fw.name.map(norm_name)).merge(cbe[['id','nn','country']].rename(columns={'country':'cbc'}),on='nn',how='left')
fwm=fwm[fwm.cbc.notna()].drop_duplicates('name')
fwm['country']=fwm.country.fillna(fwm.cbc); fwm=fwm.rename(columns={'id':'cb_id'}).drop(columns=['cbc','nn','country_raw']); fwm['match_method']='name(+country from CB)'
prog=pd.concat([prog,fwm],ignore_index=True)
# UK Companies House filing signals
chf={}
p='/home/claude/data/raw/chfh/filings.jsonl'
if os.path.exists(p):
    for l in open(p):
        j=json.loads(l); chf[j['crn']]=j['filings']
def ch_signal(crn,asof,win):
    f=chf.get(crn)
    if f is None: return None
    sh=[x for x in f if x['type'] in ('SH01',)]
    art=[x for x in f if x['type'] in ('RESOLUTIONS','MA','SH08','SH10','RES01')]
    def round_dates(lo,hi):
        out=[]
        for s in sh:
            d=pd.Timestamp(s['date'])
            if lo<d<=hi and any(abs((pd.Timestamp(a['date'])-d).days)<=60 for a in art): out.append(d)
        return sorted(out)
    pre=round_dates(pd.Timestamp('1990-01-01'),asof); post=round_dates(asof,win)
    return dict(ch_rounds_pre=len(pre),ch_rounds_window=len(post),ch_first_round=str(post[0].date()) if post else None,
                ch_sh01_window=sum(1 for s in sh if asof<pd.Timestamp(s['date'])<=win))
rows=[]
for r in prog.to_dict('records'):
    asof=pd.Timestamp(r['asof_date']); win=asof+pd.DateOffset(months=24)
    cbrow=cbe_idx.loc[r['cb_id']].to_dict() if isinstance(r.get('cb_id'),str) and r['cb_id'] in cbe_idx.index else None
    extra=None
    dom=r.get('domain') if isinstance(r.get('domain'),str) else (cbrow.get('domain') if cbrow else None)
    if isinstance(dom,str): extra=R_dom.get(dom)
    if not extra: extra=R_nn.get((norm_name(r['name']),r.get('country')))
    if cbrow:
        f=funding_fields(cbrow,asof,win,extra_rounds=extra); f['funding_source']='crunchbase_2024'
        for k in ['id','url','categories','founded_on','website','short_description','operating_status','n_rounds','funding_total_usd','last_funding_type','last_funding_at']: r['cb_'+k]=cbrow.get(k)
        if not isinstance(r.get('domain'),str): r['domain']=cbrow.get('domain')
    else:
        f=dict(pre_asof_funding_eur=np.nan,pre_known=0,label=np.nan,label_complete=int(win<=pd.Timestamp('2024-08-15')),first_round_date=None,label_basis='',label_evidence_url=None,funding_source='none')
        if extra:
            post=sorted([x for x in extra if asof<x[0]<=win and x[1] not in ('Grant','Debt')])
            if post: f.update(label=1,first_round_date=str(post[0][0].date()),label_basis='news_round_data',label_evidence_url=post[0][3])
    # UK filing-based signal
    if isinstance(r.get('crn'),str):
        s=ch_signal(r['crn'],asof,win)
        if s:
            r.update(s)
            if pd.isna(f['label']) or (f['label']==0 and f['funding_source']=='none'):
                if s['ch_rounds_window']>0:
                    f.update(label=1,label_complete=1,first_round_date=s['ch_first_round'],label_basis='companies_house_SH01+articles',label_evidence_url=f"https://find-and-update.company-information.service.gov.uk/company/{r['crn']}/filing-history")
                else:
                    # filings are current to Nov 2026, so the 24-month window is fully observed for every 2021-2023 as-of date
                    f.update(label=0,label_complete=1,label_basis='companies_house_no_round_in_window')
            if f['funding_source']=='none' and s['ch_rounds_pre']==0:
                f.update(pre_asof_funding_eur=0.0,pre_known=1,funding_source='companies_house')
    # eligibility
    if f['pre_known'] and f['pre_asof_funding_eur']<=CAP_EUR: el='yes'
    elif f.get('pre_bound_ok'): el='yes_bounded'
    elif f['pre_known']: el='no_over_cap'
    else: el='unknown'
    r.update(f); r['eligible']=el; rows.append(r)
P=pd.DataFrame(rows)
P=P[P.country.isin(EUROPE)]
print('programs',len(P)); print(P.eligible.value_counts()); print(P.label_basis.value_counts())
# ---------- unify ----------
def tidy_cb(c):
    return pd.DataFrame({'name':c.name,'domain':c.domain,'country':c.country,'cohort_source':c.cohort_source,'asof_date':c.asof_date,
      'label':c.label,'label_complete':c.label_complete,'first_round_date':c.first_round_date,'label_evidence_url':c.label_evidence_url,'label_basis':c.label_basis,
      'comp_placement':c.comp_placement,'program_type':c.program_type,'program_evidence_url':c.program_evidence_url,
      'pre_asof_funding_eur':c.pre_asof_funding_eur,'eligible':c.eligible,'founded_year':c.fy,'categories':c.categories,'short_description':c.short_description,
      'cb_url':c.url,'cb_status_2024':c.operating_status,'cb_id':c.id})
A=tidy_cb(cbc)
B=pd.DataFrame({'name':P.name,'domain':P.domain,'country':P.country,'cohort_source':P.cohort_source,'asof_date':P.asof_date,'label':P.label,'label_complete':P.label_complete,
  'first_round_date':P.first_round_date,'label_evidence_url':P.label_evidence_url,'label_basis':P.label_basis,'comp_placement':pd.to_numeric(P.comp_placement),
  'program_type':P.program_type,'program_evidence_url':P.program_evidence_url,'pre_asof_funding_eur':P.pre_asof_funding_eur,'eligible':P.eligible,
  'founded_year':pd.to_datetime(P.get('cb_founded_on'),errors='coerce').dt.year.fillna(pd.to_datetime(P.get('incorporated'),errors='coerce').dt.year),
  'categories':P.get('cb_categories'),'short_description':P.get('cb_short_description'),'cb_url':P.get('cb_url'),'cb_status_2024':P.get('cb_operating_status'),'cb_id':P.get('cb_id'),
  'crn':P.get('crn'),'ch_status_2026':P.get('ch_status_2026'),'ch_sh01_window':P.get('ch_sh01_window'),'ch_rounds_window':P.get('ch_rounds_window'),'ch_rounds_pre':P.get('ch_rounds_pre'),'funding_source':P.get('funding_source'),'grant_amount':P.get('eic_grant_eur').fillna(P.get('iuk_award_gbp'))})
# companies in both: keep program row (has comp_placement) and drop the CB-cohort duplicate with same cb_id
A=A[~A.cb_id.isin(B.cb_id.dropna())]
U=pd.concat([B,A],ignore_index=True)
W='Artificial Intelligence|Machine Learning|SaaS|Software|FinTech|Biotechnology|Medical Device|Health Care|Robotics|Internet of Things|Cyber Security|Blockchain|Analytics|Big Data|Cloud|Developer|Enterprise Software|EdTech|E-Learning|Mobile Apps|Apps|Marketplace|Clean|Renewable|Energy|Electric Vehicle|Hardware|Semiconductor|Drones|Aerospace|3D|Augmented Reality|Virtual Reality|Gaming|Video Games|Payments|InsurTech|PropTech|Logistics|Supply Chain|Food Tech|AgTech|Agriculture|Life Science|Pharmaceutical|Therapeutics|Health Diagnostics|Digital Health|mHealth|Health Tech|Sustainability|Recycling|Mobility|Transportation|Autonomous|Sensor|Quantum|Nanotechnology|Battery|Smart Cities|GreenTech|CleanTech|Web3|Cryptocurrency|Information Technology|Internet|Platform|API|Computer Vision|Natural Language|Generative AI|Data|Biometrics|Wearables|Telehealth|Medical'
U['tech_core']=(U.program_type.ne('none')|U.categories.fillna('').str.contains(W,case=False)).astype(int)
U['open_source_hint']=U.categories.fillna('').str.contains('Open Source',case=False).astype(int)
U.insert(0,'id',['s%06d'%i for i in range(len(U))])
U.to_parquet('/home/claude/data/startups_all.parquet',index=False)
print('TOTAL',len(U)); print(U.eligible.value_counts()); print(pd.crosstab(U.asof_date.str[:4],U.label.fillna(-1)))
