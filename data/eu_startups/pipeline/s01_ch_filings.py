# Scrape public Companies House filing history for the Innovate UK cohort (polite: ~4 req/s)
import pandas as pd, re, json, time, os, requests, concurrent.futures as cf, sys
OUT='/home/claude/data/raw/chfh/filings.jsonl'
d=pd.read_csv('/home/claude/data/cohorts/iuk.csv',dtype=str)
done=set()
if os.path.exists(OUT):
    for l in open(OUT): done.add(json.loads(l)['crn'])
todo=[c for c in d.crn.dropna().unique() if c not in done]
H={'User-Agent':'Mozilla/5.0 (research; startup dataset for hackathon)'}
MON={m:i+1 for i,m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}
def parse(s):
    out=[]
    for r in re.findall(r'<tr>(.*?)</tr>',s,flags=re.S):
        t=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',r)).strip()
        m=re.match(r'(\d{2}) (\w{3}) (\d{4}) (\S+) (.*)',t)
        if m and m.group(2) in MON: out.append({'date':f'{m.group(3)}-{MON[m.group(2)]:02d}-{m.group(1)}','type':m.group(4),'desc':m.group(5)[:160]})
    return out
def get(crn):
    rows=[]; 
    for page in range(1,8):
        for attempt in range(3):
            try:
                r=requests.get(f'https://find-and-update.company-information.service.gov.uk/company/{crn}/filing-history?page={page}',headers=H,timeout=20)
                break
            except Exception: time.sleep(2)
        else: return {'crn':crn,'status':'error','filings':rows}
        if r.status_code==404: return {'crn':crn,'status':'404','filings':rows}
        if r.status_code==429: time.sleep(30); continue
        p=parse(r.text); rows+=p
        time.sleep(0.5)
        if not p or min(x['date'] for x in p)<'2017-01-01' or 'page='+str(page+1) not in r.text: break
    return {'crn':crn,'status':'ok','filings':rows}
with open(OUT,'a') as f, cf.ThreadPoolExecutor(8) as ex:
    for i,res in enumerate(ex.map(get,todo)):
        f.write(json.dumps(res)+'\n'); f.flush()
        if i%200==0: print(i,len(todo),flush=True)
print('done')
