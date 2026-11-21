"""Build an evidence pack per company: YC page data + Google News headlines + Wikipedia summaries."""
import json, re, html, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 research-script'}
def get(u):
    for _ in range(2):
        try:
            return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read().decode('utf-8', 'ignore')
        except Exception:
            time.sleep(2)
    return ''

def news(q, n=8):
    x = get('https://news.google.com/rss/search?q=' + urllib.parse.quote(q) + '&hl=en-US&gl=US&ceid=US:en')
    items = re.findall(r'<item><title>(.*?)</title><link>(.*?)</link>.*?<pubDate>(.*?)</pubDate>', x, re.S)
    return [{'title': html.unescape(t), 'url': l, 'date': d[5:16]} for t, l, d in items[:n]]

def wiki(q):
    s = get('https://en.wikipedia.org/w/api.php?action=query&list=search&srlimit=1&format=json&srsearch=' + urllib.parse.quote(q))
    try:
        title = json.loads(s)['query']['search'][0]['title']
    except Exception:
        return None
    e = get('https://en.wikipedia.org/api/rest_v1/page/summary/' + urllib.parse.quote(title.replace(' ', '_')))
    try:
        j = json.loads(e); return {'title': title, 'extract': j.get('extract', '')[:900], 'url': j.get('content_urls', {}).get('desktop', {}).get('page')}
    except Exception:
        return None

sample = json.load(open('sample1000.json', encoding='utf-8'))
yc = json.load(open('yc_pages.json', encoding='utf-8'))
yc.update(json.load(open('yc_pages_add.json', encoding='utf-8')))

def build(r):
    p = yc.get(r['slug'], {})
    name = r['name']
    ev = {'slug': r['slug'], 'name': name, 'batch': r['batch'], 'website': r['website'], 'one_liner': r['one_liner'],
          'yc_url': r['yc_url'], 'year_founded': p.get('year_founded'), 'location': p.get('location'),
          'description': p.get('long_description'), 'founders': p.get('founders', [])}
    ev['news_company'] = news(f'"{name}" startup') + news(f'"{name}" (raises OR funding OR seed OR acquired OR "shut down" OR shutting)')
    wc = wiki(f'{name} company')
    ev['wikipedia_company'] = wc if wc and name.lower().split()[0] in wc['title'].lower() else None
    fl = []
    for f in ev['founders'][:4]:
        fn = f.get('full_name') or ''
        if not fn: continue
        wf = wiki(fn)
        fl.append({'founder': fn,
                   'news': news(f'"{fn}" {name}', 5) + news(f'"{fn}" founder', 5),
                   'wikipedia': wf if wf and fn.split()[-1].lower() in wf['title'].lower() else None})
    ev['founder_research'] = fl
    return ev

with ThreadPoolExecutor(4) as ex:
    packs = list(ex.map(build, sample))
json.dump(packs, open('evidence.json', 'w', encoding='utf-8'), ensure_ascii=False)
print('packs', len(packs), '| with founders', sum(1 for p in packs if p['founders']),
      '| avg company news', sum(len(p['news_company']) for p in packs) / len(packs))
