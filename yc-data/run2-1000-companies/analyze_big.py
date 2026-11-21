import json,glob,math,collections
from math import comb
grp={r['slug']:r['group'] for r in json.load(open('sample1000.json',encoding='utf-8'))}
def load(pattern):
    out={}
    for f in glob.glob(pattern):
        for l in open(f,encoding='utf-8'):
            l=l.strip()
            if l.startswith('{'):
                try: r=json.loads(l); out[r['slug']]=r
                except Exception: pass
    return out
sol=load('sol_out/*.jsonl'); sol.update(load('out_s01.jsonl'))
hai=load('out_h*.jsonl')
# 60-company run (different coding names)
old={}
for r in json.load(open('../research/coded.json',encoding='utf-8')):
    old[r['slug']]={'domain_expert':r.get('domain_expert'),'technical_founder':r.get('technical_founder'),'repeat_founder':r.get('repeat_founder'),
        'phd_or_researcher':r.get('phd'),'real_pivot':r.get('real_pivot'),'regulated_or_hardware':r.get('regulated_or_hardware')}
F=['domain_expert','technical_founder','business_founder','repeat_founder','prior_exit','phd_or_researcher','elite_school','big_tech_alum',
   'university_spinoff','regulated_or_hardware','b2b','deep_tech','launched_before_yc','paying_customers_before_yc','funding_before_yc',
   'real_pivot','non_us_hq','raised_after_demo_day']
def norm(v):
    v=str(v).strip().lower()
    return 'yes' if v in('yes','true') else 'no' if v in('no','false') else 'unknown'
merged={}; src=collections.Counter()
for slug in set(sol)|set(hai):
    if slug not in grp: continue
    m={'group':grp[slug]}
    for f in F:
        val='unknown'
        for s in (sol,hai,old):
            if slug in s and norm(s[slug].get(f))!='unknown': val=norm(s[slug].get(f)); break
        m[f]=val
    base=sol.get(slug) or hai.get(slug)
    m['founder_count']=base.get('founder_count'); m['failure_reasons']=base.get('failure_reasons') or []
    m['traces']=base.get('public_trace_types') or []
    m['solo']='yes' if isinstance(m['founder_count'],int) and m['founder_count']==1 else ('no' if isinstance(m['founder_count'],int) else 'unknown')
    m['has_trace']='yes' if [t for t in m['traces'] if t!='none_found'] else ('no' if m['traces'] else 'unknown')
    merged[slug]=m; src['sol' if slug in sol else 'haiku']+=1
json.dump(merged,open('merged.json','w',encoding='utf-8'),ensure_ascii=False)
G=collections.Counter(m['group'] for m in merged.values())
print('companies',len(merged),dict(G),'| main source',dict(src))
def fisher(a,b,c,d):
    n=a+b+c+d;r1=a+b;c1=a+c
    if n==0: return 1
    p=lambda x: comb(r1,x)*comb(n-r1,c1-x)/comb(n,c1)
    p0=p(a); return min(1,sum(p(x) for x in range(max(0,c1-(n-r1)),min(r1,c1)+1) if p(x)<=p0*(1+1e-9)))
def wil(k,n,z=1.96):
    if n==0: return (0,0)
    p=k/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;return (c-h,c+h)
rows=[]
for f in F+['solo','has_trace']:
    def cnt(groups):
        v=[m[f] for m in merged.values() if m['group'] in groups]; y=v.count('yes'); n=v.count('no'); return y,n,len(v)-y-n
    sy,sn,su=cnt(('top','other')); dy,dn,du=cnt(('inactive',)); ty,tn,tu=cnt(('top',))
    if sy+sn<10 or dy+dn<10: continue
    p=fisher(sy,sn,dy,dn)
    rows.append(dict(f=f,s=sy/(sy+sn),sn=sy+sn,su=su,d=dy/(dy+dn),dn=dy+dn,du=du,t=ty/(ty+tn) if ty+tn else None,tn=ty+tn,p=p))
rows.sort(key=lambda r:r['p']); m=len(rows)
for i,r in enumerate(rows): r['holm']=min(1,r['p']*(m-i))
for i in range(1,m): rows[i]['holm']=max(rows[i]['holm'],rows[i-1]['holm'])
json.dump(rows,open('results_rows.json','w'),indent=1)
for r in rows: print(f"{r['f']:28} surv {r['s']*100:4.0f}% (n={r['sn']:3}, unk {r['su']:3}) | dead {r['d']*100:4.0f}% (n={r['dn']:3}, unk {r['du']:3}) | top {'' if r['t'] is None else round(r['t']*100)}% | p={r['p']:.4f} holm={r['holm']:.3f}")
fr=collections.Counter(x for m in merged.values() if m['group']=='inactive' for x in set(m['failure_reasons']))
print('\nfailure reasons (dead, n=%d):'%G['inactive'],fr.most_common())
fc={g:[m['founder_count'] for m in merged.values() if m['group']==g and isinstance(m['founder_count'],int)] for g in G}
print('avg founders',{g:round(sum(v)/len(v),2) for g,v in fc.items() if v})
tr={g:collections.Counter(t for m in merged.values() if m['group']==g for t in set(m['traces'])) for g in G}
print('traces',{g:dict(v.most_common(6)) for g,v in tr.items()})
