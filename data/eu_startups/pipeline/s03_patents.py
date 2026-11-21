# Patent counts per company published before asof_date, via Google Patents public search JSON (polite rate)
import pandas as pd, requests, json, re, os, time, sys, urllib.parse, concurrent.futures as cf
sys.path.insert(0,'/home/claude/pipeline'); from lib import norm_name
OUT='/home/claude/data/raw/pat/patents.jsonl'
U=pd.read_parquet('/home/claude/data/startups_all.parquet')
pri=U.program_type.ne('none')
deep=U.categories.fillna('').str.contains('Biotech|Medical Device|Hardware|Robotics|Manufacturing|Energy|Semiconductor|Pharma|Chemical|Materials|Industrial|Electronics|Clean|Quantum|Aerospace|Battery|Photonics|Nanotech|Agriculture|Life Science|Therapeutics|Automotive|3D|Sensor|Internet of Things',case=False)
order=pd.concat([U[pri],U[~pri&deep],U[~pri&~deep]])
done=set()
if os.path.exists(OUT):
    for l in open(OUT): done.add(json.loads(l)['id'])
LEGAL=r'\b(gmbh|ug|ag|ltd|limited|sas|sarl|sa|bv|b\.v\.|nv|ab|oy|aps|as|asa|srl|spa|sl|inc|llc|plc|kg|se|oü|ou|sp\. z o\.o\.|s\.r\.o\.|d\.o\.o\.|haftungsbeschränkt|\(haftungsbeschränkt\))\b\.?'
def qname(n):
    n=re.sub(LEGAL,'',n,flags=re.I); n=re.sub(r'[\"&()]',' ',n); n=re.sub(r'\s+',' ',n).strip(' ,.-')
    return n
H={'User-Agent':'Mozilla/5.0 (research dataset; low rate)'}
def run(r):
    n=qname(r['name'])
    if len(norm_name(n))<3: return {'id':r['id'],'status':'skip'}
    asof=r['asof_date'].replace('-','')
    url='https://patents.google.com/xhr/query?url='+urllib.parse.quote(f'assignee="{n}"&before=publication:{asof}&num=100')+'&exp='
    for a in range(4):
        try:
            x=requests.get(url,headers=H,timeout=25)
            if x.status_code==429: time.sleep(20*(a+1)); continue
            j=x.json()['results']; break
        except Exception as e: time.sleep(5)
    else: return {'id':r['id'],'status':'error'}
    target=norm_name(n); pubs=[]
    for c in j.get('cluster',[]):
        for it in c.get('result',[]):
            p=it.get('patent',{}); a=re.sub(r'<[^>]+>','',p.get('assignee','') or '')
            if norm_name(a).startswith(target) or target.startswith(norm_name(a)) and len(norm_name(a))>=4:
                pubs.append((p.get('publication_number'),p.get('priority_date'),p.get('publication_date'),a))
    time.sleep(0.2)
    return {'id':r['id'],'status':'ok','q':n,'n_pubs':len(pubs),'n_families':len({x[1] for x in pubs}),'ep_or_wo':sum(1 for x in pubs if str(x[0])[:2] in ('EP','WO')),'pubs':pubs[:20]}
todo=[r for r in order[['id','name','asof_date']].to_dict('records') if r['id'] not in done]
with open(OUT,'a') as f, cf.ThreadPoolExecutor(10) as ex:
    for i,res in enumerate(ex.map(run,todo)):
        f.write(json.dumps(res)+'\n'); f.flush()
        if i%500==0: print(i,len(todo),flush=True)
