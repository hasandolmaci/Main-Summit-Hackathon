import pandas as pd, numpy as np, os, json
U=pd.read_parquet('/home/claude/data/startups_all.parquet')
REL=[('cc-main-2021-feb-apr-may','2021-05-31'),('cc-main-2021-22-oct-nov-jan','2022-01-31'),('cc-main-2022-may-jun-aug','2022-08-31'),('cc-main-2022-23-sep-nov-jan','2023-01-31')]
ranks={}
for rel,_ in REL:
    N=int(open(f'/home/claude/data/raw/cc/{rel}.total').read())
    t=pd.read_csv(f'/home/claude/data/raw/cc/{rel}.tsv',sep='\t',header=None,names=['domain','hc_pos','pr_pos'])
    ranks[rel]=(N,dict(zip(t.domain,t.hc_pos)))
def pick(asof):
    ok=[r for r,end in REL if end<asof]
    return (ok[-1],0) if ok else (REL[0][0],1)
dom=U.domain.str.lower().str.replace(r'^www\d?\.','',regex=True)
cr=[];rels=[];flag=[];ing=[]
for d,a in zip(dom,U.asof_date):
    rel,after=pick(a); rels.append(rel); flag.append(after)
    if not isinstance(d,str): cr.append(np.nan); ing.append(np.nan); continue
    N,m=ranks[rel]; pos=m.get(d)
    if pos is None: cr.append(0.0); ing.append(0)
    else: cr.append(round(100*(1-(pos-1)/N),3)); ing.append(1)
U['cc_rank_pct']=cr; U['cc_in_graph']=ing; U['cc_release']=rels; U['cc_release_after_asof']=flag
U.loc[U.domain.isna(),'cc_release']=None
# IP + GitHub columns: not filled in this build (sources blocked here); keep schema
for c in ['tm_any','tm_eu','pat_any','pat_count','ip_bundle','gh_ext_contrib','gh_commit_accel']: U[c]=np.nan
U['label']=U.label.astype('Int64'); U['comp_placement']=U.comp_placement.astype('Int64')
U['founded_year']=pd.to_numeric(U.founded_year,errors='coerce').astype('Int64')
U['train_ready']=((U.eligible.str.startswith('yes'))&U.label.notna()&(U.label_complete==1)&(U.tech_core==1)).astype(int)
cols=['id','name','domain','country','cohort_source','asof_date','label','first_round_date','label_evidence_url','comp_placement',
      'tm_any','tm_eu','pat_any','pat_count','ip_bundle','gh_ext_contrib','gh_commit_accel','cc_rank_pct',
      'train_ready','eligible','pre_asof_funding_eur','label_basis','label_complete','program_type','program_evidence_url','founded_year','categories','short_description',
      'tech_core','open_source_hint','cc_in_graph','cc_release','cc_release_after_asof','cb_url','cb_status_2024','crn','ch_status_2026','ch_sh01_window','grant_amount']
U=U[cols].drop_duplicates(['name','country','asof_date'],keep='first')
os.makedirs('/home/claude/out',exist_ok=True)
U.to_csv('/home/claude/out/eu_startups_2021_2023_all.csv',index=False)
T=U[U.train_ready==1]; T.to_csv('/home/claude/out/eu_startups_2021_2023_train.csv',index=False)
print('all',len(U),'train_ready',len(T)); print(T.groupby(T.asof_date.str[:4]).label.agg(['count','mean']))
print(T.program_type.value_counts()); print(T.country.value_counts().head(12)); print('cc coverage', T.cc_rank_pct.notna().mean(), (T.cc_in_graph==1).mean())
