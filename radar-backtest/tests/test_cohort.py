import inspect
from datetime import datetime, timezone

import pandas as pd
import pytest

from scripts.lib import cohort
from scripts.select_controls import build_universe, hash_uniform

UTC = timezone.utc
A = datetime(2016, 1, 1, tzinfo=UTC)


def _ent(**over):
    base = dict(entity_id=["a.com", "b.com", "c.com", "d.de", "e.com"],
                in_B=[True, True, False, True, False],
                inclusion_prob=[0.1, 0.1, 0.0, 0.5, 0.0],
                github_org=["a", "b", "c", None, "e"],
                github_org_created_at=[None] * 5,
                gh_first_event_ts=["2013-06-01", "2010-01-01", "2014-02-01", None, "2016-01-02"],
                cordis_first_public_ts=[None, None, None, "2015-03-01", None])
    base.update(over)
    return pd.DataFrame(base)


def test_visible_at_rules():
    v = cohort.visible_at(_ent(), A, channels=["github", "cordis"])
    # a: org age 2.7y, active before as_of -> visible; b: org older than 5y -> not; c: visible;
    # d: CORDIS grant before as_of -> visible; e: first event after as_of -> not visible
    assert v["visible"].tolist() == [True, False, True, True, False]
    assert "label" not in inspect.signature(cohort.visible_at).parameters
    v_gh = cohort.visible_at(_ent(), A, channels=["github"])       # CORDIS no longer makes an entity visible
    assert v_gh["visible"].tolist() == [True, False, True, False, False]
    assert v_gh["cordis_cov"].tolist() == [False, False, False, True, False]


def test_label_window_and_already_funded():
    f = pd.Series(["2015-12-31", "2016-01-01", "2017-12-31", "2018-01-01", None])
    lab = cohort.label_at(f, A, horizon_months=24)
    assert lab["already_funded"].tolist() == [True, False, False, False, False]
    assert lab["label"].tolist() == [0, 1, 1, 0, 0]


def test_label_window_complete():
    assert cohort.label_window_complete(datetime(2024, 1, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))
    assert not cohort.label_window_complete(datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))


def test_case_cohort_composition_and_weights():
    ent = _ent()
    f = pd.Series([None, None, "2016-06-01", "2014-01-01", None])  # c funded in window (case, not in B); d already funded
    cc = cohort.case_cohort(ent, f, A)
    assert set(cc["entity_id"]) == {"a.com", "c.com"}   # d.de already funded; e not yet visible
    w = dict(zip(cc["entity_id"], cc["weight"]))
    assert w["c.com"] == 1.0 and w["a.com"] == pytest.approx(10.0)


def test_case_cohort_drops_labelled_noncases_outside_B():
    ent = _ent()
    f = pd.Series([None, None, "2019-06-01", None, None])  # c funded later (not a case now) and not in B -> excluded
    cc = cohort.case_cohort(ent, f, A)
    assert "c.com" not in set(cc["entity_id"])


def test_hash_uniform_deterministic():
    assert hash_uniform("acme.com", 1) == hash_uniform("acme.com", 1)
    assert hash_uniform("acme.com", 1) != hash_uniform("acme.com", 2)
    assert 0 <= hash_uniform("x", 3) < 1


def test_universe_never_reads_labels_and_combines_probs():
    import ast
    import scripts.select_controls as sc
    tree = ast.parse(inspect.getsource(sc))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and not n.value.lstrip().startswith("Build the outcome-blind")]
    assert "LABELS" not in names
    assert not any("companies.csv" in c or "data/labels" in c for c in consts)
    sample = pd.DataFrame({"login": ["acme", "zz"], "uuid": [1, 2], "first_event_ts": ["2014-01-01", "2015-01-01"],
                           "first_event_year": [2014, 2015], "website_domain": ["acme.com", None],
                           "display_name": ["Acme", None], "in_pool": [True, False], "inclusion_prob": [0.2, 0.2]})
    companies = pd.DataFrame({"domain": ["acme.com", "beta.de"], "names": [["ACME GmbH"], ["Beta"]],
                              "first_public_ts": ["2015-01-01", "2016-01-01"]})
    u = build_universe(sample, companies, fraction=1.0, salt=1)
    acme = u.set_index("entity_id").loc["acme.com"]
    assert acme["inclusion_prob"] == pytest.approx(1.0)
    assert set(u["entity_id"]) == {"acme.com", "beta.de"}
    u0 = build_universe(sample, companies, fraction=0.0, salt=1)
    assert u0.set_index("entity_id").loc["acme.com", "inclusion_prob"] == pytest.approx(0.2)


def test_visibility_requires_pool_entry_by_as_of():
    ent = _ent()
    ent["gh_pool_entry_ts"] = ["2014-01-01", None, "2016-06-01", None, None]   # c enters only after as_of
    v = cohort.visible_at(ent, A, channels=["github"])
    assert v["visible"].tolist() == [True, False, False, False, False]


def test_pipeline_quota_parser():
    from scripts.pipeline import quota_reset_wait
    assert quota_reset_wait("some other error") is None
    w = quota_reset_wait("Code: 201. Quota for user `play` for 3600s has been exceeded: read_rows = 1/1. "
                         "Interval will end at 2000-01-01 00:00:00.")
    assert w == 30.0                                   # interval already over -> short wait
