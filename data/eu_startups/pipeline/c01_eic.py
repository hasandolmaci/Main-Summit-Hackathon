import pandas as pd, re
D='/home/claude/data/raw/cordis/he/'
p=pd.read_csv(D+'project.csv',sep=';',dtype=str,on_bad_lines='skip')
o=pd.read_csv(D+'organization.csv',sep=';',dtype=str,on_bad_lines='skip')
eic=p[p.fundingScheme.isin(['HORIZON-EIC-ACC','HORIZON-EIC-ACC-BF'])].copy()
eic['asof_dt']=pd.to_datetime(eic.ecSignatureDate.fillna(eic.startDate),errors='coerce')
eic=eic[eic.asof_dt.dt.year.between(2021,2023)]
oo=o[o.projectID.isin(eic.id)&(o.role=='coordinator')]
m=oo.merge(eic[['id','acronym','title','asof_dt','fundingScheme','ecMaxContribution','masterCall','startDate']],left_on='projectID',right_on='id')
EUROPE=set('AT BE BG HR CY CZ DK EE FI FR DE EL GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE UK GB CH NO IS LI TR RS ME MK AL BA MD UA XK FO'.split())
m=m[m.country.isin(EUROPE)]
def dom(u):
    if not isinstance(u,str) or not u.strip(): return None
    u=u.strip().lower(); u=re.sub(r'^https?://','',u); u=u.split('/')[0]; u=re.sub(r'^www\d?\.','',u); return u or None
out=pd.DataFrame({'name':m.name.str.strip(),'domain':m.organizationURL.map(dom),'country':m.country.replace({'EL':'GR','UK':'GB'}),
 'cohort_source':'EIC Accelerator '+m.fundingScheme.str.replace('HORIZON-EIC-',''),'asof_date':m.asof_dt.dt.date.astype(str),
 'comp_placement':2,'program_type':'grant_competition','program_evidence_url':'https://cordis.europa.eu/project/id/'+m.projectID,
 'sme':m.SME,'eic_grant_eur':m.ecContribution})
out=out.drop_duplicates(['name','asof_date'])
out.to_csv('/home/claude/data/cohorts/eic.csv',index=False)
print(len(out)); print(out.country.value_counts().head(15)); print(out.asof_date.str[:4].value_counts()); print(out.domain.isna().mean())
