"""Offline tests for scripts.sources.github_owners (fake ClickHouse + fake ecosyste.ms; no network)."""
import gzip
import hashlib
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from scripts.lib.domains import login_candidates
from scripts.lib.http import CachedResponse
from scripts.sources import gharchive as g
from scripts.sources import github as gh
from scripts.sources import github_owners as go

FIX = Path(__file__).parent / "fixtures" / "github_owners"
FETCHED = "2026-11-20T00:00:00+00:00"
CH_URL = "https://play.clickhouse.com/?user=play"


def fx(name: str) -> str:
    return (FIX / name).read_text()


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(go, "_FAILED_STREAK", [0])
    monkeypatch.setattr(go, "STATS", go.RunStats())


# ---------------------------------------------------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------------------------------------------------
def tsv(columns, rows) -> str:
    return "\t".join(columns) + "\n" + "".join("\t".join(str(v) for v in r) + "\n" for r in rows)


def act_rec(n_events, first=None, last=None, n_actor=0, n_personal=0):
    first = first or ("2015-01-01 00:00:00" if n_events else "")
    last = last or (first if n_events else "")
    return (n_events, first, last, n_actor, n_personal)


class FakeCHClient:
    """Answers the three SQL shapes of github_owners from a synthetic GH Archive world.

    world: login -> (n_events, first_ts, last_ts, n_actor, n_personal) for owner_activity;
    population: list of dicts (login, bucket, first_year, n_events, n_actor, n_personal) for pilot / sample.
    """

    def __init__(self, world=None, population=None, status=200):
        self.world = world or {}
        self.population = population or []
        self.sqls: list[str] = []
        self.max_retries = 0
        self.status = status

    def post(self, url, data=None, **kw):
        self.sqls.append(data)
        if self.status != 200:
            return CachedResponse(url, self.status, "Code: 241. DB::Exception: memory", FETCHED, False)
        if data.startswith("-- owner_activity"):
            lst = re.search(r"arrayJoin\(\[(.*?)\]\) AS login", data).group(1)
            logins = re.findall(r"'([a-z0-9-]+)'", lst)
            rows = [(l,) + tuple(self.world[l]) for l in sorted(logins) if l in self.world]
            return CachedResponse(url, 200, tsv(go.ACT_RAW_COLUMNS, rows), FETCHED, False)
        if data.startswith("-- owner_pilot"):
            m = int(re.search(r"% 10000 < (\d+)", data).group(1))
            agg = {}
            for o in self.population:
                if o["bucket"] < m:
                    k = (o["first_year"], o["bucket"])
                    a = agg.setdefault(k, [0, 0, 0])
                    a[0] += 1
                    a[1] += int(o["n_actor"] == 0)
                    a[2] += int(go.is_org_like([o["n_events"]], [o["n_personal"]])[0])
            rows = [(y, b, *v) for (y, b), v in sorted(agg.items())]
            return CachedResponse(url, 200, tsv(go.PILOT_COLUMNS, rows), FETCHED, False)
        if data.startswith("-- owner_sample"):
            conds = {int(y): int(m) for y, m in re.findall(r"\(first_year = (\d+) AND hash_bucket < (\d+)\)", data)}
            rows = []
            for o in sorted(self.population, key=lambda o: (o["first_year"], o["bucket"], o["login"])):
                if o["first_year"] in conds and o["bucket"] < conds[o["first_year"]] and \
                        go.is_org_like([o["n_events"]], [o["n_personal"]])[0]:
                    ts = f"{o['first_year']}-03-01 00:00:00"
                    rows.append((o["login"], o["bucket"], o["first_year"], ts, ts, o["n_events"], o["n_actor"],
                                 o["n_personal"]))
            return CachedResponse(url, 200, tsv(go.SAMPLE_RAW_COLUMNS, rows), FETCHED, False)
        return CachedResponse(url, 404, "Code: 60. DB::Exception: unknown", FETCHED, False)

    def _cache_path(self, host, key):
        return Path("/nonexistent") / key


def fake_ch(tmp_path, **kw) -> g.ClickHouse:
    return g.ClickHouse(url=CH_URL, client=FakeCHClient(**kw), raw_dir=tmp_path / "raw_ch")


def org_payload(login, website=None, name=None, uuid=1, kind="organization"):
    return {"login": login, "kind": kind, "uuid": str(uuid), "name": name, "website": website,
            "email": f"contact@{login}.example", "twitter": login, "location": "Somewhere",
            "description": "desc", "created_at": "2024-01-01T00:00:00.000Z"}


class FakeEco:
    """Stand-in for scripts.lib.http.Client against repos.ecosyste.ms (owners endpoint only)."""

    def __init__(self, owners=None, failures=None):
        self.owners = {k.lower(): v for k, v in (owners or {}).items()}
        self.failures = dict(failures or {})     # login -> remaining failing calls (-1 = always)
        self.gets: list[str] = []

    def get(self, url, use_cache=True, **kw):
        assert use_cache is False, "ecosyste.ms responses carry personal fields and must bypass the disk cache"
        m = re.fullmatch(r"https://repos\.ecosyste\.ms/api/v1/hosts/GitHub/owners/([a-z0-9-]+)", url)
        assert m, url
        login = m.group(1)
        self.gets.append(login)
        n = self.failures.get(login, 0)
        if n:
            self.failures[login] = n - 1 if n > 0 else n
            return CachedResponse(url, 503, "<html>unavailable</html>", FETCHED, False)
        p = self.owners.get(login)
        if p is None:
            return CachedResponse(url, 404, '{"error":"not found"}', FETCHED, False)
        return CachedResponse(url, 200, json.dumps(p), FETCHED, False)


# ---------------------------------------------------------------------------------------------------------------------
# SQL builders, chunking
# ---------------------------------------------------------------------------------------------------------------------
def test_sql_owner_activity_single_scan_actor_check_and_bots():
    sql = go.sql_owner_activity(["acme", "acme-hq", "0x1"])
    assert sql.startswith("-- owner_activity") and sql.rstrip().endswith("FORMAT TSVWithNames")
    assert sql.count("FROM github_events") == 1                                  # one scan only (20B-row limit)
    assert sql.count("'acme-hq'") == 1                                           # list written once (CTE)
    assert "WITH cands AS (SELECT arrayJoin(['acme', 'acme-hq', '0x1']) AS login)" in sql
    assert g.OWNER_SQL in sql and "lower(actor_login) IN (SELECT login FROM cands)" in sql
    assert "(lower(actor_login), 2)" in sql                                      # actor role in the same scan
    assert "match(lower(actor_login), " + g.sql_str(g.bots_regex()) + ")" in sql  # bots -> role 3 (ignored)
    for t in go.ORGISH_ACTOR_TYPES:
        assert f"'{t}'" in sql
    assert "event_type NOT IN (" in sql and "LIMIT " in sql
    assert sql == go.sql_owner_activity(["acme", "acme-hq", "0x1"])               # deterministic


@pytest.mark.parametrize("bad", ["acme'; DROP TABLE x --", "Acme", "a_b", "a b", "", "a" * 40, "acmé", "a/b",
                                 "a\\b", None, 5])
def test_sql_owner_activity_rejects_unsafe_logins(bad):
    with pytest.raises(ValueError):
        go.sql_owner_activity(["acme", bad])


def test_sql_owner_activity_size_guard():
    with pytest.raises(ValueError):
        go.sql_owner_activity([f"l{i:06d}" + "x" * 20 for i in range(12_000)])


def test_byte_chunks():
    items = [f"login{i}" for i in range(25)]
    ch = go.byte_chunks(items, max_items=10)
    assert [len(c) for c in ch] == [10, 10, 5] and sum(ch, []) == items
    ch = go.byte_chunks(items, max_items=100, max_bytes=50)
    assert sum(ch, []) == items and all(sum(len(x) + 4 for x in c) <= 50 for c in ch)
    assert go.byte_chunks([]) == [] and go.byte_chunks(items) == go.byte_chunks(items)
    with pytest.raises(ValueError):
        go.byte_chunks(items, max_items=0)


def test_pilot_and_sample_sql():
    p = go.sql_owner_pilot(400)
    assert p.startswith("-- owner_pilot") and p.count("FROM github_events") == 1
    assert p.count("% 10000 < 400") == 3 and "cityHash64(" in p
    assert "countIf(n_personal_actor_events <= 0.01 * n_events) AS n_org_like" in p
    s = go.sql_owner_sample({2018: 7, 2014: 23})
    assert s.startswith("-- owner_sample") and s.count("% 10000 < 23") == 3       # scan bound = max m
    assert "(first_year = 2014 AND hash_bucket < 23) OR (first_year = 2018 AND hash_bucket < 7)" in s
    assert "WHERE n_personal_actor_events <= 0.01 * n_events AND (" in s
    for bad in (0, 10_001, 2.5, "7"):
        with pytest.raises(ValueError):
            go.sql_owner_pilot(bad)
    with pytest.raises(ValueError):
        go.sql_owner_sample({})


# ---------------------------------------------------------------------------------------------------------------------
# owner_activity: typed frame, org_like rule, chunked queries
# ---------------------------------------------------------------------------------------------------------------------
def test_frame_activity_org_like_rule_from_fixture():
    raw = g.parse_tsv(fx("activity.tsv"), go.ACT_RAW_COLUMNS)
    df = go.frame_activity(raw, ["acme", "bigorg", "edgeuser", "jdoe", "quietorg", "onlyactor", "absent"])
    assert list(df.columns) == go.ACTIVITY_COLUMNS
    r = df.set_index("login")
    assert r.loc["acme", "org_like"] and r.loc["acme", "is_actor"]               # orgs DO act (fork/member/...)
    assert r.loc["bigorg", "org_like"]                                           # 1 personal <= 0.01 * 100
    assert not r.loc["edgeuser", "org_like"]                                     # 2 personal > 0.01 * 100
    assert not r.loc["jdoe", "org_like"]
    assert r.loc["quietorg", "org_like"] and not r.loc["quietorg", "is_actor"]
    assert not r.loc["onlyactor", "exists_as_owner"] and r.loc["onlyactor", "is_actor"]
    assert not r.loc["onlyactor", "org_like"] and pd.isna(r.loc["onlyactor", "first_event_ts"])
    assert not r.loc["absent", "exists_as_owner"] and r.loc["absent", "n_events"] == 0
    assert r.loc["acme", "first_event_ts"] == pd.Timestamp("2015-03-01 10:00:00", tz="UTC")
    assert str(df["first_event_ts"].dt.tz) == "UTC"


def test_frame_activity_validates():
    bad = pd.DataFrame([["acme", "5", "", "", "0", "0"]], columns=go.ACT_RAW_COLUMNS)
    with pytest.raises(go.OwnersError):
        go.frame_activity(bad)
    bad = pd.DataFrame([["acme", "5", "2015-01-01 00:00:00", "2015-01-01 00:00:00", "1", "2"]],
                       columns=go.ACT_RAW_COLUMNS)
    with pytest.raises(go.OwnersError):
        go.frame_activity(bad)


def test_owner_activity_chunks_and_reindexes(tmp_path):
    world = {f"org{i:02d}": act_rec(100, n_actor=3, n_personal=0) for i in range(0, 25, 2)}
    ch = fake_ch(tmp_path, world=world)
    logins = [f"Org{i:02d}" for i in range(25)] + ["bad_login", "org00"]
    df = go.owner_activity(logins, ch=ch, chunk_size=10)
    assert len(ch.client.sqls) == 3                                              # 25 distinct valid logins / 10
    assert df["login"].tolist() == [f"org{i:02d}" for i in range(25)]
    assert (df["exists_as_owner"] == (df.index % 2 == 0)).all() and df.loc[df["exists_as_owner"], "org_like"].all()
    assert list((tmp_path / "raw_ch").glob("owner_activity_*.tsv.gz"))          # raw TSV + SQL persisted


# ---------------------------------------------------------------------------------------------------------------------
# ecosyste.ms parsing: 404, personal-field dropping
# ---------------------------------------------------------------------------------------------------------------------
def test_parse_owner_org_drops_personal_fields():
    row, raw = go.parse_owner("acmehq", 200, fx("eco_org.json"), FETCHED)
    assert row["exists"] is True and row["kind"] == "organization" and row["login"] == "AcmeHQ"
    assert row["uuid"] == 4242 and row["website_url"] == "https://www.acme.com/" and row["website_domain"] == "acme.com"
    assert row["display_name"] == "Acme Robotics" and row["lookup_error"] is None and row["note"] is None
    assert row["owner_key"] == hashlib.sha256(b"acmehq").hexdigest()[:16]
    assert set(raw) <= set(go.ORG_RAW_FIELDS)
    for k in ("email", "twitter", "location", "description", "company", "icon_url", "funding_links", "metadata"):
        assert k not in raw
    assert "@" not in json.dumps(raw)


def test_parse_owner_user_404_errors_and_mismatch():
    row, raw = go.parse_owner("jdoe", 200, fx("eco_user.json"), FETCHED)
    assert row["exists"] is True and row["kind"] == "user"
    assert row["login"] is None and row["uuid"] is None and row["display_name"] is None and row["website_url"] is None
    assert raw == {"login": "jdoe", "kind": "user"}                              # natural person: identifiers only
    row, raw = go.parse_owner("ghost", 404, fx("eco_404.json"), FETCHED)
    assert row["exists"] is False and row["lookup_error"] is None and raw == {"error": "not found"}
    for status, text, err in [(500, "oops", "HTTP_500"), (-1, "", "HTTP_-1"), (429, "", "HTTP_429"),
                              (200, "<html>", "BAD_PAYLOAD"), (200, '{"x": 1}', "BAD_PAYLOAD")]:
        row, raw = go.parse_owner("acme", status, text, FETCHED)
        assert row["lookup_error"] == err and row["exists"] is None and raw is None
    row, _ = go.parse_owner("acme", 200, json.dumps(org_payload("acme-renamed", "https://acme.com")), FETCHED)
    assert row["note"] == "login_mismatch" and row["login"] is None and not go.accept(row, "acme.com")
    row, raw = go.parse_owner("acme", 200, json.dumps(org_payload("acme", "mailto:ceo@acme.com", "Acme")), FETCHED)
    assert row["website_url"] is None and raw["website"] is None                 # e-mail-like strings blanked


def test_lookup_owners_persists_reuses_and_never_writes_personal_fields(tmp_path):
    owners = {"acme": org_payload("Acme", "https://acme.com", "Acme", 11),
              "jdoe": json.loads(fx("eco_user.json")),
              "acmehq": json.loads(fx("eco_org.json"))}
    eco = FakeEco(owners)
    kw = dict(owners_path=tmp_path / "owners.parquet", raw_dir=tmp_path / "raw")
    df = go.lookup_owners(["acme", "JDoe", "ghost", "acmehq", "acme", "bad_login"], client=eco, **kw)
    assert list(df.columns) == go.LOOKUP_COLUMNS
    assert df["query_login"].tolist() == ["acme", "acmehq", "ghost", "jdoe"]
    assert sorted(eco.gets) == ["acme", "acmehq", "ghost", "jdoe"]
    r = df.set_index("query_login")
    assert r.loc["ghost", "exists"] == False and r.loc["jdoe", "kind"] == "user" and r.loc["jdoe", "login"] is None  # noqa: E712
    assert r.loc["acme", "uuid"] == 11 and str(df["fetched_at"].dt.tz) == "UTC"
    stored = pd.read_parquet(tmp_path / "owners.parquet")
    assert list(stored.columns) == go.OWNER_COLUMNS and len(stored) == 4
    assert stored.loc[stored["kind"] == "user", ["login", "uuid", "display_name", "website_url"]].isna().all().all()
    meta = json.loads((tmp_path / "owners.parquet.meta.json").read_text())
    assert "CC BY-SA 4.0" in meta["params"]["licence"]
    raws = list((tmp_path / "raw").glob("owners_*.jsonl.gz"))
    assert len(raws) == 1 and re.fullmatch(r"owners_[0-9a-f]{8}\.jsonl\.gz", raws[0].name)
    text = gzip.open(raws[0], "rt").read()
    assert len(text.splitlines()) == 4 and "@" not in text and "twitter" not in text and "Jane" not in text
    assert "CC BY-SA 4.0" in Path(str(raws[0]) + ".meta.json").read_text()
    # second call: everything reused from the parquet, no request
    eco2 = FakeEco(owners)
    df2 = go.lookup_owners(["acme", "jdoe", "ghost", "acmehq"], client=eco2, **kw)
    assert eco2.gets == []
    pd.testing.assert_frame_equal(df.drop(columns="fetched_at"), df2.drop(columns="fetched_at"))


def test_lookup_owners_retry_pass_budget_and_unpersisted_errors(tmp_path):
    owners = {l: org_payload(l, f"https://{l}.com", l.title(), i) for i, l in enumerate(["aa", "bb", "cc", "dd"], 1)}
    kw = dict(owners_path=tmp_path / "o.parquet", raw_dir=tmp_path / "raw")
    eco = FakeEco(owners, failures={"aa": 1, "bb": -1})
    df = go.lookup_owners(["aa", "bb", "cc"], client=eco, **kw).set_index("query_login")
    assert eco.gets.count("aa") == 2 and df.loc["aa", "lookup_error"] is None    # transient: retried at the end
    assert df.loc["bb", "lookup_error"] == "HTTP_503" and eco.gets.count("bb") == 2
    stored = pd.read_parquet(tmp_path / "o.parquet")
    assert go.owner_key("bb") not in set(stored["owner_key"])                     # failures are never persisted
    # request cap: 1 request allowed -> the rest is NOT_REQUESTED (and not persisted)
    b = go.Budget(1)
    df = go.lookup_owners(["bb", "dd"], client=FakeEco(owners), budget=b, **kw).set_index("query_login")
    assert b.used == 1 and df.loc["bb", "lookup_error"] is None and df.loc["dd", "lookup_error"] == "NOT_REQUESTED"
    df = go.lookup_owners(["bb", "dd"], client=FakeEco(owners), **kw).set_index("query_login")
    assert df["lookup_error"].isna().all()


def test_persistent_outage_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(go, "MAX_FAILED_STREAK", 3)
    logins = [f"x{i}" for i in range(6)]
    with pytest.raises(go.EcosystemsUnavailable):
        go.lookup_owners(logins, client=FakeEco({}, failures={l: -1 for l in logins}),
                         owners_path=tmp_path / "o.parquet", raw_dir=tmp_path / "raw")
    assert list((tmp_path / "raw").glob("owners_*.jsonl.gz"))                    # raw kept despite the stop


# ---------------------------------------------------------------------------------------------------------------------
# staged search
# ---------------------------------------------------------------------------------------------------------------------
ORG = dict(n_actor=5, n_personal=0)
WORLD = {
    # acme.com: tier-1 org (2016) beats an earlier tier-2 org (2012): stop at the first tier with an acceptance
    "acme": act_rec(500, "2016-05-01 00:00:00", **ORG),
    "acmehq": act_rec(500, "2012-01-01 00:00:00", **ORG),
    # zeta.ai: three tier-1 acceptances; earliest first event wins, tie -> login asc
    "zeta": act_rec(500, "2016-01-01 00:00:00", **ORG),
    "zetalabs": act_rec(500, "2014-01-01 00:00:00", **ORG),
    "zeta-labs": act_rec(500, "2014-01-01 00:00:00", **ORG),
    # beta.io: tier-1 org has another website; tier-1 'betaio'?? not a candidate; tier 2 accepted; tier 3 never used
    "beta": act_rec(500, "2010-06-01 00:00:00", **ORG),
    "getbeta": act_rec(500, "2017-01-01 00:00:00", **ORG),
    "beta-team": act_rec(500, "2011-03-01 00:00:00", **ORG),
    # delta.de: tier-1 'delta' is a user account in GH Archive (personal actor events) -> never looked up;
    #           tier-1 'deltaio'... no; tier 3 'joindelta' org accepted
    "delta": act_rec(100, "2013-01-01 00:00:00", n_actor=80, n_personal=80),
    "joindelta": act_rec(500, "2018-03-01 00:00:00", **ORG),
    # omega.dev: tier-1 owner is org_like by activity but ecosyste.ms says kind=user -> rejected
    "omega": act_rec(20, "2019-01-01 00:00:00", **ORG),
    # kappa.io: tier-1 owner org_like but unknown to ecosyste.ms (404) -> not accepted
    "kappa": act_rec(20, "2019-01-01 00:00:00", **ORG),
}
ECO = {
    "acme": org_payload("acme", "https://www.acme.com", "Acme", 1),
    "acmehq": org_payload("acmehq", "https://acme.com", "Acme", 2),
    "zeta": org_payload("zeta", "https://zeta.ai", "Zeta", 3),
    "zetalabs": org_payload("ZetaLabs", "https://zeta.ai", "Zeta Labs", 4),
    "zeta-labs": org_payload("zeta-labs", "http://zeta.ai/", "Zeta Labs", 5),
    "beta": org_payload("beta", "https://other.com", "Beta", 6),
    "getbeta": org_payload("getbeta", "https://beta.io", "Beta", 7),
    "beta-team": org_payload("beta-team", "https://beta.io", "Beta", 8),
    "delta": org_payload("delta", "https://delta.de", "Delta", 9),        # would match, but GH Archive says user
    "joindelta": org_payload("joindelta", "https://delta.de", "Delta", 10),
    "omega": org_payload("omega", "https://omega.dev", "Omega", 11, kind="user"),
}


def _entities():
    return pd.DataFrame({"entity_id": ["acme.com", "zeta.ai", "beta.io", "gamma.de", "delta.de", "foo.github.io",
                                       "omega.dev", "kappa.io"],
                         "names": [("Acme Robotics",), "Zeta Labs", None, "", "[]", None, "", ""]})


def _find(tmp_path, eco, ents=None, tag="a", **kw):
    d = tmp_path / tag
    ch = fake_ch(d, world=WORLD)
    out = go.find_org(_entities() if ents is None else ents, ch=ch, client=eco, out_path=d / "e.parquet",
                      search_log_path=d / "s.parquet", owners_path=d / "o.parquet", raw_dir=d / "raw_eco", **kw)
    return out, ch, d


def test_find_org_tiers_stop_tiebreak_and_rules(tmp_path):
    eco = FakeEco(ECO)
    out, ch, d = _find(tmp_path, eco)
    assert list(out.columns) == go.ENTITY_ORG_COLUMNS
    r = out.set_index("entity_id")
    assert set(r.index) == {"acme.com", "zeta.ai", "beta.io", "delta.de"}
    assert r.loc["acme.com", "org_login"] == "acme" and r.loc["acme.com", "tier"] == 1
    assert r.loc["acme.com", "n_accepted_in_tier"] == 1 and r.loc["acme.com", "org_uuid"] == 1
    assert r.loc["zeta.ai", "org_login"] == "zeta-labs" and r.loc["zeta.ai", "n_accepted_in_tier"] == 3
    assert r.loc["zeta.ai", "org_first_event_ts"] == pd.Timestamp("2014-01-01", tz="UTC")
    assert r.loc["beta.io", "org_login"] == "getbeta" and r.loc["beta.io", "tier"] == 2
    assert r.loc["delta.de", "org_login"] == "joindelta" and r.loc["delta.de", "tier"] == 3
    assert (out["match_method"] == "website_equals_domain+login_rule+org_kind (ecosyste.ms)").all()
    assert (out["match_confidence"] == "high").all() and str(out["org_first_event_ts"].dt.tz) == "UTC"
    # only org_like owners are looked up; lower tiers only for still-unresolved entities
    assert "delta" not in eco.gets and "acmehq" not in eco.gets and "beta-team" not in eco.gets
    assert set(eco.gets) <= set(WORLD) and "omega" in eco.gets and "kappa" in eco.gets
    acts = [s for s in ch.client.sqls if s.startswith("-- owner_activity")]
    assert len(acts) == 3 and "'acmehq'" not in acts[1] and "'gammahq'" in acts[1] and "'joingamma'" in acts[2]
    s = pd.read_parquet(d / "s.parquet").set_index("entity_id")
    assert list(s.reset_index().columns) == go.SEARCH_LOG_COLUMNS and len(s) == 8
    assert not s.loc["gamma.de", "resolved"] and s.loc["gamma.de", "tiers_searched"] == 3
    assert not s.loc["omega.dev", "resolved"] and not s.loc["kappa.io", "resolved"]
    assert s.loc["foo.github.io", "note"] == "not_a_company_domain" and s.loc["acme.com", "tiers_searched"] == 1
    assert s.loc["delta.de", "n_exists_searched"] >= 1 and s["n_lookup_errors"].sum() == 0
    assert pd.read_parquet(d / "e.parquet").equals(out)
    assert json.loads((d / "e.parquet.meta.json").read_text())["params"]["org_like_personal_ratio"] == 0.01


def test_find_org_is_order_independent_and_merges(tmp_path):
    a, _, _ = _find(tmp_path, FakeEco(ECO), tag="a")
    b, _, _ = _find(tmp_path, FakeEco(ECO), ents=_entities().sample(frac=1, random_state=3), tag="b")
    pd.testing.assert_frame_equal(a, b)
    d = tmp_path / "a"
    eco2 = dict(ECO)
    eco2.pop("getbeta")
    go.find_org(pd.DataFrame({"entity_id": ["beta.io"]}), ch=fake_ch(d, world=WORLD), client=FakeEco(eco2),
                out_path=d / "e.parquet", search_log_path=d / "s.parquet", owners_path=None, raw_dir=d / "raw2")
    got = pd.read_parquet(d / "e.parquet").set_index("entity_id")
    assert set(got.index) == {"acme.com", "zeta.ai", "delta.de", "beta.io"}
    assert got.loc["beta.io", "org_login"] == "beta-team" and got.loc["beta.io", "tier"] == 3


def test_find_org_holds_entity_on_transient_failure_instead_of_descending(tmp_path):
    ents = pd.DataFrame({"entity_id": ["acme.com"]})
    eco = FakeEco(ECO, failures={"acme": -1})                                    # tier-1 candidate never answers
    out, ch, d = _find(tmp_path, eco, ents=ents)
    assert out.empty and "acmehq" not in eco.gets                               # not matched to the tier-2 org
    assert eco.gets.count("acme") == 2                                           # retried before giving up
    assert len([s for s in ch.client.sqls if s.startswith("-- owner_activity")]) == 1
    s = pd.read_parquet(d / "s.parquet").set_index("entity_id")
    assert s.loc["acme.com", "note"] == "lookup_error_tier1" and not s.loc["acme.com", "resolved"]
    assert s.loc["acme.com", "n_lookup_errors"] == 1 and s.loc["acme.com", "tiers_searched"] == 1
    out2, _, _ = _find(tmp_path, FakeEco(ECO), ents=ents)                        # rerun once it answers
    assert out2["org_login"].tolist() == ["acme"] and out2["tier"].tolist() == [1]
    s2 = pd.read_parquet(d / "s.parquet").set_index("entity_id")
    assert s2.loc["acme.com", "resolved"] and s2.loc["acme.com", "n_lookup_errors"] == 0


def test_find_org_request_cap_holds_and_resumes(tmp_path):
    out, _, d = _find(tmp_path, FakeEco(ECO), budget=go.Budget(2))
    s = pd.read_parquet(d / "s.parquet").set_index("entity_id")
    capped = s[s["note"].fillna("").str.contains("request_cap_tier1")]
    assert len(capped) >= 1 and not set(capped.index) & set(out["entity_id"])
    full, _, _ = _find(tmp_path, FakeEco(ECO))                                   # resume: same final answer
    ref, _, _ = _find(tmp_path, FakeEco(ECO), tag="ref")
    pd.testing.assert_frame_equal(full, ref)


def test_accept_rule():
    o = {"exists": True, "kind": "organization", "login": "acme", "website_url": "https://acme.com",
         "display_name": None, "lookup_error": None, "note": None}
    assert go.accept(o, "acme.com")
    assert not go.accept({**o, "kind": "user"}, "acme.com")
    assert not go.accept({**o, "exists": pd.NA}, "acme.com") and not go.accept({**o, "exists": False}, "acme.com")
    assert not go.accept({**o, "website_url": "https://acme.github.io"}, "acme.com")
    assert not go.accept({**o, "lookup_error": "HTTP_503"}, "acme.com") and not go.accept(None, "acme.com")
    assert go.accept({**o, "login": "AcmeRobotics"}, "acme.com", ("Acme Robotics",))   # extra names extend


def test_tiers_are_those_of_the_github_route():
    t = go.tier_candidates("acme.com", ("Acme Robotics",))
    assert t == gh.tier_candidates("acme.com", ("Acme Robotics",))
    assert set(t[1]) | set(t[2]) | set(t[3]) == set(login_candidates("acme", "Acme Robotics"))


def test_plan_find_counts_without_requests():
    p = go.plan_find(_entities())
    assert p["entities"] == 8 and p["company_domains"] == 7
    assert p["tier1_entity_candidate_pairs"] == sum(len(go.tier_candidates(e, gh._names_tuple(n))[1])
                                                    for e, n in zip(_entities()["entity_id"], _entities()["names"])
                                                    if e != "foo.github.io")
    assert p["tier1_clickhouse_queries"] == 1


def test_read_search_input_bom(tmp_path):
    p = tmp_path / "in.csv"
    p.write_bytes("﻿entity_id,names\nacme.com,Acme Robotics|Acme\nzeta.ai,\n".encode("utf-8"))
    df = go.read_search_input(p)
    assert list(df.columns) == ["entity_id", "names"] and df["entity_id"].tolist() == ["acme.com", "zeta.ai"]
    p2 = tmp_path / "in2.csv"
    p2.write_text("entity_id\nacme.com\n")
    assert go.read_search_input(p2)["names"].tolist() == [""]
    p3 = tmp_path / "in3.csv"
    p3.write_text("domain\nacme.com\n")
    with pytest.raises(go.OwnersError):
        go.read_search_input(p3)


def test_cli_find_dry_run_makes_no_requests(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(g, "backend", lambda: (_ for _ in ()).throw(AssertionError("no ClickHouse in dry run")))
    p = tmp_path / "in.csv"
    _entities().assign(names=["Acme Robotics", "Zeta Labs", "", "", "", "", "", ""]).to_csv(p, index=False)
    assert go.main(["find", "--input", str(p), "--dry-run", "--org-like-rate", "0.05"]) == 0
    out = capsys.readouterr().out
    assert "tier1_distinct_logins" in out and "expected ecosyste.ms lookups" in out


# ---------------------------------------------------------------------------------------------------------------------
# outcome-blind pool sample
# ---------------------------------------------------------------------------------------------------------------------
def test_pool_rule():
    assert go.pool_rule("organization", "acme", "https://acme.com", "acme.com", None)
    assert go.pool_rule("organization", "zetarobotics", "https://zeta.ai", "zeta.ai", "Zeta Robotics")
    assert not go.pool_rule("user", "acme", "https://acme.com", "acme.com", None)
    assert not go.pool_rule(None, "acme", "https://acme.com", "acme.com", None)
    assert not go.pool_rule("organization", "acme", None, None, None)
    assert not go.pool_rule("organization", "randomorg", "https://acme.com", "acme.com", None)
    assert not go.pool_rule("organization", None, "https://acme.com", "acme.com", None)
    assert not go.pool_rule("organization", "acme", "https://acme.com", float("nan"), None)


def test_choose_m_and_inclusion_prob():
    py = pd.DataFrame({"hash_bucket": [0, 1, 2, 5, 7], "n_org_like": [2, 0, 3, 4, 1]})
    assert go.choose_m(py, 1) == (1, True)
    assert go.choose_m(py, 5) == (3, True)                       # buckets 0..2 hold 5
    assert go.choose_m(py, 6, pilot_m=10) == (6, True)           # bucket 5 needed -> m = 6
    assert go.choose_m(py, 11, pilot_m=10) == (10, False)        # never reached -> pilot bound
    assert go.choose_m(py, 9, pilot_m=6) == (6, True)
    assert go.choose_m(pd.DataFrame({"hash_bucket": [], "n_org_like": []}), 3, 50) == (50, False)
    with pytest.raises(ValueError):
        go.choose_m(py, 0)
    assert go.inclusion_prob(15) == 0.0015 and go.inclusion_prob(10_000) == 1.0


def _bucket(login: str) -> int:                                  # stands in for cityHash64 % 10000
    return int(hashlib.sha256(login.encode()).hexdigest(), 16) % 10_000


def population():
    pop = []
    for y in (2014, 2015):
        for i in range(4000):
            login = f"o{y}x{i}"
            kind = i % 10                                        # 0-2 org-like (+ one actor-heavy user), rest users
            n_personal = 0 if kind in (0, 1, 2) else 30
            pop.append({"login": login, "bucket": _bucket(login), "first_year": y, "n_events": 40,
                        "n_actor": 3 if kind in (1, 2) else (0 if kind == 0 else 30), "n_personal": n_personal})
    return pop


def eco_for_population(pop):
    owners = {}
    for o in pop:
        i = int(o["login"].split("x")[1])
        if i % 10 == 0:
            owners[o["login"]] = org_payload(o["login"], f"https://{o['login']}.com", None, i + 1)   # in pool
        elif i % 10 == 1:
            owners[o["login"]] = org_payload(o["login"], None, "No Website", i + 1)                 # org, no site
        # i % 10 == 2: org_like by activity but unknown to ecosyste.ms (404)
    return owners


def _sample(tmp_path, tag, budget=None, per_year=5, pop=None):
    d = tmp_path / tag
    pop = pop or population()
    ch = fake_ch(d, population=pop)
    eco = FakeEco(eco_for_population(pop))
    df, st = go.sample_pool(years=[2013, 2014, 2015], per_year=per_year, ch=ch, client=eco, pilot_m=400,
                            out_path=d / "s.parquet", years_path=d / "y.csv", owners_path=d / "o.parquet",
                            raw_dir=d / "raw", budget=budget)
    return df, st, d, ch, eco, pop


def test_sample_pool_end_to_end_and_deterministic(tmp_path):
    df, st, d, ch, eco, pop = _sample(tmp_path, "a")
    assert list(df.columns) == go.SAMPLE_COLUMNS and list(st.columns) == go.YEAR_COLUMNS
    s = st.set_index("year")
    for y in (2014, 2015):
        orglike = sorted(o["bucket"] for o in pop if o["first_year"] == y and o["n_personal"] == 0)
        m = orglike[4] + 1                                                       # smallest m with >= 5 owners
        assert s.loc[y, "hash_m"] == m and s.loc[y, "inclusion_prob"] == m / 10_000 and s.loc[y, "target_reached"]
        sub = df[df["first_event_year"] == y]
        assert (sub["hash_bucket"] < m).all() and (sub["hash_m"] == m).all() and (sub["inclusion_prob"] == m / 1e4).all()
        assert len(sub) == s.loc[y, "n_org_like_listed"] == s.loc[y, "n_looked_up"] == sum(b < m for b in orglike)
        assert s.loc[y, "n_owners_sampled"] == sum(1 for o in pop if o["first_year"] == y and o["bucket"] < m)
        assert s.loc[y, "complete"] and s.loc[y, "n_pending"] == 0
        exp_pool = sub["owner_key"].isin({go.owner_key(o["login"]) for o in pop
                                          if o["first_year"] == y and int(o["login"].split("x")[1]) % 10 == 0})
        assert (sub["in_pool"] == exp_pool).all()
        assert s.loc[y, "n_in_pool"] == int(exp_pool.sum())
        assert s.loc[y, "n_kind_organization"] + s.loc[y, "n_not_found"] == s.loc[y, "n_looked_up"]
    assert s.loc[2013, "n_owners_sampled"] == 0 and not s.loc[2013, "target_reached"]   # year without owners
    assert df.loc[df["in_pool"], "website_domain"].notna().all() and df.loc[df["in_pool"], "login"].notna().all()
    assert df.loc[df["kind"].isna(), "login"].isna().all()                      # unknown owners: no login stored
    assert str(df["first_event_ts"].dt.tz) == "UTC"
    # only org_like owners were looked up
    assert set(eco.gets) <= {o["login"] for o in pop if o["n_personal"] == 0}
    stored = pd.read_parquet(d / "s.parquet")
    assert len(stored) == len(df) and json.loads((d / "s.parquet.meta.json").read_text())["params"]["pilot_m"] == 400
    df2, st2, *_ = _sample(tmp_path, "b")
    pd.testing.assert_frame_equal(df.drop(columns="fetched_at"), df2.drop(columns="fetched_at"))
    pd.testing.assert_frame_equal(st, st2)


def test_sample_pool_incomplete_year_is_not_written(tmp_path):
    df, st, d, *_ = _sample(tmp_path, "cap", budget=go.Budget(3))
    s = st.set_index("year")
    assert s.loc[2014, "n_looked_up"] == 3 and not s.loc[2014, "complete"] and not s.loc[2015, "complete"]
    assert s.loc[2013, "complete"] and (d / "y.csv").exists()                   # no owners: trivially complete
    assert pd.read_parquet(d / "s.parquet").empty                                # incomplete years not written
    df, st, *_ = _sample(tmp_path, "cap")                                         # resume completes it
    assert st["complete"].all()
    assert set(pd.read_parquet(d / "s.parquet")["first_event_year"]) == {2014, 2015}


def test_sample_listing_validation():
    raw = pd.DataFrame([["acme", "3", "2015", "2014-12-31 23:00:00", "2015-01-01 00:00:00", "10", "0", "0"]],
                       columns=go.SAMPLE_RAW_COLUMNS)
    with pytest.raises(go.OwnersError):
        go.frame_sample_list(raw)                                                # first_event_ts not in first_year
    raw = pd.DataFrame([["acme", "3", "2015", "2015-02-01 00:00:00", "2015-02-01 00:00:00", "10", "5", "5"]],
                       columns=go.SAMPLE_RAW_COLUMNS)
    with pytest.raises(go.OwnersError):
        go.frame_sample_list(raw)                                                # not org_like


def test_sample_module_reads_no_labels():
    src = Path(go.__file__).read_text()
    assert "LABELS" not in src and "labels/" not in src and "companies.csv" not in src
    assert "label_at" not in src and "funding" not in src.replace("funding_links", "")


# ---------------------------------------------------------------------------------------------------------------------
# calibration helpers
# ---------------------------------------------------------------------------------------------------------------------
def test_owner_kinds_and_calibration_metrics():
    events = [{"repo": {"name": "Acme/x"}, "org": {"login": "acme"}},
              {"repo": {"name": "acme/y"}, "org": {"login": "ACME"}},
              {"repo": {"name": "jdoe/z"}},
              {"repo": {"name": "both/a"}, "org": {"login": "both"}},
              {"repo": {"name": "both/b"}},
              {"repo": {"name": "noslash"}},
              {"repo": {"name": "fork/r"}, "org": {"login": "otherorg"}},
              "garbage"]
    orgs, users = go.owner_kinds_from_events(events)
    assert orgs == {"acme", "both"} and users == {"jdoe", "both"}
    lab = go.calibration_labels(orgs | {"org2", "bad_name"}, users | {"u2"}, per_class=5)
    assert set(lab.loc[lab["label"] == "org", "login"]) == {"acme", "org2"}
    assert set(lab.loc[lab["label"] == "user", "login"]) == {"jdoe", "u2"}        # ambiguous 'both' dropped
    raw = g.parse_tsv(tsv(go.ACT_RAW_COLUMNS, [("acme", 500, "2015-01-01 00:00:00", "2015-01-01 00:00:00", 9, 0),
                                               ("org2", 5, "2015-01-01 00:00:00", "2015-01-01 00:00:00", 0, 0),
                                               ("jdoe", 50, "2015-01-01 00:00:00", "2015-01-01 00:00:00", 60, 60),
                                               ("u2", 1, "2015-01-01 00:00:00", "2015-01-01 00:00:00", 1, 1)]),
                      go.ACT_RAW_COLUMNS)
    act = go.frame_activity(raw, lab["login"])
    res = go.calibration_metrics(act, lab).set_index(["rule", "n_events_bin"])
    assert res.loc[("spec_not_actor", "all"), "org_recall"] == 0.5               # acme acts (forks) -> missed
    assert res.loc[("org_like", "all"), "org_recall"] == 1.0
    assert res.loc[("org_like", "all"), "user_pass_rate"] == 0.0
    assert res.loc[("org_like", "1"), "n_user"] == 1
