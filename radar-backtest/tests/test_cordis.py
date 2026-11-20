"""Offline tests for the CORDIS collector (synthetic fixtures under tests/fixtures/cordis/, no network)."""
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.lib.http import CachedResponse
from scripts.sources import cordis

FIX = Path(__file__).parent / "fixtures" / "cordis"
TS = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def _zip(tmp_path: Path, programme: str, subdir: str = "") -> Path:
    name = Path(cordis.PROGRAMMES[programme][1]).name
    z = tmp_path / "zips" / name
    z.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z, "w") as zf:
        for f in ("organization.csv", "project.csv"):
            zf.write(FIX / programme / f, arcname=subdir + f)
        zf.writestr(subdir + "euroSciVoc.csv", "projectID;euroSciVocCode\n")
    return z


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cordis")
    zips = {"FP7": _zip(tmp, "FP7"), "H2020": _zip(tmp, "H2020", "csv/"), "HORIZON": _zip(tmp, "HORIZON")}
    part, comp, stats = cordis.run(zips, tmp / "processed")
    return part, comp, stats, tmp / "processed"


def _row(part, programme, project_id, organisation_id):
    r = part[(part.programme == programme) & (part.project_id == project_id) & (part.organisation_id == organisation_id)]
    assert len(r) == 1
    return r.iloc[0]


# ---------------------------------------------------------------------------------------------------------------- parse
@pytest.mark.parametrize("programme,sep,encoding,rows_ok,rows_bad", [
    ("H2020", ";", "utf-8", 8, 0),      # current header, BOM, CRLF, all fields quoted, multi-line objective
    ("HORIZON", ";", "utf-8", 5, 0),    # current header, minimal quoting, LF, decimal comma
    ("FP7", ",", "cp1252", 4, 1),       # old header (bare 'id', contact person columns), cp1252, one malformed row
])
def test_header_variants(parsed, programme, sep, encoding, rows_ok, rows_bad):
    st = parsed[2][programme]
    assert st["organization"]["separator"] == sep
    assert st["organization"]["encoding"] == encoding
    assert st["organization"]["rows_ok"] == rows_ok
    assert st["organization"]["rows_bad"] == rows_bad
    assert st["project"]["rows_bad"] == 0
    assert set(st["organization"]["mapped"]) >= set(cordis.REQUIRED_ORG)


def test_project_multiline_objective_parsed():
    text, enc = cordis.decode_bytes((FIX / "H2020" / "project.csv").read_bytes())
    df, st = cordis.read_table(text, cordis.PROJECT_ALIASES, cordis.REQUIRED_PROJECT)
    assert enc == "utf-8" and st["rows_ok"] == 3 and st["rows_bad"] == 0
    assert df["project_id"].tolist() == ["700001", "700002", "700003"]   # BOM stripped from the first header


def test_old_header_maps_bare_id_to_organisation():
    text, enc = cordis.decode_bytes((FIX / "FP7" / "organization.csv").read_bytes())
    df, st = cordis.read_table(text, cordis.ORG_ALIASES, cordis.REQUIRED_ORG)
    assert st["mapped"]["organisation_id"] == "id" and st["mapped"]["organization_url"] == "organizationUrl"
    assert "Müller Technik GmbH" in df["name"].tolist()                 # cp1252 decoded correctly
    assert df["short_name"].notna().all()


def test_missing_required_column_raises():
    with pytest.raises(ValueError, match="required columns"):
        cordis.read_table("projectID;name\n1;x\n", cordis.ORG_ALIASES, cordis.REQUIRED_ORG, "t")


def test_output_schema_and_types(parsed):
    part, comp, _, out = parsed
    assert list(part.columns) == cordis.PART_COLUMNS
    assert not part.duplicated(["programme", "project_id", "organisation_id"]).any()
    for c in ("ec_signature_date", "start_date", "public_ts"):
        assert isinstance(part[c].dtype, pd.DatetimeTZDtype) and str(part[c].dt.tz) == "UTC"
    assert str(part["sme"].dtype) == "boolean"
    assert part["ec_contribution"].dtype == float
    assert set(part["activity_type"].dropna()) <= cordis.ACTIVITY_TYPES
    rt = pd.read_parquet(out / "cordis_participations.parquet")
    assert len(rt) == len(part) and list(rt.columns) == cordis.PART_COLUMNS
    assert (out / "cordis_participations.parquet.meta.json").exists()
    assert (out / "cordis_companies.parquet.meta.json").exists()
    meta = json.loads((out / "cordis_participations.parquet.meta.json").read_text())
    assert meta["rows"] == len(part)


def test_row_counts_and_dedupe(parsed):
    part = parsed[0]
    assert part.groupby("programme").size().to_dict() == {"FP7": 4, "H2020": 7, "HORIZON": 5}
    r = _row(part, "H2020", "700003", "999000001")                      # listed twice (thirdParty + participant)
    assert r["role"] == "participant" and r["ec_contribution"] == pytest.approx(30.0)
    assert parsed[2]["H2020"]["duplicate_key_rows"] == 2


def test_values_normalised(parsed):
    part = parsed[0]
    assert _row(part, "HORIZON", "101000001", "999000001")["ec_contribution"] == pytest.approx(750000.25)
    assert _row(part, "FP7", "200001", "999000010")["ec_contribution"] == pytest.approx(123456.78)
    assert np.isnan(_row(part, "HORIZON", "101000002", "999000008")["ec_contribution"])
    assert _row(part, "H2020", "700001", "999000002")["country"] == "GB"   # UK -> GB
    assert _row(part, "HORIZON", "101000001", "999000006")["country"] == "GR"  # EL -> GR
    assert bool(_row(part, "H2020", "700001", "999000001")["sme"]) is True
    assert bool(_row(part, "H2020", "700001", "999000002")["sme"]) is False
    assert pd.isna(_row(part, "H2020", "700002", "999000003")["sme"])
    assert pd.isna(_row(part, "HORIZON", "101000002", "999000009")["activity_type"])  # 'XYZ' is not a valid type
    assert parsed[2]["HORIZON"]["unknown_activity_type"] == 1
    assert _row(part, "H2020", "700001", "999000001")["framework"] == "H2020"


# ------------------------------------------------------------------------------------------------------------ public_ts
def test_public_ts_signature_date_first(parsed):
    r = _row(parsed[0], "H2020", "700001", "999000001")
    assert r["public_ts"] == TS("2015-11-20") == r["ec_signature_date"]
    assert r["start_date"] == TS("2016-01-01")


def test_public_ts_falls_back_to_start_date(parsed):
    part = parsed[0]
    r = _row(part, "H2020", "700002", "999000001")                      # empty ecSignatureDate
    assert pd.isna(r["ec_signature_date"]) and r["public_ts"] == TS("2017-03-01")
    r = _row(part, "HORIZON", "101000002", "999000007")
    assert r["public_ts"] == TS("2023-06-01")
    r = _row(part, "FP7", "200001", "999000010")                        # old header: no signature column at all
    assert pd.isna(r["ec_signature_date"]) and r["public_ts"] == TS("2009-03-01")  # day-first '01/03/2009'
    r = _row(part, "FP7", "200099", "999000012")                        # project missing from project.csv
    assert pd.isna(r["public_ts"])
    assert parsed[2]["FP7"]["join_rate"] == pytest.approx(3 / 4)


def test_parse_date_formats():
    assert cordis.parse_date("2015-11-20") == TS("2015-11-20")
    assert cordis.parse_date("2015-11-20 00:00:00") == TS("2015-11-20")
    assert cordis.parse_date("20/11/2015") == TS("2015-11-20")
    assert cordis.parse_date("20.11.2015") == TS("2015-11-20")
    assert pd.isna(cordis.parse_date(None)) and pd.isna(cordis.parse_date("2015-13-01"))
    assert pd.isna(cordis.parse_date("Nov 2015"))


def test_parse_amount():
    assert cordis.parse_amount("1234.5") == 1234.5
    assert cordis.parse_amount("1234,5") == 1234.5
    assert cordis.parse_amount("1.234.567,89") == pytest.approx(1234567.89)
    assert cordis.parse_amount("1,234,567.89") == pytest.approx(1234567.89)
    assert cordis.parse_amount("1.234.567") == 1234567.0
    assert np.isnan(cordis.parse_amount(None)) and np.isnan(cordis.parse_amount("n/a"))


# ------------------------------------------------------------------------------------------------------------- contacts
def test_contact_columns_dropped(parsed):
    part, comp, stats, out = parsed
    assert not [c for c in part.columns if cordis.CONTACT_RE.search(c)]
    for frame in (part, pd.read_parquet(out / "cordis_participations.parquet"), comp.astype(str)):
        assert not frame.astype(str).apply(lambda s: s.str.contains("REDACTED_CONTACT")).any().any()
    assert stats["H2020"]["organization"]["contact_columns_dropped"] == ["contactForm"]
    assert set(stats["FP7"]["organization"]["contact_columns_dropped"]) == {
        "contactForm", "contactType", "contactTitle", "contactFirstNames", "contactLastNames", "contactFunction",
        "contactTelephoneNumber", "contactFaxNumber"}


def test_contact_column_never_mapped_even_if_aliased():
    hdr = ["projectID", "organisationID", "name", "activityType", "country", "contactURL"]
    cols = cordis.map_columns(hdr, {**cordis.ORG_ALIASES, "organization_url": ["contactURL"]})
    assert "organization_url" not in cols


# -------------------------------------------------------------------------------------------------------------- domains
def test_domain_normalisation(parsed):
    part = parsed[0]
    dom = lambda p, j, o: _row(part, p, j, o)["domain"]  # noqa: E731
    assert dom("H2020", "700001", "999000001") == "acme-robotics.de"    # http://www.acme-robotics.de/en
    assert dom("H2020", "700002", "999000001") == "acme-robotics.de"    # bare host
    assert dom("HORIZON", "101000001", "999000006") == "epsilon.gr"     # www. without scheme
    assert dom("FP7", "200002", "999000011") == "theta.co.uk"
    assert dom("H2020", "700001", "999000002") == "uni-example.ac.uk"
    assert pd.isna(dom("H2020", "700003", "999000004"))                 # platform (github.io)
    assert pd.isna(dom("H2020", "700003", "999000005"))                 # no URL
    assert pd.isna(_row(part, "H2020", "700003", "999000005")["organization_url"])


# ----------------------------------------------------------------------------------------------------------- companies
def test_prc_filter(parsed):
    comp = parsed[1]
    assert comp["domain"].tolist() == sorted(["acme-robotics.de", "epsilon.gr", "zeta.ai", "mueller-technik.de",
                                              "theta.co.uk", "orphan-systems.ro"])
    for not_prc in ("uni-example.ac.uk", "example.nl", "odd-type.be"):  # HES, PUB, invalid type
        assert not_prc not in set(comp["domain"])
    assert comp["domain"].is_unique


def test_company_aggregation(parsed):
    comp = parsed[1].set_index("domain")
    a = comp.loc["acme-robotics.de"]
    assert a["n_participations"] == 6
    assert list(a["names"]) == ["Acme Robotics Austria GmbH", "Acme Robotics GmbH"]
    assert a["country"] == "DE" and list(a["countries"]) == ["AT", "DE"] and bool(a["multi_country"]) is True
    assert bool(a["sme_any"]) is True
    assert a["first_public_ts"] == TS("2009-03-01")                     # FP7 start date (no signature date)
    assert list(a["programmes"]) == ["FP7", "H2020", "HORIZON"]
    assert list(a["organisation_ids"]) == ["999000001", "999000003"]
    e = comp.loc["epsilon.gr"]
    assert e["country"] == "GR" and bool(e["multi_country"]) is False
    assert e["first_public_ts"] == TS("2021-12-15")
    assert pd.isna(comp.loc["mueller-technik.de", "sme_any"])           # FP7 old header has no SME column
    assert bool(comp.loc["zeta.ai", "sme_any"]) is False
    assert pd.isna(comp.loc["orphan-systems.ro", "first_public_ts"])


def test_country_mode_tie_is_alphabetical_and_platforms_excluded():
    base = dict(programme="H2020", name="X", short_name=None, activity_type="PRC", organization_url="x",
                role="participant", ec_contribution=1.0, ec_signature_date=pd.NaT, start_date=pd.NaT, framework="H2020")
    rows = [dict(base, project_id=str(i), organisation_id=str(i), country=c, sme=s, domain=d,
                 public_ts=TS(f"201{i}-01-01"))
            for i, (c, s, d) in enumerate([("SE", None, "x.se"), ("AT", None, "x.se"), (None, None, "x.se"),
                                           ("SE", None, "x.se"), ("AT", None, "x.se"), ("DE", True, None)])]
    part = pd.DataFrame(rows)[cordis.PART_COLUMNS]
    part["sme"] = pd.array(part["sme"].tolist(), dtype="boolean")
    comp = cordis.build_companies(part)
    assert comp["domain"].tolist() == ["x.se"]                          # domain None (platform/no URL) excluded
    r = comp.iloc[0]
    assert r["country"] == "AT" and list(r["countries"]) == ["AT", "SE"] and bool(r["multi_country"])
    assert pd.isna(r["sme_any"]) and r["n_participations"] == 5 and r["first_public_ts"] == TS("2010-01-01")
    empty = cordis.build_companies(part.iloc[0:0])
    assert list(empty.columns) == cordis.COMPANY_COLUMNS and len(empty) == 0


def test_deterministic(parsed, tmp_path):
    zips = {"FP7": _zip(tmp_path, "FP7"), "H2020": _zip(tmp_path, "H2020"), "HORIZON": _zip(tmp_path, "HORIZON")}
    part2, comp2, _ = cordis.run(zips, tmp_path / "p2")
    pd.testing.assert_frame_equal(parsed[0], part2)
    pd.testing.assert_frame_equal(parsed[1], comp2)


def test_summary_prints(parsed):
    s = cordis.summarize(parsed[0], parsed[1])
    assert "distinct PRC company domains: 6" in s and "2009:" in s and "pct_missing_sig" in s


# ------------------------------------------------------------------------------------------------- obtaining the files
def _tiny_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("organization.csv", "projectID;organisationID\n")
    return buf.getvalue()


class FakeClient:
    """Offline stand-in for scripts.lib.http.Client."""

    def __init__(self, robots: dict[str, str], portal: dict, payload: bytes | None = None,
                 robots_status: dict[str, int] | None = None):
        self.robots, self.portal, self.streamed = robots, portal, []
        self.payload = _tiny_zip_bytes() if payload is None else payload
        self.robots_status = robots_status or {}
        self.robots_use_cache = []
        self.session = type("S", (), {"headers": {"User-Agent": "radar-backtest/0.1"}})()

    def get(self, url, stream_to=None, use_cache=True, **kw):
        if url.endswith("/robots.txt"):
            host = url.split("/")[2]
            self.robots_use_cache.append(use_cache)
            return CachedResponse(url, self.robots_status.get(host, 200), self.robots.get(host, ""),
                                  "2026-01-01T00:00:00+00:00", False)
        if "data.europa.eu/api" in url:
            return CachedResponse(url, 200, json.dumps(self.portal), "2026-01-01T00:00:00+00:00", False)
        if stream_to is not None:
            self.streamed.append(url)
            Path(stream_to).write_bytes(self.payload)
            return CachedResponse(url, 200, "", "2026-01-01T00:00:00+00:00", False)
        return CachedResponse(url, 404, "", "", False)


PORTAL = {"result": {"distributions": [
    {"format": {"id": "XLSX"}, "download_url": ["https://cordis.europa.eu/data/cordis-h2020projects-xlsx.zip"]},
    {"format": {"id": "CSV"}, "download_url": ["https://cordis.europa.eu/data/cordis-h2020projectPublications-csv.zip"]},
    {"format": {"id": "CSV"}, "download_url": ["https://cordis.europa.eu/data/cordis-h2020projects-csv.zip"]},
]}}


def test_resolve_url_from_portal(tmp_path):
    c = FakeClient({}, PORTAL)
    url, how = cordis.resolve_url(c, "H2020", tmp_path)
    assert (url, how) == ("https://cordis.europa.eu/data/cordis-h2020projects-csv.zip", "data.europa.eu")
    assert (tmp_path / "portal_cordish2020projects.json.gz").exists()
    assert (tmp_path / "portal_cordish2020projects.json.gz.meta.json").exists()
    url, how = cordis.resolve_url(FakeClient({}, {"result": {}}), "HORIZON", tmp_path)
    assert how == "fallback" and url.endswith("cordis-HORIZONprojects-csv.zip")


def test_download_respects_robots(tmp_path):
    c = FakeClient({"cordis.europa.eu": "User-agent: *\nDisallow: /data/\n"}, PORTAL)
    with pytest.raises(cordis.RobotsDisallowed):
        cordis.download(c, "H2020", tmp_path)
    assert c.streamed == [] and not list(tmp_path.glob("*.zip"))
    with pytest.raises(SystemExit, match="--zip-dir"):
        cordis.obtain_zips(["H2020"], None, False, tmp_path, client=c)


def test_download_when_allowed_writes_manifest(tmp_path):
    c = FakeClient({"cordis.europa.eu": "User-agent: *\nDisallow: /search\n"}, PORTAL)
    z = cordis.download(c, "H2020", tmp_path)
    assert c.streamed == ["https://cordis.europa.eu/data/cordis-h2020projects-csv.zip"]
    man = json.loads((tmp_path / (z.name + ".manifest.json")).read_text())
    assert set(man) >= {"url", "sha256", "bytes", "fetched_at"} and man["bytes"] == len(c.payload)
    assert (tmp_path / (z.name + ".meta.json")).exists()
    cordis.download(c, "H2020", tmp_path)                               # second call reuses the file
    assert len(c.streamed) == 1


def test_import_zip_dir(tmp_path):
    src = _zip(tmp_path, "HORIZON")
    raw = tmp_path / "raw"
    zips = cordis.obtain_zips(["HORIZON"], src.parent, False, raw)
    dest = zips["HORIZON"]
    assert dest.parent == raw and dest.read_bytes() == src.read_bytes()
    man = json.loads((raw / (dest.name + ".manifest.json")).read_text())
    assert man["url"] == cordis.PROGRAMMES["HORIZON"][1] and man["provenance"].startswith("manual")
    assert set(man) >= {"url", "sha256", "bytes", "fetched_at"}


# ------------------------------------------------------------------------------------------------ robustness (review)
def test_robots_fetched_fresh_and_unreachable_means_disallow(tmp_path):
    c = FakeClient({}, PORTAL, robots_status={"cordis.europa.eu": 503})
    assert cordis.robots_allows(c, "https://cordis.europa.eu/data/x.zip") is False
    with pytest.raises(cordis.RobotsDisallowed):
        cordis.download(c, "H2020", tmp_path)
    assert c.streamed == [] and c.robots_use_cache and not any(c.robots_use_cache)   # never replayed from cache
    c404 = FakeClient({}, PORTAL, robots_status={"cordis.europa.eu": 404})
    assert cordis.robots_allows(c404, "https://cordis.europa.eu/data/x.zip") is None   # no robots.txt: allowed


def test_download_rejects_non_zip_payload(tmp_path):
    c = FakeClient({"cordis.europa.eu": "User-agent: *\nDisallow: /search\n"}, PORTAL, payload=b"<html>busy</html>")
    with pytest.raises(RuntimeError, match="not a zip"):
        cordis.download(c, "H2020", tmp_path)
    assert not list(tmp_path.glob("cordis-*"))                          # nothing left in raw (no zip, manifest, meta)


def test_import_rejects_non_zip(tmp_path):
    src = tmp_path / "in" / Path(cordis.PROGRAMMES["H2020"][1]).name
    src.parent.mkdir()
    src.write_bytes(b"<!doctype html><html></html>")
    with pytest.raises(SystemExit, match="not a zip"):
        cordis.obtain_zips(["H2020"], src.parent, False, tmp_path / "raw")
    assert not (tmp_path / "raw").exists() or not list((tmp_path / "raw").iterdir())


def test_utf8_with_stray_byte_is_not_reread_as_cp1252():
    hdr = '﻿"projectID";"organisationID";"name";"activityType";"country";"organizationURL"\r\n'
    good = '"1";"2";"Société Ünïcode";"PRC";"FR";"www.a.fr"\r\n' * 20
    raw = (hdr + good).encode("utf-8") + b'"1";"3";"bad \xff byte";"PRC";"FR";"www.b.fr"\r\n'
    text, enc = cordis.decode_bytes(raw)
    assert enc == "utf-8+replace(1)"
    df, st = cordis.read_table(text, cordis.ORG_ALIASES, cordis.REQUIRED_ORG, "t")   # BOM did not break the header
    assert st["rows_ok"] == 21 and df["name"].iloc[0] == "Société Ünïcode"
    # a genuinely cp1252 file still falls back to cp1252
    assert cordis.decode_bytes("x;y\nMüller;Café\n".encode("cp1252")) == ("x;y\nMüller;Café\n", "cp1252")


def test_unbalanced_quote_does_not_abort_parsing():
    hdr = "projectID;organisationID;name;activityType;country;organizationURL\n"
    ok = "".join(f"1;{i};Org {i};PRC;DE;www.o{i}.de\n" for i in range(30000))
    text = hdr + '1;9;"broken name;PRC;DE;www.b.de\n' + ok
    df, st = cordis.read_table(text, cordis.ORG_ALIASES, cordis.REQUIRED_ORG, "t")
    assert st["rows_csv_error"] == 1 and st["rows_bad"] >= 1
    assert st["lines_in_bad_rows"] > 1000                               # the swallowed region is made visible
    assert st["rows_ok"] + st["lines_in_bad_rows"] == 30001
    assert df["organisation_id"].iloc[-1] == "29999"                   # parsing resumed after the broken quote


def test_unparsed_dates_counted():
    org = pd.DataFrame({c: [None] for c in cordis.ORG_ALIASES}, dtype=object)
    org.loc[0, ["project_id", "organisation_id", "name", "activity_type", "country"]] = ["1", "2", "X", "PRC", "DE"]
    proj = pd.DataFrame({"project_id": ["1"], "start_date": ["03/13/2015"], "ec_signature_date": ["2015-13-01"],
                         "framework": [None]}, dtype=object)
    part, st = cordis.build_participations(org, proj, "H2020")
    assert st["unparsed_dates"] == {"ec_signature_date": 1, "start_date": 1}   # month 13 / month-first date
    assert pd.isna(part.loc[0, "public_ts"])


def test_email_in_url_field_is_dropped():
    urls = ["mailto:jane.doe@gmail.com", "info@acme-x.de", "https://medium.com/@acme", "www.acme-y.de"]
    org = pd.DataFrame({c: [None] * 4 for c in cordis.ORG_ALIASES}, dtype=object)
    org["project_id"], org["organisation_id"] = "1", ["1", "2", "3", "4"]
    org["activity_type"], org["organization_url"] = "PRC", urls
    proj = pd.DataFrame({"project_id": ["1"], "start_date": ["2015-01-01"], "ec_signature_date": [None],
                         "framework": [None]}, dtype=object)
    part, st = cordis.build_participations(org, proj, "H2020")
    assert part["organization_url"].iloc[:2].isna().all() and st["url_email_like_dropped"] == 2
    assert part["domain"].iloc[:3].isna().all() and part["domain"].iloc[3] == "acme-y.de"  # no webmail "company"
    assert part["organization_url"].iloc[2] == "https://medium.com/@acme"
    assert not part.astype(str).apply(lambda s: s.str.contains("jane.doe")).any().any()
