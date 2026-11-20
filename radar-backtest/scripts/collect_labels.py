"""Label collector: YC directory (yc-oss public mirror) + HTGF portfolio -> data/labels/companies.csv.

Usage (from radar-backtest/):
    .venv/bin/python -m scripts.collect_labels                 # fetch (cached) + build all label files
    .venv/bin/python -m scripts.collect_labels --skip-htgf     # YC only
    .venv/bin/python -m scripts.collect_labels --htgf-limit 20 # smoke run on the first 20 HTGF pages

Outputs (each with a .meta.json sidecar):
    data/raw/yc/companies_all.json.gz          yc-oss records, gzip, minus free-text fields that can carry contact data
    data/raw/htgf/...                          see scripts/sources/htgf.py
    data/labels/companies.csv                  one row per company domain; YC date per cfg label.yc_date_mode
    data/labels/companies_batchstart.csv       sensitivity: YC date = batch start
    data/labels/label_provenance.csv           one row per source record (kept or not)
    data/labels/label_sample_20.csv            seeded random 20 rows of companies.csv for the human check

Everything is deterministic: no text is read or classified by a model. Batches are parsed with a regex,
countries with a fixed name -> ISO2 table, domains with scripts.lib.domains.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scripts.lib.config import LABELS, RAW, cfg, data_cutoff, get_logger, seed
from scripts.lib.domains import company_domain
from scripts.lib.http import Client
from scripts.lib.meta import write_meta
from scripts.lib.schema import LABEL_COLUMNS, validate_labels

log = get_logger("labels")

YC_URL = "https://yc-oss.github.io/api/companies/all.json"
YC_COMPANY_URL = "https://www.ycombinator.com/companies/{slug}"
SCRIPT = "scripts/collect_labels.py"

PROVENANCE_COLUMNS = ["domain", "company", "label_source", "batch", "batch_start", "acceptance_proxy", "yc_status",
                      "htgf_date", "date_precision", "source_url",
                      # extra (documented) columns after the required ones
                      "first_funding_date", "round_type", "sector", "country", "evidence", "excluded_reason"]

# ---------------------------------------------------------------------------------------------------------------
# YC batch parsing
# ---------------------------------------------------------------------------------------------------------------
_SEASONS = {"winter": "Winter", "spring": "Spring", "summer": "Summer", "fall": "Fall", "autumn": "Fall"}
# YC short codes: W = Winter, X = Spring (introduced 2025), S = Summer, F = Fall.
_SHORT = {"w": "Winter", "x": "Spring", "s": "Summer", "f": "Fall"}
_LONG_RE = re.compile(r"^\s*(winter|spring|summer|fall|autumn)\s*[-/']?\s*(\d{4}|\d{2})\s*$", re.I)
_LONG_REV_RE = re.compile(r"^\s*(\d{4})\s*[-/]?\s*(winter|spring|summer|fall|autumn)\s*$", re.I)
_SHORT_RE = re.compile(r"^\s*([wxsf])\s*'?(\d{2}|\d{4})\s*$", re.I)


def _year(y: str) -> int | None:
    v = int(y)
    if len(y) == 2:
        v += 2000
    return v if 2005 <= v <= 2100 else None   # YC's first batch was Summer 2005


def parse_batch(batch) -> tuple[str, int] | None:
    """'Winter 2015' -> ('Winter', 2015); 'S21' -> ('Summer', 2021); 'X25' -> ('Spring', 2025).

    Returns None for anything else ('Unspecified', 'IK12', '', None ...). Never guesses.
    """
    if not isinstance(batch, str) or not batch.strip():
        return None
    m = _LONG_RE.match(batch)
    if m:
        y = _year(m.group(2))
        return (_SEASONS[m.group(1).lower()], y) if y else None
    m = _LONG_REV_RE.match(batch)
    if m:
        y = _year(m.group(1))
        return (_SEASONS[m.group(2).lower()], y) if y else None
    m = _SHORT_RE.match(batch)
    if m:
        y = _year(m.group(2))
        return (_SHORT[m.group(1).lower()], y) if y else None
    return None


def batch_start(season: str, year: int, season_start: dict | None = None) -> date:
    season_start = season_start or cfg()["label"]["yc_season_start"]
    month, day = season_start[season]
    return date(year, month, day)


def acceptance_proxy(start: date, offset_days: int | None = None) -> date:
    if offset_days is None:
        offset_days = int(cfg()["label"]["acceptance_offset_days"])
    return start - timedelta(days=int(offset_days))


# ---------------------------------------------------------------------------------------------------------------
# Country names -> ISO2 (fixed table; exact match after normalisation, no fuzzy matching)
# ---------------------------------------------------------------------------------------------------------------
_COUNTRIES = {
    "AD": ["Andorra"], "AE": ["United Arab Emirates", "UAE", "U.A.E."], "AF": ["Afghanistan"],
    "AG": ["Antigua and Barbuda"], "AL": ["Albania"], "AM": ["Armenia"], "AO": ["Angola"],
    "AR": ["Argentina"], "AT": ["Austria", "Österreich", "Oesterreich"], "AU": ["Australia"], "AZ": ["Azerbaijan"],
    "BA": ["Bosnia and Herzegovina", "Bosnia & Herzegovina", "Bosnia"], "BB": ["Barbados"], "BD": ["Bangladesh"],
    "BE": ["Belgium", "Belgien"], "BF": ["Burkina Faso"], "BG": ["Bulgaria"], "BH": ["Bahrain"], "BI": ["Burundi"],
    "BJ": ["Benin"], "BM": ["Bermuda"], "BN": ["Brunei", "Brunei Darussalam"], "BO": ["Bolivia"],
    "BR": ["Brazil", "Brasil"], "BS": ["Bahamas", "The Bahamas"], "BT": ["Bhutan"], "BW": ["Botswana"],
    "BY": ["Belarus"], "BZ": ["Belize"], "CA": ["Canada"], "CD": ["Democratic Republic of the Congo", "DR Congo",
                                                               "Congo (Kinshasa)"],
    "CF": ["Central African Republic"], "CG": ["Republic of the Congo", "Congo (Brazzaville)"],
    "CH": ["Switzerland", "Schweiz"], "CI": ["Côte d'Ivoire", "Cote d'Ivoire", "Ivory Coast"], "CL": ["Chile"],
    "CM": ["Cameroon"], "CN": ["China", "People's Republic of China", "PRC"], "CO": ["Colombia"],
    "CR": ["Costa Rica"], "CU": ["Cuba"], "CV": ["Cape Verde", "Cabo Verde"], "CY": ["Cyprus"],
    "CZ": ["Czech Republic", "Czechia", "Tschechien"], "DE": ["Germany", "Deutschland"], "DJ": ["Djibouti"],
    "DK": ["Denmark", "Dänemark"], "DM": ["Dominica"], "DO": ["Dominican Republic"], "DZ": ["Algeria"],
    "EC": ["Ecuador"], "EE": ["Estonia"], "EG": ["Egypt"], "ER": ["Eritrea"], "ES": ["Spain", "Spanien"],
    "ET": ["Ethiopia"], "FI": ["Finland", "Finnland"], "FJ": ["Fiji"], "FR": ["France", "Frankreich"],
    "GA": ["Gabon"], "GB": ["United Kingdom", "UK", "U.K.", "Great Britain", "England", "Scotland", "Wales",
                            "Northern Ireland", "Großbritannien", "Vereinigtes Königreich"],
    "GD": ["Grenada"], "GH": ["Ghana"], "GI": ["Gibraltar"], "GM": ["Gambia",
                                                                                                 "The Gambia"],
    "GN": ["Guinea"], "GQ": ["Equatorial Guinea"], "GR": ["Greece", "Griechenland"], "GT": ["Guatemala"],
    "GW": ["Guinea-Bissau"], "GY": ["Guyana"], "HK": ["Hong Kong", "Hong Kong SAR"], "HN": ["Honduras"],
    "HR": ["Croatia", "Kroatien"], "HT": ["Haiti"], "HU": ["Hungary", "Ungarn"], "ID": ["Indonesia"],
    "IE": ["Ireland", "Irland"], "IL": ["Israel"], "IN": ["India", "Indien"], "IQ": ["Iraq"], "IR": ["Iran"],
    "IS": ["Iceland"], "IT": ["Italy", "Italien"], "JM": ["Jamaica"], "JO": ["Jordan"],
    "JP": ["Japan"], "KE": ["Kenya"], "KG": ["Kyrgyzstan"], "KH": ["Cambodia"], "KN": ["Saint Kitts and Nevis"],
    "KR": ["South Korea", "Korea", "Republic of Korea", "Korea, Republic of"], "KW": ["Kuwait"],
    "KY": ["Cayman Islands"], "KZ": ["Kazakhstan"], "LA": ["Laos"], "LB": ["Lebanon"], "LC": ["Saint Lucia"],
    "LI": ["Liechtenstein"], "LK": ["Sri Lanka"], "LR": ["Liberia"], "LS": ["Lesotho"],
    "LT": ["Lithuania", "Litauen"], "LU": ["Luxembourg", "Luxemburg"], "LV": ["Latvia", "Lettland"],
    "LY": ["Libya"], "MA": ["Morocco"], "MC": ["Monaco"], "MD": ["Moldova"], "ME": ["Montenegro"],
    "MG": ["Madagascar"], "MK": ["North Macedonia", "Macedonia"], "ML": ["Mali"], "MM": ["Myanmar", "Burma"],
    "MN": ["Mongolia"], "MO": ["Macau", "Macao"], "MR": ["Mauritania"], "MT": ["Malta"], "MU": ["Mauritius"],
    "MV": ["Maldives"], "MW": ["Malawi"], "MX": ["Mexico", "México"], "MY": ["Malaysia"], "MZ": ["Mozambique"],
    "NA": ["Namibia"], "NE": ["Niger"], "NG": ["Nigeria"], "NI": ["Nicaragua"],
    "NL": ["Netherlands", "The Netherlands", "Holland", "Niederlande"], "NO": ["Norway", "Norwegen"],
    "NP": ["Nepal"], "NZ": ["New Zealand"], "OM": ["Oman"], "PA": ["Panama"], "PE": ["Peru"],
    "PG": ["Papua New Guinea"], "PH": ["Philippines"], "PK": ["Pakistan"], "PL": ["Poland", "Polen"],
    "PR": ["Puerto Rico"], "PS": ["Palestine", "Palestinian Territories"], "PT": ["Portugal"], "PY": ["Paraguay"],
    "QA": ["Qatar"], "RO": ["Romania", "Rumänien"], "RS": ["Serbia"], "RU": ["Russia", "Russian Federation"],
    "RW": ["Rwanda"], "SA": ["Saudi Arabia"], "SC": ["Seychelles"], "SD": ["Sudan"], "SE": ["Sweden",
                                                                                             "Schweden"],
    "SG": ["Singapore"], "SI": ["Slovenia", "Slowenien"], "SK": ["Slovakia", "Slowakei"], "SL": ["Sierra Leone"],
    "SM": ["San Marino"], "SN": ["Senegal"], "SO": ["Somalia"], "SR": ["Suriname"], "SS": ["South Sudan"],
    "SV": ["El Salvador"], "SY": ["Syria"], "SZ": ["Eswatini", "Swaziland"], "TD": ["Chad"], "TG": ["Togo"],
    "TH": ["Thailand"], "TJ": ["Tajikistan"], "TM": ["Turkmenistan"], "TN": ["Tunisia"],
    "TR": ["Turkey", "Türkiye", "Turkiye", "Türkei"], "TT": ["Trinidad and Tobago"], "TW": ["Taiwan"],
    "TZ": ["Tanzania"], "UA": ["Ukraine"], "UG": ["Uganda"],
    "US": ["United States", "United States of America", "USA", "U.S.A.", "US", "U.S."],
    "UY": ["Uruguay"], "UZ": ["Uzbekistan"], "VE": ["Venezuela"], "VN": ["Vietnam", "Viet Nam"],
    "XK": ["Kosovo"], "YE": ["Yemen"], "ZA": ["South Africa", "Südafrika"], "ZM": ["Zambia"], "ZW": ["Zimbabwe"],
}


def _norm_country(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().strip(".").lower()) if isinstance(s, str) else ""


COUNTRY_TO_ISO2 = {_norm_country(n): iso for iso, names in _COUNTRIES.items() for n in names}


def country_iso2(name) -> str | None:
    """Exact (case/space-insensitive) country-name lookup. 'Georgia' is deliberately absent (US state clash)."""
    return COUNTRY_TO_ISO2.get(_norm_country(name)) if isinstance(name, str) else None


def yc_country(all_locations, regions) -> tuple[str | None, str]:
    """Country of a YC company: last comma token of the FIRST location in all_locations (the HQ), else the first
    entry of `regions` that is a country name. Returns (iso2 or None, rule used)."""
    if isinstance(all_locations, str) and all_locations.strip():
        first = all_locations.split(";")[0]
        last = first.split(",")[-1]
        iso = country_iso2(last)
        if iso:
            return iso, "location"
    for r in regions or []:
        iso = country_iso2(r)
        if iso:
            return iso, "region"
    return None, "none"


# ---------------------------------------------------------------------------------------------------------------
# YC records
# ---------------------------------------------------------------------------------------------------------------
# Fields the label rows are built from. They are always stored unchanged, so the labels can be rebuilt from raw.
YC_LABEL_FIELDS = ("name", "slug", "website", "batch", "industry", "regions", "all_locations", "status")
# Free-text fields that can carry founders' e-mail addresses / phone numbers; never stored (and not used).
YC_ALWAYS_DROP = ("long_description",)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_INTL_PHONE_RE = re.compile(r"(?<![\w/.-])\+\d{1,3}[ .-]?\(?\d{2,4}\)?[ .-]?\d{3,4}[ .-]?\d{3,4}(?![\w/])")


def yc_storable(companies: list[dict]) -> tuple[list[dict], list[str], int]:
    """Copy of the YC records for data/raw without columns that carry contact data (rule: no personal e-mails or
    phone numbers on disk). Drops YC_ALWAYS_DROP plus every other non-label column in which an e-mail-like or
    international phone-like string occurs. Returns (records, dropped columns, label-field hits)."""
    drop = set(YC_ALWAYS_DROP)
    label_hits = 0
    for c in companies:
        for k, v in c.items():
            if k in drop:
                continue
            s = json.dumps(v, ensure_ascii=False)
            if _EMAIL_RE.search(s) or _INTL_PHONE_RE.search(s):
                if k in YC_LABEL_FIELDS:
                    label_hits += 1
                else:
                    drop.add(k)
    stored = [{k: v for k, v in c.items() if k not in drop} for c in companies]
    return stored, sorted(drop), label_hits


def fetch_yc(client: Client) -> tuple[list[dict], Path]:
    r = client.get(YC_URL)
    if r.status != 200:
        raise RuntimeError(f"yc-oss returned status {r.status}")
    data = json.loads(r.text)
    if not isinstance(data, list):
        raise RuntimeError(f"yc-oss returned {type(data).__name__}, expected a list")
    stored, dropped, label_hits = yc_storable(data)
    if label_hits:
        log.warning("yc: %d label-field values look like e-mail/phone strings (kept: needed for the labels)",
                    label_hits)
    body = r.text.encode("utf-8")
    out = RAW / "yc" / "companies_all.json.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fb, gzip.GzipFile(filename="companies_all.json", mode="wb", fileobj=fb, mtime=0) as f:
        f.write(json.dumps(stored, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    write_meta(out, script=SCRIPT, params={"url": YC_URL, "fetched_at": r.fetched_at, "from_cache": r.from_cache,
                                           "response_sha256": hashlib.sha256(body).hexdigest(),
                                           "response_bytes": len(body), "dropped_fields": dropped,
                                           "label_field_contact_hits": label_hits},
               rows=len(data), sources=[YC_URL],
               notes="yc-oss mirror response (public YC directory) as parsed JSON, record order and values "
                     "unchanged, minus the dropped_fields (free text that can carry personal e-mail addresses / "
                     "phone numbers; not used for labels). response_sha256 identifies the full upstream response. "
                     "gzip, mtime=0.")
    return data, out


def yc_records(companies: list[dict], counts: Counter | None = None) -> list[dict]:
    """One provenance row per YC directory record. Excluded rows carry excluded_reason."""
    counts = counts if counts is not None else Counter()
    offset = int(cfg()["label"]["acceptance_offset_days"])
    season_start = cfg()["label"]["yc_season_start"]
    rows = []
    for c in companies:
        counts["yc_records"] += 1
        parsed = parse_batch(c.get("batch"))
        dom = company_domain(c.get("website"), (c.get("name") or "",))
        iso, rule = yc_country(c.get("all_locations"), c.get("regions"))
        counts[f"yc_country_{rule}"] += 1
        bs = batch_start(*parsed, season_start) if parsed else None
        ap = acceptance_proxy(bs, offset) if bs else None
        reason = ""
        if parsed is None:
            reason = "yc_unparseable_batch"
        elif dom is None:
            reason = "no_company_domain"
        if reason:
            counts[f"drop:{reason}"] += 1
        slug = c.get("slug") or ""
        rows.append({
            "domain": dom or "", "company": (c.get("name") or "").strip(), "label_source": "yc",
            "batch": c.get("batch") or "", "batch_start": bs.isoformat() if bs else "",
            "acceptance_proxy": ap.isoformat() if ap else "", "yc_status": c.get("status") or "",
            "htgf_date": "", "date_precision": "day" if bs else "",
            "source_url": YC_COMPANY_URL.format(slug=slug) if slug else "",
            "first_funding_date": "", "round_type": "accelerator", "sector": c.get("industry") or "",
            "country": iso or "", "evidence": f"country_rule={rule}", "excluded_reason": reason,
        })
    return rows


def record_date(row: dict, yc_date_mode: str) -> str:
    """The funding date one provenance row contributes ('' if none)."""
    if row["label_source"] == "yc":
        if yc_date_mode == "acceptance":
            return row["acceptance_proxy"]
        if yc_date_mode == "batch_start":
            return row["batch_start"]
        raise ValueError(f"unknown yc_date_mode {yc_date_mode!r}")
    return row.get("htgf_date") or ""


# ---------------------------------------------------------------------------------------------------------------
# Combine sources -> one row per domain
# ---------------------------------------------------------------------------------------------------------------
def build_companies(prov: list[dict], yc_date_mode: str, cutoff: date | None = None,
                    counts: Counter | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """first_funding_date = min over all usable source records of the same domain. Returns (companies, dup_log).

    The other columns come from the record that supplies the minimum (ties: yc before htgf, then company name,
    then source_url, so the result is deterministic). Rows dated after the data cutoff are dropped (counted).
    """
    counts = counts if counts is not None else Counter()
    counts["duplicate_domains"] += 0
    counts["duplicate_domain_records_merged"] += 0
    usable = []
    for r in prov:
        if r.get("excluded_reason"):
            continue
        d = record_date(r, yc_date_mode)
        if not d or not r.get("domain"):
            continue
        usable.append({**r, "_date": d})
    usable.sort(key=lambda r: (r["domain"], r["_date"], r["label_source"] != "yc", r["company"], r["source_url"]))
    out, dup_log = [], []
    i = 0
    while i < len(usable):
        j = i
        while j < len(usable) and usable[j]["domain"] == usable[i]["domain"]:
            j += 1
        group = usable[i:j]
        best = group[0]
        if len(group) > 1:
            counts["duplicate_domains"] += 1
            counts["duplicate_domain_records_merged"] += len(group) - 1
            dup_log.append({"domain": best["domain"], "n_records": len(group),
                            "sources": sorted({g["label_source"] for g in group}),
                            "kept_date": best["_date"], "kept_source": best["label_source"],
                            "dates": [g["_date"] for g in group]})
        if cutoff is not None and date.fromisoformat(best["_date"]) > cutoff:
            counts["drop:after_data_cutoff"] += 1
        else:
            out.append({"company": best["company"], "domain": best["domain"], "first_funding_date": best["_date"],
                        "round_type": best["round_type"], "sector": best["sector"], "country": best["country"],
                        "source_url": best["source_url"], "verified_by_human": "n"})
        i = j
    df = pd.DataFrame(out, columns=LABEL_COLUMNS)
    return df, dup_log


def provenance_frame(prov: list[dict], yc_date_mode: str) -> pd.DataFrame:
    rows = []
    for r in prov:
        rr = dict(r)
        rr["first_funding_date"] = "" if r.get("excluded_reason") else record_date(r, yc_date_mode)
        rows.append(rr)
    df = pd.DataFrame(rows, columns=PROVENANCE_COLUMNS)
    return df.sort_values(["label_source", "domain", "company", "source_url"], kind="mergesort").reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------
def _write_csv(df: pd.DataFrame, path: Path, **meta):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n")
    write_meta(path, script=SCRIPT, rows=len(df), **meta)


def summarise(df: pd.DataFrame, prov: pd.DataFrame, counts: Counter, title: str) -> str:
    lines = [f"== {title}: {len(df)} companies =="]
    src = df["source_url"].map(lambda u: "yc" if "ycombinator.com" in u else "htgf")
    lines.append("rows by winning source: " + json.dumps(src.value_counts().sort_index().to_dict()))
    lines.append("provenance records by source: " + json.dumps(prov["label_source"].value_counts().sort_index()
                                                               .to_dict()))
    year = pd.to_datetime(df["first_funding_date"]).dt.year
    yrs = year.value_counts().sort_index()
    lines.append("first_funding year: " + json.dumps({int(k): int(v) for k, v in yrs.items()}))
    for s in sorted(src.unique()):
        ys = year[src == s].value_counts().sort_index()
        lines.append(f"first_funding year [{s}]: " + json.dumps({int(k): int(v) for k, v in ys.items()}))
    if "htgf" in set(src):
        prec = prov.loc[prov["label_source"] == "htgf", "date_precision"].replace("", "(none)").value_counts()
        lines.append("htgf date_precision (provenance): " + json.dumps(prec.to_dict()))
    both = prov.loc[prov["excluded_reason"] == ""].groupby("domain")["label_source"].nunique()
    lines.append(f"domains with records in both sources: {int((both > 1).sum())}")
    lines.append("sector top 15: " + json.dumps(df["sector"].replace("", "(empty)").value_counts().head(15)
                                                .to_dict()))
    lines.append("country top 10: " + json.dumps(df["country"].replace("", "(empty)").value_counts().head(10)
                                                 .to_dict()))
    drops = {k[5:]: v for k, v in sorted(counts.items()) if k.startswith("drop:")}
    lines.append("dropped by reason: " + json.dumps(drops))
    other = {k: v for k, v in sorted(counts.items()) if not k.startswith("drop:")}
    lines.append("counters: " + json.dumps(other))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--skip-htgf", action="store_true", help="YC only")
    ap.add_argument("--htgf-limit", type=int, default=None, help="only the first N HTGF pages (smoke test)")
    args = ap.parse_args(argv)

    client = Client()
    mode = cfg()["label"]["yc_date_mode"]
    cutoff = data_cutoff().date()
    counts = Counter()

    companies, yc_raw = fetch_yc(client)
    prov = yc_records(companies, counts)
    inputs = [yc_raw]
    sources = [YC_URL]
    if not args.skip_htgf:
        from scripts.sources import htgf
        h_rows, h_inputs = htgf.collect(client, limit=args.htgf_limit, counts=counts)
        prov += h_rows
        inputs += h_inputs
        sources += [htgf.BASE]

    params = {"yc_date_mode": mode, "acceptance_offset_days": cfg()["label"]["acceptance_offset_days"],
              "yc_season_start": cfg()["label"]["yc_season_start"], "data_cutoff": cutoff.isoformat(),
              "skip_htgf": args.skip_htgf, "htgf_limit": args.htgf_limit}

    main_counts = Counter(counts)
    df, dups = build_companies(prov, mode, cutoff, main_counts)
    bs_counts = Counter(counts)
    df_bs, _ = build_companies(prov, "batch_start", cutoff, bs_counts)
    prov_df = provenance_frame(prov, mode)

    for d in (df, df_bs):
        errs = validate_labels(d)
        if errs:
            raise SystemExit(f"label validation failed: {errs}")
    for d in dups:
        log.info("duplicate domain %s: %d records %s, kept %s from %s", d["domain"], d["n_records"], d["sources"],
                 d["kept_date"], d["kept_source"])

    _write_csv(df, LABELS / "companies.csv", inputs=inputs, params=params, sources=sources,
               notes=f"first_funding_date = min over sources per domain; YC date mode = {mode}. "
                     f"{len(dups)} duplicate domains merged (see logs/collection.log).")
    _write_csv(df_bs, LABELS / "companies_batchstart.csv", inputs=inputs, params={**params, "yc_date_mode":
                                                                                   "batch_start"},
               sources=sources, notes="Sensitivity file: YC date = batch start.")
    _write_csv(prov_df, LABELS / "label_provenance.csv", inputs=inputs, params=params, sources=sources,
               notes="One row per source record; excluded_reason is set when a record is not usable; "
                     "first_funding_date is the date the record contributes under yc_date_mode.")
    sample = df.sample(n=min(20, len(df)), random_state=seed()).sort_values("domain")
    _write_csv(sample, LABELS / "label_sample_20.csv", inputs=[LABELS / "companies.csv"],
               params={"seed": seed(), "n": 20}, notes="Seeded random sample for the human check.")

    print(summarise(df, prov_df, main_counts, f"companies.csv (yc_date_mode={mode})"))
    print(summarise(df_bs, prov_df, bs_counts, "companies_batchstart.csv"))
    return df


if __name__ == "__main__":
    main()
