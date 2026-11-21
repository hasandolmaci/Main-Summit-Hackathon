import json,re,html,time,urllib.request
s=json.load(open('sample.json',encoding='utf-8'))
out={}
for r in s:
    try:
        req=urllib.request.Request(r['yc_url'],headers={'User-Agent':'Mozilla/5.0'})
        t=urllib.request.urlopen(req,timeout=20).read().decode('utf-8')
        c=json.loads(html.unescape(re.search(r'data-page="([^"]+)"',t).group(1)))['props']['company']
        out[r['slug']]={'year_founded':c.get('year_founded'),'location':c.get('location'),'country':c.get('country'),
          'long_description':(c.get('long_description') or '')[:600],
          'founders':[{k:f.get(k) for k in ('full_name','title','founder_bio','linkedin_url','twitter_url')} for f in c.get('founders',[])]}
    except Exception as e:
        out[r['slug']]={'error':str(e)}
    time.sleep(0.8)
json.dump(out,open('yc_pages.json','w',encoding='utf-8'),ensure_ascii=False)
print('done',len(out),'errors',sum(1 for v in out.values() if 'error' in v))
