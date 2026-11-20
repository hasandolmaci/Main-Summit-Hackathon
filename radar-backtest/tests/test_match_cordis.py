import pandas as pd

from scripts import match_cordis as mc


def _part():
    return pd.DataFrame({
        "organisation_id": ["1", "1", "2", "3", "4", "5"],
        "name": ["PROXIMA FUSION GMBH", "PROXIMA FUSION GMBH", "ACME GMBH", "ACME SRL", "SOLAR AG", "SOLAR GMBH"],
        "short_name": [None, "PF", None, None, None, None],
        "country": ["DE", "DE", "DE", "IT", "DE", "DE"],
        "activity_type": ["PRC"] * 6,
        "domain": [None, None, "acme-old.de", None, None, None],
    })


def test_keys_strip_legal_suffix_and_hyphens():
    assert mc.key("Proxima Fusion GmbH") == "proximafusion"
    assert mc.key("proxima-fusion") == "proximafusion"
    assert mc.entity_keys("proximafusion.de", "proxima-fusion", "Proxima Fusion") == {"proximafusion"}
    assert mc.entity_keys("ab.de", "ab", None) == set()            # too short to be a safe key


def test_rules_and_ambiguity():
    orgs = mc.cordis_orgs(_part())
    ent = pd.DataFrame({"entity_id": ["proximafusion.de", "acme-old.de", "acme.it", "solar.de", "proximafusion.com"],
                        "github_org": ["proxima-fusion", "acme", "acme", "solar", "proxima-fusion"],
                        "github_display_name": ["Proxima Fusion", None, None, None, None]})
    links, stats = mc.match(ent, orgs)
    got = dict(zip(links.entity_id, zip(links.organisation_id, links.match_method)))
    assert got["proximafusion.de"] == ("1", "name_country")
    assert got["acme-old.de"] == ("2", "domain")                    # R1 first
    assert got["acme.it"] == ("3", "name_country")                  # country disambiguates
    assert "solar.de" not in got                                     # two DE orgs share the key -> ambiguous
    assert "proximafusion.com" not in got                            # no country from .com -> R2 not applicable
    assert stats["r2_ambiguous"] == 1 and stats["r2_no_country"] == 1


def test_label_names_never_used():
    import inspect
    src = inspect.getsource(mc)
    assert "companies.csv" not in src and "LABELS" not in src
