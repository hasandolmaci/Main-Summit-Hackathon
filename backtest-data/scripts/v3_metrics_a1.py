"""Current public startup research. Stdlib only; raw responses cached, no credentials saved."""
import concurrent.futures, csv, hashlib, html.parser, json, pathlib, re, threading, time, urllib.request, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / 'raw/cache/v3_metrics_a1'
OUT = ROOT / 'raw/v3_metrics_A1.csv'
KEYS = 'name website country city legal_form register_id founded_year founders founder_count founder_technical founder_phd founder_industry_years_3plus founder_prior_vc_backed_or_exit star_academic funding_status funding_total_usd investors funding_evidence_url grants grant_score pricing_page impressum_or_imprint customer_logos_or_named_customers named_customers jobs_open hiring_roles product_live waitlist github_org github_open_source product_hunt trademark_filed patent_filed press_mentions_12m awards team_size_estimate is_consulting_or_agency sector one_line red_flags evidence_urls'.split()
COMPANIES = [r for r in csv.DictReader((ROOT/'raw/v3_disc_all.csv').open(encoding='utf-8-sig')) if r['src_file']=='raw/v3_disc_A.csv'][:25]
LOCK = threading.Lock()
HOSTS = {}

class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.text=[]; self.links=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag in ('script','style'): self.skip+=1
        if tag=='a' and a.get('href'): self.links.append(a['href'])
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip and data.strip(): self.text.append(data.strip())

def fetch(url):
    CACHE.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(url.encode()).hexdigest()
    path=CACHE/(key+'.json')
    if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
    host=urllib.parse.urlsplit(url).netloc
    with LOCK: state=HOSTS.setdefault(host,{'lock':threading.Lock(),'last':0,'fails':0})
    with state['lock']:
        if state['fails']>=3: return {'url':url,'error':'source skipped after 3 failures'}
        for attempt in range(3):
            time.sleep(max(0,0.55-(time.monotonic()-state['last'])))
            state['last']=time.monotonic()
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'research script'})
                with urllib.request.urlopen(req,timeout=18) as res:
                    raw=res.read(3500000); charset=res.headers.get_content_charset() or 'utf-8'; final=res.url
                (CACHE/(key+'.raw')).write_bytes(raw)
                body=raw.decode(charset,errors='replace')
                p=Page(); p.feed(body)
                result={'url':url,'final_url':final,'checked_date':'2026-11-21','text':'\n'.join(p.text),'links':list(dict.fromkeys(urllib.parse.urljoin(final,x) for x in p.links))}
                path.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8'); return result
            except Exception as e:
                state['fails']+=1
                if state['fails']>=3 or attempt==2:
                    result={'url':url,'error':type(e).__name__+': '+str(e)}
                    path.write_text(json.dumps(result),encoding='utf-8'); return result
                time.sleep(2**attempt)

def save(rows):
    with OUT.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=KEYS); w.writeheader(); w.writerows(rows)

def crawl(c):
    home=fetch(c['website']); pages=[home]
    links=home.get('links',[])
    patterns=[r'impress|imprint|legal-notice',r'about|ueber|über|team',r'pricing|preise|tarife',r'careers|karriere|jobs',r'customer|kunden|referenz',r'press|presse',r'product|shop|collection']
    host=urllib.parse.urlsplit(c['website']).netloc.removeprefix('www.')
    for pat in patterns:
        match=next((u for u in links if host in urllib.parse.urlsplit(u).netloc and re.search(pat,u,re.I) and u not in [p['url'] for p in pages]),None)
        if match: pages.append(fetch(match))
    if not any(re.search('impress|imprint',p['url'],re.I) for p in pages): pages.append(fetch(urllib.parse.urljoin(c['website'],'impressum')))
    for u in dict.fromkeys([c['discovery_source_url'],c['funding_evidence_url']]):
        if u: pages.append(fetch(u))
    (CACHE/(re.sub(r'[^a-z0-9]+','_',c['name'].lower())+'_pages.json')).write_text(json.dumps(pages,ensure_ascii=False),encoding='utf-8')
    return c['name'],[(p['url'],len(p.get('text','')),p.get('error','')) for p in pages]

def main():
    if not OUT.exists(): save([{k:c.get(k,'') if k in ('name','website') else '' for k in KEYS} for c in COMPANIES])
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for name,pages in pool.map(crawl,COMPANIES): print(json.dumps({'name':name,'pages':pages},ensure_ascii=False),flush=True)

# Analyst-reviewed facts; unknowns intentionally stay blank. All are LIVE observations.
REVIEWED = {
 'Solago': dict(legal_form='GmbH',register_id='Amtsgericht Düsseldorf HRB 98527',founder_count=3,jobs_open=1,hiring_roles='Reinigungskraft / Hauswirtschaftskraft;Design Lead – Creative Strategy;Performance Marketing Manager – Google Ads;Performance Marketing Manager – Meta Ads;Influencer Marketing Manager;Affiliate Marketing Manager;E-Commerce Manager',pricing_page=1,product_live=1,team_size_estimate='approximately 300 (2026-08-31)'),
 'Wellness Anarchy': dict(legal_form='other',founder_count=1,team_size_estimate=1,pricing_page=1,product_live=1,waitlist=1,trademark_filed=1,red_flags='Chaga and Tremella waitlisted;other products available. Trademark registration self-reported on company factsheet.'),
 'ImmerRuf': dict(legal_form='other',founder_count=1,team_size_estimate=2,pricing_page=1,product_live=1,jobs_open=1,hiring_roles='Vertriebspartner (m/w/d)'),
 'CV Analyz': dict(legal_form='other',founder_count=1,team_size_estimate=1,pricing_page=1,product_live=1),
 'Branchly': dict(legal_form='GmbH',register_id='Amtsgericht München HRB 278979',founders='Sebastian Flick;Markus Linnenberg',founder_count=2,founder_technical=1,founder_industry_years_3plus=1,pricing_page=1,product_live=1,grants='Founder grants (programme and amount undisclosed, 2025-09-17);BLM-funded news-assist project (company website)',grant_score=1,customer_logos_or_named_customers=1,named_customers='Radio Gong 2000 GmbH & Co. KG;Regensburg Tourismus GmbH;RIW Touristik GmbH;Stuttgart-Marketing GmbH;Weltbild D2C Group GmbH;IKB Innsbrucker Kommunalbetriebe AG;TANO Tourismus-Agentur Nordsee',funding_total_usd='',red_flags='Bootstrapped with grants;grant amount undisclosed. Prior E-Bot7 exit was as an employee, so founder prior exit remains unknown.'),
 'Memofluent': dict(legal_form='other',founder_count=1,team_size_estimate=1,founder_industry_years_3plus=1,pricing_page=1,product_live=1),
 'smart-job-match': dict(legal_form='other',founder_count=1,team_size_estimate=1,founder_technical=1,pricing_page=1,product_live=1,red_flags='Public jobs page is a job-search product, not company hiring.'),
 'PassTag Studio': dict(legal_form='other',founder_count=1,team_size_estimate=1,pricing_page=1,product_live=1),
 'Webmetic': dict(city='Oberhaching',legal_form='GmbH',register_id='Amtsgericht München HRB 298861',founder_count=1,pricing_page=1,product_live=1,customer_logos_or_named_customers=1,named_customers='freelance.de;Messe München;Condor',red_flags='Founding year conflict: interview introduction says 2024;founder says January 2025. Legal seat is Oberhaching.'),
 'Pinpap': dict(legal_form='other',founder_count=1,founder_technical=1,founder_industry_years_3plus=1,team_size_estimate=1,pricing_page=0,product_live=1),
 'watch.diy': dict(legal_form='GmbH',register_id='Amtsgericht Charlottenburg HRB 286771 B',founder_count=2,founder_phd=1,founder_technical=1,founder_industry_years_3plus=1,pricing_page=1,product_live=1,red_flags='Monitored retailers are data sources, not verified customers;paid pilot is sales-led.'),
 'Loggerfish': dict(legal_form='other',founder_count=1,team_size_estimate=1,pricing_page=1,product_live=1),
 'Law me': dict(founder_count=1,team_size_estimate='',pricing_page='',impressum_or_imprint='',product_live='',waitlist='',customer_logos_or_named_customers='',jobs_open='',red_flags='Website returns JavaScript shell;current pricing, legal operator and product access could not be verified.'),
 'CHILISHOT': dict(founders='Nikolaus Hartl',founder_count=1,legal_form='other',pricing_page=0,product_live=0,red_flags='Shop collection returned no purchasable products;current direct purchase availability unverified.'),
 'German Elite Fashion Academy UniFash': dict(city='Uettingen',legal_form='GmbH',register_id='Amtsgericht Würzburg HRB 17278',founders='Iris Peitzmeier;Francesco Roberto Valguarnera',founder_count=2,founder_phd=1,founder_technical=1,founder_industry_years_3plus=1,pricing_page=0,product_live=1,red_flags='Legal seat is Uettingen;AZAV/Bildungsgutschein supports course participants and is not evidence of a company startup grant.'),
 'PlantCam': dict(legal_form='UG',register_id='Amtsgericht Stuttgart HRB 799635',founder_count=1,pricing_page=1,product_live=1),
 'Plaudli': dict(country='Estonia',city='Tallinn',legal_form='other',register_id='Estonian commercial register 16216363',founders='Viktor Hendelmann',founder_count=1,team_size_estimate=1,pricing_page=1,product_live=1,customer_logos_or_named_customers=1,red_flags='Legal operator is Estonian OD. MEDIA & TECHNOLOGY SERVICES OÜ;input Germany/Berlin differs. Testimonials are consumer self-reports;personal names omitted.'),
 'HeyCarla': dict(legal_form='GmbH',register_id='Amtsgericht Stuttgart HRB 797605',founders='Talat Jashanica;Fetah Jusufi',founder_count=2,pricing_page=0,product_live=1,jobs_open=1,hiring_roles='Account Executive – B2B SaaS;Werkstudent Finanzen & Controlling;Junior Marketing Manager – B2B SaaS',customer_logos_or_named_customers=1,named_customers='Die Alltagsbegleiter;Kother Mobile;ATB Heil',grants='EXIST-Gründerstipendium (2024-10-01 to 2025-09-30);Startup BW Pre-Seed (confirmed 2025-08-26);OHA accelerator (since July 2025);The Sandbox',grant_score=1,funding_status='UNKNOWN',funding_total_usd='',investors='L-Bank (Startup BW Pre-Seed)',funding_evidence_url='https://heycarla.app/unternehmen/ueber-uns',awards='Hochschulpreis der Region Stuttgart (October 2025)',red_flags='Input bootstrap/zero-funding claim contradicted by company and regional sources confirming a first financing round and Startup BW Pre-Seed;individual amount undisclosed, so funding threshold classification unknown.'),
 'Rocket Routine': dict(city='Pfinztal',legal_form='GmbH',register_id='Amtsgericht Mannheim HRB 744796',founders='Sven O. Rimmelspacher',founder_count='',founder_technical=1,founder_industry_years_3plus=1,pricing_page=0,product_live=0,waitlist=1,team_size_estimate='',red_flags='Current Rocket Routine OS is in development with waitlist and pilots;legacy product directory may be stale. Complete founding team not verified;legal seat Pfinztal.'),
 'ZvgScout': dict(city='Quickborn',legal_form='UG',register_id='Amtsgericht Pinneberg HRB 15124 PI',founders='Tell Tobler',founder_count=1,pricing_page=1,product_live=1,red_flags='Input city/founder spelling corrected from current imprint.'),
 'Cryptohall24': dict(legal_form='GmbH',register_id='Amtsgericht Hamm HRB 11144 (Dürr Solutions);Amtsgericht Hamm HRB 11809 (Dürr Distribution);Amtsgericht Hamm HRB 11110 (Dürr Ventures, brand owner)',founder_count=1,pricing_page=1,product_live=1,team_size_estimate='approximately 15',red_flags='Brand spans multiple legal entities;hardware and hosting operators differ.'),
 'Sortie': dict(legal_form='other',founders='Heiko Raschl',founder_count=1,team_size_estimate=1,pricing_page=0,product_live=0,red_flags='Website says coming to iOS and Android;current public product access unverified. Founder name corrected to Heiko Raschl.'),
 'The Consumer AI': dict(legal_form='GmbH',register_id='Amtsgericht München HRB 307872',founders='Fabian Roschig;Ismail Reimchen',founder_count=2,team_size_estimate='',pricing_page=0,product_live=1,red_flags='Founding year conflict: current company About says 2026;June 2026 founder interview says founded end-2025.'),
 'SANOVIO': dict(legal_form='GmbH',register_id='Amtsgericht München HRB 297987',founder_count=3,pricing_page=0,product_live=1,jobs_open=1,hiring_roles='AI Engineering Intern;Forward Deployed Engineer;Founding Engineer;Software Engineering Intern (Full-Stack)',team_size_estimate='12 listed team members (2026-11-21)',red_flags='Product UI examples are demonstration data, not verified named customers.'),
 'dry Software': dict(legal_form='UG',register_id='Amtsgericht Straubing HRB 13494',founders='Simon Hofmann',founder_count=1,founder_technical=1,founder_industry_years_3plus=1,team_size_estimate=1,pricing_page=1,product_live=1,github_org='nut-tree',github_open_source=1,customer_logos_or_named_customers=1,named_customers='AUTORO Inc.;nele.ai;octologs.com',is_consulting_or_agency=1,red_flags='Software product plus explicit paid consulting services;historical founder-database prices are stale.'),
}

EXTRA_URLS = {
 'Wellness Anarchy':['https://wellness-anarchy.de/pages/ki-info'],
 'Branchly':['https://branchly.io/autoren/markus'],
 'Pinpap':['https://startupvalley.news/de/pinpap-kaufberatung-ki-technik/'],
 'CHILISHOT':['https://www.munich-startup.de/startups/deinflaschendesign/'],
 'German Elite Fashion Academy UniFash':['https://unifash.de/iris-peitzmeier'],
 'HeyCarla':['https://heycarla.app/unternehmen/talat-jashanica','https://heycarla.app/grunderstory','https://heycarla.app/unternehmen/ueber-uns','https://heycarla.app/neuigkeiten/exist-heycarla','https://www.startup-region-stuttgart.de/news/heycarla-stuttgarter-startup-digitalisiert-alltagshilfe-und-betreuungsdienste/'],
 'Rocket Routine':['https://www.rocket-routine.com/blog/author/sven-o-rimmelspacher','https://www.presseportal.de/pm/166207/5354975'],
 'The Consumer AI':['https://www.munich-startup.de/startups/the-consumer-ai/'],
 'SANOVIO':['https://www.munich-startup.de/startups/sanovio-gmbh/'],
 'dry Software':['https://nutjs.dev/blog/we-have-company','https://github.com/nut-tree/nut.js'],
}

def build():
    rows=[]
    for c in COMPANIES:
        slug=re.sub(r'[^a-z0-9]+','_',c['name'].lower())
        pages=json.loads((CACHE/(slug+'_pages.json')).read_text(encoding='utf-8'))
        row={k:'' for k in KEYS}
        for k in ('name','website','country','city','founded_year','founders','sector','one_line'):
            row[k]=c[k]
        row.update(funding_status='NONE_FOUND',funding_evidence_url=c['funding_evidence_url'],grant_score=0,pricing_page=0,impressum_or_imprint=1,customer_logos_or_named_customers=0,jobs_open=0,product_live=1,waitlist=0,is_consulting_or_agency=0)
        # Zero means no outside financing is reported; total undisclosed remains blank.
        text='\n'.join(p.get('text','') for p in pages)
        if re.search(r'ohne (?:Fremdkapital|Investoren)|no outside investors|without investors|without external capital',text,re.I): row['funding_total_usd']=0
        row['team_size_estimate']=c['team_size_estimate']
        row.update(REVIEWED[c['name']])
        row['red_flags']='Checked 2026-11-21. '+str(row['red_flags'] or '')
        urls=[p.get('final_url',p['url']) for p in pages if p.get('text') and len(p['text'])>100]
        row['evidence_urls']=';'.join(dict.fromkeys(urls+EXTRA_URLS.get(c['name'],[])))
        rows.append(row); save(rows)
    return rows

def github_audit():
    audit=[]
    for c in COMPANIES:
        q='"'+c['name']+'" type:org'
        url='https://api.github.com/search/users?'+urllib.parse.urlencode({'q':q,'per_page':5})
        r=fetch(url)
        if r.get('error'):
            audit.append({'name':c['name'],'error':r['error']})
            if 'skipped' in r['error']: break
        else:
            raw=CACHE/(hashlib.sha256(url.encode()).hexdigest()+'.raw')
            data=json.loads(raw.read_bytes())
            profiles=[]
            for item in data.get('items',[]):
                pr=fetch(item['url'])
                if not pr.get('error'):
                    pp=CACHE/(hashlib.sha256(item['url'].encode()).hexdigest()+'.raw')
                    j=json.loads(pp.read_bytes())
                    profiles.append({k:j.get(k) for k in ('login','html_url','blog','name','description','public_repos')})
            audit.append({'name':c['name'],'query_url':url,'total_count':data.get('total_count'),'profiles':profiles})
        (CACHE/'github_audit.json').write_text(json.dumps(audit,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(audit[-1],ensure_ascii=False),flush=True)
        time.sleep(7)

class Forms(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.values={}; self.action=''; self.select=None; self.options=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='form' and a.get('method')=='post': self.action=a['action']
        if tag=='input' and a.get('name'):
            typ=a.get('type','text')
            if typ in ('checkbox','radio') and 'checked' not in a: return
            if typ=='submit' and a['name']!='rechercheStarten': return
            if 'disabled' in a:return
            self.values[a['name']]=a.get('value','on' if typ=='checkbox' else '')
        if tag=='select': self.select=a.get('name');self.options=[]
        if tag=='option' and self.select:
            self.options.append(a.get('value',''))
            if 'selected' in a:self.values[self.select]=a.get('value','')
    def handle_endtag(self,tag):
        if tag=='select' and self.select:
            if self.select not in self.values and self.options:self.values[self.select]=self.options[0]
            self.select=None

def dpma_audit():
    import http.cookiejar
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    audit=[]; failures=0
    for c in COMPANIES:
        if failures>=3: break
        try:
            base='https://register.dpma.de/DPMAregister/marke/einsteiger'
            with opener.open(urllib.request.Request(base,headers={'User-Agent':'research script'}),timeout=20) as res: raw=res.read(); body=raw.decode('utf-8','replace')
            form=Forms();form.feed(body)
            form.values['marke']=c['name'] if c['name']!='German Elite Fashion Academy UniFash' else 'UniFash'
            form.values.pop('smartsearchQueryString',None);form.values.pop('smartsearchQueryString_kl',None)
            time.sleep(0.6)
            req=urllib.request.Request(form.action,data=urllib.parse.urlencode(form.values).encode(),headers={'User-Agent':'research script'})
            with opener.open(req,timeout=25) as res: raw=res.read(); final=res.url
            slug=re.sub(r'[^a-z0-9]+','_',c['name'].lower())
            (CACHE/('dpma_'+slug+'.raw')).write_bytes(raw)
            p=Page();p.feed(raw.decode('utf-8','replace'));txt='\n'.join(p.text)
            (CACHE/('dpma_'+slug+'.json')).write_text(json.dumps({'query':form.values['marke'],'url':final,'checked_date':'2026-11-21','text':txt,'links':p.links},ensure_ascii=False),encoding='utf-8')
            findings=[txt[max(0,m.start()-80):m.end()+220] for m in re.finditer(r'keine Treffer|\d+ Treffer|Trefferliste|Fehler|fehlgeschlagen|Keine Marke|keine Marke|gefunden|Trefferanzahl',txt,re.I)]
            audit.append({'name':c['name'],'url':final,'findings':findings[:8],'length':len(txt)})
            if not findings: failures+=1
        except Exception as e:
            failures+=1;audit.append({'name':c['name'],'error':str(e)})
        (CACHE/'dpma_audit.json').write_text(json.dumps(audit,ensure_ascii=False),encoding='utf-8')
        print(json.dumps(audit[-1],ensure_ascii=False),flush=True)
        time.sleep(0.6)

if __name__=='__main__':
    import sys
    if '--dpma' in sys.argv: dpma_audit()
    elif '--github' in sys.argv: github_audit()
    elif '--build' in sys.argv: build()
    else: main()
