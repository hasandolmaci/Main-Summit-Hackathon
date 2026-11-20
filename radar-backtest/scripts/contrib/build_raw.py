"""Build first raw tables: YC labels + CORDIS grant events, joined on registrable domain."""
import json, re, datetime as dt
import pandas as pd
import tldextract

EXT = tldextract.TLDExtract(suffix_list_urls=())  # offline snapshot, no network
PLATFORMS = {"github.com", "github.io", "medium.com", "substack.com", "herokuapp.com",
             "blogspot.com", "wordpress.com", "linkedin.com", "facebook.com", "twitter.com",
             "google.com", "apple.com", "youtube.com", "notion.site", "wixsite.com",
             "europa.eu", "angel.co", "crunchbase.com", "ycombinator.com"}
EUROPE = {"AT","BE","BG","CH","CY","CZ","DE","DK","EE","EL","ES","FI","FR","HR","HU","IE",
          "IS","IT","LI","LT","LU","LV","MT","NL","NO","PL","PT","RO","SE","SI","SK","UK"}


def domain(url):
    if not isinstance(url, str) or not url.strip():
        return None
    u = url.strip().lower()
    if not re.match(r"^[a-z]+://", u):
        u = "http://" + u
    e = EXT(u)
    if not e.domain or not e.suffix:
        return None
    d = f"{e.domain}.{e.suffix}"
    return None if d in PLATFORMS else d


def yc_acceptance(batch):
    """Acceptance proxy = batch start minus ~2.5 months."""
    m = re.match(r"(Winter|Spring|Summer|Fall)\s+(\d{4})", str(batch))
    if not m:
        return None
    season, y = m.group(1), int(m.group(2))
    return {"Winter": dt.date(y - 1, 10, 31), "Spring": dt.date(y, 1, 15),
            "Summer": dt.date(y, 3, 31), "Fall": dt.date(y, 7, 15)}[season]


# ---- labels: YC ----
yc = pd.DataFrame(json.load(open("yc.json")))
yc["domain"] = yc["website"].map(domain)
yc["first_funding_date"] = yc["batch"].map(yc_acceptance)
yc["is_europe"] = yc["regions"].map(lambda r: isinstance(r, list) and "Europe" in r)
labels = yc[["name", "domain", "website", "batch", "first_funding_date", "status",
             "industry", "is_europe"]].copy()
labels["round_type"] = "accelerator"
labels["source"] = "yc-oss"
labels = labels.dropna(subset=["domain", "first_funding_date"])
labels = labels.sort_values("first_funding_date").drop_duplicates("domain")
labels.to_csv("labels_yc.csv", index=False)

# ---- events: CORDIS grants to private companies ----
frames = []
for prog in ["h2020", "horizon"]:
    o = pd.read_csv(f"{prog}/organization.csv", sep=";", dtype=str, on_bad_lines="skip", low_memory=False)
    p = pd.read_csv(f"{prog}/project.csv", sep=";", dtype=str, on_bad_lines="skip", low_memory=False)
    o = o[o["activityType"] == "PRC"]
    m = o.merge(p[["id", "ecSignatureDate", "startDate", "fundingScheme", "frameworkProgramme"]],
                left_on="projectID", right_on="id", how="left")
    frames.append(m)
g = pd.concat(frames, ignore_index=True)
g["domain"] = g["organizationURL"].map(domain)
g["ts"] = pd.to_datetime(g["ecSignatureDate"].fillna(g["startDate"]), errors="coerce")
events = pd.DataFrame({
    "entity_id": g["domain"], "ts": g["ts"], "source": "cordis", "type": "public_rd_grant",
    "value": pd.to_numeric(g["ecContribution"], errors="coerce"),
    "detail": g.apply(lambda r: json.dumps({"name": r["name"], "country": r["country"],
                                            "sme": r["SME"], "scheme": r["fundingScheme"],
                                            "project": r["projectID"]}), axis=1),
})
events.dropna(subset=["entity_id", "ts"]).to_parquet("events_cordis.parquet", index=False)

# ---- gate report ----
ev = events.dropna(subset=["entity_id", "ts"])
first_grant = ev.groupby("entity_id")["ts"].min()
L = labels.copy()
L["first_grant"] = L["domain"].map(first_grant)
L["grant_before_funding"] = L["first_grant"] < pd.to_datetime(L["first_funding_date"])
L["year"] = pd.to_datetime(L["first_funding_date"]).dt.year

print("CORDIS private-company participations:", len(g))
print("  with a usable domain:", g["domain"].notna().sum(),
      "| distinct companies:", ev["entity_id"].nunique())
print("  in Europe:", g["country"].isin(EUROPE).sum())
print()
print("YC companies with a usable domain + date:", len(L))
print("  tagged Europe:", int(L["is_europe"].sum()))
print("  found in CORDIS at any time:", int(L["first_grant"].notna().sum()))
print("  CORDIS grant BEFORE YC acceptance:", int(L["grant_before_funding"].sum()))
print()
print("YC positives per acceptance year (all / Europe):")
t = L.groupby("year").agg(all=("domain", "size"), europe=("is_europe", "sum"))
print(t.loc[2012:2025].to_string())
L.to_csv("gate_yc_with_cordis.csv", index=False)
