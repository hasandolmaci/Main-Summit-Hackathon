"""Cleaning rules (fixed in advance). This module NEVER reads y_* columns: they are split off before any rule
runs (`split_outcomes`) and re-attached afterwards. Blanks stay NaN; nothing is filled with 0.

Rules
 a) missing != 0 ............ NaN preserved; <col>_missing indicators are added at feature time
 b) domain_invalid .......... id_domain is a platform / university domain or the snapshot shows another site
                              -> blank all domain- and snapshot-derived fields (row kept)
 c) f_snapshot_invalid ...... parked / for-sale / unrelated page -> blank page-derived fields (row kept)
 d) not_a_startup ........... legal form e.V. or cohort description says student group/club -> row dropped, logged
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..lib.domains import PLATFORM_DOMAINS, registrable_domain

# --- field groups ---------------------------------------------------------------------------------------------
SNAPSHOT_DERIVED = ["f_snapshot_date", "f_impressum", "f_legal_entity", "f_gmbh_ig", "f_pricing_page", "f_waitlist",
                    "f_customer_logos", "f_jobs_page", "f_product_live", "f_description_text",
                    "s_homepage_snapshot_ts", "s_impressum_snapshot_ts", "s_analytics_tools", "s_ab_testing_tool",
                    "s_founder_names", "s_managing_directors", "s_founders_named_in_impressum", "s_legal_form",
                    "s_registered_court_hrb", "s_parent_company_mentioned", "s_team_page_found", "s_team_size_on_page",
                    "s_jobs_page_found", "s_job_titles", "s_contact_email_present", "s_careers_engineering_roles",
                    "s_careers_sales_roles"]
DOMAIN_DERIVED = ["f_domain", "f_first_capture", "f_age_months", "f_snapshot_missing", "f_first_cert_date",
                  "f_certs_before"] + SNAPSHOT_DERIVED
GH_FIELDS = ["m_gh_account", "m_gh_account_type", "m_gh_match_method", "m_gh_created", "m_gh_public_repos_before",
             "m_gh_has_open_source", "m_gh_members_checked", "m_gh_external_contribs_12m", "m_gh_external_score"]
GH_DOMAIN_METHODS = {"blog_domain", "homepage_link"}          # a GitHub match made THROUGH the (invalid) domain

# --- explicit lists (from manual inspection, fixed before the loop) -------------------------------------------
DOMAIN_INVALID = {"beworm": "id_domain is linkedin.com (platform)",
                  "opsyon": "id_domain is a uni-muenchen.de subdomain (university)",
                  "mbee": "snapshot is another site (stabl.com, 2005 page)"}
SNAPSHOT_INVALID = {"cedardb": "snapshot is an unrelated Chinese-language site",
                    "esgx": "parked / unrelated page", "medicsen": "parked / unrelated page",
                    "reonic": "parked / unrelated page"}
NOT_STARTUP = {"neurotum": "student group", "warrspacelabs": "student group (WARR)",
               "impetussailingteam": "student sailing team", "tufastmotoelectric": "student racing team",
               "robotum": "student robotics club", "enhands": "student group"}

UNIVERSITY_RX = re.compile(r"(^|\.)(uni-[a-z-]+\.de|tum\.de|lmu\.de|rwth-aachen\.de|kit\.edu|tu-[a-z-]+\.de|"
                           r"fraunhofer\.de|mpg\.de|helmholtz\.de|ethz\.ch|epfl\.ch|uzh\.ch|univie\.ac\.at|[a-z-]+\.edu|"
                           r"ac\.uk|ac\.at)$")
PARKED_RX = re.compile(r"domain (is )?(for sale|zu verkaufen)|buy this domain|diese domain kaufen|parked (free|domain)|"
                       r"this domain may be for sale|sedo|dan\.com", re.I)
STUDENT_RX = re.compile(r"\b(student(ische|en)?\s*(group|team|club|initiative|verein|gruppe)|studentengruppe|"
                        r"hochschulgruppe|student club|student team)\b", re.I)
EV_RX = re.compile(r"^\s*e\.\s?v\.?\s*$", re.I)


def split_outcomes(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(features+ids without y_*/cb_*/needs_review/evidence, outcome columns keyed by id_company)."""
    out_cols = [c for c in df.columns if c.startswith(("y_", "cb_")) or c in ("needs_review", "evidence_url")]
    return df.drop(columns=out_cols), df[["id_company"] + out_cols]


def is_bad_domain(domain) -> tuple[bool, str]:
    if not isinstance(domain, str) or not domain.strip():
        return False, ""
    d = registrable_domain(domain) or domain
    host = domain.lower().strip()
    if d in PLATFORM_DOMAINS:
        return True, f"platform domain {d}"
    if UNIVERSITY_RX.search(host) or UNIVERSITY_RX.search(d):
        return True, f"university/research domain {host}"
    return False, ""


def clean(x: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """x: feature frame WITHOUT outcome columns. Returns (cleaned frame, log entries)."""
    assert not [c for c in x.columns if c.startswith("y_")], "cleaning must not see y_* columns"
    x = x.copy()
    log = []
    x["domain_invalid"] = False
    x["f_snapshot_invalid"] = False
    for i, r in x.iterrows():
        cid = r["id_company"]
        bad, why = is_bad_domain(r.get("id_domain"))
        if cid in DOMAIN_INVALID or bad:
            reason = DOMAIN_INVALID.get(cid, why)
            x.at[i, "domain_invalid"] = True
            gh_via_domain = r.get("m_gh_match_method") in GH_DOMAIN_METHODS
            cols = DOMAIN_DERIVED + (GH_FIELDS if gh_via_domain else [])
            x.loc[i, [c for c in cols if c in x]] = np.nan
            log.append({"rule": "b_domain_invalid", "id_company": cid, "reason": reason + ("; GitHub match via domain blanked" if gh_via_domain else "")})
            continue
        txt = r.get("f_description_text")
        parked = isinstance(txt, str) and bool(PARKED_RX.search(txt))
        if cid in SNAPSHOT_INVALID or parked:
            x.at[i, "f_snapshot_invalid"] = True
            x.loc[i, [c for c in SNAPSHOT_DERIVED if c in x]] = np.nan
            log.append({"rule": "c_snapshot_invalid", "id_company": cid,
                        "reason": SNAPSHOT_INVALID.get(cid, "page text matches parked/for-sale pattern")})
    drop = []
    for i, r in x.iterrows():
        cid = r["id_company"]
        forms = [r.get("s_legal_form"), r.get("f_legal_entity")]
        ev = any(isinstance(f, str) and EV_RX.match(f) for f in forms)
        desc = r.get("info_f_description_cohort")
        student = isinstance(desc, str) and bool(STUDENT_RX.search(desc))
        if cid in NOT_STARTUP or ev or student:
            reason = NOT_STARTUP.get(cid) or ("legal form e.V." if ev else "cohort description: student group/club")
            drop.append(i)
            log.append({"rule": "d_not_a_startup", "id_company": cid, "reason": reason})
    x = x.drop(index=drop)
    return x, log
