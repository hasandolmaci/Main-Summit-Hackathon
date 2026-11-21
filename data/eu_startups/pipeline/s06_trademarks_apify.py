"""Fill tm_any / tm_eu with an EUIPO trademark actor on Apify (the register search itself blocks scripted access).
Usage:  APIFY_TOKEN=... python s06_trademarks_apify.py eu_startups_2021_2023_all.csv
1) Open the actor page (default: scrapers_lat/euipo-trademarks-scraper) and check its input schema.
2) Edit build_input() so it searches by OWNER/APPLICANT name. The field names below are placeholders.
tm_any = an EUIPO filing by this applicant before asof_date. tm_eu = 1 when that filing is an EU trade mark.
National marks (DPMA, INPI, UKIPO, ...) need a TMview-based actor. Add it the same way and set tm_any from either source."""
import os, sys, re, pandas as pd
from apify_client import ApifyClient   # pip install apify-client
ACTOR = os.environ.get('TM_ACTOR', 'scrapers_lat/euipo-trademarks-scraper')
client = ApifyClient(os.environ['APIFY_TOKEN'])
def norm(s): return re.sub(r'[^a-z0-9]', '', re.sub(r'\b(gmbh|ltd|limited|sas|bv|ab|oy|ag|sl|srl|spa|inc)\b', '', str(s).lower()))
def build_input(name):                      # <-- adjust to the actor's real schema
    return {'ownerName': name, 'maxItems': 50}
df = pd.read_csv(sys.argv[1], dtype=str); res = []
for _, r in df.iterrows():
    run = client.actor(ACTOR).call(run_input=build_input(r['name']))
    items = list(client.dataset(run['defaultDatasetId']).iterate_items())
    hits = [it for it in items if norm(it.get('owner_name') or it.get('applicant_name') or '').startswith(norm(r['name']))
            and str(it.get('filing_date') or it.get('application_date') or '9999') < r['asof_date']]
    res.append((r['id'], int(bool(hits)), int(bool(hits))))
out = pd.DataFrame(res, columns=['id', 'tm_any', 'tm_eu'])
df.drop(columns=['tm_any', 'tm_eu']).merge(out, on='id', how='left').to_csv(sys.argv[1].replace('.csv', '_with_tm.csv'), index=False)
