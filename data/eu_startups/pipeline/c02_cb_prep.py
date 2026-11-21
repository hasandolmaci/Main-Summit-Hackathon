# Build a clean European slice of the Aug-2024 Crunchbase snapshot with inferred country
import duckdb, re, pandas as pd
con=duckdb.connect('/home/claude/data/cb.duckdb')
df=con.execute("""select id,name,short_description,website,linkedin,phone_number,locations,categories,founded_on,company_type,operating_status,
 try_cast(funding_total as double) funding_total_usd, try_cast(num_funding_rounds as int) n_rounds, last_funding_type, try_cast(last_funding_at as date) last_funding_at,
 try_cast(num_investors as int) n_investors, num_employees_enum, permalink, url, ipo_status
 from cb where locations like '%Europe%' and company_type='for_profit'""").fetchdf()
print('EMEA for-profit', len(df))
TLD={'de':'DE','fr':'FR','nl':'NL','es':'ES','it':'IT','se':'SE','fi':'FI','dk':'DK','no':'NO','be':'BE','at':'AT','ch':'CH','pt':'PT','ie':'IE','pl':'PL','cz':'CZ','sk':'SK','hu':'HU','ro':'RO','bg':'BG','gr':'GR','hr':'HR','si':'SI','ee':'EE','lv':'LV','lt':'LT','lu':'LU','mt':'MT','cy':'CY','is':'IS','li':'LI','uk':'GB','tr':'TR','rs':'RS','ua':'UA','md':'MD','al':'AL','ba':'BA','me':'ME','mk':'MK','gg':'GB','je':'GB','im':'GB','berlin':'DE','paris':'FR','amsterdam':'NL','london':'GB','wien':'AT','swiss':'CH','scot':'GB','wales':'GB','bayern':'DE','hamburg':'DE','koeln':'DE','brussels':'BE','barcelona':'ES','madrid':'ES'}
CC={'49':'DE','33':'FR','31':'NL','34':'ES','39':'IT','46':'SE','358':'FI','45':'DK','47':'NO','32':'BE','43':'AT','41':'CH','351':'PT','353':'IE','48':'PL','420':'CZ','421':'SK','36':'HU','40':'RO','359':'BG','30':'GR','385':'HR','386':'SI','372':'EE','371':'LV','370':'LT','352':'LU','356':'MT','357':'CY','354':'IS','423':'LI','44':'GB','90':'TR','381':'RS','380':'UA','373':'MD','355':'AL','387':'BA','382':'ME','389':'MK'}
NONEU_CC=set('20 27 212 213 216 218 971 972 966 974 965 973 968 962 961 963 964 98 92 91 234 254 233 255 256 251 7 1 86 81 82 61 55 52 57 54'.split())
def dom(u):
    if not isinstance(u,str): return None
    u=u.strip().lower(); u=re.sub(r'^https?://','',u); u=u.split('/')[0].split('?')[0]; u=re.sub(r'^www\d?\.','',u); return u or None
df['domain']=df.website.map(dom)
def country(r):
    d=r.domain if isinstance(r.domain,str) else ''
    t=d.rsplit('.',1)[-1] if '.' in d else ''
    if d.endswith('.co.uk') or d.endswith('.org.uk'): return 'GB','tld'
    if t in TLD: return TLD[t],'tld'
    ph=re.sub(r'[^\d+]','',str(r.phone_number) if isinstance(r.phone_number,str) else '')
    if ph.startswith('00'): ph='+'+ph[2:]
    if ph.startswith('+'):
        p=ph[1:]
        for k in (4,3,2,1):
            if p[:k] in CC: return CC[p[:k]],'phone'
            if p[:k] in NONEU_CC: return 'NON_EU','phone'
    return None,None
cc=df.apply(country,axis=1,result_type='expand'); df['country']=cc[0]; df['country_src']=cc[1]
eu_tag=df.locations.str.contains('European Union',na=False)
df.loc[df.country.isna()&eu_tag,'country']='EU_UNKNOWN'; df.loc[(df.country=='EU_UNKNOWN'),'country_src']='cb_region'
df=df[df.country.notna()&(df.country!='NON_EU')]
print('European with country/EU', len(df)); print(df.country.value_counts().head(20))
df.to_parquet('/home/claude/data/cb_europe.parquet',index=False)
