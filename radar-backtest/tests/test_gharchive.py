"""Offline tests for scripts.sources.gharchive (no network: a fake HTTP client serves fixture TSVs)."""
import gzip
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.lib.http import CachedResponse
from scripts.sources import gharchive as g

FIX = Path(__file__).parent / "fixtures" / "gharchive"


def fx(name: str) -> str:
    return (FIX / name).read_text()


class FakeClient:
    """Stands in for scripts.lib.http.Client: answers POSTed SQL with fn(sql) -> (status, text)."""

    def __init__(self, fn):
        self.fn = fn
        self.sqls: list[str] = []
        self.max_retries = 0

    def post(self, url, data=None, **kw):
        self.sqls.append(data)
        status, text = self.fn(data)
        return CachedResponse(url, status, text, "2026-01-01T00:00:00+00:00", False)

    def _cache_path(self, host, key):
        return Path("/nonexistent") / key


def fixture_server(sql: str):
    if "AS first_push_ts" in sql:
        return 200, fx("team.tsv")
    if "AS pr_opened" in sql:
        return 200, fx("ext.tsv")
    if "AS first_event_ts" in sql:
        return 200, fx("span.tsv")
    if "AS release" in sql:
        return 200, fx("daily.tsv")
    return 404, "Code: 60. DB::Exception: unknown"


def fake_ch(tmp_path, fn=fixture_server) -> g.ClickHouse:
    return g.ClickHouse(url="https://play.clickhouse.com/?user=play", client=FakeClient(fn), raw_dir=tmp_path / "raw")


# ---------------------------------------------------------------- logins / IN-lists
@pytest.mark.parametrize("bad", ["acme'; DROP TABLE x --", "Acme", "a_b", "a b", "", "a" * 40, "acmé", "a/b",
                                 "a\\b", "a'b", None, 5])
def test_validate_login_rejects(bad):
    with pytest.raises(ValueError):
        g.validate_login(bad)


def test_sql_in_list_and_normalize():
    assert g.sql_in_list(["acme", "acme-hq", "0x1"]) == "('acme', 'acme-hq', '0x1')"
    with pytest.raises(ValueError):
        g.sql_in_list([])
    with pytest.raises(ValueError):
        g.sql_in_list(["ok", "x'y"])
    ok, bad = g.normalize_logins(["Acme", " acme ", "Globex", "bad_login", "# comment", "", None, np.nan, "o'neil"])
    assert ok == ["acme", "globex"]
    assert bad == ["bad_login", "o'neil"]


def test_builders_reject_unsafe_logins():
    for build in (g.sql_org_span, g.sql_team_first_push, g.sql_org_activity_daily):
        with pytest.raises(ValueError):
            build(["acme", "evil') OR 1=1 --"])
    with pytest.raises(ValueError):
        g.sql_external_monthly(["alice"], ["acme", "Bad Org"])
    with pytest.raises(ValueError):
        g.bq_sql("span", ["acme'"])


# ---------------------------------------------------------------- owner extraction + bot filter
def test_owner_extraction():
    assert g.owner_of("PostHog/posthog") == "posthog"
    assert g.owner_of("acme/repo/extra") == "acme"
    assert g.owner_of("noslash") == ""
    assert g.owner_of(None) == ""
    # the SQL twin lower-cases and returns '' when there is no '/'
    assert "lower(if(position(repo_name, '/') > 0" in g.OWNER_SQL and ", ''))" in g.OWNER_SQL
    for sql in (g.sql_org_span(["acme"]), g.sql_team_first_push(["acme"]), g.sql_org_activity_daily(["acme"]),
                g.sql_external_monthly(["alice"], ["acme"])):
        assert g.OWNER_SQL in sql


def test_bot_filter():
    rx = g.bots_regex()
    for b in ["dependabot[bot]", "dependabot-preview[bot]", "renovate-bot", "github-actions[bot]", "greenkeeperio-bot",
              "my-ci-bot", "Renovate"]:
        assert g.is_bot(b, rx), b
    for h in ["alice", "robotics", "bottle", "abbot", "bot-maker"]:
        assert not g.is_bot(h, rx), h
    sql = g.sql_team_first_push(["acme"])
    assert "NOT match(lower(actor_login), " in sql
    # backslashes of the regex are doubled inside the ClickHouse string literal
    assert g.sql_str(rx) in sql and "\\\\[bot\\\\]$" in g.sql_str(rx)
    assert g.sql_str("a'b\\c") == "'a\\'b\\\\c'"


# ---------------------------------------------------------------- SQL builders + chunking
def test_sql_builders_shape_and_determinism():
    span = g.sql_org_span(["acme", "globex"])
    team = g.sql_team_first_push(["acme"])
    daily = g.sql_org_activity_daily(["acme"])
    ext = g.sql_external_monthly(["alice", "bob-dev"], ["acme"])
    for s in (span, team, daily, ext):
        assert s.rstrip().endswith("FORMAT TSVWithNames")
        assert f"LIMIT {g.MAX_ROWS + 1}" in s
    assert "event_type = 'PushEvent'" in team and "actor_login != ''" in team
    assert "event_type IN ('PushEvent', 'PullRequestEvent', 'IssuesEvent', 'ReleaseEvent')" in daily
    assert "toDate(created_at, 'UTC')" in daily
    assert "event_type = 'PullRequestEvent' AND action = 'opened'" in ext
    assert "event_type = 'IssuesEvent' AND action = 'opened'" in ext
    assert "toStartOfMonth(created_at, 'UTC')" in ext and "'_ext'" in ext
    assert "if(owner IN ('acme'), owner, '_ext')" in ext
    assert "lower(actor_login) IN ('alice', 'bob-dev')" in ext
    assert "author_association" not in span + team + daily + ext      # not populated before 2017
    assert "file_time" not in span + team + daily + ext                # file label is not UTC pre-2015
    assert g.sql_org_span(["acme", "globex"]) == span
    with pytest.raises(ValueError):
        g.sql_org_span(["a" + str(i) for i in range(30000)])         # > max query size


def test_chunked():
    assert [len(c) for c in g.chunked(list(range(1201)), 500)] == [500, 500, 201]
    assert g.chunked([], 500) == []
    with pytest.raises(ValueError):
        g.chunked([1], 0)


def test_run_chunked_splits_on_limit_guard_and_timeout(tmp_path):
    def server(sql):
        n = len(re.findall(r"'u\d+'", sql))
        if n > 2:
            return 500, "Code: 159. DB::Exception: Timeout exceeded: elapsed 60 seconds"
        rows = "".join(f"{u}\t1\n" for u in re.findall(r"'(u\d+)'", sql))
        return 200, "actor\tn\n" + rows

    ch = fake_ch(tmp_path, server)
    build = lambda ch_: "SELECT actor, n FROM t WHERE actor IN " + g.sql_in_list(ch_) + " FORMAT TSVWithNames"
    users = [f"u{i}" for i in range(7)]
    df = ch.run_chunked("t", build, users, ["actor", "n"], chunk_size=5, max_rows=10)
    assert sorted(df["actor"]) == users                      # nothing lost, nothing duplicated
    # LIMIT guard: more rows than max_rows -> split until each chunk fits
    df2 = ch.run_chunked("t2", build, ["u1", "u2"], ["actor", "n"], chunk_size=2, max_rows=1)
    assert sorted(df2["actor"]) == ["u1", "u2"]
    # non-splittable errors propagate
    bad = fake_ch(tmp_path, lambda s: (400, "Code: 62. DB::Exception: Syntax error"))
    with pytest.raises(g.ClickHouseError) as e:
        bad.run_chunked("t3", build, ["u1", "u2"], ["actor", "n"])
    assert e.value.code == "62"


def test_query_detects_exception_in_http_200_body(tmp_path):
    body = "actor\tn\nu1\t1\nCode: 396. DB::Exception: Limit for result exceeded, max rows: 1.00 million"
    ch = fake_ch(tmp_path, lambda s: (200, body))
    with pytest.raises(g.ClickHouseError) as e:
        ch.query("x", "SELECT 1", ["actor", "n"])
    assert e.value.code == "396" and e.value.splittable
    assert not (tmp_path / "raw").exists() or not list((tmp_path / "raw").glob("x_*"))   # nothing persisted


# ---------------------------------------------------------------- TSV parsing + typed frames
def test_parse_tsv_strict():
    df = g.parse_tsv(fx("team.tsv"), ["owner", "actor", "first_push_ts"])
    assert len(df) == 3 and list(df.columns) == ["owner", "actor", "first_push_ts"]
    assert len(g.parse_tsv("owner\tn\n", ["owner", "n"])) == 0
    with pytest.raises(g.GHArchiveError):
        g.parse_tsv(fx("team.tsv"), ["owner", "actor"])                       # header mismatch
    with pytest.raises(g.GHArchiveError):
        g.parse_tsv("owner\tn\nacme\t1\nglob", ["owner", "n"])                 # truncated row
    with pytest.raises(g.GHArchiveError):
        g.parse_tsv("", ["owner"])
    assert g.parse_tsv("a\tb\nx\\ty\tz\\\\w\n", ["a", "b"]).iloc[0].tolist() == ["x\ty", "z\\w"]


def test_typed_frames():
    span = g.frame_span(g.parse_tsv(fx("span.tsv"), ["owner", "first_event_ts", "last_event_ts", "n_events"]))
    assert str(span["first_event_ts"].dt.tz) == "UTC" and span["n_events"].dtype == "int64"
    assert span.loc[span.owner == "globex", "first_event_ts"].iloc[0] == pd.Timestamp("2015-12-31 23:59:59", tz="UTC")
    daily = g.frame_daily(g.parse_tsv(fx("daily.tsv"), ["owner", "day", "push", "pr", "issues", "release"]))
    assert daily["day"].iloc[0] == date(2014, 3, 2) and int(daily["push"].sum()) == 9
    ext = g.frame_external(g.parse_tsv(fx("ext.tsv"), ["actor", "month", "owner_bucket", "pr_opened",
                                                       "issues_opened", "pushes"]))
    assert all(d.day == 1 for d in ext["month"])
    bad = fx("ext.tsv").replace("2015-07-01", "2015-07-02")
    with pytest.raises(g.GHArchiveError):
        g.frame_external(g.parse_tsv(bad, ["actor", "month", "owner_bucket", "pr_opened", "issues_opened", "pushes"]))
    with pytest.raises(g.GHArchiveError):
        g.frame_daily(g.parse_tsv(fx("daily.tsv").replace("\t3\t", "\tx\t"),
                                  ["owner", "day", "push", "pr", "issues", "release"]))


# ---------------------------------------------------------------- hashing + month bucketing
def test_actor_hash():
    h = g.actor_hash("alice")
    assert h == hashlib.sha256(b"alice").hexdigest()[:16] and len(h) == 16
    assert g.actor_hash("Alice") == g.actor_hash(" alice ") == h
    assert g.actor_hash("bob-dev") != h
    team = g.frame_team(g.parse_tsv(fx("team.tsv"), ["owner", "actor", "first_push_ts"]))
    p = g.team_to_processed(team)
    assert list(p.columns) == g.TEAM_COLUMNS
    assert not set(p.astype(str).values.ravel()) & {"alice", "bob-dev"}


def test_month_bucketing():
    assert g.month_start("2015-03-31T23:59:59Z") == date(2015, 3, 1)
    assert g.month_start("2015-04-01T01:00:00+02:00") == date(2015, 3, 1)     # = 2015-03-31 23:00 UTC
    assert g.month_start(date(2016, 1, 15)) == date(2016, 1, 1)
    assert g.months_window("2016-01-15") == (date(2015, 1, 1), date(2016, 1, 1))
    assert g.months_window(datetime(2016, 3, 1, tzinfo=timezone.utc)) == (date(2015, 3, 1), date(2016, 3, 1))
    # case variants of one login collapse onto one hash and their counts add up
    ext = pd.DataFrame({"actor_login": ["alice", "alice"], "month": [date(2015, 1, 1)] * 2,
                        "owner_bucket": ["_ext", "_ext"], "pr_opened": [1, 2], "issues_opened": [0, 1],
                        "pushes": [3, 0]})
    out = g.external_to_processed(ext)
    assert len(out) == 1 and out[["pr_opened", "issues_opened", "pushes"]].iloc[0].tolist() == [3, 1, 3]
    assert list(out.columns) == g.EXT_COLUMNS


# ---------------------------------------------------------------- end-to-end with the fake server
def test_end_to_end_processed_outputs(tmp_path):
    ch = fake_ch(tmp_path)
    orgs = ["acme", "globex"]
    span = g.run_span(orgs, ch, out=tmp_path / "span.parquet")
    team = g.run_team(orgs, ch, out=tmp_path / "team.parquet")
    daily = g.run_activity(orgs, ch, out=tmp_path / "daily.parquet")
    ext = g.run_external(None, ch, team_path=tmp_path / "team.parquet", out=tmp_path / "ext.parquet")
    assert list(span.columns) == g.SPAN_COLUMNS and len(span) == 2
    assert list(team.columns) == g.TEAM_COLUMNS and len(team) == 3
    assert list(daily.columns) == g.DAILY_COLUMNS and len(daily) == 3
    assert list(ext.columns) == g.EXT_COLUMNS and len(ext) == 5
    # external resolved hashes -> logins from the raw team TSV listed in gh_team's meta and bucketed by team owners
    assert "'alice', 'bob-dev'" in ch.client.sqls[-1] and "if(owner IN ('acme', 'globex')" in ch.client.sqls[-1]
    # no raw logins in processed outputs
    for p in tmp_path.glob("*.parquet"):
        vals = set(pd.read_parquet(p).astype(str).values.ravel())
        assert not vals & {"alice", "bob-dev"}, p
        assert (p.with_name(p.name + ".meta.json")).exists()
    # raw SQL + TSV + meta persisted per query, gz deterministic
    raws = sorted((tmp_path / "raw").glob("*.tsv.gz"))
    assert {r.name.split("_")[0] for r in raws} == {"span", "team", "daily", "ext"}
    for r in raws:
        assert r.with_name(r.name.replace(".tsv.gz", ".sql")).exists()
        assert r.with_name(r.name + ".meta.json").exists()
        assert re.fullmatch(r"[a-z]+_[0-9a-f]{8}\.tsv\.gz", r.name)
    team_raw = [r for r in raws if r.name.startswith("team_")][0]
    assert gzip.decompress(team_raw.read_bytes()).decode() == fx("team.tsv")
    assert team_raw.read_bytes() == g._gz_bytes(fx("team.tsv"))
    # gh_team meta records the raw input used to resolve logins
    meta = json.loads((tmp_path / "team.parquet.meta.json").read_text())
    assert any(i["path"].endswith(team_raw.name) for i in meta["inputs"])


def test_external_refuses_unresolvable_team(tmp_path):
    ch = fake_ch(tmp_path)
    g.run_team(["acme"], ch, out=tmp_path / "team.parquet")
    t = pd.read_parquet(tmp_path / "team.parquet")
    t.loc[0, "actor_hash"] = "0" * 16
    t.to_parquet(tmp_path / "team.parquet", index=False)
    with pytest.raises(g.GHArchiveError):
        g.team_logins_from_raw(tmp_path / "team.parquet")


# ---------------------------------------------------------------- schema check
def test_schema_coverage_flags():
    df = g.schema_coverage_frame(g.parse_tsv(fx("schema.tsv"), g.SCHEMA_RAW_COLUMNS))
    f = df.set_index(["year", "event_type"])["flag"]
    assert f[(2012, "PullRequestEvent")] == "action_missing"
    assert f[(2016, "PullRequestEvent")] == ""
    assert set(f[(2012, "PushEvent")].split(";")) == {"repo_name_without_owner", "actor_missing"}
    assert f[(2016, "WatchEvent")] == ""                                     # not a feature type
    assert np.isnan(df.set_index(["year", "event_type"]).loc[(2012, "PushEvent"), "share_action_set"])
    assert df.set_index(["year", "event_type"]).loc[(2012, "PullRequestEvent"), "share_action_opened"] == 0.5
    with pytest.raises(ValueError):
        g.sql_schema_coverage("NotAnEvent")


# ---------------------------------------------------------------- fidelity helpers
def _write_hour(tmp_path, lines) -> Path:
    p = tmp_path / "h.json.gz"
    with gzip.open(p, "wt") as f:
        f.write("\n".join(lines) + "\n")
    return p


def test_count_hour_file_timeline_offset(tmp_path):
    # Timeline-era file labelled 15:00 US/Pacific: events carry -07:00 -> effective UTC hour 22:00 (offset +7)
    ev = lambda t, ts: json.dumps({"type": t, "created_at": ts, "repository": {"owner": "x", "name": "y"}})
    lines = [ev("PushEvent", "2013-05-08T15:00:01-07:00"), ev("PushEvent", "2013-05-08T15:59:59-07:00"),
             ev("IssuesEvent", "2013-05-08T15:30:00-07:00") + ev("WatchEvent", "2013-05-08T15:31:00-07:00"),
             ev("PushEvent", "2013-05-08T16:00:00-07:00"),            # spills into the next hour
             '{"type": "ForkEvent", "created_at": "2013-05-08T15:10:00"}',   # naive ts -> not guessed
             '{"broken": ']
    rc = g.count_hour_file(_write_hour(tmp_path, lines), "2013-05-08-15")
    assert rc["offset_h"] == 7 and rc["window_start"] == datetime(2013, 5, 8, 22, tzinfo=timezone.utc)
    assert rc["all"] == {"PushEvent": 3, "IssuesEvent": 1, "WatchEvent": 1, "ForkEvent": 1}
    assert rc["in_hour"] == {"PushEvent": 2, "IssuesEvent": 1, "WatchEvent": 1}
    assert rc["stats"]["parse_errors"] == 1 and rc["stats"]["ts_unparsed"] == 1
    assert rc["unique_ids"] == {}                                          # Timeline events have no id
    fr = g.fidelity_frame("2013-05-08-15", rc, {"PushEvent": 2, "IssuesEvent": 1, "WatchEvent": 1},
                          {"PushEvent": 3, "IssuesEvent": 1, "WatchEvent": 1, "ForkEvent": 1})
    tot = fr.set_index("event_type").loc["_total"]
    assert tot["raw_file_events"] == 6 and tot["ch_file_time"] == 6 and tot["rel_diff_file"] == 0
    assert tot["rel_diff_created"] == 0 and np.isnan(tot["raw_unique_ids"])
    assert list(fr.columns) == g.FIDELITY_COLUMNS


def test_count_hour_file_modern_ids(tmp_path):
    ev = lambda i, t: json.dumps({"id": str(i), "type": t, "created_at": "2021-05-12T15:00:0%dZ" % (i % 10)})
    rc = g.count_hour_file(_write_hour(tmp_path, [ev(1, "PushEvent"), ev(2, "PushEvent"), ev(2, "PushEvent")]),
                           "2021-05-12-15")
    assert rc["offset_h"] == 0 and rc["all"] == {"PushEvent": 3} and rc["unique_ids"] == {"PushEvent": 2}
    fr = g.fidelity_frame("2021-05-12-15", rc, {"PushEvent": 2}, {"PushEvent": 2}, {"PushEvent": 2})
    row = fr.set_index("event_type").loc["PushEvent"]
    assert row["rel_diff_file"] == pytest.approx(-1 / 3) and row["rel_diff_file_unique"] == 0


def test_fidelity_sql_and_helpers():
    s = g.sql_fidelity_hour("2013-05-08-15", "created_same_file", offset_h=7)
    assert "file_time = toDateTime('2013-05-08 15:00:00', 'UTC')" in s
    assert "created_at >= toDateTime('2013-05-08 22:00:00', 'UTC')" in s
    assert "created_at < toDateTime('2013-05-08 23:00:00', 'UTC')" in s
    assert "file_time" not in g.sql_fidelity_hour("2016-05-11-15", "created_at")
    with pytest.raises(ValueError):
        g.parse_hour("2016-05-11-24")
    with pytest.raises(ValueError):
        g.parse_hour("2016-05-11T15")
    assert g.parse_event_ts("2016-05-11T15:00:00Z") == datetime(2016, 5, 11, 15, tzinfo=timezone.utc)
    assert g.parse_event_ts("2013-05-08T15:00:01-07:00") == datetime(2013, 5, 8, 22, 0, 1, tzinfo=timezone.utc)
    assert g.parse_event_ts("2013-05-08T15:00:01") is None and g.parse_event_ts(None) is None
    assert g.rel_diff(99, 100) == pytest.approx(-0.01)
    assert np.isnan(g.rel_diff(0, 0)) and g.rel_diff(1, 0) == float("inf")
    objs = list(g.iter_json_objects(['{"a": 1}{"b": 2}', "", '{"c": 3}, {"d": 4}', "[1]"]))
    assert [o for o, ok in objs if ok] == [{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}]


# ---------------------------------------------------------------- BigQuery alternative backend
def test_bigquery_guard_refuses_over_limit():
    calls = []
    bq = g.BigQueryBackend(max_gb=200, dry_run_fn=lambda sql: 250 * 10**9,
                           run_fn=lambda sql: calls.append(sql) or pd.DataFrame())
    with pytest.raises(g.BigQueryBudgetError):
        bq.org_activity_span(["acme"])
    assert calls == []                                                        # never executed
    ok = g.BigQueryBackend(max_gb=200, dry_run_fn=lambda sql: 150 * 10**9,
                           run_fn=lambda sql: calls.append(sql) or pd.DataFrame({"x": [1]}))
    assert len(ok.team_first_push(["acme"])) == 1 and len(calls) == 1
    with pytest.raises(g.BigQueryBudgetError):
        g.bq_guard(200 * 10**9 + 1, 200)
    assert g.bq_guard(200 * 10**9, 200) == 200 * 10**9
    with pytest.raises(g.BigQueryBudgetError):
        g.bq_guard(None, 200)
    # default limit comes from config
    with pytest.raises(g.BigQueryBudgetError):
        g.bq_guard(int((g.cfg()["gharchive"]["bigquery_max_gb"] + 1) * 1e9))


def test_bigquery_sql_and_lazy_import(capsys):
    for kind in ("span", "team", "daily"):
        s = g.bq_sql(kind, ["acme"], end="201912")
        assert "`githubarchive.month.*`" in s and "_TABLE_SUFFIX BETWEEN '201102' AND '201912'" in s
        assert "SELECT *" not in s and "payload" not in s                    # only needed columns
        assert "NOT REGEXP_CONTAINS(LOWER(actor.login)" in s
    x = g.bq_sql("external", ["acme"], ["alice"], end="201912")
    assert "JSON_EXTRACT_SCALAR(payload, '$.action') = 'opened'" in x and "'_ext'" in x
    with pytest.raises(ValueError):
        g.bq_sql("external", ["acme"], None)
    with pytest.raises(ValueError):
        g.bq_sql("span", ["acme"], end="2019-12")
    bq = g.BigQueryBackend(dry_run_fn=lambda sql: 12_345_678_901)
    assert bq.dry_run("SELECT 1") == 12_345_678_901
    assert "totalBytesProcessed=12345678901" in capsys.readouterr().out


def test_module_importable_without_bigquery_and_fallback(monkeypatch):
    import subprocess

    root = Path(__file__).resolve().parents[1]
    code = ("import sys; sys.modules['google'] = None; sys.modules['google.cloud'] = None; "
            "import scripts.sources.gharchive as g; print(g.BigQueryBackend.__name__)")
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-500:]
    # library missing -> bq CLI fallback; CLI missing too -> clear error (never a silent run)
    monkeypatch.setitem(sys.modules, "google.cloud", None)
    monkeypatch.setattr(g.shutil, "which", lambda name: None)
    with pytest.raises(g.GHArchiveError):
        g._bq_dry_run_default("SELECT 1")


# ---------------------------------------------------------------- review fixes
def test_error_messages_never_carry_data_rows(tmp_path):
    body = "actor\tn\nalice\t1\nbob-dev\t2\nCode: 396. DB::Exception: Limit for result exceeded, max rows: 1.00 million"
    assert g.exception_text(body).startswith("Code: 396. DB::Exception")
    ch = fake_ch(tmp_path, lambda s: (200, body))
    with pytest.raises(g.ClickHouseError) as e:
        ch.query("x", "SELECT 1", ["actor", "n"])
    assert "alice" not in str(e.value) and "bob-dev" not in str(e.value) and "Code: 396" in str(e.value)
    with pytest.raises(g.ClickHouseError) as e2:
        g.parse_tsv(body, ["actor", "n"])
    assert "alice" not in str(e2.value)


def test_limit_guard_result_is_not_recorded_as_input(tmp_path):
    def server(sql):
        return 200, "actor\tn\n" + "".join(f"{u}\t1\n" for u in re.findall(r"'(u\d+)'", sql))

    ch = fake_ch(tmp_path, server)
    build = lambda ch_: "SELECT actor, n FROM t WHERE actor IN " + g.sql_in_list(ch_) + " FORMAT TSVWithNames"
    df = ch.run_chunked("t", build, ["u1", "u2", "u3", "u4"], ["actor", "n"], chunk_size=4, max_rows=2)
    assert sorted(df["actor"]) == ["u1", "u2", "u3", "u4"]
    assert [q.rows for q in ch.log] == [2, 2]                 # only the halves that made it into the output


def test_external_refuses_team_without_raw_inputs(tmp_path):
    ch = fake_ch(tmp_path)
    g.run_team(["acme"], ch, out=tmp_path / "team.parquet")
    mp = tmp_path / "team.parquet.meta.json"
    m = json.loads(mp.read_text())
    m["inputs"] = []
    mp.write_text(json.dumps(m))
    with pytest.raises(g.GHArchiveError):
        g.team_logins_from_raw(tmp_path / "team.parquet")


class FakeDownload:
    """Stands in for Client.get(url, stream_to=...): writes a gz payload once, then reports a cache hit."""

    def __init__(self, payload: bytes):
        self.payload, self.calls = payload, 0

    def get(self, url, stream_to=None, **kw):
        if Path(stream_to).exists():
            return CachedResponse(url, 200, "", "", True)
        self.calls += 1
        Path(stream_to).write_bytes(self.payload)
        return CachedResponse(url, 200, "", "2026-01-01T00:00:00+00:00", False)


def test_fidelity_check_deletes_hourly_file_and_keeps_manifest(tmp_path):
    ev = [json.dumps({"id": str(i), "type": "PushEvent", "created_at": "2021-05-12T15:00:0%dZ" % i,
                      "payload": {"commits": [{"author": {"email": "x@example.invalid"}}]}}) for i in range(3)]
    payload = gzip.compress(("\n".join(ev) + "\n").encode(), mtime=0)
    dl = FakeDownload(payload)
    ch = fake_ch(tmp_path, lambda s: (200, "event_type\tn\nPushEvent\t3\n"))
    cache, man = tmp_path / "cache", tmp_path / "manifests"
    df = g.fidelity_check(["2021-05-12-15"], ch=ch, out_path=tmp_path / "fid.csv", client=dl, cache_dir=cache,
                          manifest_dir=man)
    tot = df.set_index("event_type").loc["_total"]
    assert tot["raw_file_events"] == 3 and tot["ch_file_time"] == 3 and tot["rel_diff_file"] == 0
    assert not (cache / "2021-05-12-15.json.gz").exists()                 # e-mails are not kept on disk
    m = json.loads((man / "2021-05-12-15.json.gz.manifest.json").read_text())
    assert m["sha256"] == hashlib.sha256(payload).hexdigest() and m["bytes"] == len(payload)
    assert m["retained"] is False and m["local_path"] is None and m["fetched_at"]
    assert m["url"] == "https://data.gharchive.org/2021-05-12-15.json.gz"
    assert (tmp_path / "fid.csv").exists() and (tmp_path / "fid.csv.meta.json").exists()
    # --keep-hourly keeps the file; the original fetched_at is preserved for identical bytes
    g.fidelity_check(["2021-05-12-15"], ch=ch, out_path=tmp_path / "fid.csv", client=dl, cache_dir=cache,
                     manifest_dir=man, keep_hourly=True)
    assert (cache / "2021-05-12-15.json.gz").exists()
    m2 = json.loads((man / "2021-05-12-15.json.gz.manifest.json").read_text())
    assert m2["retained"] is True and m2["fetched_at"] == m["fetched_at"] and m2["sha256"] == m["sha256"]
