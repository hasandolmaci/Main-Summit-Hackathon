import re,html,pandas as pd
s=open('/home/claude/data/raw/src/fw.html',errors='ignore').read()
t=re.sub(r'<script.*?</script>|<style.*?</style>','',s,flags=re.S); t=re.sub(r'<[^>]+>','\n',t); t=html.unescape(t)
lines=[l.strip() for l in t.split('\n') if l.strip()]
COUNTRIES={'Germany':'DE','United Kingdom':'GB','France':'FR','Spain':'ES','Italy':'IT','Netherlands':'NL','Norway':'NO','Switzerland':'CH','Austria':'AT','Finland':'FI','Sweden':'SE','Belgium':'BE','Denmark':'DK','Ukraine':'UA'}
SKIP={'Our Winners','Previous','Next','Science Breakthrough of the Year 2023','Science Breakthrough of the Year 2022','Science Breakthrough of the Year 2021','Cluster Winner Sustainability','Cluster Winner Engineering of the Future','Cluster Winner Health Solutions'}
rows=[]
for yr in (2021,2022,2023):
    i=lines.index(f'Finalists {yr}'); j=lines.index('Previous',i)
    seg=lines[i+1:j]
    winners=set()
    for k,l in enumerate(seg):
        if l.startswith('presented by') or l.startswith(', presented by'):
            winners.add(seg[k-1].strip(', ').lower())
        if re.match(r'^(Science Breakthrough of the Year|Cluster Winner)',l) and k+1<len(seg) and l.endswith(':'): pass
    # names = lines that are not headers/countries/presenters
    k=0; names=[]
    while k<len(seg):
        l=seg[k]
        if l in SKIP or l.startswith('&') or l.startswith(',') or 'presented by' in l or l.endswith(':') or l.startswith('Science Breakthrough of the Year') or l.startswith('Cluster Winner'):
            k+=1; continue
        if l in COUNTRIES or l in ('Australia','Canada','Israel','Argentina','Japan','Hong Kong','United States of America','Egypt'): k+=1; continue
        ctry=seg[k+1] if k+1<len(seg) else None
        names.append((l, COUNTRIES.get(ctry) if ctry else None, ctry if ctry and (ctry in COUNTRIES or ctry in ('Australia','Canada','Israel','Argentina','Japan','Hong Kong','United States of America','Egypt')) else None)); k+=1
    seen=set()
    for n,c,raw in names:
        if n.lower() in seen: continue
        seen.add(n.lower())
        rows.append({'name':n,'country':c,'country_raw':raw,'cohort_source':f'Falling Walls Venture {yr}','asof_date':f'{yr}-11-07','comp_placement':2 if n.lower() in winners else 1,
                     'program_type':'pitch_competition','program_evidence_url':'https://falling-walls.com/activities/venture/finalists'})
d=pd.DataFrame(rows)
# drop presenter names (persons) accidentally captured
d=d[~d.name.str.match(r'^[A-Z][a-z]+ [A-Z][a-zü\-]+(-[A-Z][a-z]+)?$') | d.name.isin(['Puna Bio','Onego Bio','Proxima Fusion','Nomad Atomics','Carbon One','Kite Magnetics'])]
print(d.groupby('cohort_source').size(), d.comp_placement.value_counts()); print(d.to_string())
d.to_csv('/home/claude/data/cohorts/fw_raw.csv',index=False)
