import pandas as pd

from scripts.sources import org_like_pit as olp


def test_first_year_rule_ignores_later_years():
    rows = pd.DataFrame({"login": ["a", "a", "b", "b"], "y": ["2014", "2020", "2015", "2016"],
                         "n_events": ["200", "10000", "50", "100"], "n_personal": ["5", "0", "0", "90"]})
    r = olp.evaluate(rows).set_index("login")
    assert not r.loc["a", "org_like_pit"]        # personal events in its first year (converted user account)
    assert r.loc["b", "org_like_pit"]            # clean first year; later personal events do not matter
    assert r.loc["a", "first_year"] == 2014


def test_sql_single_scan_and_validated_logins():
    sql = olp.sql_owner_year(["acme", "beta-io"])
    assert sql.count("FROM github_events") == 1 and "toYear(created_at" in sql
