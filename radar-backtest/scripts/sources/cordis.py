"""CORDIS collector: EU research & innovation grants (FP7, H2020, Horizon Europe), open data, CC BY 4.0.

Steps
1. Resolve the CSV bulk-package URL of each programme from the EU Open Data Portal (data.europa.eu DCAT metadata,
   stored verbatim gz-compressed under data/raw/cordis/). Fallback: the known cordis.europa.eu URL.
2. Obtain each zip into data/raw/cordis/ (+ <zip>.manifest.json with url, sha256, bytes, fetched_at; + .meta.json):
   - automatic download through scripts.lib.http.Client(stream_to=...) ONLY if the host's robots.txt allows it.
     cordis.europa.eu/robots.txt currently has "Disallow: /data/", so the collector refuses and prints the URLs;
   - otherwise download the zips by hand (browser) and import them with --zip-dir DIR (copied, never edited).
3. Parse project.csv + organization.csv of each programme with explicit separator / quoting / encoding handling and a
   header alias map (header variants differ by programme and release). Contact columns are never loaded.
4. Write data/processed/cordis_participations.parquet (one row per programme x project x organisation) and
   data/processed/cordis_companies.parquet (one row per company domain of private companies, activity_type == PRC).

Point in time: public_ts = ecSignatureDate if present, else startDate (00:00 UTC of that day). Nothing observed later
(contentUpdateDate, endOfParticipation, ...) is used to build a timestamp.

CLI
    .venv/bin/python -m scripts.sources.cordis                      # resolve + download (if robots allows) + parse
    .venv/bin/python -m scripts.sources.cordis --zip-dir ~/Downloads # import hand-downloaded zips, then parse
    .venv/bin/python -m scripts.sources.cordis --skip-download      # parse zips already in data/raw/cordis
    .venv/bin/python -m scripts.sources.cordis --programmes H2020 HORIZON
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import shutil
import sys
import urllib.robotparser
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from scripts.lib.config import PROCESSED, RAW, get_logger
from scripts.lib.domains import company_domain
from scripts.lib.http import Client
from scripts.lib.meta import sha256_file, write_meta

log = get_logger("cordis")
SCRIPT = "scripts/sources/cordis.py"
RAW_DIR = RAW / "cordis"
LICENSE = "CC BY 4.0 (CORDIS, European Union; reuse per Commission Decision 2011/833/EU)"

PORTAL_API = "https://data.europa.eu/api/hub/search/datasets/{dataset_id}"
PROGRAMMES = {
    # programme key -> (data.europa.eu dataset id, fallback CSV package url)
    "FP7": ("cordisfp7projects", "https://cordis.europa.eu/data/cordis-fp7projects-csv.zip"),
    "H2020": ("cordish2020projects", "https://cordis.europa.eu/data/cordis-h2020projects-csv.zip"),
    "HORIZON": ("cordis-eu-research-projects-under-horizon-europe-2021-2027",
                "https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip"),
}
_CSV_ZIP_RE = re.compile(r"/cordis-(fp7|h2020|horizon)projects-csv\.zip$", re.I)

# ----------------------------------------------------------------------------------------------------------------------
# Header aliases (matched case-insensitively after stripping BOM / whitespace / quotes). First alias found wins.
# Current releases (2022+): organization.csv "projectID;projectAcronym;organisationID;vatNumber;name;shortName;SME;
#   activityType;...;country;...;organizationURL;contactForm;...;order;role;ecContribution;netEcContribution;..."
# Older releases: "projectRcn;projectID;projectAcronym;role;id;name;shortName;activityType;endOfParticipation;
#   ecContribution;country;street;city;postCode;organizationUrl;vatNumber;contactForm;contactType;...;
#   contactTelephoneNumber;contactFaxNumber"  (organisation id is the bare 'id' column).
ORG_ALIASES = {
    "project_id": ["projectID", "projectId", "project_id"],
    "organisation_id": ["organisationID", "organizationID", "organisationId", "organizationId", "id"],
    "name": ["name", "legalName", "organisationName", "organizationName"],
    "short_name": ["shortName", "short_name"],
    "sme": ["SME", "isSme"],
    "activity_type": ["activityType", "activity_type"],
    "country": ["country", "countryCode"],
    "organization_url": ["organizationURL", "organisationURL", "organizationUrl", "organisationUrl"],
    "role": ["role"],
    "ec_contribution": ["ecContribution", "ec_contribution", "netEcContribution"],
    "order": ["order"],
}
PROJECT_ALIASES = {
    "project_id": ["id", "projectID", "projectId", "project_id"],
    "start_date": ["startDate", "start_date"],
    "ec_signature_date": ["ecSignatureDate", "signatureDate", "ec_signature_date"],
    "framework": ["frameworkProgramme", "framework"],
}
REQUIRED_ORG = ["project_id", "organisation_id", "name", "activity_type", "country", "organization_url"]
REQUIRED_PROJECT = ["project_id", "start_date"]
# Any column whose header matches this is never loaded (contact data: contact forms, names, e-mails, phones).
CONTACT_RE = re.compile(r"contact|e-?mail|phone|fax|telephone|mobile", re.I)
# An e-mail address typed into the URL field ('info@x.de', 'mailto:jane.doe@gmail.com') is not a website and may be
# personal: such values are dropped (URL and domain None) and counted, so no address is stored and no webmail domain
# becomes a "company". '/@handle' paths (medium.com/@acme) do not match.
EMAIL_IN_URL_RE = re.compile(r"[^\s@:/]+@[^\s@/]+\.[^\s@/]+")

ACTIVITY_TYPES = {"PRC", "HES", "REC", "PUB", "OTH"}
COUNTRY_FIX = {"UK": "GB", "EL": "GR"}  # EU institutional codes -> ISO 3166-1 alpha-2

PART_COLUMNS = ["programme", "project_id", "organisation_id", "name", "short_name", "sme", "activity_type", "country",
                "organization_url", "domain", "role", "ec_contribution", "ec_signature_date", "start_date",
                "public_ts", "framework"]
COMPANY_COLUMNS = ["domain", "names", "country", "countries", "multi_country", "sme_any", "first_public_ts",
                   "n_participations", "programmes", "organisation_ids"]


class RobotsDisallowed(RuntimeError):
    pass


# ======================================================================================================================
# Obtain raw files
def robots_allows(client: Client, url: str) -> bool | None:
    """True/False from the host's robots.txt for our user agent; None if there is none (4xx: no restrictions).

    robots.txt is always fetched fresh (never replayed from the HTTP cache, so a later change is honoured). Per RFC 9309
    an unreachable robots.txt (5xx / connection error) means complete disallow.
    """
    p = urlparse(url)
    r = client.get(f"{p.scheme}://{p.netloc}/robots.txt", use_cache=False)
    if r.status == -1 or r.status >= 500:
        log.warning("robots.txt of %s unreachable (status=%s): treating as disallow", p.netloc, r.status)
        return False
    if r.status != 200:
        return None
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp.can_fetch(client.session.headers.get("User-Agent", "*"), url)


def _match_programme(url: str, programme: str) -> bool:
    m = _CSV_ZIP_RE.search(url or "")
    return bool(m) and m.group(1).lower() == programme.lower()


def resolve_url(client: Client, programme: str, raw_dir: Path = RAW_DIR) -> tuple[str, str]:
    """CSV bulk-package URL from the EU Open Data Portal (DCAT download_url). Returns (url, 'data.europa.eu'|'fallback').

    The portal response is stored verbatim (gz) in raw_dir as provenance (license, modified date).
    """
    dataset_id, fallback = PROGRAMMES[programme]
    api = PORTAL_API.format(dataset_id=dataset_id)
    if robots_allows(client, api) is False:
        log.warning("robots.txt disallows %s; using fallback url for %s", api, programme)
        return fallback, "fallback"
    r = client.get(api)
    if r.status == 200:
        out = raw_dir / f"portal_{dataset_id}.json.gz"
        if not out.exists():
            raw_dir.mkdir(parents=True, exist_ok=True)
            with gzip.open(out, "wt", encoding="utf-8") as f:
                f.write(r.text)
            write_meta(out, script=SCRIPT, params={"url": api, "fetched_at": r.fetched_at}, rows=1, sources=[api],
                       notes="EU Open Data Portal DCAT metadata (verbatim), used to resolve the CSV package URL")
        try:
            dists = r.json()["result"].get("distributions", [])
        except (ValueError, KeyError, AttributeError):
            dists = []
        for d in dists:
            for u in (d.get("download_url") or []) + (d.get("access_url") or []):
                if isinstance(u, str) and _match_programme(u, programme):
                    return u, "data.europa.eu"
    log.warning("could not resolve %s from the portal (status=%s); using fallback %s", programme, r.status, fallback)
    return fallback, "fallback"


def _write_manifest(dest: Path, rec: dict, programme: str):
    dest.with_name(dest.name + ".manifest.json").write_text(json.dumps(rec, indent=2, sort_keys=True))
    write_meta(dest, script=SCRIPT, params={"url": rec["url"], "programme": programme, "provenance": rec["provenance"]},
               sources=[rec["url"]], notes=f"CORDIS bulk CSV package ({LICENSE}); not edited; see .manifest.json")


def download(client: Client, programme: str, raw_dir: Path = RAW_DIR) -> Path:
    """Download one CSV package if robots.txt allows it; otherwise raise RobotsDisallowed (use --zip-dir)."""
    url, how = resolve_url(client, programme, raw_dir)
    dest = raw_dir / Path(urlparse(url).path).name
    if dest.exists() and dest.with_name(dest.name + ".manifest.json").exists():
        log.info("%s: using existing %s", programme, dest)
        return dest
    allowed = robots_allows(client, url)
    if allowed is False:
        raise RobotsDisallowed(
            f"{urlparse(url).netloc}/robots.txt disallows (or could not be read for) automated access to {url}. "
            f"Retry later if it was unreachable; otherwise download it by hand and run "
            f"`python -m scripts.sources.cordis --zip-dir <folder with {dest.name}>`.")
    raw_dir.mkdir(parents=True, exist_ok=True)
    r = client.get(url, stream_to=dest)
    if r.status != 200 or not dest.exists():
        raise RuntimeError(f"download failed for {programme}: {url} status={r.status}")
    if not zipfile.is_zipfile(dest):  # e.g. an HTML error / challenge page served with status 200
        size = dest.stat().st_size
        dest.unlink()
        raise RuntimeError(f"download of {url} is not a zip archive ({size} bytes); deleted, not used")
    # fetched_at "" = Client found the file already on disk (a run that died before the manifest): use its mtime.
    fetched_at = r.fetched_at or datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc).isoformat()
    _write_manifest(dest, {"url": url, "resolved_via": how, "provenance": "downloaded", "sha256": sha256_file(dest),
                           "bytes": dest.stat().st_size, "license": LICENSE, "robots_txt_allows": allowed,
                           "fetched_at": fetched_at}, programme)
    log.info("%s: downloaded %s (%d bytes)", programme, dest, dest.stat().st_size)
    return dest


def import_zip(src: Path, programme: str, url: str, raw_dir: Path = RAW_DIR) -> Path:
    """Copy a hand-downloaded package into raw_dir (byte-identical) and write its manifest + meta."""
    dest = raw_dir / Path(urlparse(url).path).name
    if not zipfile.is_zipfile(src):
        raise SystemExit(f"{src} is not a zip archive (an HTML page saved instead of the file?); not imported")
    if src.resolve() != dest.resolve():
        raw_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    mtime = datetime.fromtimestamp(src.stat().st_mtime, tz=timezone.utc).isoformat()
    _write_manifest(dest, {"url": url, "resolved_via": "manual", "provenance": "manual download imported via --zip-dir",
                           "sha256": sha256_file(dest), "bytes": dest.stat().st_size, "license": LICENSE,
                           "fetched_at": mtime, "imported_at": datetime.now(timezone.utc).isoformat()}, programme)
    log.info("%s: imported %s -> %s", programme, src, dest)
    return dest


# ======================================================================================================================
# Parsing
def _norm_header(h: str) -> str:
    return h.replace("﻿", "").strip().strip('"').strip().lower()


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Explicit encoding handling: UTF-8 (BOM stripped) first; cp1252 as a recorded fallback.

    A UTF-8 file with a few stray invalid bytes is NOT re-read as cp1252 (that would garble every non-ASCII name and
    turn the BOM into 'ï»¿', breaking the header): if it has a UTF-8 BOM, or at most 1% of its non-ASCII characters
    are invalid, it is decoded as UTF-8 with U+FFFD replacements, recorded as 'utf-8+replace(<n>)'.
    """
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    text = raw.decode("utf-8-sig", errors="replace")
    n_repl = text.count("�") - raw.count("�".encode("utf-8"))  # genuine U+FFFD in the source not counted
    n_non_ascii = len(text) - len(text.encode("ascii", "ignore"))
    if raw.startswith(b"\xef\xbb\xbf") or n_repl <= 0.01 * n_non_ascii:
        return text, f"utf-8+replace({n_repl})"
    return raw.decode("cp1252", errors="replace"), "cp1252"


def sniff_separator(header_line: str) -> str:
    counts = {s: header_line.count(s) for s in (";", "\t", ",", "|")}
    sep = max(counts, key=lambda s: (counts[s], s == ";"))
    if counts[sep] == 0:
        raise ValueError("cannot detect separator from header line")
    return sep


def map_columns(header: list[str], aliases: dict[str, list[str]]) -> dict[str, int]:
    """canonical name -> column index via the alias map. Contact columns are never mapped; a column maps once."""
    norm = [_norm_header(h) for h in header]
    out: dict[str, int] = {}
    for canon, names in aliases.items():
        for n in names:
            n = n.lower()
            if n in norm:
                idx = norm.index(n)
                if not CONTACT_RE.search(header[idx]) and idx not in out.values():
                    out[canon] = idx
                    break
    return out


def read_table(text: str, aliases: dict[str, list[str]], required: list[str], label: str = "") -> tuple[pd.DataFrame, dict]:
    """Parse one CORDIS CSV text with the csv module (explicit separator / '"' quoting with doubled quotes).

    Returns (frame of canonical str|None columns, stats). Rows whose field count differs from the header are counted
    as bad and skipped (one trailing empty field is tolerated). A row the csv module rejects (e.g. an unbalanced quote
    that runs past the field size limit) is counted as bad too and parsing resumes on the next line, so one broken
    quote cannot abort the run; `lines_in_bad_rows` reports how many physical lines the bad rows swallowed.
    Unmapped columns, incl. contact data, are discarded while reading and never reach a DataFrame.
    """
    first = text.split("\n", 1)[0]
    sep = sniff_separator(first)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=sep, quotechar='"', doublequote=True, strict=False)
    header = next(reader)
    cols = map_columns(header, aliases)
    missing = [c for c in required if c not in cols]
    if missing:
        raise ValueError(f"{label}: required columns {missing} not found in header {[_norm_header(h) for h in header]}")
    n_fields = len(header)
    data: dict[str, list] = {c: [] for c in cols}
    n_ok = n_bad = n_csv_err = bad_lines = 0
    while True:
        prev_line = reader.line_num
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error:
            n_bad += 1
            n_csv_err += 1
            bad_lines += reader.line_num - prev_line
            if reader.line_num == prev_line:  # no progress: stop instead of looping
                break
            continue
        if not row or (len(row) == 1 and not row[0].strip()):
            continue
        if len(row) == n_fields + 1 and row[-1] == "":
            row = row[:-1]
        if len(row) != n_fields:
            n_bad += 1
            bad_lines += reader.line_num - prev_line
            continue
        n_ok += 1
        for c, i in cols.items():
            v = row[i].strip()
            data[c].append(v if v != "" else None)
    df = pd.DataFrame(data, dtype=object)
    for c in aliases:
        if c not in df.columns:
            df[c] = pd.Series([None] * len(df), dtype=object)
    used = set(cols.values())
    stats = {"separator": sep, "n_header_fields": n_fields, "rows_ok": n_ok, "rows_bad": n_bad,
             "rows_csv_error": n_csv_err, "lines_in_bad_rows": bad_lines, "lines_read": reader.line_num,
             "parse_rate": n_ok / max(1, n_ok + n_bad), "mapped": {c: header[i] for c, i in cols.items()},
             "contact_columns_dropped": [h for i, h in enumerate(header) if i not in used and CONTACT_RE.search(h)]}
    return df[list(aliases)], stats


def parse_bool(v) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"true", "t", "yes", "y", "1"}:
        return True
    if s in {"false", "f", "no", "n", "0"}:
        return False
    return None


def parse_amount(v) -> float:
    """'1234.5' / '1234,5' / '1 234 567,89' / '1,234,567.89' -> float; anything else -> NaN."""
    if v is None:
        return np.nan
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if not s:
        return np.nan
    if "," in s and "." in s:  # thousands separator + decimal separator
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif s.count(",") == 1:
        s = s.replace(",", ".")
    elif s.count(",") > 1:
        s = s.replace(",", "")
    elif s.count(".") > 1:  # '1.234.567' (dots as thousands separators)
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


# Explicit formats only (never guessed). Day-first for slashed dates (EU source).
_DATE_RES = [
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$"), (1, 2, 3)),
    (re.compile(r"^(\d{4})/(\d{2})/(\d{2})$"), (1, 2, 3)),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), (3, 2, 1)),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), (3, 2, 1)),
]


def parse_date(v) -> pd.Timestamp:
    """Calendar date -> 00:00 UTC timestamp; NaT if missing or unparseable."""
    if v is None:
        return pd.NaT
    s = str(v).strip()
    for rx, (yi, mi, di) in _DATE_RES:
        m = rx.match(s)
        if m:
            try:
                return pd.Timestamp(int(m.group(yi)), int(m.group(mi)), int(m.group(di)), tz="UTC")
            except ValueError:
                return pd.NaT
    return pd.NaT


def norm_country(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().upper()
    s = COUNTRY_FIX.get(s, s)
    return s if re.fullmatch(r"[A-Z]{2}", s) else None


def norm_id(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0+", s):  # spreadsheet-style float ids
        s = s.split(".")[0]
    return s or None


def _role_rank(role) -> int:
    return {"coordinator": 0, "participant": 1, "beneficiary": 1, "hostinstitution": 1, "partner": 2,
            "associatedpartner": 3, "thirdparty": 4, "internationalpartner": 5}.get(
        str(role or "").lower().replace(" ", ""), 9)


def build_participations(org: pd.DataFrame, proj: pd.DataFrame, programme: str) -> tuple[pd.DataFrame, dict]:
    """Join organisations to projects and normalise. One row per (programme, project_id, organisation_id)."""
    org = org.copy()
    proj = proj.copy()
    org["project_id"] = org["project_id"].map(norm_id)
    org["organisation_id"] = org["organisation_id"].map(norm_id)
    proj["project_id"] = proj["project_id"].map(norm_id)
    proj = proj.dropna(subset=["project_id"]).drop_duplicates("project_id", keep="first")
    pj = proj[["project_id", "start_date", "ec_signature_date", "framework"]]
    df = org.merge(pj, on="project_id", how="left", indicator=True)
    stats = {"org_rows": int(len(org)), "project_rows": int(len(proj)),
             "join_rate": float((df["_merge"] == "both").mean()) if len(df) else 0.0}

    out = pd.DataFrame({
        "programme": programme,
        "project_id": df["project_id"],
        "organisation_id": df["organisation_id"],
        "name": df["name"],
        "short_name": df["short_name"],
        "sme": pd.array(df["sme"].map(parse_bool).tolist(), dtype="boolean"),
        "activity_type": df["activity_type"].map(lambda v: str(v).strip().upper() if v else None),
        "country": df["country"].map(norm_country),
        "organization_url": df["organization_url"].map(
            lambda v: None if v is None or EMAIL_IN_URL_RE.search(str(v)) else v),
        "role": df["role"].map(lambda v: str(v).strip().lower() if v else None),
        "ec_contribution": df["ec_contribution"].map(parse_amount).astype(float),
        "ec_signature_date": pd.to_datetime(df["ec_signature_date"].map(parse_date), utc=True),
        "start_date": pd.to_datetime(df["start_date"].map(parse_date), utc=True),
        "framework": df["framework"].where(df["framework"].notna(), programme),
        "_order": pd.to_numeric(df["order"], errors="coerce"),
    })
    # Non-empty date strings that match none of the explicit formats (or are impossible, e.g. month 13): a format
    # change or month-first dates would show up here instead of silently shifting public_ts.
    stats["unparsed_dates"] = {c: int((df[c].notna() & out[c].isna()).sum()) for c in ("ec_signature_date", "start_date")}
    stats["url_email_like_dropped"] = int((df["organization_url"].notna() & out["organization_url"].isna()).sum())
    bad_type = out["activity_type"].notna() & ~out["activity_type"].isin(ACTIVITY_TYPES)
    stats["unknown_activity_type"] = int(bad_type.sum())
    out.loc[bad_type, "activity_type"] = None
    out["public_ts"] = out["ec_signature_date"].fillna(out["start_date"])
    out["domain"] = out["organization_url"].map(company_domain)

    n0 = len(out)
    out = out.dropna(subset=["project_id", "organisation_id"])
    stats["dropped_no_ids"] = int(n0 - len(out))
    # Deterministic de-duplication: an organisation can be listed more than once in a project (e.g. a second role).
    # Keep the most senior role (coordinator first), then the lowest 'order'; EC contribution is summed.
    out = out.assign(_rank=out["role"].map(_role_rank))
    out = out.sort_values(["project_id", "organisation_id", "_rank", "_order"], kind="mergesort", na_position="last")
    key = ["programme", "project_id", "organisation_id"]
    dup = out.duplicated(key, keep=False)
    stats["duplicate_key_rows"] = int(dup.sum())
    if dup.any():
        ec_sum = out.groupby(key, sort=False)["ec_contribution"].sum(min_count=1)
        out = out.drop_duplicates(key, keep="first").set_index(key)
        out["ec_contribution"] = ec_sum.reindex(out.index)
        out = out.reset_index()
    out = out[PART_COLUMNS].reset_index(drop=True)
    stats["rows"] = int(len(out))
    return out, stats


def _zip_member(zf: zipfile.ZipFile, stem: str) -> str:
    """Find '<stem>.csv' in a zip (any folder, any case). Exactly one match is required."""
    cands = [n for n in zf.namelist() if Path(n).name.lower() == f"{stem}.csv"]
    if len(cands) != 1:
        raise ValueError(f"expected one {stem}.csv in zip, found {cands} (members: {zf.namelist()})")
    return cands[0]


def parse_zip(path: Path, programme: str) -> tuple[pd.DataFrame, dict]:
    with zipfile.ZipFile(path) as zf:
        members = sorted(zf.namelist())
        org_text, org_enc = decode_bytes(zf.read(_zip_member(zf, "organization")))
        org, org_stats = read_table(org_text, ORG_ALIASES, REQUIRED_ORG, f"{programme}/organization.csv")
        del org_text
        proj_text, proj_enc = decode_bytes(zf.read(_zip_member(zf, "project")))
        proj, proj_stats = read_table(proj_text, PROJECT_ALIASES, REQUIRED_PROJECT, f"{programme}/project.csv")
        del proj_text
    part, join_stats = build_participations(org, proj, programme)
    stats = {"zip": path.name, "members": members,
             "organization": {**org_stats, "encoding": org_enc}, "project": {**proj_stats, "encoding": proj_enc},
             **join_stats}
    return part, stats


# ======================================================================================================================
# Companies
def _sorted_unique_lists(df: pd.DataFrame, col: str, index: pd.Index) -> pd.Series:
    d = df[["domain", col]].dropna().drop_duplicates().sort_values(["domain", col], kind="mergesort")
    lists = d.groupby("domain", sort=True)[col].agg(list)
    return pd.Series([lists.get(k, []) for k in index], index=index, dtype=object)


def build_companies(part: pd.DataFrame) -> pd.DataFrame:
    """One row per company domain among private companies (activity_type == 'PRC') with a non-platform domain.

    country = mode over participations (ties -> alphabetical); multi_country = organisations of the domain appear with
    more than one country (kept, flagged); sme_any = any participation flagged SME (NA if SME never reported).
    """
    prc = part.loc[(part["activity_type"] == "PRC") & part["domain"].notna(),
                   ["domain", "name", "country", "sme", "public_ts", "programme", "organisation_id"]]
    if prc.empty:
        out = pd.DataFrame({c: pd.Series(dtype=object) for c in COMPANY_COLUMNS})
        out["first_public_ts"] = pd.to_datetime(out["first_public_ts"], utc=True)
        return out
    g = prc.groupby("domain", sort=True)
    idx = g.size().index
    cc = prc.dropna(subset=["country"]).groupby(["domain", "country"]).size().rename("n").reset_index()
    cc = cc.sort_values(["domain", "n", "country"], ascending=[True, False, True], kind="mergesort")
    mode = cc.drop_duplicates("domain").set_index("domain")["country"]
    sme = prc["sme"].astype("Float64").groupby(prc["domain"]).max()
    countries = _sorted_unique_lists(prc, "country", idx)
    out = pd.DataFrame({
        "domain": idx,
        "names": _sorted_unique_lists(prc, "name", idx).values,
        "country": mode.reindex(idx).values,
        "countries": countries.values,
        "multi_country": countries.map(len).gt(1).values,
        "sme_any": pd.array([pd.NA if pd.isna(v) else bool(v) for v in sme.reindex(idx)], dtype="boolean"),
        "first_public_ts": g["public_ts"].min().reindex(idx).array,
        "n_participations": g.size().reindex(idx).astype("int64").values,
        "programmes": _sorted_unique_lists(prc, "programme", idx).values,
        "organisation_ids": _sorted_unique_lists(prc, "organisation_id", idx).values,
    }, columns=COMPANY_COLUMNS)
    out["first_public_ts"] = pd.to_datetime(out["first_public_ts"], utc=True)
    return out.reset_index(drop=True)


# ======================================================================================================================
# Summary
def summarize(part: pd.DataFrame, comp: pd.DataFrame) -> str:
    def block(d: pd.DataFrame) -> dict:
        prc = d["activity_type"] == "PRC"
        return {"rows": len(d), "projects": d["project_id"].nunique(),
                "pct_url": round(100 * d["organization_url"].notna().mean(), 1),
                "pct_domain": round(100 * d["domain"].notna().mean(), 1),
                "pct_PRC": round(100 * prc.mean(), 1),
                "PRC_domains": d.loc[prc, "domain"].nunique(),
                "pct_missing_sig": round(100 * d["ec_signature_date"].isna().mean(), 1),
                "pct_missing_public_ts": round(100 * d["public_ts"].isna().mean(), 2)}

    rows = {p: block(g) for p, g in part.groupby("programme", sort=True)}
    rows["ALL"] = block(part)
    lines = ["CORDIS participations by programme", pd.DataFrame.from_dict(rows, orient="index").to_string(), ""]
    lines.append(f"distinct PRC company domains: {len(comp)}  (multi_country: {int(comp['multi_country'].sum())}; "
                 f"sme_any true: {int(comp['sme_any'].fillna(False).sum())})")
    yrs = comp["first_public_ts"].dt.year.value_counts().sort_index()
    lines.append("first_public_ts by year (PRC domains): "
                 + ("  ".join(f"{int(y)}:{int(n)}" for y, n in yrs.items()) or "-"))
    return "\n".join(lines)


# ======================================================================================================================
def obtain_zips(programmes: list[str], zip_dir: Path | None, skip_download: bool, raw_dir: Path,
                client: Client | None = None) -> dict[str, Path]:
    zips: dict[str, Path] = {}
    blocked = []
    for p in programmes:
        fallback_name = Path(PROGRAMMES[p][1]).name
        if zip_dir:
            cands = sorted(f for f in Path(zip_dir).iterdir() if f.name.lower() == fallback_name.lower())
            if not cands:
                raise SystemExit(f"{fallback_name} not found in {zip_dir}")
            zips[p] = import_zip(cands[0], p, PROGRAMMES[p][1], raw_dir)
        elif skip_download:
            cands = sorted(f for f in raw_dir.glob("*.zip") if f.name.lower() == fallback_name.lower())
            if not cands:
                raise SystemExit(f"{raw_dir / fallback_name} missing; run without --skip-download or with --zip-dir")
            zips[p] = cands[0]
        else:
            try:
                zips[p] = download(client or Client(), p, raw_dir)
            except RobotsDisallowed as e:
                log.error("%s: %s", p, e)
                blocked.append(p)
    if blocked:
        urls = "\n".join(f"  {PROGRAMMES[p][1]}" for p in blocked)
        raise SystemExit("robots.txt disallows automated download of:\n" + urls +
                         "\nDownload these by hand into one folder and run:\n"
                         "  .venv/bin/python -m scripts.sources.cordis --zip-dir <folder>")
    return zips


def run(zips: dict[str, Path], out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    parts, all_stats = [], {}
    for p, z in zips.items():
        part, st = parse_zip(z, p)
        log.info("%s: %d participations (org parse %.4f, project parse %.4f, join %.4f)", p, len(part),
                 st["organization"]["parse_rate"], st["project"]["parse_rate"], st["join_rate"])
        parts.append(part)
        all_stats[p] = st
    part = pd.concat(parts, ignore_index=True)
    part = part.sort_values(["programme", "project_id", "organisation_id"], kind="mergesort").reset_index(drop=True)
    comp = build_companies(part)

    out_dir.mkdir(parents=True, exist_ok=True)
    p_out, c_out = out_dir / "cordis_participations.parquet", out_dir / "cordis_companies.parquet"
    part.to_parquet(p_out, index=False)
    comp.to_parquet(c_out, index=False)
    srcs = []
    for z in zips.values():
        m = z.with_name(z.name + ".manifest.json")
        srcs.append(json.loads(m.read_text())["url"] if m.exists() else str(z))
    common = dict(script=SCRIPT, inputs=list(zips.values()), sources=srcs)
    write_meta(p_out, rows=len(part), params={"programmes": list(zips), "parse_stats": all_stats},
               notes="one row per (programme, project_id, organisation_id); public_ts = ecSignatureDate else startDate "
                     "(00:00 UTC); contact columns never loaded; e-mail-like organizationURL values dropped; "
                     "country UK->GB, EL->GR; " + LICENSE, **common)
    write_meta(c_out, rows=len(comp), params={"programmes": list(zips), "filter": "activity_type == 'PRC' and domain"},
               notes="one row per company domain; country = mode over participations (ties alphabetical); "
                     "multi_country flags domains seen with >1 country; " + LICENSE, **common)
    return part, comp, all_stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--programmes", nargs="+", default=list(PROGRAMMES), choices=list(PROGRAMMES))
    ap.add_argument("--zip-dir", type=Path, help="import hand-downloaded CSV packages from this folder")
    ap.add_argument("--skip-download", action="store_true", help="only parse packages already in the raw dir")
    ap.add_argument("--raw-dir", type=Path, default=RAW_DIR, help=argparse.SUPPRESS)
    ap.add_argument("--out-dir", type=Path, default=PROCESSED, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    zips = obtain_zips(args.programmes, args.zip_dir, args.skip_download, args.raw_dir)
    part, comp, stats = run(zips, args.out_dir)
    print(json.dumps({p: {"separator": s["organization"]["separator"], "encoding": s["organization"]["encoding"],
                          "org_parse_rate": round(s["organization"]["parse_rate"], 5),
                          "org_rows_bad": s["organization"]["rows_bad"],
                          "org_lines_in_bad_rows": s["organization"]["lines_in_bad_rows"],
                          "project_encoding": s["project"]["encoding"],
                          "project_parse_rate": round(s["project"]["parse_rate"], 5),
                          "project_rows_bad": s["project"]["rows_bad"],
                          "project_lines_in_bad_rows": s["project"]["lines_in_bad_rows"],
                          "unparsed_dates": s["unparsed_dates"],
                          "url_email_like_dropped": s["url_email_like_dropped"],
                          "join_rate": round(s["join_rate"], 5), "duplicate_key_rows": s["duplicate_key_rows"],
                          "unknown_activity_type": s["unknown_activity_type"],
                          "contact_columns_dropped": s["organization"]["contact_columns_dropped"]}
                      for p, s in stats.items()}, indent=1))
    print(summarize(part, comp))
    print(f"\nwrote {args.out_dir / 'cordis_participations.parquet'} ({len(part)} rows), "
          f"{args.out_dir / 'cordis_companies.parquet'} ({len(comp)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
