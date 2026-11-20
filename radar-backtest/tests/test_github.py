"""Offline tests for the GitHub collector (mocked client, synthetic data; no network)."""
import gzip
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from scripts.lib.domains import login_candidates
from scripts.lib.http import CachedResponse
from scripts.sources import github as gh

FIX = Path(__file__).parent / "fixtures" / "github"
FETCHED = "2026-11-20T00:00:00+00:00"
T0 = datetime(2007, 1, 1, tzinfo=timezone.utc)
IDS_PER_YEAR = 10_000
MAX_ID = 200_000


def created(i: int) -> datetime:
    return T0 + timedelta(days=365.25 * i / IDS_PER_YEAR)


def deleted(i: int) -> bool:
    return i % 7 == 0 or 30_000 <= i < 30_500  # scattered deletions + one long purged run


def is_org(i: int) -> bool:
    return i % 10 == 0 and not deleted(i)


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    """Minimal stand-in for scripts.lib.http.Client answering GraphQL + the REST paths the collector uses."""

    def __init__(self, orgs: dict | None = None, gql_failures: dict | None = None, status: int = 200):
        self.orgs = {k.lower(): v for k, v in (orgs or {}).items()}
        self.posts: list[list[str]] = []
        self.gets: list[tuple[str, dict]] = []
        self.gql_failures = dict(gql_failures or {})  # login -> remaining failing calls (-1 = always)
        self.status = status

    def _resp(self, url, obj, status=200):
        return CachedResponse(url, status, json.dumps(obj), FETCHED, False)

    def post(self, url, json_body=None, **kw):
        assert url == gh.GRAPHQL_URL
        if self.status != 200:
            return self._resp(url, {"message": "GitHub GraphQL is not available"}, self.status)
        pairs = re.findall(r'(o\d+): organization\(login: "([^"]*)"\)', json_body["query"])
        logins = [l for _, l in pairs]
        self.posts.append(logins)
        for l in logins:
            n = self.gql_failures.get(l, 0)
            if n:
                self.gql_failures[l] = n - 1 if n > 0 else n
                return self._resp(url, {"errors": [{"message": "Something went wrong while executing your query."}]})
        data, errors = {"rateLimit": {"cost": 1}}, []
        for a, l in pairs:
            node = self.orgs.get(l.lower())
            if node is None and l.startswith("org") and l[3:].isdigit() and is_org(int(l[3:])):
                node = synthetic_org(int(l[3:]))
            data[a] = node
            if node is None:
                errors.append({"type": "NOT_FOUND", "path": [a], "message": f"Could not resolve '{l}'"})
        out = {"data": data}
        if errors:
            out["errors"] = errors
        return self._resp(url, out)

    def get(self, url, params=None, use_cache=True, **kw):
        path = url[len(gh.API):]
        self.gets.append((path, dict(params or {})))
        if self.status != 200:
            return self._resp(url, {"message": "This GitHub API path is not available"}, self.status)
        if path == "/rate_limit":
            r = {"limit": 5000, "used": 1, "remaining": 4999, "reset": 1}
            return self._resp(url, {"resources": {"core": r, "graphql": r}})
        m = re.fullmatch(r"/user/(\d+)", path)
        if m:
            i = int(m.group(1))
            assert use_cache is False  # profile responses must bypass the on-disk cache
            if i > MAX_ID or deleted(i):
                return self._resp(url, {"message": "Not Found"}, 404)
            return self._resp(url, {"id": i, "login": f"u{i}", "type": "Organization" if is_org(i) else "User",
                                    "created_at": iso(created(i)), "email": "someone@example.org"})
        if path == "/users":
            s = int(params["since"])
            nxt = next((j for j in range(s + 1, MAX_ID + 1) if not deleted(j)), None)
            return self._resp(url, [] if nxt is None else [{"id": nxt, "login": f"u{nxt}", "type": "User"}])
        if path == "/organizations":
            s, n = int(params["since"]), int(params["per_page"])
            ids = []
            j = s + 1
            while len(ids) < n and j <= MAX_ID:
                if is_org(j):
                    ids.append(j)
                j += 1
            return self._resp(url, [{"id": j, "login": f"org{j}", "description": None} for j in ids])
        raise AssertionError(f"unexpected GET {path}")


def synthetic_org(i: int) -> dict:
    kind = i % 30
    if kind == 0:
        url = f"https://www.org{i}.com"          # login == stem -> in pool
    elif kind == 10:
        url = "https://unrelated-company.com"    # mismatching login -> not in pool
    else:
        url = None                                # no website -> not in pool
    # display names never repeat the login for the mismatching kind: login_rule lets the display name add
    # candidates, so "Org <i>" would make org<i> a candidate for any website.
    name = f"Org {i}" if kind == 0 else "Unrelated Name"
    return {"login": f"org{i}", "databaseId": i, "createdAt": iso(created(i)), "websiteUrl": url, "name": name}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(gh, "_sleep", lambda s: None)
    monkeypatch.setattr(gh, "_FAILED_STREAK", [0])


def node(login, created_at, website, name=None, dbid=None):
    return {"login": login, "databaseId": dbid or abs(hash(login)) % 10**6, "createdAt": created_at,
            "websiteUrl": website, "name": name}


# ----------------------------------------------------------------------------------------------------------------------
# A) GraphQL parsing + lookup_orgs
# ----------------------------------------------------------------------------------------------------------------------
def test_build_query_aliases_and_escaping():
    q = gh.build_query(["acme", 'we"ird'])
    assert 'o0: organization(login: "acme")' in q
    assert 'o1: organization(login: "we\\"ird")' in q
    assert q.count("organization(") == 2 and "databaseId createdAt websiteUrl name" in q


def test_parse_graphql_fixture_with_not_found_and_errors():
    payload = json.loads((FIX / "graphql_mixed.json").read_text())
    logins = ["acme", "getacme", "acme-hq", "locked"]
    assert gh.classify_payload(payload, 4) == "ok"
    df = gh.org_frame(gh.parse_org_batch(logins, payload, FETCHED))
    assert list(df.columns) == gh.ORG_COLUMNS
    r = df.set_index("query_login")
    assert r.loc["acme", "exists"] == True and r.loc["acme", "login"] == "Acme"  # noqa: E712
    assert r.loc["acme", "website_domain"] == "acme.com" and r.loc["acme", "database_id"] == 1001
    assert r.loc["acme", "created_at"] == pd.Timestamp("2014-03-02T10:00:00Z")
    assert str(df["created_at"].dt.tz) == "UTC" and str(df["fetched_at"].dt.tz) == "UTC"
    assert r.loc["getacme", "exists"] == False and r.loc["getacme", "lookup_error"] is None  # noqa: E712
    assert r.loc["acme-hq", "website_domain"] is None                     # platform website -> no company domain
    assert pd.isna(r.loc["locked", "exists"]) and r.loc["locked", "lookup_error"] == "FORBIDDEN"


def test_classify_payload_batch_failures():
    assert gh.classify_payload(None, 1) == "retry"
    assert gh.classify_payload({"errors": [{"message": "timeout"}]}, 1) == "retry"
    assert gh.classify_payload({"data": {"o0": None}, "errors": [{"type": "RATE_LIMITED", "path": ["o0"]}]}, 1) == "retry"
    assert gh.classify_payload({"data": {"o0": None}}, 2) == "retry"        # alias missing
    assert gh.classify_payload({"data": {"o0": None, "o1": None},
                                "errors": [{"type": "NOT_FOUND", "path": ["o0"]},
                                           {"type": "NOT_FOUND", "path": ["o1"]}]}, 2) == "ok"


def test_lookup_orgs_batches_persists_and_reuses(tmp_path):
    fake = FakeGitHub({"acme": node("Acme", "2014-01-01T00:00:00Z", "https://acme.com", dbid=1),
                       "acmehq": node("acmehq", "2012-01-01T00:00:00Z", "acme.com", dbid=2)})
    orgs_path, raw_dir = tmp_path / "orgs.parquet", tmp_path / "raw"
    df = gh.lookup_orgs(["Acme", "nope", "acme", None, ""], client=fake, batch_size=1, orgs_path=orgs_path,
                        raw_dir=raw_dir)
    assert df["query_login"].tolist() == ["acme", "nope"]                 # input order, deduped case-insensitively
    assert df["exists"].tolist() == [True, False]
    assert sorted(map(tuple, fake.posts)) == [("acme",), ("nope",)]      # sorted batches of size 1
    files = list(raw_dir.glob("lookup_*.jsonl.gz"))
    assert len(files) == 1 and Path(str(files[0]) + ".meta.json").exists()
    with gzip.open(files[0], "rt") as f:
        lines = [json.loads(x) for x in f]
    assert len(lines) == 2 and all(l["kind"] == "graphql_org_lookup" and l["outcome"] == "ok" for l in lines)
    stored = pd.read_parquet(orgs_path)
    assert sorted(stored["query_login"]) == ["acme", "nope"]
    assert Path(str(orgs_path) + ".meta.json").exists()

    fake.posts.clear()
    df2 = gh.lookup_orgs(["acme", "ACMEHQ"], client=fake, orgs_path=orgs_path, raw_dir=raw_dir)
    assert fake.posts == [["acmehq"]]                                     # known logins are not looked up again
    assert df2["login"].tolist() == ["Acme", "acmehq"] and df2["exists"].all()
    stored = pd.read_parquet(orgs_path)
    assert sorted(stored["query_login"]) == ["acme", "acmehq", "nope"] and not stored["query_login"].duplicated().any()

    fake.posts.clear()
    gh.lookup_orgs(["acme", "acmehq"], client=fake, orgs_path=orgs_path, raw_dir=raw_dir, refresh=True)
    assert fake.posts == [["acme", "acmehq"]]
    assert len(pd.read_parquet(orgs_path)) == 3


def test_lookup_retries_then_splits_failing_batch(tmp_path):
    fake = FakeGitHub({"a1": node("a1", "2014-01-01T00:00:00Z", None), "a2": node("a2", "2014-01-01T00:00:00Z", None)},
                      gql_failures={"a1": 1, "bad": -1})
    df = gh.lookup_orgs(["a1", "a2", "bad", "zz"], client=fake, batch_size=10, orgs_path=tmp_path / "o.parquet",
                        raw_dir=tmp_path / "raw")
    r = df.set_index("query_login")
    assert r.loc["a1", "exists"] == True and r.loc["a2", "exists"] == True  # noqa: E712
    assert r.loc["zz", "exists"] == False  # noqa: E712
    assert pd.isna(r.loc["bad", "exists"]) and r.loc["bad", "lookup_error"] == "BATCH_FAILED"
    assert ["bad"] in fake.posts                                          # split down to the failing login
    stored = pd.read_parquet(tmp_path / "o.parquet")
    assert "bad" not in set(stored["query_login"])                        # errors are not persisted -> retried later


class CachingFake(FakeGitHub):
    """FakeGitHub that writes every GraphQL reply to a cache file like scripts.lib.http.Client does."""

    def __init__(self, *a, cache_dir: Path, forbidden=(), **kw):
        super().__init__(*a, **kw)
        self.cache_dir, self.forbidden = cache_dir, set(forbidden)

    def _cache_path(self, host, key):
        return self.cache_dir / host / f"{key}.json"

    def post(self, url, json_body=None, **kw):
        r = super().post(url, json_body=json_body, **kw)
        j = json.loads(r.text)
        for e in j.get("errors") or []:
            alias = (e.get("path") or [None])[0]
            if alias and re.search(rf'{alias}: organization\(login: "({"|".join(self.forbidden) or "^$"})"\)',
                                   json_body["query"]):
                e["type"] = "FORBIDDEN"
        r = CachedResponse(url, r.status, json.dumps(j), r.fetched_at, False)
        p = self._cache_path("api.github.com", gh._key("POST", url, None, json.dumps(json_body, sort_keys=True)))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(r.text)
        return r


def test_cached_reply_with_per_alias_error_is_evicted(tmp_path):
    cache = tmp_path / "cache"
    fake = CachingFake({"acme": node("acme", "2014-01-01T00:00:00Z", "https://acme.com")}, cache_dir=cache,
                       forbidden={"locked"})
    df = gh.lookup_orgs(["acme", "locked"], client=fake, orgs_path=tmp_path / "o.parquet", raw_dir=tmp_path / "raw")
    assert df.set_index("query_login").loc["locked", "lookup_error"] == "FORBIDDEN"
    assert not list(cache.rglob("*.json"))                 # error reply evicted -> a rerun asks GitHub again
    gh.lookup_orgs(["nope"], client=fake, orgs_path=tmp_path / "o.parquet", raw_dir=tmp_path / "raw")
    assert len(list(cache.rglob("*.json"))) == 1           # NOT_FOUND-only reply stays cached


def test_persistent_graphql_failure_stops_the_run(tmp_path):
    logins = [f"x{i}" for i in range(64)]
    fake = FakeGitHub(gql_failures={l: -1 for l in logins})
    with pytest.raises(gh.GitHubUnavailableError):
        gh.lookup_orgs(logins, client=fake, batch_size=64, orgs_path=tmp_path / "o.parquet", raw_dir=tmp_path / "raw")
    assert len(fake.posts) == gh.MAX_FAILED_STREAK * gh.GQL_ATTEMPTS
    assert not (tmp_path / "o.parquet").exists()


def test_access_error_is_raised_not_swallowed(tmp_path):
    with pytest.raises(gh.GitHubAccessError):
        gh.lookup_orgs(["acme"], client=FakeGitHub(status=403), orgs_path=tmp_path / "o.parquet",
                       raw_dir=tmp_path / "raw")
    assert not (tmp_path / "o.parquet").exists()


# ----------------------------------------------------------------------------------------------------------------------
# B) staged search
# ----------------------------------------------------------------------------------------------------------------------
def test_tier_candidates_partition_login_candidates():
    t = gh.tier_candidates("acme.com", ("Acme Robotics",))
    allc = set(login_candidates("acme", "Acme Robotics"))
    assert set(t[1]) | set(t[2]) | set(t[3]) == allc
    assert not (set(t[1]) & set(t[2]) or set(t[1]) & set(t[3]) or set(t[2]) & set(t[3]))
    assert set(t[1]) == {"acme", "acme-robotics", "acmerobotics"}
    for x in ["acmehq", "acme-hq", "acme-io", "acmelabs", "getacme", "useacme", "tryacme", "acme-dev", "acmeai"]:
        assert x in t[2]
    for x in ["acme-team", "joinacme", "acmelab", "go-acme", "acmesoftware"]:
        assert x in t[3]
    assert t == gh.tier_candidates("acme.com", ("Acme Robotics",))      # deterministic


ORGS_B = {
    # acme.com: tier-1 match (2015) beats an earlier tier-2 match (2012) -> stop at first tier
    "acme": node("acme", "2015-05-01T00:00:00Z", "https://www.acme.com", dbid=10),
    "acmehq": node("acmehq", "2012-01-01T00:00:00Z", "https://acme.com", dbid=11),
    # zeta.ai: three tier-1 matches; earliest created_at wins, tie -> login asc
    "zeta": node("zeta", "2016-01-01T00:00:00Z", "https://zeta.ai", dbid=20),
    "zetalabs": node("zetalabs", "2014-01-01T00:00:00Z", "https://zeta.ai", dbid=21),
    "zeta-labs": node("zeta-labs", "2014-01-01T00:00:00Z", "http://zeta.ai/", dbid=22),
    # beta.io: tier-1 org has another website -> rejected; tier 2 accepted; tier 3 (earlier) never consulted
    "beta": node("beta", "2010-01-01T00:00:00Z", "https://other.com", dbid=30),
    "getbeta": node("getbeta", "2017-01-01T00:00:00Z", "https://beta.io", dbid=31),
    "beta-team": node("beta-team", "2011-01-01T00:00:00Z", "https://beta.io", dbid=32),
    # delta.de: only a tier-3 org
    "joindelta": node("joindelta", "2018-03-01T00:00:00Z", "https://delta.de", dbid=40),
}


def _entities():
    return pd.DataFrame({"entity_id": ["acme.com", "zeta.ai", "beta.io", "gamma.de", "delta.de", "foo.github.io"],
                         "names": [("Acme Robotics",), "Zeta Labs", None, "", "[]", None]})


def test_find_org_tiers_stop_and_tiebreak(tmp_path):
    fake = FakeGitHub(ORGS_B)
    out = gh.find_org(_entities(), client=fake, out_path=tmp_path / "e.parquet",
                      search_log_path=tmp_path / "s.parquet", orgs_path=tmp_path / "o.parquet",
                      raw_dir=tmp_path / "raw")
    assert list(out.columns) == gh.ENTITY_ORG_COLUMNS
    r = out.set_index("entity_id")
    assert set(r.index) == {"acme.com", "zeta.ai", "beta.io", "delta.de"}    # gamma.de unresolved -> absent
    assert r.loc["acme.com", "org_login"] == "acme" and r.loc["acme.com", "tier"] == 1
    assert r.loc["acme.com", "n_accepted_in_tier"] == 1
    assert r.loc["zeta.ai", "org_login"] == "zeta-labs" and r.loc["zeta.ai", "n_accepted_in_tier"] == 3
    assert r.loc["zeta.ai", "org_created_at"] == pd.Timestamp("2014-01-01", tz="UTC")
    assert r.loc["beta.io", "org_login"] == "getbeta" and r.loc["beta.io", "tier"] == 2
    assert r.loc["delta.de", "org_login"] == "joindelta" and r.loc["delta.de", "tier"] == 3
    assert (out["match_method"] == "website_equals_domain+login_rule").all() and (out["match_confidence"] == "high").all()
    assert str(out["org_created_at"].dt.tz) == "UTC"
    # tier-2/3 lookups only for still-unresolved entities
    looked = {l for batch in fake.posts for l in batch}
    assert "acmehq" not in looked and "zetahq" not in looked and "beta-team" not in looked
    assert "gammahq" in looked and "joingamma" in looked
    s = pd.read_parquet(tmp_path / "s.parquet").set_index("entity_id")
    assert len(s) == 6 and not s.loc["gamma.de", "resolved"] and s.loc["gamma.de", "tiers_searched"] == 3
    assert s.loc["foo.github.io", "note"] == "not_a_company_domain" and s.loc["acme.com", "tiers_searched"] == 1
    assert pd.read_parquet(tmp_path / "e.parquet").equals(out)


def test_find_org_is_order_independent_and_merges(tmp_path):
    kw = dict(orgs_path=None, raw_dir=tmp_path / "raw", search_log_path=None)
    a = gh.find_org(_entities(), client=FakeGitHub(ORGS_B), out_path=None, **kw)
    shuffled = _entities().sample(frac=1, random_state=3)
    b = gh.find_org(shuffled, client=FakeGitHub(ORGS_B), out_path=None, **kw)
    pd.testing.assert_frame_equal(a, b)
    # merge: a second run with other entities keeps earlier rows; re-searched entities are replaced
    out = tmp_path / "e.parquet"
    gh.find_org(_entities(), client=FakeGitHub(ORGS_B), out_path=out, **kw)
    orgs2 = dict(ORGS_B)
    orgs2.pop("getbeta")
    gh.find_org(pd.DataFrame({"entity_id": ["beta.io"]}), client=FakeGitHub(orgs2), out_path=out, **kw)
    got = pd.read_parquet(out).set_index("entity_id")
    assert set(got.index) == {"acme.com", "zeta.ai", "delta.de", "beta.io"}
    assert got.loc["beta.io", "org_login"] == "beta-team" and got.loc["beta.io", "tier"] == 3


def test_find_org_holds_entity_on_lookup_error_instead_of_descending(tmp_path):
    orgs = {"acme": node("acme", "2015-05-01T00:00:00Z", "https://www.acme.com", dbid=10),
            "acmehq": node("acmehq", "2012-01-01T00:00:00Z", "https://acme.com", dbid=11)}
    kw = dict(orgs_path=tmp_path / "o.parquet", raw_dir=tmp_path / "raw", out_path=tmp_path / "e.parquet",
              search_log_path=tmp_path / "s.parquet")
    ents = pd.DataFrame({"entity_id": ["acme.com"]})
    fake = FakeGitHub(orgs, gql_failures={"acme": -1})                    # tier-1 candidate never answers
    out = gh.find_org(ents, client=fake, **kw)
    assert out.empty                                                      # not matched to the tier-2 org
    assert "acmehq" not in {l for b in fake.posts for l in b}             # tier 2 never searched
    s = pd.read_parquet(tmp_path / "s.parquet").set_index("entity_id")
    assert s.loc["acme.com", "note"] == "lookup_error_tier1" and not s.loc["acme.com", "resolved"]
    assert s.loc["acme.com", "n_lookup_errors"] == 1 and s.loc["acme.com", "tiers_searched"] == 1
    # rerun once GitHub answers: the canonical tier-1 match replaces the held row
    out2 = gh.find_org(ents, client=FakeGitHub(orgs), **kw)
    assert out2["org_login"].tolist() == ["acme"] and out2["tier"].tolist() == [1]
    s2 = pd.read_parquet(tmp_path / "s.parquet").set_index("entity_id")
    assert s2.loc["acme.com", "resolved"] and s2.loc["acme.com", "n_lookup_errors"] == 0


def test_names_parsing():
    assert gh._names_tuple("B Corp|A Corp") == ("A Corp", "B Corp")
    assert gh._names_tuple('["X", "Y"]') == ("X", "Y")
    assert gh._names_tuple(None) == () and gh._names_tuple(float("nan")) == () and gh._names_tuple("") == ()
    assert gh._names_tuple(("Z",)) == ("Z",)


# ----------------------------------------------------------------------------------------------------------------------
# C) sample: pool rule, inclusion probability, determinism
# ----------------------------------------------------------------------------------------------------------------------
def test_pool_rule():
    assert gh.pool_rule("acme", "https://acme.com", "acme.com", None)
    assert gh.pool_rule("acme-hq", "acme.com", "acme.com", "Acme")
    assert gh.pool_rule("zetarobotics", "https://zeta.ai", "zeta.ai", "Zeta Robotics")   # display name extends
    assert not gh.pool_rule("acme", None, None, None)
    assert not gh.pool_rule("acme", "https://acme.github.io", None, None)                 # platform website
    assert not gh.pool_rule("randomorg", "https://acme.com", "acme.com", None)            # login not a candidate
    assert not gh.pool_rule("acme", "https://acme.com", float("nan"), None)
    # documented lib behaviour: a display name equal to the login makes the login a candidate for ANY website
    assert gh.pool_rule("foobar", "https://randomblog.net", "randomblog.net", "Foobar")


def test_inclusion_prob_arithmetic():
    iv = [(10, 20), (15, 30), (100, math.inf)]
    assert gh.covered_length(iv, 0, 50) == 20               # ids 11..30
    assert gh.covered_length(iv, 0, 150) == 20 + 49         # + ids 101..149 (page reached end of list)
    assert gh.covered_length(iv, 25, 50) == 6               # ids 25..30
    assert gh.covered_length([], 0, 10) == 0
    s = gh.inclusion_stats(10, 20, 0, 100)
    assert s["density_per_id"] == pytest.approx(0.5)
    assert s["est_total_orgs"] == pytest.approx(50)
    assert s["inclusion_prob"] == pytest.approx(0.2) == pytest.approx(20 / 100)
    assert gh.inclusion_stats(0, 20, 0, 100)["inclusion_prob"] == pytest.approx(0.2)


def test_sample_draw_is_deterministic():
    a = gh.draw_since(1000, 50_000, 40, 20251120, 2015)
    assert a == gh.draw_since(1000, 50_000, 40, 20251120, 2015)
    assert a != gh.draw_since(1000, 50_000, 40, 20251120, 2016)
    assert a != gh.draw_since(1000, 50_000, 40, 20251121, 2015)
    assert all(1000 <= s < 50_000 for s in a) and len(a) == 40
    assert gh.draw_since(5, 5, 3, 1, 2015) == []


def _brute_bound(year: int) -> int:
    t = datetime(year, 1, 1, tzinfo=timezone.utc)
    return next(i for i in range(1, MAX_ID + 1) if not deleted(i) and created(i) >= t)


def test_id_bounds_binary_search_with_deleted_ids(tmp_path):
    fake = FakeGitHub()
    bounds_path = tmp_path / "bounds.csv"
    b = gh.year_bounds([2008, 2010, 2011], client=fake, bounds_path=bounds_path, probes_dir=tmp_path / "probes")
    assert b == {y: _brute_bound(y) for y in (2008, 2010, 2011)}
    csv = pd.read_csv(bounds_path)
    assert list(csv.columns) == gh.BOUNDS_COLUMNS and csv["year"].tolist() == [2008, 2010, 2011]
    # raw probe log keeps no profile fields (no email etc.)
    for f in (tmp_path / "probes").glob("*.jsonl.gz"):
        with gzip.open(f, "rt") as fh:
            for line in fh:
                rec = json.loads(line)
                assert "email" not in json.dumps(rec)
                if rec["kind"] == "user" and rec["account"]:
                    assert set(rec["account"]) == {"id", "type", "created_at"}
    # second call reuses the csv -> no network
    fake2 = FakeGitHub()
    assert gh.year_bounds([2010, 2011], client=fake2, bounds_path=bounds_path, probes_dir=tmp_path / "probes") == \
        {2010: b[2010], 2011: b[2011]}
    assert fake2.gets == []


def test_bound_in_purged_run(tmp_path):
    # boundary falling inside the deleted id run 30000..30499 resolves to the next existing id
    target = created(30_200)
    with gh.RawLog(tmp_path / "probes", "x") as raw:
        prober = gh.IdProber(FakeGitHub(), raw)
        (fid, fts, _), n = gh.find_bound(prober, pd.Timestamp(target))
    assert fid == next(i for i in range(30_200, MAX_ID) if not deleted(i)) and fts >= pd.Timestamp(target) and n > 0


def _run_sample(tmp_path, tag):
    d = tmp_path / tag
    df, st = gh.sample(pages=3, years=[2010, 2011], client=FakeGitHub(), base_seed=123, out_path=d / "s.parquet",
                       bounds_path=d / "b.csv", orgs_path=d / "o.parquet", pages_raw=d / "pages",
                       probes_raw=d / "probes", lookup_raw=d / "lookup", batch_size=50)
    return df, st, d


def test_sample_end_to_end_and_deterministic(tmp_path):
    df, st, d = _run_sample(tmp_path, "a")
    assert list(df.columns) == gh.SAMPLE_COLUMNS and len(df) > 0
    assert set(df["sample_year"]) == {2010, 2011}
    assert not df["database_id"].duplicated().any()
    for y in (2010, 2011):
        s = st["per_year"][y]
        sub = df[df["sample_year"] == y]
        assert s["pages"] == 3 and s["sampled_orgs"] == len(sub) == s["enriched"]
        assert s["inclusion_prob"] == pytest.approx(s["covered_ids"] / (s["hi"] - s["lo"]))
        assert 0 < s["inclusion_prob"] < 1 and (sub["inclusion_prob"] == s["inclusion_prob"]).all()
        # density estimate close to the synthetic truth (1 org per ~10 ids minus deletions)
        true_total = sum(1 for i in range(s["lo"], s["hi"]) if is_org(i))
        assert abs(s["est_total_orgs"] - true_total) / true_total < 0.05
        assert sub["database_id"].between(s["lo"], s["hi"] - 1).all()
        assert (sub["created_year"] == y).all()
    # pool rule: only orgs whose website stem equals the login
    exp = df["database_id"] % 30 == 0
    assert (df["in_pool"] == exp).all() and df["in_pool"].any() and not df["in_pool"].all()
    assert df.loc[df["in_pool"], "website_domain"].notna().all()
    assert str(df["created_at"].dt.tz) == "UTC"
    # page since values come from the seeded draw
    lo, hi = st["per_year"][2010]["lo"], st["per_year"][2010]["hi"]
    assert set(df.loc[df["sample_year"] == 2010, "sample_page_since"]) <= set(gh.draw_since(lo, hi, 3, 123, 2010))
    assert Path(str(d / "s.parquet") + ".meta.json").exists()
    assert list((d / "pages").glob("pages_*.jsonl.gz")) and list((d / "lookup").glob("lookup_*.jsonl.gz"))
    # deterministic given the seed
    df2, st2, _ = _run_sample(tmp_path, "b")
    pd.testing.assert_frame_equal(df, df2)


def test_sample_module_reads_no_labels():
    src = Path(gh.__file__).read_text()
    assert "LABELS" not in src and "labels/" not in src and "companies.csv" not in src


class FilteringFake(FakeGitHub):
    """GitHub hides some accounts from list endpoints, so pages mid-list can hold fewer than per_page orgs."""

    def get(self, url, params=None, use_cache=True, **kw):
        r = super().get(url, params=params, use_cache=use_cache, **kw)
        if url.endswith("/organizations"):
            lst = [o for o in json.loads(r.text) if o["id"] % 1000 != 500]
            r = CachedResponse(r.url, r.status, json.dumps(lst), r.fetched_at, False)
        return r


def test_short_page_mid_list_does_not_inflate_inclusion_prob(tmp_path):
    d = tmp_path / "f"
    df, st = gh.sample(pages=3, years=[2010, 2011], client=FilteringFake(), base_seed=123, out_path=None,
                       bounds_path=d / "b.csv", orgs_path=None, pages_raw=d / "pages", probes_raw=d / "probes",
                       lookup_raw=d / "lookup", batch_size=50)
    assert sum(s["short_pages"] for s in st["per_year"].values()) > 0
    for y, s in st["per_year"].items():
        visible = sum(1 for i in range(s["lo"], s["hi"]) if is_org(i) and i % 1000 != 500)
        assert abs(s["est_total_orgs"] - visible) / visible < 0.05      # a short page used to count as "covers to inf"
        assert s["inclusion_prob"] < 0.5
