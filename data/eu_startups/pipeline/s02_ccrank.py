import sys, gzip, subprocess, io
rel=sys.argv[1]
doms=set(l.strip() for l in open('/home/claude/data/raw/cc/domains.txt') if l.strip())
# CC domain ranks use reversed domain names: example.com -> com.example
rev={'.'.join(d.split('.')[::-1]):d for d in doms}
url=f'https://data.commoncrawl.org/projects/hyperlinkgraph/{rel}/domain/{rel}-domain-ranks.txt.gz'
p=subprocess.Popen(['curl','-sL',url],stdout=subprocess.PIPE)
n=0; hits=0
with open(f'/home/claude/data/raw/cc/{rel}.tsv','w') as out:
    for line in io.TextIOWrapper(gzip.GzipFile(fileobj=p.stdout),encoding='utf-8',errors='ignore'):
        if line.startswith('#'): continue
        n+=1
        parts=line.rstrip('\n').split('\t')
        if len(parts)>=5 and parts[4] in rev:
            out.write(f"{rev[parts[4]]}\t{parts[0]}\t{parts[2]}\n"); hits+=1
open(f'/home/claude/data/raw/cc/{rel}.total','w').write(str(n))
print(rel,n,hits)
