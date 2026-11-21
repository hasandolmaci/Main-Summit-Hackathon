import pandas as pd, numpy as np, sys
sys.path.insert(0,'/home/claude/pipeline'); from lib import *
cb=pd.read_parquet('/home/claude/data/cb_europe.parquet',columns=['id','name','domain','country','founded_on'])
cb['nn']=cb.name.map(norm_name); cb=cb[cb.nn.str.len()>=3]
EU=set('AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE'.split())
prog=pd.concat([pd.read_csv(f'/home/claude/data/cohorts/{f}.csv',dtype=str) for f in ['eic','wte','iuk']],ignore_index=True)
prog['pid']=range(len(prog)); prog['nn']=prog.name.map(norm_name)
# domain matches
dm=prog[prog.domain.notna()].merge(cb[cb.domain.notna()][['id','domain']],on='domain').drop_duplicates('pid')[['pid','id']].assign(match_method='domain')
# name matches
nm=prog[['pid','nn','country']].merge(cb[['id','nn','country','founded_on']].rename(columns={'country':'cbc'}),on='nn')
nm=nm[(nm.cbc==nm.country)|((nm.cbc=='EU_UNKNOWN')&nm.country.isin(EU))]
nm['fy']=pd.to_datetime(nm.founded_on,errors='coerce').dt.year
cnt=nm.groupby('pid').size()
uni=nm[nm.pid.isin(cnt[cnt==1].index)][['pid','id']].assign(match_method='name+country')
multi=nm[nm.pid.isin(cnt[cnt>1].index)&(nm.fy>=2010)]
c2=multi.groupby('pid').size(); uni2=multi[multi.pid.isin(c2[c2==1].index)][['pid','id']].assign(match_method='name+country+recent')
mm=pd.concat([dm,uni[~uni.pid.isin(dm.pid)],uni2[~uni2.pid.isin(dm.pid)]]).drop_duplicates('pid')
prog=prog.merge(mm,on='pid',how='left').rename(columns={'id':'cb_id'})
prog['src']=prog.cohort_source.str.split(':').str[0].str.replace(r' (ACC.*|20\d\d)$','',regex=True)
print(prog.groupby('src').cb_id.apply(lambda s:round(s.notna().mean(),3)), prog.match_method.value_counts(dropna=False))
prog.to_csv('/home/claude/data/cohorts/programs_matched.csv',index=False)
