"""Tests for the DACH cohort backtest (leakage guards, cleaning, conversion, loop controls)."""
import inspect

import numpy as np
import pandas as pd
import pytest

from scripts.dach import clean as C
from scripts.dach import features as FE
from scripts.dach import io, labels as LB, loop as LP


def _row(**kw):
    base = {c: np.nan for c in ["id_company", "id_domain", "id_name", "meta_cohort_source", "meta_cohort",
                                "meta_asof_date", "m_gh_created", "m_gh_public_repos_before", "m_gh_has_open_source",
                                "m_gh_external_contribs_12m", "f_snapshot_date", "s_homepage_snapshot_ts",
                                "s_impressum_snapshot_ts", "f_pricing_page", "f_customer_logos", "f_waitlist",
                                "f_product_live", "s_legal_form", "f_legal_entity", "s_registered_court_hrb",
                                "s_parent_company_mentioned", "s_team_size_on_page", "s_careers_engineering_roles",
                                "s_careers_sales_roles", "s_analytics_tools", "s_ab_testing_tool", "f_certs_before",
                                "f_first_capture", "m_exist_or_programme_grant", "m_foekat_projects_before",
                                "e_news_mentions_before_12m", "m_patent_verified_before_asof", "m_serial_score",
                                "s_managing_directors", "f_description_text"]}
    base.update({"id_company": "x", "meta_cohort_source": "s", "meta_cohort": "c", "meta_asof_date": "2022-03-01"})
    base.update(kw)
    return base


def test_github_created_after_asof_is_nan():
    df = pd.DataFrame([_row(id_company="a", m_gh_created="2023-01-01", m_gh_public_repos_before="5", m_gh_has_open_source="1"),
                       _row(id_company="b", m_gh_created="2020-01-01", m_gh_public_repos_before="5", m_gh_has_open_source="1")])
    F, _ = FE.build_features(df)
    assert np.isnan(F.loc[0, "gh_open_source"]) and np.isnan(F.loc[0, "gh_public_repos"])
    assert F.loc[1, "gh_open_source"] == 1


def test_snapshot_after_asof_blanks_page_fields():
    df = pd.DataFrame([_row(id_company="a", f_snapshot_date="2022-05-01", f_pricing_page="1"),
                       _row(id_company="b", f_snapshot_date="2021-12-01", f_pricing_page="1"),
                       _row(id_company="c", s_homepage_snapshot_ts="2022-03-01", s_parent_company_mentioned="X"),
                       _row(id_company="d", s_homepage_snapshot_ts="2022-01-01")])
    F, _ = FE.build_features(df)
    assert np.isnan(F.loc[0, "pricing_page"]) and F.loc[1, "pricing_page"] == 1
    assert np.isnan(F.loc[2, "corporate_spinoff"])          # snapshot month == as_of month -> not provably before
    assert F.loc[3, "corporate_spinoff"] == 0                # valid page, field blank -> 0


def test_missing_is_not_zero():
    df = pd.DataFrame([_row(id_company="a")])               # no snapshot at all
    F, _ = FE.build_features(df)
    for c in ["pricing_page", "registered_hrb", "growth_tooling", "team_size", "legal_form_level"]:
        assert np.isnan(F.loc[0, c]), c


def test_excel_timestamps_repaired_and_rejected():
    assert io.repair_wayback_ts("2.02202E+13")[0] == pd.Timestamp("2022-02-01")
    assert io.repair_wayback_ts("2.0221E+13")[0] == pd.Timestamp("2022-10-01")
    iso = io.to_iso_frame(pd.DataFrame({"meta_asof_date": ["01/03/2022"], "s_homepage_snapshot_ts": ["2.02202E+13"],
                                        "s_impressum_snapshot_ts": [""]}))
    assert iso.loc[0, "meta_asof_date"] == "2022-03-01" and iso.loc[0, "s_homepage_snapshot_ts"] == "2022-02-01"


def test_cleaning_never_sees_outcomes():
    df = pd.DataFrame([_row(id_company="a")]).assign(y_label="1")
    with pytest.raises(AssertionError):
        C.clean(df)
    src = inspect.getsource(C.clean)
    assert "y_label" not in src and "y_funding" not in src


def test_rules_on_real_data():
    x, _ = C.split_outcomes(io.read_iso())
    cx, log = C.clean(x)
    dropped = {l["id_company"] for l in log if l["rule"] == "d_not_a_startup"}
    assert {"neurotum", "warrspacelabs", "impetussailingteam", "tufastmotoelectric", "robotum", "enhands"} <= dropped
    assert not set(cx["id_company"]) & dropped
    inv = cx.set_index("id_company")
    assert inv.loc["beworm", "domain_invalid"] and pd.isna(inv.loc["beworm", "f_pricing_page"])
    assert inv.loc["cedardb", "f_snapshot_invalid"] and pd.isna(inv.loc["cedardb", "f_description_text"])


def test_tripwire_catches_needs_review():
    iso = io.read_iso()
    x, outcomes = C.split_outcomes(iso)
    lab = outcomes[outcomes["y_label"].isin(["0", "1"])]
    y = lab["y_label"].astype(int).to_numpy()
    nr = lab["needs_review"].astype(float).to_numpy()
    reason = LP.tripwire("needs_review", nr, y, {"timestamp_derived": False})
    assert reason is not None and "univariate AUC" in reason
    assert LP.tripwire("anything", np.zeros(len(y)), y, {"timestamp_derived": True}) == "derived from a timestamp"


def test_label_set():
    feats = pd.DataFrame({"id_company": list("abcdef")})
    outs = pd.DataFrame({"id_company": list("abcdef"), "y_label": ["1", "0", "", np.nan, "1", "0"],
                         "y_funding_status": ["FUNDED_WITHIN", "NO_FUNDING_FOUND", "FUNDED_BEFORE", "EXCLUDE_ACQUISITION",
                                              "FUNDED_WITHIN", "NO_FUNDING_FOUND"]})
    L = LB.labelled(feats, outs)
    assert list(L["id_company"]) == ["a", "b", "e", "f"]


def test_shuffle_within_groups_keeps_group_counts():
    y = np.array([1, 0, 0, 1, 0, 0, 0, 1])
    g = np.array(["a", "a", "a", "b", "b", "c", "c", "c"])
    ys = LP.permute_within_groups(y, g, np.random.default_rng(0))
    for k in "abc":
        assert ys[g == k].sum() == y[g == k].sum()


def test_allowlist_blocks_forbidden_sources():
    with pytest.raises(ValueError):
        FE.check_allowlist(pd.DataFrame({"x": [1]}), {"bad": {"source": ["info_li_employees_now"], "kind": "candidate"}})
    with pytest.raises(ValueError):
        FE.check_allowlist(pd.DataFrame({"needs_review": [1]}), {})


def test_backtest_and_live_conversion_identical(tmp_path):
    out = tmp_path / "live.csv"
    FE.main(["--input", str(io.RAW), "--output", str(out)])
    live = pd.read_csv(out)
    x, _ = C.split_outcomes(io.read_iso())
    cx, _ = C.clean(x)
    import json
    ref = json.load(open(FE.SCALING_REF)) if FE.SCALING_REF.exists() else None
    F, _ = FE.build_features(cx, ref)
    cols = [c for c in F.columns if c not in ("meta_cohort_source", "meta_cohort", "meta_asof_date", "id_company")]
    np.testing.assert_allclose(live[cols].to_numpy(float), F[cols].to_numpy(float), equal_nan=True)


def test_loop_keeps_planted_signal_and_drops_noise():
    rng = np.random.default_rng(1)
    n = 160
    L = pd.DataFrame({"meta_cohort_source": rng.choice(["p", "q"], n), "meta_cohort": rng.choice(list("abcdefgh"), n)})
    sig = rng.normal(size=n)
    y = (sig + rng.normal(scale=1.3, size=n) > 1.0).astype(int)      # univariate AUC ~0.75 (< tripwire)
    L["signal"], L["noise"], L["cohort_year"], L["src_p"] = sig, rng.normal(size=n), 2020.0, (L.meta_cohort_source == "p").astype(float)
    L["sparse"] = np.where(rng.random(n) < 0.8, np.nan, rng.normal(size=n))
    out = LP.run_loop(L, y, [], [("noise", {}), ("signal", {}), ("sparse", {})], ["src_p", "cohort_year"], seed=3)
    assert out["final_set"] == ["signal"]
    assert out["oof_auc"] > out["baseline_auc"] + 0.1
