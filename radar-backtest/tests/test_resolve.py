import pandas as pd

from scripts.resolve_entities import cordis_entity_summary


def test_cordis_entity_summary_first_grant_and_country():
    links = pd.DataFrame({"entity_id": ["a.de", "b.gr"], "organisation_id": ["1", "2"],
                          "match_method": ["name_country", "domain"], "match_key": ["a", "b.gr"]})
    part = pd.DataFrame({"organisation_id": ["1", "1", "2", "3", "2"], "activity_type": ["PRC"] * 5,
                         "public_ts": pd.to_datetime(["2018-05-01", "2016-02-01", "2019-01-01", "2010-01-01", "2015-01-01"], utc=True),
                         "country": ["DE", "DE", "EL", "FR", "EL"],
                         "ec_contribution": [1e5, 2e5, 5e4, 1e5, 0.0]})          # org 2's 2015 role is unfunded
    s = cordis_entity_summary(links, part).set_index("entity_id")
    assert s.loc["a.de", "cordis_first_public_ts"] == pd.Timestamp("2016-02-01", tz="UTC")
    assert s.loc["b.gr", "cordis_country"] == "GR"                 # CORDIS 'EL' mapped to ISO 'GR'
    assert s.loc["a.de", "cordis_match_method"] == "name_country"
    assert set(s.index) == {"a.de", "b.gr"}                        # org 3 is not linked to any entity
    assert s.loc["b.gr", "cordis_first_public_ts"] == pd.Timestamp("2019-01-01", tz="UTC")   # unfunded 2015 role ignored


from scripts.resolve_entities import pool_entry_ts


def _daily(days, totals):
    return pd.DataFrame({"day": pd.to_datetime(days, utc=True), "total": totals})


def test_pool_entry_rule_met_on_first_qualifying_day():
    first = pd.Timestamp("2016-03-01 10:00", tz="UTC")
    daily = _daily(["2016-03-01", "2016-03-05", "2016-04-01"], [4, 4, 5])
    team = pd.Series(pd.to_datetime(["2016-03-01 10:00", "2016-04-01 08:00"], utc=True))
    # cum activity reaches 13 on 04-01 and the 2nd pusher arrives that day -> entry at end of 2016-04-01
    assert pool_entry_ts(first, daily, team, min_act=10, min_pushers=2) == pd.Timestamp("2016-04-02", tz="UTC")


def test_pool_entry_rule_never_met_after_first_year():
    first = pd.Timestamp("2016-03-01", tz="UTC")
    daily = _daily(["2016-03-01", "2017-06-01"], [3, 50])            # activity after the first 365 days does not count
    team = pd.Series(pd.to_datetime(["2016-03-01", "2016-03-02"], utc=True))
    assert pd.isna(pool_entry_ts(first, daily, team, min_act=10, min_pushers=2))


def test_pool_entry_needs_two_pushers():
    first = pd.Timestamp("2016-03-01", tz="UTC")
    daily = _daily(["2016-03-01"], [100])
    team = pd.Series(pd.to_datetime(["2016-03-01"], utc=True))
    assert pd.isna(pool_entry_ts(first, daily, team, min_act=10, min_pushers=2))


def test_decontaminate_removes_name_clash_controls():
    from scripts.resolve_entities import decontaminate
    ent = pd.DataFrame({"entity_id": ["airbyte.io", "zeta.com", "acme.com"], "in_B": [True, True, False],
                        "is_labelled": [False, False, True], "inclusion_prob": [0.01, 0.01, 0.0],
                        "github_org": ["airbytehq", "zetaorg", "acme"]})
    labels = pd.DataFrame({"company": ["Airbyte", "Acme"], "domain": ["airbyte.com", "acme.com"]})
    out = decontaminate(ent, labels).set_index("entity_id")
    assert not out.loc["airbyte.io", "in_B"] and out.loc["airbyte.io", "inclusion_prob"] == 0   # funded under another domain
    assert out.loc["zeta.com", "in_B"]


def test_recycled_guard_clears_links_with_pandas3_dtypes(tmp_path, monkeypatch):
    import scripts.resolve_entities as re_
    monkeypatch.setattr(re_, "PROCESSED", tmp_path)
    pd.DataFrame({"hour_ts": ["2015-01-01T04:00:00+00:00", "2021-01-01T04:00:00+00:00", "2021-04-01T04:00:00+00:00"],
                  "max_id": [10_000_000, 70_000_000, 80_000_000], "n_events": [1, 1, 1]}).to_csv(tmp_path / "gh_id_date_map.csv", index=False)
    pd.DataFrame({"owner": ["calcom"], "day": [pd.Timestamp("2014-03-01").date()], "push": [20], "pr": [0], "issues": [0],
                  "release": [0]}).to_parquet(tmp_path / "gh_org_activity_daily.parquet")
    pd.DataFrame({"owner": ["calcom", "calcom"], "actor_hash": ["a", "b"],
                  "first_push_ts": pd.to_datetime(["2014-03-01", "2014-03-01"], utc=True)}).to_parquet(tmp_path / "gh_team.parquet")
    ent = pd.DataFrame({"entity_id": ["cal.com"], "github_org": ["calcom"], "github_org_uuid": [79_145_102.0],
                        "github_org_created_at": pd.to_datetime(["2014-03-01"], utc=True), "github_match_method": ["x"],
                        "github_match_confidence": ["high"], "github_tier": [1.0],
                        "gh_first_event_ts": pd.to_datetime(["2014-03-01"], utc=True)})
    out = re_.add_pool_entry(ent)
    assert bool(out.loc[0, "gh_login_recycled"]) and pd.isna(out.loc[0, "github_org"]) and pd.isna(out.loc[0, "github_tier"])
