import csv,json,pathlib,urllib.request,urllib.parse,hashlib,time,threading,concurrent.futures,re,html,sys
from html.parser import HTMLParser
ROOT=pathlib.Path(__file__).resolve().parent.parent
CACHE=ROOT/'raw/cache/v3_metrics_a2'
CACHE.mkdir(parents=True,exist_ok=True)
KEYS="name website country city legal_form register_id founded_year founders founder_count founder_technical founder_phd founder_industry_years_3plus founder_prior_vc_backed_or_exit star_academic funding_status funding_total_usd investors funding_evidence_url grants grant_score pricing_page impressum_or_imprint customer_logos_or_named_customers named_customers jobs_open hiring_roles product_live waitlist github_org github_open_source product_hunt trademark_filed patent_filed press_mentions_12m awards team_size_estimate is_consulting_or_agency sector one_line red_flags evidence_urls".split()
INPUT=[r for r in csv.DictReader(open(ROOT/'raw/v3_disc_all.csv',encoding='utf-8-sig')) if r['src_file']=='raw/v3_disc_A.csv'][25:50]
OUT=ROOT/'raw/v3_metrics_A2.csv'
locks={}; fails={}; guard=threading.Lock()
class Parse(HTMLParser):
 def __init__(self): super().__init__(); self.text=[]; self.links=[]; self.skip=0
 def handle_starttag(self,t,a):
  if t in ('script','style','noscript'): self.skip+=1
  d=dict(a)
  if t=='a' and d.get('href'): self.links.append(d['href'])
 def handle_endtag(self,t):
  if t in ('script','style','noscript'): self.skip=max(0,self.skip-1)
 def handle_data(self,d):
  if not self.skip and d.strip():self.text.append(d.strip())
def fetch(url):
 host=urllib.parse.urlsplit(url).netloc.lower()
 key=hashlib.sha256(url.encode()).hexdigest()
 file=CACHE/(key+'.json')
 if file.exists():return json.loads(file.read_text(encoding='utf-8'))
 with guard: lock=locks.setdefault(host,threading.Lock())
 with lock:
  if fails.get(host,0)>=3:return {'url':url,'error':'host skipped after 3 failures'}
  for attempt in range(3):
   try:
    time.sleep(.55)
    req=urllib.request.Request(url,headers={'User-Agent':'research script'})
    with urllib.request.urlopen(req,timeout=22) as res:
     raw=res.read(4000000); encoding=res.headers.get_content_charset() or 'utf-8'; final=res.url
    (CACHE/(key+'.html')).write_bytes(raw)
    parser=Parse();parser.feed(raw.decode(encoding,errors='replace'))
    data={'url':url,'final_url':final,'checked':'2026-11-21','text':' '.join(parser.text),'links':list(dict.fromkeys(urllib.parse.urljoin(final,x) for x in parser.links if x.startswith(('http','/'))))}
    file.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');return data
   except Exception as e:
    fails[host]=fails.get(host,0)+1
    if fails[host]>=3:break
    time.sleep(2**attempt)
  data={'url':url,'error':str(e) if 'e' in locals() else 'request failed','checked':'2026-11-21'}
  file.write_text(json.dumps(data),encoding='utf-8');return data
def save(rows):
 with open(OUT,'w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=KEYS);w.writeheader();w.writerows(rows)
def initial():
 rows=[]
 for r in INPUT:
  d=dict.fromkeys(KEYS); d.update({k:r[k] or None for k in ('name','website')});d['funding_status']='UNKNOWN'; rows.append(d)
 save(rows)
def crawl(r):
 d=fetch(r['website']); links=d.get('links',[])
 chosen=[]
 for pattern in ('impress|imprint|legal-notice','about|ueber|uber-uns|team|founder|gruender','pricing|preise|plans','career|karriere|jobs','customer|kunden|press|presse'):
  matched=[x for x in links if re.search(pattern,x,re.I) and urllib.parse.urlsplit(x).netloc==urllib.parse.urlsplit(d.get('final_url',r['website'])).netloc]
  chosen+=matched[:2]
 if not any(re.search('impress|imprint',x,re.I) for x in chosen):chosen.append(urllib.parse.urljoin(r['website'],'impressum'))
 pages=[d]+[fetch(u) for u in dict.fromkeys(chosen)]
 (CACHE/(re.sub(r'[^a-z0-9]','_',r['name'].lower())+'_pages.json')).write_text(json.dumps(pages,ensure_ascii=False),encoding='utf-8')
 return {'name':r['name'],'pages':[{'url':x['url'],'chars':len(x.get('text','')),'error':x.get('error')} for x in pages]}
if __name__=='__main__':
 if '--init' in sys.argv: initial()
 if '--crawl' in sys.argv:
  with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
   for d in ex.map(crawl,INPUT):print(json.dumps(d,ensure_ascii=False),flush=True)

def apply_updates(updates):
 rows=list(csv.DictReader(open(OUT,encoding='utf-8')))
 for d in rows:
  u=dict(updates.get(d['name'],{}))
  extra=u.pop('_evidence',[])
  note=u.pop('_note',None)
  d.update(u)
  if note:d['red_flags']=(d.get('red_flags') or '')+' '+note
  d['evidence_urls']=';'.join(dict.fromkeys([x for x in (d.get('evidence_urls') or '').split(';')+extra if x]))
 save(rows)
 old=json.loads((CACHE/'curated_updates.json').read_text(encoding='utf-8')) if (CACHE/'curated_updates.json').exists() else []
 old.append(updates);(CACHE/'curated_updates.json').write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')

class SearchForm(HTMLParser):
 def __init__(self):super().__init__();self.data={};self.action=None;self.active=False;self.select=None
 def handle_starttag(self,t,a):
  d=dict(a)
  if t=='form':self.active=d.get('id')=='form';self.action=d.get('action') if self.active else self.action
  if not self.active:return
  if t=='input' and d.get('name'):
   typ=d.get('type');n=d['name']
   if typ not in ['checkbox','radio','submit','image'] or 'checked' in d:self.data[n]=d.get('value','on' if typ in ['checkbox','radio'] else '')
  if t=='select':self.select=d.get('name')
  if t=='option' and self.select:
   if self.select not in self.data or 'selected' in d:self.data[self.select]=d.get('value','')
 def handle_endtag(self,t):
  if t=='form':self.active=False
  if t=='select':self.select=None

def dpma_search(name,kind='marke',owner=False):
 import http.cookiejar
 slug=re.sub('[^a-z0-9]','_',name.lower());path=CACHE/('dpma_'+kind+'_'+slug+'.json')
 if path.exists():return json.loads(path.read_text(encoding='utf-8'))
 url='https://register.dpma.de/DPMAregister/'+kind+'/basis'
 op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));op.addheaders=[('User-Agent','research script')]
 last_error=''
 for attempt in range(3):
  try:
   time.sleep(.6)
   with op.open(url,timeout=25) as res:raw=res.read()
   (CACHE/('dpma_form_'+kind+'_'+slug+'.html')).write_bytes(raw)
   form=SearchForm();form.feed(raw.decode('utf-8',errors='replace'))
   form.data['anmelder_erfinder' if kind=='pat' else ('inh' if owner else 'marke')]=name
   form.data['rechercheStarten']='Recherche starten'
   time.sleep(.6)
   with op.open(urllib.request.Request(form.action,data=urllib.parse.urlencode(form.data).encode()),timeout=25) as res:raw=res.read();final=res.url
   (CACHE/('dpma_'+kind+'_'+slug+'.html')).write_bytes(raw)
   p=Parse();p.feed(raw.decode('utf-8',errors='replace'));d={'query':name,'owner_search':owner,'url':final,'checked':'2026-11-21','text':' '.join(p.text),'links':p.links}
   path.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8');return d
  except Exception as exc:last_error=str(exc);time.sleep(2**attempt)
 d={'query':name,'url':url,'error':last_error};path.write_text(json.dumps(d),encoding='utf-8');return d
