import pandas as pd
import pytest

from scripts.sources import gh_features as gf


def test_sql_has_until_bound_and_single_scan_shape():
    sql = gf.sql_org_combined(["acme", "beta-io"], "2020-01-01")
    assert "created_at < toDateTime('2020-01-01 00:00:00', 'UTC')" in sql
    assert sql.count("FROM github_events") == 1 and "ARRAY JOIN" in sql
    with pytest.raises(ValueError):
        gf.sql_org_combined(["bad login; drop"], "2020-01-01")
    ext = gf.sql_external_until(["u1", "u2"], ["acme"], "2020-01-01")
    assert ext.count("FROM github_events") == 1 and "created_at < toDateTime('2020-01-01" in ext


def test_split_combined():
    raw = pd.DataFrame([
        # day rows
        {"owner": "acme", "day_s": "2015-03-01", "actor": "", "push": "2", "pr": "1", "issues": "0", "release": "0",
         "n_all": "5", "first_ts": "2015-03-01 10:00:00", "last_ts": "2015-03-01 18:00:00", "first_push_ts": "2015-03-01 11:00:00"},
        {"owner": "acme", "day_s": "2015-03-04", "actor": "", "push": "0", "pr": "0", "issues": "0", "release": "0",
         "n_all": "1", "first_ts": "2015-03-04 09:00:00", "last_ts": "2015-03-04 09:00:00", "first_push_ts": "1970-01-01 00:00:00"},
        # actor rows
        {"owner": "acme", "day_s": "", "actor": "alice", "push": "2", "pr": "0", "issues": "0", "release": "0",
         "n_all": "3", "first_ts": "2015-03-01 10:00:00", "last_ts": "2015-03-04 09:00:00", "first_push_ts": "2015-03-01 11:00:00"},
        {"owner": "acme", "day_s": "", "actor": "bob", "push": "0", "pr": "1", "issues": "0", "release": "0",
         "n_all": "3", "first_ts": "2015-03-01 12:00:00", "last_ts": "2015-03-01 18:00:00", "first_push_ts": "1970-01-01 00:00:00"},
    ])
    span, daily, team = gf.split_combined(raw)
    assert span.iloc[0]["n_events"] == 6
    assert str(span.iloc[0]["first_event_ts"]).startswith("2015-03-01 10:00")
    assert len(daily) == 1 and daily.iloc[0]["push"] == 2          # day with only other event types is not 'activity'
    assert team["actor_login"].tolist() == ["alice"]                 # bob never pushed -> not team
