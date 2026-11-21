import pandas as pd, duckdb, sys
sys.path.insert(0,'/home/claude/pipeline'); from lib import norm_name
d=pd.read_parquet('/home/claude/data/raw/iuk/iuk.parquet')
d['start']=pd.to_datetime(d['Project Start Date'],errors='coerce')
s=d[d.start.dt.year.between(2021,2023)&d['Enterprise Size'].astype(str).str.contains('Micro|Small|SME',case=False)&(d['Is Lead Participant']=='Yes')].copy()
s['CRN']=s.CRN.astype(str).str.strip().str.upper().str.zfill(8)
# one row per company: earliest 2021-2023 win = asof
s=s.sort_values('start').drop_duplicates('CRN',keep='first')
con=duckdb.connect()
crns=pd.DataFrame({'crn':s.CRN.unique()}); con.register('crns',crns)
ch=con.execute("""select trim("CompanyNumber") crn, CompanyName ch_name, IncorporationDate, CompanyStatus, "SICCode.SicText_1" sic1, "RegAddress.PostCode" pc
 from read_csv('/home/claude/data/raw/ch/BasicCompanyDataAsOneFile-2026-11-01.csv',header=true,all_varchar=true,ignore_errors=true) where trim("CompanyNumber") in (select crn from crns)""").fetchdf()
s=s.merge(ch,left_on='CRN',right_on='crn',how='left')
s['inc']=pd.to_datetime(s.IncorporationDate,format='%d/%m/%Y',errors='coerce')
s['age_y']=(s.start-s.inc).dt.days/365.25
print('winners',len(s),'found in CH (live)',s.inc.notna().mean())
young=s[(s.age_y<=5)|s.inc.isna()]
print('young (<=5y at win) or not-live-in-CH:',len(young), (young.inc.isna()).sum())
PL={'Grant for R&D':2,'Feasibility Studies':2,'Collaborative R&D':2,'Investment Accelerator':2,'Small Business Research Initiative':2}
out=pd.DataFrame({'name':young['Participant Name'].str.strip(),'domain':None,'country':'GB','cohort_source':'Innovate UK: '+young['Competition Title'].astype(str),
 'asof_date':young.start.dt.date.astype(str),'comp_placement':2,'program_type':'grant_competition',
 'program_evidence_url':'https://www.ukri.org/publications/innovate-uk-funded-projects-since-2004/','crn':young.CRN,'incorporated':young.inc.dt.date.astype(str),
 'ch_status_2026':young.CompanyStatus.fillna('not_in_live_register'),'sic1':young.sic1,'iuk_award_gbp':young['Award Offered (£)'],'iuk_product':young['Innovate UK Product Type']})
out.to_csv('/home/claude/data/cohorts/iuk.csv',index=False); print(len(out)); print(out.asof_date.str[:4].value_counts().to_dict())
