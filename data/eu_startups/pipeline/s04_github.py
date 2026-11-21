# Detect a company's GitHub org (domain-verified) and compute pre-asof activity metrics
import pandas as pd, requests, json, re, os, time, concurrent.futures as cf, threading
OUT='/home/claude/data/raw/gh/github.jsonl'; os.makedirs('/home/claude/data/raw/gh',exist_ok=True)
U=pd.read_parquet('/home/claude/data/startups_all.parquet')
U=U[U.domain.notna()]
U=pd.concat([U[U.program_type.ne('none')],U[U.program_type.eq('none')&U.label.notna()],U[U.program_type.eq('none')&U.label.isna()]])
done=set()
if os.path.exists(OUT):
    for l in open(OUT): done.add(json.loads(l)['id'])
S=requests.Session(); S.headers.update({'Accept':'application/vnd.github+json','User-Agent':'startup-dataset'})
if os.environ.get('GITHUB_TOKEN'): S.headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
lock=threading.Lock()
def get(url,params=None):
    for a in range(4):
        r=S.get(url,params=params,timeout=20)
        if r.status_code in (403,429) and 'rate' in r.text.lower():
            reset=int(r.headers.get('x-ratelimit-reset',time.time()+60)); time.sleep(max(5,min(900,reset-time.time()+2))); continue
        return r
    return r
def lastpage(r):
    m=re.search(r'[?&]page=(\d+)>; rel="last"',r.headers.get('link',''))
    return int(m.group(1)) if m else (len(r.json()) if r.status_code==200 and isinstance(r.json(),list) else 0)
def candidates(dom,name):
    stem=dom.split('.')[0]
    c=[stem, re.sub(r'[^a-z0-9-]','',name.lower().replace(' ','-')), re.sub(r'[^a-z0-9]','',name.lower()), stem+'-ai', stem+'hq']
    out=[]; [out.append(x) for x in c if x and x not in out]; return out[:3]
def run(r):
    dom=r['domain'].lower(); asof=pd.Timestamp(r['asof_date'])
    for cand in candidates(dom,r['name']):
        o=get(f'https://api.github.com/orgs/{cand}')
        if o.status_code!=200: continue
        j=o.json(); blog=(j.get('blog') or '').lower()
        if dom not in blog: continue
        res={'id':r['id'],'status':'found','org':cand,'org_created':j.get('created_at'),'public_repos_now':j.get('public_repos')}
        if pd.Timestamp(j['created_at']).tz_localize(None)>asof: res['org_created_after_asof']=1; return res
        repos=get(f'https://api.github.com/orgs/{cand}/repos',{'per_page':100,'sort':'pushed','type':'public'}).json()
        repos=[x for x in repos if isinstance(x,dict) and not x.get('fork') and pd.Timestamp(x['created_at']).tz_localize(None)<=asof][:5]
        mem={m['login'] for m in get(f'https://api.github.com/orgs/{cand}/public_members',{'per_page':100}).json() if isinstance(m,dict)}
        c_recent=c_prior=0; authors=set()
        w0=(asof-pd.DateOffset(months=12)).isoformat()+'Z'; w1=(asof-pd.DateOffset(months=6)).isoformat()+'Z'; w2=asof.isoformat()+'Z'
        for rp in repos:
            full=rp['full_name']
            a=get(f'https://api.github.com/repos/{full}/commits',{'since':w1,'until':w2,'per_page':1}); c_recent+=lastpage(a) if a.status_code==200 else 0
            b=get(f'https://api.github.com/repos/{full}/commits',{'since':w0,'until':w1,'per_page':1}); c_prior+=lastpage(b) if b.status_code==200 else 0
            l=get(f'https://api.github.com/repos/{full}/commits',{'since':w0,'until':w2,'per_page':100})
            if l.status_code==200:
                for c in l.json():
                    au=(c.get('author') or {}).get('login') or (c.get('commit',{}).get('author',{}) or {}).get('email')
                    if au and 'bot' not in str(au): authors.add(au)
        ext=len([a for a in authors if a not in mem]) if mem else None
        res.update(repos_pre_asof=len(repos),commits_6m=c_recent,commits_prev6m=c_prior,gh_commit_accel=round((c_recent+1)/(c_prior+1),3),
                   authors_12m=len(authors),gh_ext_contrib=ext,public_members=len(mem))
        return res
    return {'id':r['id'],'status':'none'}
todo=[r for r in U[['id','name','domain','asof_date']].to_dict('records') if r['id'] not in done]
with open(OUT,'a') as f, cf.ThreadPoolExecutor(6) as ex:
    for i,res in enumerate(ex.map(run,todo)):
        f.write(json.dumps(res)+'\n'); f.flush()
        if i%1000==0: print(i,len(todo),flush=True)
