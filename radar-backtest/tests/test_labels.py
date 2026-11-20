"""Offline tests for the label collector (YC + HTGF). No network: synthetic fixtures and a fake HTTP client."""
import gzip
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from scripts import collect_labels as cl
from scripts.lib.http import CachedResponse
from scripts.lib.schema import LABEL_COLUMNS, validate_labels
from scripts.sources import htgf

FIX = Path(__file__).parent / "fixtures" / "labels"
PAGE = (FIX / "htgf_portfolio_page_synthetic.html").read_text()


# ------------------------------------------------------------------------------------------------ YC batch parsing
@pytest.mark.parametrize("raw,expected", [
    ("Winter 2015", ("Winter", 2015)),
    ("Summer 2021", ("Summer", 2021)),
    ("Fall 2024", ("Fall", 2024)),
    ("Spring 2025", ("Spring", 2025)),
    ("  winter   2015 ", ("Winter", 2015)),
    ("Autumn 2024", ("Fall", 2024)),
    ("W15", ("Winter", 2015)),
    ("S21", ("Summer", 2021)),
    ("X25", ("Spring", 2025)),
    ("F24", ("Fall", 2024)),
    ("w'09", ("Winter", 2009)),
    ("2015 Winter", ("Winter", 2015)),
    ("Unspecified", None),
    ("IK12", None),
    ("", None),
    (None, None),
    (2015, None),
    ("Winter 1999", None),
    ("Winter", None),
    ("Q3 2020", None),
])
def test_parse_batch(raw, expected):
    assert cl.parse_batch(raw) == expected


def test_batch_start_and_acceptance_offset():
    ss = {"Winter": [1, 15], "Spring": [4, 1], "Summer": [6, 15], "Fall": [9, 15]}
    assert cl.batch_start("Winter", 2015, ss) == date(2015, 1, 15)
    assert cl.batch_start("Fall", 2024, ss) == date(2024, 9, 15)
    assert cl.acceptance_proxy(date(2015, 1, 15), 76) == date(2014, 10, 31)
    assert cl.acceptance_proxy(date(2021, 6, 15), 0) == date(2021, 6, 15)
    # default offset comes from config
    off = cl.cfg()["label"]["acceptance_offset_days"]
    assert (date(2020, 1, 15) - cl.acceptance_proxy(date(2020, 1, 15))).days == off


def test_country_mapping():
    assert cl.country_iso2("USA") == "US"
    assert cl.country_iso2(" United Kingdom ") == "GB"
    assert cl.country_iso2("germany") == "DE"
    assert cl.country_iso2("Georgia") is None          # US state clash: deliberately unmapped
    assert cl.country_iso2("Remote") is None
    assert cl.yc_country("San Francisco, CA, USA; London, England, United Kingdom", ["Remote"]) == ("US", "location")
    assert cl.yc_country("", ["America / Canada", "Canada"]) == ("CA", "region")
    assert cl.yc_country("Remote", ["Remote", "Fully Remote"]) == (None, "none")


# ------------------------------------------------------------------------------------------------ YC records
def _yc(name, slug, website, batch, industry="B2B", status="Active", loc="Berlin, Germany", regions=("Germany",)):
    return {"name": name, "slug": slug, "website": website, "batch": batch, "industry": industry, "status": status,
            "all_locations": loc, "regions": list(regions), "launched_at": 0}


YC = [
    _yc("Acme", "acme", "https://www.Acme.io/about", "Winter 2015"),
    _yc("Acme Again", "acme-again", "http://acme.io", "Summer 2016"),          # duplicate domain, later batch
    _yc("NoSite", "nosite", None, "Winter 2016"),                              # no website
    _yc("Platform", "platform", "https://platform.github.io", "Winter 2016"),  # platform domain
    _yc("Odd", "odd", "https://odd.com", "Unspecified"),                       # unparseable batch
    _yc("Future", "future", "https://future.dev", "Winter 2027"),              # acceptance 2026-10-31
    _yc("Later", "later", "https://later.ai", "Summer 2027"),                  # after the data cutoff
]


def test_yc_records_and_domain_normalisation():
    counts = Counter()
    rows = cl.yc_records(YC, counts)
    assert len(rows) == len(YC)
    by = {r["company"]: r for r in rows}
    assert by["Acme"]["domain"] == "acme.io"
    assert by["Acme"]["batch_start"] == "2015-01-15" and by["Acme"]["acceptance_proxy"] == "2014-10-31"
    assert by["Acme"]["source_url"] == "https://www.ycombinator.com/companies/acme"
    assert by["Acme"]["country"] == "DE" and by["Acme"]["round_type"] == "accelerator"
    assert by["NoSite"]["excluded_reason"] == "no_company_domain"
    assert by["Platform"]["excluded_reason"] == "no_company_domain"
    assert by["Odd"]["excluded_reason"] == "yc_unparseable_batch" and by["Odd"]["batch_start"] == ""
    assert counts["drop:no_company_domain"] == 2 and counts["drop:yc_unparseable_batch"] == 1


def test_min_over_sources_dedup_and_cutoff():
    prov = cl.yc_records(YC)
    h = {"domain": "acme.io", "company": "Acme GmbH", "label_source": "htgf", "batch": "", "batch_start": "",
         "acceptance_proxy": "", "yc_status": "", "htgf_date": "2014-06-01", "date_precision": "day",
         "source_url": "https://www.htgf.de/en/x/", "first_funding_date": "", "round_type": "seed",
         "sector": "Digital Tech", "country": "DE", "evidence": "", "excluded_reason": ""}
    h_late = {**h, "domain": "future.dev", "htgf_date": "2026-12-01", "source_url": "https://www.htgf.de/en/y/"}
    counts = Counter()
    df, dups = cl.build_companies(prov + [h, h_late], "acceptance", date(2026, 11, 1), counts)
    assert list(df.columns) == LABEL_COLUMNS
    assert validate_labels(df) == []
    assert df["domain"].is_unique
    row = df.set_index("domain").loc["acme.io"]
    assert row["first_funding_date"] == "2014-06-01" and row["round_type"] == "seed"   # HTGF earlier than YC
    assert row["verified_by_human"] == "n"
    assert row["source_url"] == "https://www.htgf.de/en/x/"
    assert {d["domain"] for d in dups} == {"acme.io", "future.dev"}
    assert next(d for d in dups if d["domain"] == "acme.io")["n_records"] == 3
    # Winter 2027 acceptance proxy (2026-10-31) is before the cutoff; Summer 2027 is after it
    assert df.set_index("domain").loc["future.dev", "first_funding_date"] == "2026-10-31"
    assert "later.ai" not in set(df["domain"]) and counts["drop:after_data_cutoff"] == 1

    # batch_start sensitivity: YC dates move, HTGF stays; Winter 2027 batch start is now after the cutoff
    df_bs, _ = cl.build_companies(prov + [h], "batch_start", date(2026, 11, 1))
    assert "future.dev" not in set(df_bs["domain"])
    assert df_bs.set_index("domain").loc["acme.io", "first_funding_date"] == "2014-06-01"
    prov2 = cl.yc_records(YC[:2])
    df_bs2, _ = cl.build_companies(prov2, "batch_start", date(2026, 11, 1))
    assert df_bs2.set_index("domain").loc["acme.io", "first_funding_date"] == "2015-01-15"
    df_acc2, _ = cl.build_companies(prov2, "acceptance", date(2026, 11, 1))
    assert df_acc2.set_index("domain").loc["acme.io", "first_funding_date"] == "2014-10-31"
    assert df_acc2.set_index("domain").loc["acme.io", "company"] == "Acme"     # earliest record supplies the row


def test_build_companies_is_order_independent():
    prov = cl.yc_records(YC)
    a, _ = cl.build_companies(prov, "acceptance", date(2026, 11, 1))
    b, _ = cl.build_companies(list(reversed(prov)), "acceptance", date(2026, 11, 1))
    pd.testing.assert_frame_equal(a, b)


def test_yc_storable_drops_contact_columns_keeps_label_fields():
    recs = [{**_yc("Acme", "acme", "https://acme.io", "Winter 2015"),
             "long_description": "Write to jane.doe" + "@" + "acme.io any time.", "one_liner": "Robots.",
             "app_answers": None},
            {**_yc("Beta", "beta", "https://beta.dev", "Summer 2016"),
             "long_description": "No contact here.", "one_liner": "Call +49 30 1234 5678 today.",
             "app_answers": None}]
    stored, dropped, hits = cl.yc_storable(recs)
    assert dropped == ["long_description", "one_liner"] and hits == 0
    text = json.dumps(stored)
    assert "@" not in text and "+49" not in text
    assert all("app_answers" in s for s in stored)                       # columns without contact data stay
    for s, r in zip(stored, recs):
        assert {k: s[k] for k in cl.YC_LABEL_FIELDS} == {k: r[k] for k in cl.YC_LABEL_FIELDS}
    assert cl.yc_records(stored) == cl.yc_records(recs)                   # labels can be rebuilt from raw
    # integers such as launched_at / team_size are not phone numbers
    _, dropped2, _ = cl.yc_storable([{**recs[1], "one_liner": "x", "launched_at": 1700000000}])
    assert dropped2 == ["long_description"]


def test_record_date_rejects_unknown_mode():
    with pytest.raises(ValueError):
        cl.record_date({"label_source": "yc", "acceptance_proxy": "2020-01-01"}, "whatever")


# ------------------------------------------------------------------------------------------------ HTGF parsing
def test_htgf_parse_synthetic_page():
    p = htgf.parse_page(PAGE)
    assert p["has_key_facts"]
    assert p["invested_since"] == 2016                    # not the decoy table in #portfolio-same-category
    assert p["based_in"] == "Musterstadt" and p["stage"] == "Seed"
    assert p["buttons"] == ["Digital Tech", "Robotics & AI", "Bayern"]
    assert p["website"] == "https://www.examplo-robotics.de/en/"   # LinkedIn (platform) and mailto skipped
    assert p["news_n_results"] == 2 and len(p["news"]) == 2
    assert {n["date"] for n in p["news"]} == {"2019-05-03", "2016-03-14"}
    assert {n["href"] for n in p["news"]} == {"https://www.htgf.de/en/synthetic-follow-on-round/",
                                              "https://www.htgf.de/en/synthetic-seed-round/"}


def test_htgf_fragments_roundtrip_and_no_personal_data():
    p = htgf.parse_page(PAGE)
    frag = p["fragments"]
    q = htgf.parse_page(frag)
    for k in ("invested_since", "based_in", "stage", "buttons", "website", "news", "news_n_results"):
        assert p[k] == q[k], k
    assert "mailto" not in frag and "tel:" not in frag and "@" not in frag
    assert "Jane" not in frag and "Invented description" not in frag and "1234567" not in frag
    assert "Other Co" not in frag and "2008" not in frag


@pytest.mark.parametrize("text,expected", [
    ("14 March 2016", date(2016, 3, 14)),
    ("3 May 2019", date(2019, 5, 3)),
    ("07. März 2016", date(2016, 3, 7)),
    ("March 7, 2016", date(2016, 3, 7)),
    ("2016-03-07", date(2016, 3, 7)),
    ("31 February 2016", None),
    ("soon", None),
    ("", None),
])
def test_parse_news_date(text, expected):
    assert htgf.parse_news_date(text) == expected


def test_slugify_matches_wordpress_style():
    assert htgf.slugify("Robotics & AI") == "robotics-ai"
    assert htgf.slugify("Life Sciences / Chemistry") == "life-sciences-chemistry"
    assert htgf.slugify("Gesundheit") == "gesundheit"


def test_sector_and_country_rules():
    p = htgf.parse_page(PAGE)
    assert htgf.page_sector(p, ["post-1", "sectors-digital-tech", "focus_areas-robotics-ai"]) == "Digital Tech"
    assert htgf.page_sector(p, ["branchen22-robotics-ai"]) == "Robotics & AI"
    assert htgf.page_sector(p, ["post-1"]) == ""
    assert htgf.page_country(p) == ("DE", "state")
    assert htgf.page_country({"based_in": "Vienna, Austria", "buttons": []}) == ("AT", "based_in")
    assert htgf.page_country({"based_in": "Musterstadt", "buttons": ["Digital Tech"]}) == ("", "none")


def _rest(i, lang, link, day, tr, title="Examplo Robotics", cls=("sectors-digital-tech",)):
    return {"id": i, "lang": lang, "link": link, "date_gmt": f"{day}T10:00:00", "translations": tr,
            "title": {"rendered": title}, "class_list": list(cls)}


def test_translation_groups_prefer_english():
    rows = [_rest(1, "de", "https://www.htgf.de/portfolio/htgffamily/a/", "2016-05-01", {"de": 1, "en": 2}),
            _rest(2, "en", "https://www.htgf.de/en/portfolio/htgffamily/a/", "2016-06-01", {"de": 1, "en": 2}),
            _rest(3, "de", "https://www.htgf.de/portfolio/htgffamily/b/", "2017-01-01", {"de": 3})]
    g = htgf.translation_groups(rows)
    assert len(g) == 2
    links = {x["page"]["link"] for x in g}
    assert links == {"https://www.htgf.de/en/portfolio/htgffamily/a/", "https://www.htgf.de/portfolio/htgffamily/b/"}


def test_htgf_evidence_earliest_wins_and_bulk_days_ignored():
    link = "https://www.htgf.de/en/portfolio/htgffamily/a/"
    group = {"page": {"link": link}, "rows": {"en": {"date_gmt": "2016-06-01T00:00:00"},
                                              "de": {"date_gmt": "2016-05-01T00:00:00"}}}
    today = date(2026, 11, 20)
    # invested since 2016 beats the later news + entry dates -> year precision, source = portfolio page
    p = {"invested_since": 2016, "news": [{"date": "2016-03-14", "href": "https://www.htgf.de/en/n1/"}]}
    ev = htgf.htgf_evidence(group, p, set(), today)
    assert (ev["htgf_date"], ev["date_precision"], ev["source_url"], ev["kind"]) == ("2016-01-01", "year", link,
                                                                                     "invested_since")
    # a press release earlier than the stated investment year wins (earliest dated public evidence)
    p2 = {"invested_since": 2016, "news": [{"date": "2015-12-10", "href": "https://www.htgf.de/en/n0/"}]}
    ev2 = htgf.htgf_evidence(group, p2, set(), today)
    assert (ev2["htgf_date"], ev2["date_precision"], ev2["source_url"]) == ("2015-12-10", "day",
                                                                            "https://www.htgf.de/en/n0/")
    assert "conflict=earlier_than_invested_since" in ev2["evidence"]
    assert "conflict=" not in ev["evidence"]
    # only the entry date: bulk-import days are not evidence
    p3 = {"invested_since": None, "news": []}
    ev3 = htgf.htgf_evidence(group, p3, set(), today)
    assert (ev3["htgf_date"], ev3["kind"]) == ("2016-05-01", "entry")
    ev4 = htgf.htgf_evidence(group, p3, {("de", "2016-05-01")}, today)
    assert ev4["htgf_date"] == "2016-06-01"
    ev5 = htgf.htgf_evidence(group, p3, {("de", "2016-05-01"), ("en", "2016-06-01")}, today)
    assert ev5["htgf_date"] == "" and ev5["date_precision"] == ""
    # implausible years are not evidence
    ev6 = htgf.htgf_evidence(group, {"invested_since": 1990, "news": []}, {("de", "2016-05-01"),
                                                                            ("en", "2016-06-01")}, today)
    assert ev6["htgf_date"] == ""


def test_bulk_days():
    rows = [{"lang": "de", "date_gmt": "2014-04-01T00:00:00"}] * 12 + [{"lang": "en", "date_gmt": "2014-04-01T0"}]
    assert htgf.bulk_days(rows, threshold=10) == {("de", "2014-04-01")}


def test_htgf_records_exclude_undated_and_missing_domain():
    link_a = "https://www.htgf.de/en/portfolio/htgffamily/a/"
    link_b = "https://www.htgf.de/en/portfolio/htgffamily/b/"
    link_c = "https://www.htgf.de/en/portfolio/htgffamily/c/"
    rest = [_rest(1, "en", link_a, "2014-04-01", {"en": 1}),
            _rest(2, "en", link_b, "2014-04-01", {"en": 2}, title="B &amp; Co"),
            _rest(3, "en", link_c, "2014-04-01", {"en": 3})]
    groups = htgf.translation_groups(rest)
    pa = htgf.parse_page(PAGE)
    pb = {**pa, "invested_since": None, "news": []}
    parsed = {link_a: pa, link_b: pb, link_c: None}
    counts = Counter()
    rows = htgf.htgf_records(groups, parsed, rest * 4, date(2026, 11, 20), counts)   # 12 posts on one day = bulk
    a = next(r for r in rows if r["domain"] == "examplo-robotics.de")
    assert a["htgf_date"] == "2016-01-01" and a["date_precision"] == "year" and a["excluded_reason"] == ""
    assert a["sector"] == "Digital Tech" and a["country"] == "DE" and a["round_type"] == "seed"
    b = next(r for r in rows if r["company"] == "B & Co")
    assert b["htgf_date"] == "" and b["excluded_reason"] == "htgf_no_dated_evidence"
    c = next(r for r in rows if r["source_url"] == link_c)
    assert c["excluded_reason"] == "htgf_page_fetch_failed"
    assert counts["drop:htgf_no_dated_evidence"] == 1 and counts["drop:htgf_page_fetch_failed"] == 1


def test_robots_and_sitemap_parsing():
    robots = "Sitemap: https://x.test/sitemap_index.xml\nUser-agent: *\n" \
             "Content-Signal: ai-train=no, search=yes, ai-input=no\nAllow: /\nDisallow: /xmlrpc.php\n"
    rp, signals = htgf.parse_robots(robots, "radar-backtest/0.1")
    assert signals == {"ai-train": "no", "search": "yes", "ai-input": "no"}
    assert rp.can_fetch("radar-backtest/0.1", "https://x.test/en/portfolio/htgffamily/a/")
    assert not rp.can_fetch("radar-backtest/0.1", "https://x.test/xmlrpc.php")
    idx = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           '<sitemap><loc>https://x.test/portfolio-sitemap.xml</loc></sitemap>'
           '<sitemap><loc>https://x.test/post-sitemap.xml</loc></sitemap></sitemapindex>')
    assert htgf.parse_sitemap(idx) == (["https://x.test/portfolio-sitemap.xml", "https://x.test/post-sitemap.xml"],
                                       [])
    us = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          '<url><loc>https://x.test/en/portfolio/htgffamily/a/</loc><lastmod>2026-01-01</lastmod></url></urlset>')
    assert htgf.parse_sitemap(us) == ([], ["https://x.test/en/portfolio/htgffamily/a/"])


# ------------------------------------------------------------------------------------------------ end to end (offline)
class FakeClient:
    """Serves canned responses; any unknown URL fails the test (no network in tests)."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.session = type("S", (), {"headers": {"User-Agent": "radar-backtest-test"}})()

    def get(self, url, params=None, **kw):
        key = url if not params else f"{url}?page={params.get('page')}"
        self.calls.append(key)
        if key not in self.responses:
            return CachedResponse(url, 400, "", "2026-11-20T00:00:00+00:00", True)
        status, text = self.responses[key]
        return CachedResponse(url, status, text, "2026-11-20T00:00:00+00:00", True)


def _rest_page(start, n):
    return json.dumps([_rest(start + i, "en", f"https://www.htgf.de/en/portfolio/htgffamily/c{start + i}/",
                             "2016-01-01", {"en": start + i}) for i in range(n)])


def test_fetch_rest_paginates_and_stops():
    url = htgf.REST_URL
    # short last page ends the loop
    fake = FakeClient({f"{url}?page=1": (200, _rest_page(0, htgf.PER_PAGE)), f"{url}?page=2": (200, _rest_page(100, 3))})
    rows, raw = htgf.fetch_rest(fake)
    assert len(rows) == htgf.PER_PAGE + 3 and len(raw) == 2 and fake.calls == [f"{url}?page=1", f"{url}?page=2"]
    # total is a multiple of PER_PAGE: WordPress answers 400 on the next page
    fake = FakeClient({f"{url}?page=1": (200, _rest_page(0, htgf.PER_PAGE)), f"{url}?page=2": (400, "{}")})
    rows, raw = htgf.fetch_rest(fake)
    assert len(rows) == htgf.PER_PAGE and len(raw) == 1


def test_fetch_rest_refuses_truncated_listing():
    url = htgf.REST_URL
    fake = FakeClient({f"{url}?page=1": (200, _rest_page(0, htgf.PER_PAGE)), f"{url}?page=2": (503, "busy")})
    with pytest.raises(RuntimeError, match="truncated"):
        htgf.fetch_rest(fake)
    with pytest.raises(RuntimeError):                                     # no REST API at all
        htgf.fetch_rest(FakeClient({f"{url}?page=1": (400, "{}")}))
    with pytest.raises(RuntimeError, match="expected a list"):
        htgf.fetch_rest(FakeClient({f"{url}?page=1": (200, '{"code": "x"}')}))


def test_end_to_end_offline(tmp_path, monkeypatch, capsys):
    en = "https://www.htgf.de/en/portfolio/htgffamily/examplo/"
    de = "https://www.htgf.de/portfolio/htgffamily/examplo/"
    rest = [_rest(10, "de", de, "2016-05-01", {"de": 10, "en": 11}),
            _rest(11, "en", en, "2016-05-02", {"de": 10, "en": 11})]
    yc = YC[:2] + [_yc("Examplo US", "examplo-us", "https://examplo-robotics.de", "Summer 2017")]
    yc = [{**c, "long_description": "Mail founder" + "@" + "example.org"} for c in yc]
    responses = {
        cl.YC_URL: (200, json.dumps(yc)),
        htgf.ROBOTS_URL: (200, "User-agent: *\nContent-Signal: ai-input=no\nAllow: /\nDisallow: /xmlrpc.php\n"),
        htgf.SITEMAP_INDEX: (200, '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/'
                                  'sitemap/0.9"><sitemap><loc>https://www.htgf.de/portfolio-sitemap.xml</loc>'
                                  '</sitemap></sitemapindex>'),
        "https://www.htgf.de/portfolio-sitemap.xml": (200, '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps'
                                                           '.org/schemas/sitemap/0.9"><url><loc>' + en +
                                                           '</loc></url></urlset>'),
        f"{htgf.REST_URL}?page=1": (200, json.dumps(rest)),
        en: (200, PAGE),
    }
    fake = FakeClient(responses)
    monkeypatch.setattr(cl, "Client", lambda: fake)
    monkeypatch.setattr(cl, "LABELS", tmp_path / "labels")
    monkeypatch.setattr(cl, "RAW", tmp_path / "raw")
    monkeypatch.setattr(htgf, "RAW_DIR", tmp_path / "raw" / "htgf")
    df = cl.main([])
    out = capsys.readouterr().out
    assert "dropped by reason" in out
    labels = tmp_path / "labels"
    comp = pd.read_csv(labels / "companies.csv", dtype=str, keep_default_na=False)
    assert list(comp.columns) == LABEL_COLUMNS and validate_labels(comp) == []
    assert len(comp) == len(df) == 2
    ex = comp.set_index("domain").loc["examplo-robotics.de"]
    assert ex["first_funding_date"] == "2016-01-01" and ex["round_type"] == "seed"   # HTGF 2016 < YC S17
    assert comp.set_index("domain").loc["acme.io", "first_funding_date"] == "2014-10-31"
    bs = pd.read_csv(labels / "companies_batchstart.csv", dtype=str, keep_default_na=False)
    assert bs.set_index("domain").loc["acme.io", "first_funding_date"] == "2015-01-15"
    prov = pd.read_csv(labels / "label_provenance.csv", dtype=str, keep_default_na=False)
    assert list(prov.columns[:10]) == ["domain", "company", "label_source", "batch", "batch_start",
                                       "acceptance_proxy", "yc_status", "htgf_date", "date_precision", "source_url"]
    assert len(prov) == 4 and set(prov["label_source"]) == {"yc", "htgf"}
    sample = pd.read_csv(labels / "label_sample_20.csv", dtype=str, keep_default_na=False)
    assert len(sample) == 2 and set(sample["domain"]) <= set(comp["domain"])
    for f in ["companies.csv", "companies_batchstart.csv", "label_provenance.csv", "label_sample_20.csv"]:
        assert (labels / (f + ".meta.json")).exists()
    raw_yc = tmp_path / "raw" / "yc" / "companies_all.json.gz"
    stored = json.loads(gzip.decompress(raw_yc.read_bytes()))
    assert stored == [{k: v for k, v in c.items() if k != "long_description"} for c in yc]   # no contact data
    yc_meta = json.loads((tmp_path / "raw" / "yc" / "companies_all.json.gz.meta.json").read_text())
    assert yc_meta["params"]["dropped_fields"] == ["long_description"]
    assert yc_meta["params"]["response_sha256"] == cl.hashlib.sha256(json.dumps(yc).encode()).hexdigest()
    pages = htgf.read_gz_jsonl(tmp_path / "raw" / "htgf" / "portfolio_pages.jsonl.gz")
    assert len(pages) == 1 and "@" not in pages[0]["fragments"] and "mailto" not in pages[0]["fragments"]
    assert htgf.parse_page(pages[0]["fragments"])["invested_since"] == 2016   # parse is reproducible from raw
    for f in ["robots.txt", "sitemaps.jsonl.gz", "portfolio_rest.jsonl.gz", "portfolio_pages.jsonl.gz"]:
        assert (tmp_path / "raw" / "htgf" / (f + ".meta.json")).exists()
    # politeness / robots: the page was fetched once, and robots.txt before anything else on the host
    htgf_calls = [c for c in fake.calls if "htgf.de" in c]
    assert htgf_calls[0] == htgf.ROBOTS_URL and fake.calls.count(en) == 1
