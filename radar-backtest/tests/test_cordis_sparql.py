"""Offline tests for the CORDIS SPARQL collector (synthetic fixtures under tests/fixtures/cordis_sparql/; a fake
endpoint serves them with LIMIT/OFFSET semantics, no network)."""
import csv
import gzip
import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.lib.http import CachedResponse
from scripts.sources import cordis
from scripts.sources import cordis_sparql as cs

FIX = Path(__file__).parent / "fixtures" / "cordis_sparql"
TS = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
NOW = "2026-11-20T00:00:00+00:00"


# ------------------------------------------------------------------------------------------------------- fake endpoint
def _virtuoso_csv(columns, rows) -> str:
    """Emulate Virtuoso text/csv: quoted header and strings, bare numbers, empty field for unbound."""
    buf = io.StringIO()
    buf.write(",".join(f'"{c}"' for c in columns) + "\n")
    for r in rows:
        out = []
        for v in r:
            if v is None:
                out.append("")
            elif re.fullmatch(r"-?\d+(\.\d+)?", v):
                out.append(v)
            else:
                out.append('"' + v.replace('"', '""') + '"')
        buf.write(",".join(out) + "\n")
    return buf.getvalue()


def _load(name):
    with open(FIX / f"{name}.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return rows[0], [[v if v != "" else None for v in r] for r in rows[1:]]


class _Session:
    headers = {"User-Agent": "radar-backtest/0.1 (test)"}


class FakeEndpoint:
    """Serves the fixture tables for count/page queries; records every request."""

    def __init__(self, tmp: Path, cap: int = 10_000, robots_status: int = 200, overrides: dict | None = None):
        self.tables = {n: _load(n) for n in cs.DATASETS}
        self.cap = cap
        self.robots_status = robots_status
        self.overrides = overrides or {}       # (dataset, offset) -> CachedResponse
        self.calls = []
        self.max_retries = 6
        self.session = _Session()
        self.cache = tmp / "cache"

    def _cache_path(self, host, key):
        return self.cache / host / key[:2] / f"{key}.json"

    def get(self, url, params=None, headers=None, use_cache=True, **kw):
        if url.endswith("/robots.txt"):
            self.calls.append(("robots", None))
            text = (FIX / "robots.txt").read_text() if self.robots_status == 200 else "err"
            return CachedResponse(url, self.robots_status, text, NOW, False)
        q = params["query"]
        assert params["format"] == "text/csv" and headers["Accept"] == "text/csv"
        name, kind = re.search(r"dataset=(\S+) (page|count)", q).groups()
        cols, rows = self.tables[name]
        if kind == "count":
            self.calls.append((name, "count"))
            return CachedResponse(url, 200, f'"n"\n{len(rows)}\n', NOW, False)
        limit, offset = map(int, re.search(r"LIMIT (\d+) OFFSET (\d+)", q).groups())
        self.calls.append((name, offset))
        o = self.overrides.get((name, offset))
        if isinstance(o, list) and o:          # a sequence of responses, consumed one per request
            return o.pop(0)
        if isinstance(o, CachedResponse):
            return o
        return CachedResponse(url, 200, _virtuoso_csv(cols, rows[offset:offset + min(limit, self.cap)]), NOW, False)


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cs")
    fake = FakeEndpoint(tmp)
    sp = cs.Sparql(client=fake, raw_dir=tmp / "raw")
    part, comp, stats = cs.run(sp, tmp / "processed", page_size=4)
    return part, comp, stats, tmp, fake, sp


def _row(part, programme, project_id, organisation_id):
    r = part[(part.programme == programme) & (part.project_id == project_id) & (part.organisation_id == organisation_id)]
    assert len(r) == 1, r
    return r.iloc[0]


# -------------------------------------------------------------------------------------------------------- query builders
def test_page_query_builder():
    ds = cs.DATASETS["roles"]
    q = cs.build_page_query(ds, 10_000, 20_000)
    assert q.startswith("# radar-backtest cordis_sparql dataset=roles page\nPREFIX eurio: <http://data.europa.eu/s66#>")
    assert q.rstrip().endswith("ORDER BY ?role ?org ?project_id ?role_label ?order ?amount ?currency\nLIMIT 10000 OFFSET 20000")
    assert "STR(?project)" not in q and "?project " not in q.split("ORDER BY")[1]   # project IRIs never sorted
    assert "DISTINCT" not in q            # DISTINCT over computed strings overflows Virtuoso temp columns
    with pytest.raises(ValueError):
        cs.build_page_query(ds, cs.MAX_PAGE_SIZE + 1, 0)
    with pytest.raises(ValueError):
        cs.build_page_query(ds, 0, 0)
    with pytest.raises(ValueError):
        cs.build_page_query(ds, 10, -1)


@pytest.mark.parametrize("name", list(cs.DATASETS))
def test_count_query_uses_the_page_pattern(name):
    ds = cs.DATASETS[name]
    page, count = cs.build_page_query(ds, 100, 0), cs.build_count_query(ds)
    where = lambda q: q[q.index("WHERE {"):q.rindex("}") + 1]  # noqa: E731
    assert where(page) == where(count)
    assert "SELECT (COUNT(*) AS ?n)" in count and "LIMIT" not in count and "ORDER BY" not in count
    assert cs.query_sha8(ds) == cs.query_sha8(ds) and len(cs.query_sha8(ds)) == 8


@pytest.mark.parametrize("name", list(cs.DATASETS))
def test_order_by_is_a_total_order_over_selected_values(name):
    """Every variable a projected value depends on is in ORDER BY: ties can only be identical rows, so OFFSET pages
    are well defined even without DISTINCT."""
    ds = cs.DATASETS[name]
    aliases = set(re.findall(r"AS \?(\w+)\)", ds.select))
    used = set(re.findall(r"\?(\w+)", ds.select)) - aliases
    assert used <= set(re.findall(r"\?(\w+)", ds.order_by))
    projected = [m.group(1) or m.group(2) for m in re.finditer(r"AS \?(\w+)\)|(?<![(\w])\?(\w+)(?![^(]*\))", ds.select)]
    assert tuple(projected) == ds.columns


def test_no_contact_fields_selected():
    for ds in cs.DATASETS.values():
        assert not [c for c in ds.columns if cordis.CONTACT_RE.search(c)]
        cs.build_page_query(ds, 10, 0)                     # guard passes
    with pytest.raises(ValueError, match="contact"):
        cs._check_no_contact("SELECT ?x WHERE { ?o eurio:contactEmail ?x }")
    with pytest.raises(ValueError, match="contact"):
        cs._check_no_contact("SELECT ?phone WHERE { ?o eurio:x ?phone }")


# --------------------------------------------------------------------------------------------------------- parsing
def test_parse_csv_page_quoting():
    text = '﻿"a","b","c"\n"x""y, z","line1\nline2",\n"",3.5,"EUR"\n'
    df = cs.parse_csv_page(text, ("a", "b", "c"))
    assert df.to_dict("records") == [{"a": 'x"y, z', "b": "line1\nline2", "c": None},
                                     {"a": None, "b": "3.5", "c": "EUR"}]


def test_parse_csv_page_rejects_error_pages_and_bad_rows():
    with pytest.raises(cs.SparqlError, match="header"):
        cs.parse_csv_page("<html>Virtuoso 37000 Error SP030</html>\n", ("a", "b"))
    with pytest.raises(cs.SparqlError, match="fields"):
        cs.parse_csv_page('"a","b"\n1,2,3\n', ("a", "b"))
    with pytest.raises(cs.SparqlError, match="empty"):
        cs.parse_csv_page("", ("a",))
    assert cs.parse_csv_page('"a","b"\n', ("a", "b")).empty


def test_parse_count():
    assert cs.parse_count('"n"\n399071\n') == 399071
    with pytest.raises(cs.SparqlError):
        cs.parse_count('"n"\n\n')
    with pytest.raises(cs.SparqlError):
        cs.parse_count('"x"\n1\n')


# ------------------------------------------------------------------------------------------------------- pagination
def _pager(total, cap=10_000):
    calls = []

    def fetch(offset):
        calls.append(offset)
        return pd.DataFrame({"x": range(offset, min(total, offset + min(cap, 4)))})
    return fetch, calls


def test_paginate_stops_at_short_page():
    fetch, calls = _pager(10)
    pages, st = cs.paginate(fetch, 4, expected=10)
    assert calls == [0, 4, 8] and st["rows"] == 10 and st["pages"] == 3 and not st["partial"]


def test_paginate_exact_multiple_needs_no_empty_request_when_count_known():
    fetch, calls = _pager(8)
    _, st = cs.paginate(fetch, 4, expected=8)
    assert calls == [0, 4] and st["rows"] == 8
    fetch, calls = _pager(8)
    _, st = cs.paginate(fetch, 4)               # without COUNT: one empty page ends it
    assert calls == [0, 4, 8] and st["rows"] == 8


def test_paginate_detects_server_cap_below_page_size():
    fetch, calls = _pager(10, cap=3)            # server returns 3 rows although LIMIT is 4
    with pytest.raises(cs.PaginationError, match="COUNT"):
        cs.paginate(fetch, 4, expected=10)
    assert calls == [0]


def test_paginate_count_mismatch_and_oversized_page():
    fetch, _ = _pager(10)
    with pytest.raises(cs.PaginationError):
        cs.paginate(fetch, 4, expected=11)
    with pytest.raises(cs.PaginationError, match="> page size"):
        cs.paginate(lambda off: pd.DataFrame({"x": range(5)}), 4)
    with pytest.raises(ValueError):
        cs.paginate(fetch, cs.MAX_PAGE_SIZE + 1)


def test_paginate_max_pages_is_partial():
    fetch, calls = _pager(100)
    _, st = cs.paginate(fetch, 4, expected=100, max_pages=2)
    assert calls == [0, 4] and st["partial"] and st["rows"] == 8


# ------------------------------------------------------------------------------------------------------------ robots
ROBOTS = (FIX / "robots.txt").read_text()
UA = "radar-backtest/0.1 (research)"


@pytest.mark.parametrize("url,allowed", [
    ("https://cordis.europa.eu/datalab/sparql?query=SELECT+%3Fx&format=text%2Fcsv", True),
    ("https://cordis.europa.eu/data/cordis-h2020projects-csv.zip", False),
    ("https://cordis.europa.eu/datalab/sparql?query=x&format=xml", False),       # '*format=xml'
    ("https://cordis.europa.eu/search?q=x", False),
    ("https://cordis.europa.eu/project/rcn/123.xml", False),                     # '/*/rcn/*.xml'
    ("https://cordis.europa.eu/datalab/sparql?query=%2Fresult_x", False),        # '*/result_*' after decoding
    ("https://cordis.europa.eu/project/id/123", True),
])
def test_robots_decision_wildcards(url, allowed):
    assert cs.robots_decision(ROBOTS, UA, url)[0] is allowed


def test_robots_specific_group_and_allow_precedence():
    txt = "User-agent: radar-backtest\nDisallow: /datalab/\nAllow: /datalab/sparql\n\nUser-agent: *\nDisallow: /\n"
    assert cs.robots_decision(txt, UA, "https://h/datalab/sparql?query=x") == (True, "Allow: /datalab/sparql")
    assert cs.robots_decision(txt, UA, "https://h/datalab/other")[0] is False
    assert cs.robots_decision(txt, "otherbot/1.0", "https://h/datalab/sparql")[0] is False
    assert cs.robots_decision("User-agent: *\nDisallow:\n", UA, "https://h/x")[0] is True   # empty Disallow


def test_check_robots(tmp_path):
    sp = cs.Sparql(client=FakeEndpoint(tmp_path), raw_dir=tmp_path / "raw")
    urls = [sp.request_url(cs.build_page_query(ds, 10_000, 0)) for ds in cs.DATASETS.values()]
    rec = cs.check_robots(sp.client, urls)
    assert rec["allowed"] and rec["urls_checked"] == len(urls) and rec["status"] == 200
    with pytest.raises(cs.RobotsDisallowed):
        cs.check_robots(sp.client, ["https://cordis.europa.eu/data/x.zip"])
    with pytest.raises(cs.RobotsDisallowed, match="unreachable"):
        cs.check_robots(FakeEndpoint(tmp_path, robots_status=503), urls)
    assert cs.check_robots(FakeEndpoint(tmp_path, robots_status=404), urls)["allowed"]


# --------------------------------------------------------------------------------------------------------- programme
def test_type_family():
    assert cs.type_family("MC-CIG") == "MC" and cs.type_family("HORIZON-TMA-MSCA-PF-EF") == "HORIZON"
    assert cs.type_family("RIA") == "RIA" and cs.type_family(None) is None and cs.type_family("") is None


def test_programme_of():
    assert cs.programme_of("H2020_ICT-01-2015") == "H2020"
    assert cs.programme_of("FP7-PEOPLE-2009-IEF") == "FP7"
    assert cs.programme_of("HORIZON-CL4-2022-01") == "HORIZON"
    assert cs.programme_of("HORIZON.2.4") == "HORIZON"
    assert cs.programme_of("ERC-2013-StG") is None and cs.programme_of(None) is None
    assert cs.programme_of("H2020X-1") is None and cs.programme_of("FP70") is None


def test_derive_programmes_cascade():
    tables = {n: pd.DataFrame(_load(n)[1], columns=_load(n)[0], dtype=object) for n in cs.DATASETS}
    out, st = cs.derive_programmes(tables["projects"], {k: tables[f"grant_{k}"] for k in cs.SCHEME_KINDS})
    got = out.set_index("project_id")[["programme", "programme_step"]].apply(tuple, axis=1).to_dict()
    assert got == {
        "700001": ("H2020", "topic"), "700002": ("H2020", "call"), "200001": ("FP7", "topic"),
        "250001": ("FP7", "type_code"),          # MC-IEF is only used by FP7 projects
        "250002": ("FP7", "topic"), "101000001": ("HORIZON", "topic"),
        "101200000": ("HORIZON", "id_range"),    # no scheme evidence; id inside the HORIZON range only
        "101300000": ("HORIZON", "topic"),
        "IST-2001-32072": (None, "unresolved"),  # FP5 id, no evidence
        "700010": (None, "conflict"),            # topics of two programmes
        "700011": (None, "unresolved"),          # 'CSA' is used by FP7 and H2020; id outside every range
        "260001": ("FP7", "type_family"),        # 'MC-CIG' never on a tagged project; family 'MC' is FP7 only
    }
    assert st["type_codes_ambiguous"] == ["CSA"]
    assert st["type_families_mapped"] == {"HORIZON": "HORIZON", "MC": "FP7"}
    assert st["type_families_ambiguous"] == ["CSA"]
    assert st["id_ranges"] == {"FP7": [200001, 250002], "H2020": [700001, 700002], "HORIZON": [101000001, 101300000]}


# --------------------------------------------------------------------------------------------------------- end to end
def test_fetch_stats_and_requests(e2e):
    stats, fake = e2e[2], e2e[4]
    f = stats["fetch"]
    assert f["roles"]["rows"] == f["roles"]["expected"] == 22 and f["roles"]["pages"] == 6          # 5 x 4 + 2
    assert f["organisations"]["rows"] == 12 and f["organisations"]["pages"] == 3                   # exact multiple
    assert [c for c in fake.calls if c[0] == "organisations"] == [("organisations", "count"),
                                                                ("organisations", 0), ("organisations", 4),
                                                                ("organisations", 8)]
    assert f["grant_programme"]["rows"] == 1 and f["grant_programme"]["pages"] == 1
    assert stats["requests_total"] == sum(v["requests"] for v in f.values()) == len(fake.calls)
    assert not any(v["partial"] for v in f.values())


def test_raw_pages_written_verbatim_with_meta(e2e):
    tmp, sp = e2e[3], e2e[5]
    pages = sorted((tmp / "raw" / "roles").glob("roles_*_o*.csv.gz"))
    assert [p.name.rsplit("_o", 1)[1] for p in pages] == [f"{o:07d}.csv.gz" for o in range(0, 24, 4)]
    cols, rows = _load("roles")
    assert gzip.decompress(pages[1].read_bytes()).decode() == _virtuoso_csv(cols, rows[4:8])
    meta = json.loads(pages[1].with_name(pages[1].name + ".meta.json").read_text())
    assert meta["rows"] == 4 and meta["params"]["offset"] == 4 and "LIMIT 4 OFFSET 4" in meta["params"]["query"]
    assert "CC BY 4.0" in meta["notes"]
    assert list((tmp / "raw" / "roles").glob("roles_*_count.csv.gz"))
    assert {Path(e.raw_path).parent.name for e in sp.log} == set(cs.DATASETS)


def test_output_contract(e2e):
    part, comp, _, tmp, _, _ = e2e
    assert list(part.columns) == cordis.PART_COLUMNS
    assert list(comp.columns) == cordis.COMPANY_COLUMNS
    assert not part.duplicated(["programme", "project_id", "organisation_id"]).any()
    for c in ("ec_signature_date", "start_date", "public_ts"):
        assert isinstance(part[c].dtype, pd.DatetimeTZDtype) and str(part[c].dt.tz) == "UTC"
    assert str(part["sme"].dtype) == "boolean" and part["ec_contribution"].dtype == float
    assert set(part["activity_type"].dropna()) <= cordis.ACTIVITY_TYPES
    rt = pd.read_parquet(tmp / "processed" / "cordis_participations.parquet")
    assert list(rt.columns) == cordis.PART_COLUMNS and len(rt) == len(part)
    meta = json.loads((tmp / "processed" / "cordis_participations.parquet.meta.json").read_text())
    assert meta["rows"] == len(part) and meta["params"]["partial"] is False and meta["inputs"]
    assert (tmp / "processed" / "cordis_companies.parquet.meta.json").exists()


def test_rows_per_programme_and_exclusions(e2e):
    part, stats = e2e[0], e2e[2]
    assert part.groupby("programme").size().to_dict() == {"FP7": 4, "H2020": 6, "HORIZON": 7}
    assert (part["framework"] == part["programme"]).all()
    assert not part["project_id"].isin(["IST-2001-32072", "700010", "700011"]).any()   # unresolved / conflict
    assert stats["roles"]["roles_without_programme"] == 3      # unresolved, conflict, no project identifier
    assert stats["roles"]["roles_without_project_id"] == 1
    assert stats["roles"]["roles_by_programme_step"]["id_range"] == 1
    assert stats["roles"]["roles_by_programme_step"]["no_project"] == 1


def test_public_ts_signature_first_then_start_date(e2e):
    part = e2e[0]
    r = _row(part, "H2020", "700001", "999000001")
    assert r["public_ts"] == r["ec_signature_date"] == TS("2015-11-20") and r["start_date"] == TS("2016-01-01")
    r = _row(part, "H2020", "700002", "999000001")                       # no signatureDate
    assert pd.isna(r["ec_signature_date"]) and r["public_ts"] == TS("2017-03-01")
    r = _row(part, "FP7", "200001", "999000010")                         # FP7: never a signature date
    assert pd.isna(r["ec_signature_date"]) and r["public_ts"] == TS("2009-03-01")
    assert _row(part, "HORIZON", "101200000", "999000003")["public_ts"] == TS("2025-10-01")


def test_roles_amounts_and_dedupe(e2e):
    part, stats = e2e[0], e2e[2]
    r = _row(part, "H2020", "700001", "999000001")                       # coordinator + thirdParty rows of one org
    assert r["role"] == "coordinator" and r["ec_contribution"] == pytest.approx(100030.5)
    assert _row(part, "HORIZON", "101000001", "999000003")["ec_contribution"] == pytest.approx(750000.25)  # max of 2
    assert np.isnan(_row(part, "HORIZON", "101000001", "999000002")["ec_contribution"])   # USD amount ignored
    assert np.isnan(_row(part, "H2020", "700002", "999000001")["ec_contribution"])        # no payment
    assert _row(part, "FP7", "200001", "999000010")["ec_contribution"] == pytest.approx(123456.78)
    assert stats["roles"]["roles_multi_amount"] == 1 and stats["roles"]["non_eur_amounts"] == 1
    assert stats["participations"]["H2020"]["duplicate_key_rows"] == 2


def test_organisation_fields(e2e):
    part, stats = e2e[0], e2e[2]
    assert _row(part, "HORIZON", "101000001", "kg:o05")["name"] == "Zeta AI SAS"   # node without PIC
    r = _row(part, "HORIZON", "101000001", "kg:o99")                                # role of a missing org node
    assert r["name"] is None and pd.isna(r["activity_type"]) and pd.isna(r["country"])
    assert all(s["unknown_activity_type"] == 0 for s in stats["participations"].values())
    assert _row(part, "FP7", "200001", "999000010")["name"] == 'Müller "Technik" GmbH'
    assert _row(part, "H2020", "700002", "999000011")["name"] == "Iota B.V."         # 2 legal names: first sorted
    assert _row(part, "H2020", "700001", "999000002")["country"] == "GB"             # UK -> GB
    assert _row(part, "HORIZON", "101000001", "999000003")["country"] == "GR"        # EL -> GR
    assert _row(part, "FP7", "200001", "999000006")["country"] == "IE"               # 2 sites: first sorted
    assert bool(_row(part, "H2020", "700001", "999000001")["sme"]) is True
    assert bool(_row(part, "HORIZON", "101000001", "999000003")["sme"]) is False
    assert pd.isna(_row(part, "H2020", "700001", "999000007")["activity_type"])     # PRC + REC classes: conflict
    assert pd.isna(_row(part, "HORIZON", "101000001", "999000008")["activity_type"])  # no class
    assert _row(part, "H2020", "700002", "999000011")["activity_type"] == "PUB"
    o = stats["organisations"]
    assert (o["missing_pic"], o["activity_type_conflict"], o["untyped"], o["multi_name"], o["multi_country"]) == \
        (1, 1, 1, 1, 1)


def test_domain_normalisation(e2e):
    part = e2e[0]
    dom = lambda p, j, o: _row(part, p, j, o)["domain"]  # noqa: E731
    assert dom("H2020", "700001", "999000001") == "acme-robotics.de"      # http://www.acme-robotics.de/en
    assert dom("H2020", "700001", "999000002") == "uni-example.ac.uk"     # bare host with www.
    assert dom("HORIZON", "101000001", "999000003") == "epsilon.gr"
    assert dom("FP7", "200001", "999000006") == "theta.co.uk"
    assert pd.isna(dom("H2020", "700001", "999000004"))                   # platform (github.io)
    assert _row(part, "H2020", "700001", "999000004")["organization_url"] == "https://gamma.github.io"


def test_prc_filter_and_companies(e2e):
    comp = e2e[1].set_index("domain")
    assert list(comp.index) == ["acme-robotics.de", "epsilon.gr", "mueller-technik.de", "theta.co.uk", "zeta.ai"]
    for not_prc in ("uni-example.ac.uk", "conflicted-org.be", "untyped.org", "iota.nl"):  # HES, conflict, none, PUB
        assert not_prc not in comp.index
    assert comp.loc["acme-robotics.de", "first_public_ts"] == TS("2015-11-20")
    assert comp.loc["acme-robotics.de", "n_participations"] == 2 and bool(comp.loc["acme-robotics.de", "sme_any"])
    assert comp.loc["epsilon.gr", "first_public_ts"] == TS("2022-11-30") and comp.loc["epsilon.gr", "n_participations"] == 3
    assert comp.loc["mueller-technik.de", "first_public_ts"] == TS("2009-03-01")
    assert comp.loc["theta.co.uk", "country"] == "IE" and comp.loc["theta.co.uk", "programmes"] == ["FP7"]
    assert comp.loc["zeta.ai", "organisation_ids"] == ["kg:o05"]


def test_coverage_numbers(e2e):
    cov = e2e[2]["coverage"]
    assert cov["ALL"]["participations"] == 17 and cov["companies"] == 5
    assert cov["first_public_ts_by_year"] == {2009: 2, 2015: 1, 2022: 2}
    assert cov["FP7"]["pct_missing_signature"] == 100.0


def test_programme_subset(tmp_path):
    sp = cs.Sparql(client=FakeEndpoint(tmp_path), raw_dir=tmp_path / "raw")
    part, comp, _ = cs.run(sp, tmp_path / "out", page_size=10, programmes=["FP7"], write=False)
    assert set(part["programme"]) == {"FP7"} and len(part) == 4
    assert list(comp["domain"]) == ["mueller-technik.de", "theta.co.uk"]
    assert not (tmp_path / "out").exists()


def test_smoke_max_pages_is_partial(tmp_path):
    sp = cs.Sparql(client=FakeEndpoint(tmp_path), raw_dir=tmp_path / "raw")
    _, _, stats = cs.run(sp, tmp_path / "out", page_size=4, max_pages=1)
    assert stats["fetch"]["roles"]["partial"] and stats["fetch"]["roles"]["rows"] == 4
    meta = json.loads((tmp_path / "out" / "cordis_participations.parquet.meta.json").read_text())
    assert meta["params"]["partial"] is True and meta["notes"].startswith("PARTIAL")


# -------------------------------------------------------------------------------------------------- error handling
def test_error_page_raises_and_is_evicted(tmp_path):
    bad = CachedResponse("u", 200, "<html>maintenance</html>", NOW, True)
    fake = FakeEndpoint(tmp_path, overrides={("roles", 0): bad})
    sp = cs.Sparql(client=fake, raw_dir=tmp_path / "raw")
    cached = sp._cache_file(cs.build_page_query(cs.DATASETS["roles"], 4, 0))
    cached.parent.mkdir(parents=True)
    cached.write_text("{}")
    with pytest.raises(cs.SparqlError, match="header"):
        sp.page(cs.DATASETS["roles"], 4, 0)
    assert not cached.exists()
    assert not list((tmp_path / "raw").rglob("*.gz"))          # nothing written for an invalid page


def test_http_error_raises_after_bounded_retries(tmp_path):
    err = CachedResponse("u", 500, "Virtuoso 22026 Error SR477: Error serializing the value", NOW, False)
    fake = FakeEndpoint(tmp_path, overrides={("roles", 0): err})
    sp = cs.Sparql(client=fake, raw_dir=tmp_path / "raw", retry_pause_s=0)
    with pytest.raises(cs.SparqlError, match="HTTP 500.*SR477"):
        sp.page(cs.DATASETS["roles"], 4, 0)
    assert fake.calls == [("roles", 0)] * 3                    # 1 + outer_retries
    assert sp.client.max_retries == 3                          # Client backoff capped (not 6x)
    syntax = CachedResponse("u", 400, "Virtuoso 37000 Error SP030: SPARQL compiler", NOW, False)
    fake = FakeEndpoint(tmp_path, overrides={("roles", 0): syntax})
    with pytest.raises(cs.SparqlError, match="HTTP 400"):
        cs.Sparql(client=fake, raw_dir=tmp_path / "raw", retry_pause_s=0).page(cs.DATASETS["roles"], 4, 0)
    assert fake.calls == [("roles", 0)]                        # a 4xx is never retried


def test_intermittent_5xx_recovers(tmp_path):
    err = CachedResponse("u", 500, "Virtuoso 22023 Error SR014: Function ri_name_id needs a string", NOW, False)
    fake = FakeEndpoint(tmp_path, overrides={("roles", 4): [err]})
    sp = cs.Sparql(client=fake, raw_dir=tmp_path / "raw", retry_pause_s=0)
    pages, st = cs.paginate(lambda off: sp.page(cs.DATASETS["roles"], 4, off), 4, expected=22)
    assert st["rows"] == 22 and fake.calls.count(("roles", 4)) == 2


def test_cache_only_never_fetches(tmp_path):
    fake = FakeEndpoint(tmp_path)
    sp = cs.Sparql(client=fake, raw_dir=tmp_path / "raw", cache_only=True)
    with pytest.raises(cs.SparqlError, match="cache-only"):
        sp.count(cs.DATASETS["roles"])
    assert fake.calls == []


def test_cli_requires_out_dir_for_smoke():
    with pytest.raises(SystemExit):
        cs.main(["--max-pages", "1"])
    with pytest.raises(SystemExit):                 # partial tables never go to data/processed, even if named
        cs.main(["--max-pages", "1", "--out-dir", str(cs.PROCESSED)])
    with pytest.raises(SystemExit):
        cs.main(["--refresh", "--cache-only"])


# ------------------------------------------------------------------------------------------- review fixes (2026-11-20)
def test_server_side_email_filter_is_the_cordis_pattern():
    """The REGEX sent to the endpoint (SPARQL literal, backslashes doubled) is exactly cordis.EMAIL_IN_URL_RE."""
    q = cs.build_page_query(cs.DATASETS["organisations"], 10, 0)
    m = re.search(r'OPTIONAL \{ \?org eurio:url \?url0 \. FILTER\(!REGEX\(STR\(\?url0\), "((?:[^"\\]|\\.)*)"\)\) \}', q)
    assert m, "e-mail filter missing from the organisations query"
    assert m.group(1).replace("\\\\", "\\") == cordis.EMAIL_IN_URL_RE.pattern
    rx = re.compile(m.group(1).replace("\\\\", "\\"))
    assert rx.search("mailto:jane.doe@example.org") and rx.search("info@acme.de")
    assert not rx.search("https://medium.com/@acme") and not rx.search("http://www.acme.de/en")


def test_role_with_two_organisations_prefers_an_existing_node():
    roles = pd.DataFrame({"role_id": ["r1", "r1", "r2"], "org_id": ["a-dangling", "b-real", "c-real"],
                          "project_id": ["1", "1", "2"], "role_label": ["participant"] * 3, "order": ["2", "2", "1"],
                          "amount": ["10", "10", None], "currency": ["EUR", "EUR", None]}, dtype=object)
    out, st = cs.build_role_table(roles, known_orgs={"b-real", "c-real"})
    assert out.set_index("role_id")["org_id"].to_dict() == {"r1": "b-real", "r2": "c-real"}
    assert st["multi_org_id"] == 1 and st["multi_org_id_dangling_avoided"] == 1
    assert list(out.columns) == ["role_id", "project_id", "org_id", "role_label", "order", "ec_contribution"]
    out, st = cs.build_role_table(roles)                    # without the node list: alphabetical
    assert out.set_index("role_id")["org_id"].to_dict()["r1"] == "a-dangling" and "multi_org_id_dangling_avoided" not in st


def test_fp7_sme_is_missing_not_a_later_class(e2e):
    """The graph's SME class is organisation-level and comes from later H2020/Horizon declarations: FP7 rows get NA,
    like the bulk FP7 CSV; H2020/Horizon rows keep the class."""
    part, comp = e2e[0], e2e[1].set_index("domain")
    assert part.loc[part.programme == "FP7", "sme"].isna().all()
    assert part.loc[part.programme != "FP7", "sme"].notna().any()
    assert pd.isna(comp.loc["mueller-technik.de", "sme_any"]) and pd.isna(comp.loc["theta.co.uk", "sme_any"])
    assert bool(comp.loc["acme-robotics.de", "sme_any"]) is True


def test_refuses_to_mix_graph_snapshots(tmp_path):
    """A cached page fetched long before the others (resumed run after a graph update) stops the run before it is
    written as raw, and the valid cached page is not evicted; max_fetch_span_hours=None disables the check."""
    cols, rows = _load("roles")
    old = CachedResponse("u", 200, _virtuoso_csv(cols, rows[4:8]), "2026-10-01T00:00:00+00:00", True)
    fake = FakeEndpoint(tmp_path, overrides={("roles", 4): old})
    sp = cs.Sparql(client=fake, raw_dir=tmp_path / "raw")
    cached = sp._cache_file(cs.build_page_query(cs.DATASETS["roles"], 4, 4))
    cached.parent.mkdir(parents=True)
    cached.write_text("{}")
    with pytest.raises(cs.PaginationError, match="--refresh"):
        cs.collect(sp, page_size=4, datasets=["roles"])
    assert cached.exists()
    assert not list((tmp_path / "raw" / "roles").glob("*_o0000004.csv.gz"))
    sp = cs.Sparql(client=FakeEndpoint(tmp_path, overrides={("roles", 4): old}), raw_dir=tmp_path / "raw2",
                   max_fetch_span_hours=None)
    _, st = cs.collect(sp, page_size=4, datasets=["roles"])
    assert st["roles"]["rows"] == 22
    sp = cs.Sparql(client=FakeEndpoint(tmp_path), raw_dir=tmp_path / "raw3")        # one snapshot: passes
    assert cs.collect(sp, page_size=4, datasets=["roles"])[1]["roles"]["rows"] == 22


def test_refuses_to_overwrite_tables_of_another_collector(tmp_path):
    out = tmp_path / "processed"
    out.mkdir()
    (out / "cordis_participations.parquet").write_bytes(b"bulk")
    (out / "cordis_participations.parquet.meta.json").write_text(json.dumps({"script": "scripts/sources/cordis.py"}))
    fake = FakeEndpoint(tmp_path)
    with pytest.raises(cs.OutputCollision, match="cordis.py"):
        cs.run(cs.Sparql(client=fake, raw_dir=tmp_path / "raw"), out, page_size=10)
    assert fake.calls == [] and (out / "cordis_participations.parquet").read_bytes() == b"bulk"
    with pytest.raises(SystemExit):                          # CLI: refused before any request
        cs.main(["--cache-only", "--out-dir", str(out)])
    part, _, _ = cs.run(cs.Sparql(client=fake, raw_dir=tmp_path / "raw"), out, page_size=10, overwrite_other=True)
    assert len(pd.read_parquet(out / "cordis_participations.parquet")) == len(part)
    cs.run(cs.Sparql(client=FakeEndpoint(tmp_path), raw_dir=tmp_path / "raw"), out, page_size=10)  # own tables: ok


def test_robots_response_stored_with_decision(tmp_path):
    fake = FakeEndpoint(tmp_path)
    sp = cs.Sparql(client=fake, raw_dir=tmp_path / "raw")
    urls = [sp.request_url(cs.build_count_query(ds)) for ds in cs.DATASETS.values()]
    rec = cs.check_robots(fake, urls, raw_dir=tmp_path / "raw")
    p = Path(rec["raw_path"])
    assert p.parent == (tmp_path / "raw" / "robots").resolve() and p.name.startswith("robots_20261120000000_")
    assert gzip.decompress(p.read_bytes()).decode() == ROBOTS
    meta = json.loads(p.with_name(p.name + ".meta.json").read_text())
    assert meta["params"]["allowed"] is True and meta["params"]["sha256"] == rec["sha256"]
    _, _, _ = cs.run(sp, tmp_path / "out", page_size=10, robots=rec)
    inputs = json.loads((tmp_path / "out" / "cordis_participations.parquet.meta.json").read_text())["inputs"]
    assert any(i["path"].endswith(p.name) for i in inputs)
    with pytest.raises(cs.RobotsDisallowed):
        cs.check_robots(fake, ["https://cordis.europa.eu/data/x.zip"], raw_dir=tmp_path / "raw2")
    (stored,) = (tmp_path / "raw2" / "robots").glob("robots_*.txt.gz")
    assert json.loads(stored.with_name(stored.name + ".meta.json").read_text())["params"]["allowed"] is False


def test_january_first_signature_is_a_placeholder():
    """signatureDate 1 January (graph placeholder, e.g. 2014-01-01 on projects signed in 2017/18): both dates of the
    project are dropped, its participations get no public_ts instead of a years-early one; other projects unchanged."""
    tables = {n: pd.DataFrame(_load(n)[1], columns=_load(n)[0], dtype=object) for n in cs.DATASETS}
    pr = tables["projects"]
    pr.loc[pr.project_id == "700002", "signature_date"] = "2014-01-01"
    part, st = cs.build_participations_sparql(tables)
    r = _row(part, "H2020", "700002", "999000001")
    assert pd.isna(r["public_ts"]) and pd.isna(r["ec_signature_date"]) and pd.isna(r["start_date"])
    assert _row(part, "H2020", "700001", "999000001")["public_ts"] == TS("2015-11-20")
    assert st["projects"]["placeholder_signature_projects"] == 1
    assert st["projects"]["placeholder_signature_project_ids"] == ["700002"]
    proj, pst = cs.build_project_table(pd.DataFrame(
        {"project_id": ["1", "2"], "acronym": ["A", "B"], "start_date": ["2016-01-01", "2014-01-03"],
         "signature_date": ["2015-12-01", "2014-01-01"], "grant_uuid": ["g1", "g2"]}, dtype=object))
    assert proj.loc["1", "start_date"] == "2016-01-01" and proj.loc["2"].isna()[["start_date", "signature_date"]].all()
