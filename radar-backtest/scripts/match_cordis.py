"""Link entities to CORDIS private-company participants (has_public_rd_grant).

CORDIS stores a website only for FP7-era organisations (H2020/Horizon newcomers: ~0.1%), so domain matching alone
misses almost every post-2014 grantee. Two rules, applied identically to every entity, both "high" confidence:

  R1 domain        CORDIS organizationURL registrable domain == entity domain.
  R2 name+country  normalised legal-name key == one of the entity's name keys AND CORDIS country == entity country,
                   and that (key, country) identifies exactly ONE CORDIS organisation (PIC). Ambiguous -> no link.

Entity name keys come from sources that exist for EVERY entity in the GitHub-only pool, never from label sources
(YC/HTGF names would give positives an extra key and bias the grant metric):
  domain stem (>= 5 chars), matched GitHub org login and display name (>= 4 chars).
Entity country = ccTLD (scripts.lib.domains.country_from_domain); unknown country -> R2 not applicable.

Outputs: data/processed/entity_cordis.parquet (entity_id, organisation_id, match_method, match_key, country)
         data/processed/match_sample_cordis.csv (30 random R2 links for the human precision check)
"""
from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd

from .lib.config import PROCESSED, get_logger, seed
from .lib.domains import country_from_domain, domain_stem, name_slug
from .lib.meta import write_meta

log = get_logger("match_cordis")

MIN_NAME_KEY = 4
MIN_STEM_KEY = 5
# CORDIS uses EL for Greece and UK for the United Kingdom; ccTLDs use GR and GB.
CORDIS_TO_ISO = {"EL": "GR", "UK": "GB"}


def key(s: str | None) -> str:
    """Normalised comparison key: ASCII slug without legal suffixes, hyphens removed."""
    return re.sub(r"[^a-z0-9]", "", name_slug(s if isinstance(s, str) else ""))


def entity_keys(entity_id: str, org_login: str | None, display_name: str | None) -> set[str]:
    out = set()
    st = key(domain_stem(entity_id))
    if len(st) >= MIN_STEM_KEY:
        out.add(st)
    for n in (org_login, display_name):
        k = key(n)
        if len(k) >= MIN_NAME_KEY:
            out.add(k)
    return out


def cordis_orgs(part: pd.DataFrame) -> pd.DataFrame:
    """One row per PRC organisation (PIC) with its name keys, country and domain."""
    p = part[part["activity_type"] == "PRC"].copy()
    p["country_iso"] = p["country"].map(lambda c: CORDIS_TO_ISO.get(c, c) if isinstance(c, str) else None)
    rows = []
    for oid, g in p.groupby("organisation_id"):
        keys = {key(n) for n in pd.concat([g["name"], g["short_name"]]).dropna() if len(key(n)) >= MIN_NAME_KEY}
        rows.append({"organisation_id": oid, "keys": keys,
                     "country": g["country_iso"].mode().iat[0] if g["country_iso"].notna().any() else None,
                     "domains": set(g["domain"].dropna())})
    return pd.DataFrame(rows)


def match(entities: pd.DataFrame, orgs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """entities: entity_id, github_org, github_display_name. Returns links + counters."""
    by_domain: dict[str, set] = {}
    by_key_country: dict[tuple, set] = {}
    for r in orgs.itertuples():
        for d in r.domains:
            by_domain.setdefault(d, set()).add(r.organisation_id)
        if r.country:
            for k in r.keys:
                by_key_country.setdefault((k, r.country), set()).add(r.organisation_id)
    links, stats = [], {"r1_domain": 0, "r2_name_country": 0, "r2_ambiguous": 0, "r2_no_country": 0}
    for r in entities.itertuples():
        e = r.entity_id
        oids = by_domain.get(e, set())
        if oids:
            for o in sorted(oids):
                links.append({"entity_id": e, "organisation_id": o, "match_method": "domain", "match_key": e})
            stats["r1_domain"] += 1
            continue
        cc = country_from_domain(e)
        if cc is None:
            stats["r2_no_country"] += 1
            continue
        found, amb = set(), False
        for k in sorted(entity_keys(e, getattr(r, "github_org", None), getattr(r, "github_display_name", None))):
            hit = by_key_country.get((k, cc), set())
            if len(hit) == 1:
                found |= {(next(iter(hit)), k)}
            elif len(hit) > 1:
                amb = True
        if len({o for o, _ in found}) == 1 and not amb:
            o, k = sorted(found)[0]
            links.append({"entity_id": e, "organisation_id": o, "match_method": "name_country", "match_key": k})
            stats["r2_name_country"] += 1
        elif found or amb:
            stats["r2_ambiguous"] += 1
    out = pd.DataFrame(links, columns=["entity_id", "organisation_id", "match_method", "match_key"])
    out["match_confidence"] = "high"
    return out, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entities", default=str(PROCESSED / "entity_github_org.parquet"),
                    help="entity -> GitHub org links (entity_id, org_login[, display_name])")
    a = ap.parse_args(argv)
    links_gh = pd.read_parquet(a.entities)
    owners_p = PROCESSED / "github_owners.parquet"
    disp = {}
    if owners_p.exists():
        ow = pd.read_parquet(owners_p)
        disp = dict(zip(ow["login"].str.lower(), ow.get("display_name", pd.Series([None] * len(ow)))))
    ent = pd.DataFrame({"entity_id": links_gh["entity_id"], "github_org": links_gh["org_login"]})
    ent["github_display_name"] = ent["github_org"].str.lower().map(disp)
    part = pd.read_parquet(PROCESSED / "cordis_participations.parquet")
    orgs = cordis_orgs(part)
    links, stats = match(ent, orgs)
    out = PROCESSED / "entity_cordis.parquet"
    links.to_parquet(out, index=False)
    write_meta(out, "scripts/match_cordis.py", inputs=[PROCESSED / "cordis_participations.parquet", a.entities],
               rows=len(links), params={"min_name_key": MIN_NAME_KEY, "min_stem_key": MIN_STEM_KEY, **stats},
               notes="R1 domain equality; R2 unique normalised legal name + same country (ccTLD). Same keys for all "
                     "entities (domain stem, GitHub login/display name); label names never used.")
    r2 = links[links["match_method"] == "name_country"]
    if len(r2):
        samp = r2.sample(min(30, len(r2)), random_state=seed()).merge(
            part.drop_duplicates("organisation_id")[["organisation_id", "name", "country"]], on="organisation_id")
        samp = samp.merge(ent, on="entity_id", how="left")
        samp["is_correct (y/n, human)"] = ""
        sp = PROCESSED / "match_sample_cordis.csv"
        samp.to_csv(sp, index=False)
        write_meta(sp, "scripts/match_cordis.py", rows=len(samp), notes="Human precision check of R2 links.")
    log.info("CORDIS links: %d entities (%s)", links["entity_id"].nunique(), stats)


if __name__ == "__main__":
    main()
