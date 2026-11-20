import pandas as pd

from scripts.resolve_entities import cordis_entity_summary


def test_cordis_entity_summary_first_grant_and_country():
    links = pd.DataFrame({"entity_id": ["a.de", "b.gr"], "organisation_id": ["1", "2"],
                          "match_method": ["name_country", "domain"], "match_key": ["a", "b.gr"]})
    part = pd.DataFrame({"organisation_id": ["1", "1", "2", "3"], "activity_type": ["PRC", "PRC", "PRC", "PRC"],
                         "public_ts": pd.to_datetime(["2018-05-01", "2016-02-01", "2019-01-01", "2010-01-01"], utc=True),
                         "country": ["DE", "DE", "EL", "FR"]})
    s = cordis_entity_summary(links, part).set_index("entity_id")
    assert s.loc["a.de", "cordis_first_public_ts"] == pd.Timestamp("2016-02-01", tz="UTC")
    assert s.loc["b.gr", "cordis_country"] == "GR"                 # CORDIS 'EL' mapped to ISO 'GR'
    assert s.loc["a.de", "cordis_match_method"] == "name_country"
    assert set(s.index) == {"a.de", "b.gr"}                        # org 3 is not linked to any entity
