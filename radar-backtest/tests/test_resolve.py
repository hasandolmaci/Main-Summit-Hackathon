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
