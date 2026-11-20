import numpy as np
import pandas as pd
import pytest

from scripts.lib import config, domains, scaling, schema


@pytest.mark.parametrize("raw,expected", [
    ("https://www.Acme.co.uk/about?x=1", "acme.co.uk"),
    ("acme.io", "acme.io"),
    ("http://app.acme.com:8080/", "acme.com"),
    ("  WWW.EXAMPLE.DE. ", "example.de"),
    ("", None),
    (None, None),
    ("not a domain", None),
    ("localhost", None),
])
def test_registrable_domain(raw, expected):
    assert domains.registrable_domain(raw) == expected


def test_platforms_blocked():
    assert domains.company_domain("https://acme.github.io") is None
    assert domains.company_domain("https://medium.com/@acme") is None
    assert domains.company_domain("https://foo.herokuapp.com") is None
    assert domains.company_domain("https://acme.dev") == "acme.dev"


def test_platform_owner_exception():
    assert domains.company_domain("https://gitlab.com") is None
    assert domains.company_domain("https://gitlab.com", ("GitLab",)) == "gitlab.com"
    assert domains.company_domain("https://about.gitlab.com/", ("GitLab Inc.",)) == "gitlab.com"
    assert domains.company_domain("https://acme.github.io", ("Acme",)) is None       # hosted page, not the platform
    assert domains.company_domain("https://ec.europa.eu/x") is None
    assert domains.login_rule("gitlabhq", "https://gitlab.com", "gitlab.com", display_name="GitLab")


def test_country_from_domain():
    assert domains.country_from_domain("acme.de") == "DE"
    assert domains.country_from_domain("acme.co.uk") == "GB"
    assert domains.country_from_domain("acme.io") is None
    assert domains.country_from_domain("acme.com") is None
    assert domains.country_from_domain("acme.ch") == "CH"


def test_name_slug():
    assert domains.name_slug("Acme Robotics, Inc.") == "acme-robotics"
    assert domains.name_slug("Müller GmbH") == "muller"
    assert domains.name_slug(None) == ""


def test_login_candidates_and_rule():
    c = domains.login_candidates("acme", "Acme Robotics")
    for x in ["acme", "acmehq", "acme-hq", "getacme", "acme-robotics", "acmerobotics", "acme-io"]:
        assert x in c
    assert "acmexyz" not in c
    assert domains.login_rule("acmehq", "https://www.acme.com", "acme.com")
    assert not domains.login_rule("acmehq", "https://other.com", "acme.com")      # website mismatch
    assert not domains.login_rule("randomorg", "https://acme.com", "acme.com")    # login not a candidate
    assert not domains.login_rule("acme", None, "acme.com")
    # display name can extend candidates (same rule for everyone)
    assert domains.login_rule("zeta-labs", "https://zeta.ai", "zeta.ai")
    assert domains.login_rule("zetarobotics", "https://zeta.ai", "zeta.ai", display_name="Zeta Robotics")


def test_percentile_rank_bounds_and_nan():
    ref = [0, 1, 2, 3, 4]
    p = scaling.percentile_rank([0, 2, 4, 10, np.nan], ref)
    assert np.isnan(p[-1])
    assert np.all((p[:-1] >= 0) & (p[:-1] <= 1))
    assert p[0] < p[1] < p[2] <= p[3] == 1.0


def test_robust_z_sigmoid_fallback_on_zero_iqr():
    ref = [0, 0, 0, 0, 5]
    out = scaling.log_robust_z_sigmoid([0, 5], ref)
    assert np.allclose(out, scaling.percentile_rank([0, 5], ref))
    out2 = scaling.log_robust_z_sigmoid([0, 3, 30], [0, 1, 2, 3, 4, 5, 6])
    assert np.all(np.diff(out2) > 0) and np.all((out2 > 0) & (out2 < 1))


def test_log_ratio_sigmoid():
    out = scaling.log_ratio_sigmoid([0, 10, 1000], [0, 0, 0], clip=3)
    assert out[0] == pytest.approx(0.5)
    assert out[1] > 0.5
    assert out[2] == pytest.approx(scaling.sigmoid(3.0))


def test_scaling_functions_take_no_labels():
    import inspect
    for fn in (scaling.percentile_rank, scaling.log_robust_z_sigmoid, scaling.log_ratio_sigmoid):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"y", "label", "labels", "target"}


def test_event_schema_roundtrip():
    df = schema.make_events([
        {"entity_id": "acme.com", "ts": "2015-03-01", "source": "gharchive", "type": "x", "value": 1, "detail": {"a": 1}},
    ])
    assert schema.validate_events(df) == []
    bad = df.assign(source="nope")
    assert schema.validate_events(bad)


def test_locked_year_guard(monkeypatch):
    locked = config.locked_years()
    with pytest.raises(config.LockedYearError):
        config.guard_years([locked[0]])
    monkeypatch.setenv("RADAR_UNLOCK_TEST", "1")
    with pytest.raises(config.LockedYearError):
        config.guard_years([locked[0]])                     # env alone is not enough
    assert config.guard_years([locked[0]], allow_locked=True) == [locked[0]]
    assert config.guard_years(config.selection_years()) == config.selection_years()


def test_config_country_codes_are_strings():
    # YAML 1.1 parses an unquoted NO (Norway) as boolean false
    c = config.cfg()
    for key in ("europe_iso2", "horizon_eligible_iso2"):
        assert all(isinstance(x, str) for x in c[key]), key
    assert "NO" in c["europe_iso2"] and "NO" in c["horizon_eligible_iso2"]
