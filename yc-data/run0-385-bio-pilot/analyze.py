import json,re,math,collections
f=json.load(open('founders.json',encoding='utf-8'))
allc={x['slug']:x for x in json.load(open('all.json',encoding='utf-8'))}
def wilson(k,n,z=1.96):
    if n==0: return (0,0,0)
    p=k/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return p,c-h,c+h
R={
 'technical founder (title/bio)':r'\b(cto|engineer|engineering|technical|developer|programmer|software|hacker|scientist)\b',
 'PhD / Dr.':r'\b(ph\.?d|phd|doctorate|dr\.)',
 'prior founder / exit':r'(previously (co-)?founded|serial|second (company|startup)|sold (my|our|his|her) |acquired by|exited|prior startup|previous startup|founded .* (which|that) (was )?(acquired|sold))',
 'ex big tech':r'\b(google|facebook|meta|microsoft|amazon|apple|twitter|uber|airbnb|stripe|palantir)\b',
 'elite university':r'\b(stanford|mit|harvard|berkeley|carnegie|cmu|princeton|yale|caltech|oxford|cambridge)\b',
}
rows=[]
for o in f:
    if 'error' in o or not o.get('founders'): continue
    fs=o['founders']; txt=' '.join(((x.get('title') or '')+' '+(x.get('founder_bio') or '')) for x in fs).lower()
    c=allc.get(o['slug'],{})
    r={'group':o['group'],'n_founders':len(fs),'2+ founders':len(fs)>=2,
       'all bios empty/short':all(len((x.get('founder_bio') or ''))<40 for x in fs),
       'B2B':(c.get('industry')=='B2B'),'avg bio chars':sum(len(x.get('founder_bio') or '') for x in fs)/len(fs)}
    for k,p in R.items(): r[k]=bool(re.search(p,txt))
    rows.append(r)
G=collections.defaultdict(list)
for r in rows: G[r['group']].append(r)
print({g:len(v) for g,v in G.items()}, 'errors/no founders:', len(f)-len(rows))
feats=['2+ founders','technical founder (title/bio)','PhD / Dr.','prior founder / exit','ex big tech','elite university','B2B','all bios empty/short']
print(f"{'feature':32}"+''.join(f"{g:>22}" for g in ['top','other','inactive']))
for k in feats:
    line=f"{k:32}"
    for g in ['top','other','inactive']:
        v=G[g]; n=len(v); kk=sum(1 for r in v if r[k]); p,lo,hi=wilson(kk,n)
        line+=f"{p*100:7.0f}% [{lo*100:3.0f}-{hi*100:3.0f}] n={n:<3}"
    print(line)
for g in ['top','other','inactive']:
    v=G[g]; print(g,'mean founders %.2f'%(sum(r['n_founders'] for r in v)/len(v)),'mean bio chars %.0f'%(sum(r['avg bio chars'] for r in v)/len(v)))
