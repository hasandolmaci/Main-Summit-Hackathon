import pandas as pd, numpy as np, re
USD_PER_EUR=1.08
CAP_EUR=800_000
EQUITY={'pre_seed','seed','angel','series_a','series_b','series_c','series_d','series_e','series_unknown','convertible_note','equity_crowdfunding','corporate_round','undisclosed','private_equity'}
SERVICE=set("""Consulting,Marketing,Advertising,Digital Marketing,Web Development,Web Design,Real Estate,Training,Business Development,Advice,Recruiting,Human Resources,SEO,Professional Services,Management Consulting,Sales,Events,Property Management,Wholesale,Staffing Agency,Graphic Design,Rental,Brand Marketing,Service Industry,Customer Service,Social Media Marketing,Legal,Real Estate Investment,Commercial,Environmental Consulting,Accounting,Architecture,Residential,Tourism,Hospitality,Construction,Retail,Fashion,Food and Beverage,Beauty,Cosmetics,Venture Capital,Asset Management,Publishing,Content,Interior Design,Restaurants,Hotel,Travel Agency,Translation Service,Photography,Video Production,Printing,Cleaning,Home Services,Craft Beer,Wine And Spirits,Furniture,Jewelry,Lifestyle,Leisure,Coaching,Personal Development,Business Information Systems,Information Technology,Outsourcing,Lead Generation,Public Relations,Event Management,Content Creators,Copywriting,Market Research,Social Media,Charity,Non Profit,Finance,Financial Services,Insurance,Banking,Trading Platform,Wealth Management,Freight Service,Shipping,Car Sharing,Delivery,Courier Service""".split(','))
def norm_name(s):
    if not isinstance(s,str): return ''
    s=s.lower()
    s=re.sub(r'\b(gmbh|ug|ag|ltd|limited|sas|sarl|s\.a\.s|sa|bv|b\.v\.|nv|ab|oy|aps|as|asa|srl|s\.r\.l|spa|s\.p\.a|sl|s\.l|inc|llc|plc|co|kg|se|oü|ou|sp\. z o\.o\.|sp z oo|a\.s\.|s\.r\.o\.|d\.o\.o\.|private|company|technologies|technology|tech|labs|haftungsbeschrankt|haftungsbeschränkt)\b','',s)
    s=re.sub(r'[^a-z0-9]','',s); return s
def is_tech(cats):
    if not isinstance(cats,str) or not cats.strip(): return False
    cs=[c.strip() for c in cats.split(',') if c.strip()]
    return any(c not in SERVICE for c in cs)
def funding_fields(r, asof, win_end, snap_end=pd.Timestamp('2024-08-15'), extra_rounds=None):
    """Return dict: pre_asof_funding_eur (or NaN), pre_known, label, label_complete, first_round_date, label_basis.
    r: crunchbase row (n_rounds, funding_total_usd, last_funding_type, last_funding_at). extra_rounds: list of (date, stage, amount_usd, url) from round-level data."""
    out=dict(pre_asof_funding_eur=np.nan,pre_known=0,label=np.nan,label_complete=int(win_end<=snap_end),first_round_date=None,label_basis='',label_evidence_url=None)
    # Round-level data takes precedence when present
    if extra_rounds:
        rr=sorted(extra_rounds,key=lambda x:x[0])
        post=[x for x in rr if asof<x[0]<=win_end and x[1] not in ('Grant','Debt')]
        if post:
            out.update(label=1,first_round_date=str(post[0][0].date()),label_basis='round_data',label_evidence_url=post[0][3])
    n=r.get('n_rounds'); n=0 if pd.isna(n) else int(n)
    lt=r.get('last_funding_type'); la=r.get('last_funding_at'); la=pd.Timestamp(la) if pd.notna(la) else None
    tot=r.get('funding_total_usd'); tot=None if pd.isna(tot) else float(tot)
    cb_url=r.get('url')
    if n==0 or la is None:
        out.update(pre_asof_funding_eur=0.0,pre_known=1)
        if out['label']!=1: out.update(label=0 if out['label_complete'] else np.nan,label_basis=out['label_basis'] or 'cb_no_rounds')
        return out
    if la<=asof:
        out.update(pre_asof_funding_eur=(tot/USD_PER_EUR if tot is not None else np.nan),pre_known=int(tot is not None))
        if out['label']!=1: out.update(label=0 if out['label_complete'] else np.nan,label_basis=out['label_basis'] or 'cb_last_round_before_asof')
        return out
    # last round after asof
    if n==1:
        out.update(pre_asof_funding_eur=0.0,pre_known=1)
        if la<=win_end:
            if lt in EQUITY:
                if out['label']!=1: out.update(label=1,first_round_date=str(la.date()),label_basis='cb_only_round_in_window',label_evidence_url=cb_url)
            elif out['label']!=1: out.update(label=0 if out['label_complete'] else np.nan,label_basis='cb_only_round_nonequity')
        else:
            if out['label']!=1: out.update(label=0,label_basis='cb_only_round_after_window')
        return out
    # n>=2 and last after asof: pre-asof amount unknown, bounded by total
    if tot is not None and tot/USD_PER_EUR<=CAP_EUR:
        out.update(pre_asof_funding_eur=np.nan,pre_known=0); out['pre_bound_ok']=1
    if asof<la<=win_end and lt in EQUITY and out['label']!=1:
        out.update(label=1,first_round_date=None,label_basis='cb_last_round_in_window(first_date_unknown)',label_evidence_url=cb_url)
    elif out['label']!=1:
        out.update(label=np.nan,label_basis='cb_ambiguous_multi_round')
    return out
