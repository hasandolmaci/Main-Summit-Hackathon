import pandas as pd, re
D='/home/claude/data/raw/cordis/he/'
p=pd.read_csv(D+'project.csv',sep=';',dtype=str,on_bad_lines='skip')
o=pd.read_csv(D+'organization.csv',sep=';',dtype=str,on_bad_lines='skip')
w=p[p.topics.isin(['HORIZON-EIE-2022-SCALEUP-02-02','HORIZON-EIE-2021-SCALEUP-01-03'])].copy()
print(w.title.head(5).tolist())
w['asof_dt']=pd.to_datetime(w.ecSignatureDate.fillna(w.startDate),errors='coerce')
oo=o[o.projectID.isin(w.id)&(o.role=='coordinator')].merge(w[['id','asof_dt','topics']],left_on='projectID',right_on='id')
def dom(u):
    if not isinstance(u,str) or not u.strip(): return None
    u=u.strip().lower(); u=re.sub(r'^https?://','',u); u=u.split('/')[0]; u=re.sub(r'^www\d?\.','',u); return u or None
out=pd.DataFrame({'name':oo.name.str.strip(),'domain':oo.organizationURL.map(dom),'country':oo.country.replace({'EL':'GR','UK':'GB'}),
 'cohort_source':'Women TechEU '+oo.topics.str[8:12],'asof_date':oo.asof_dt.dt.date.astype(str),'comp_placement':2,'program_type':'grant_competition',
 'program_evidence_url':'https://cordis.europa.eu/project/id/'+oo.projectID,'sme':oo.SME})
out=out[out.asof_date.str[:4].isin(['2021','2022','2023'])]
out.to_csv('/home/claude/data/cohorts/wte.csv',index=False); print(len(out), out.country.value_counts().head(8).to_dict(), out.asof_date.str[:4].value_counts().to_dict())
