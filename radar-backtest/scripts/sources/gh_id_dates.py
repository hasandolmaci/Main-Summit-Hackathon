"""GitHub account-id -> creation-date bounds from raw GH Archive hours (login-recycling guard).

GH Archive (and its ClickHouse mirror) identify orgs by login only. A login can be deleted and re-registered by a new
owner, so an org's "first GH Archive event" can predate the current account (seen: an org whose first event is 2014
while its GitHub id implies ~2021). Account ids are assigned in increasing order, so the largest id seen in an hour of
public events bounds which accounts existed by then.

For one early-morning hour per quarter (default; 2015-01 .. data_cutoff) we stream the raw hourly file, keep only
(max actor.id, max org.id, event count) and discard everything else in memory — no file is written, so no e-mail or
other personal field from the payloads ever touches disk. Pre-2015 Timeline files carry no account ids.

Output: data/processed/gh_id_date_map.csv  (hour_ts, max_id, n_events) + meta
Use:    creation_bounds(uuid) -> (lower, upper): the account was created in (lower, upper];
        lower = None when the id already existed at the first sampled hour (created before 2015).
"""
from __future__ import annotations

import argparse
import json
import zlib
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from ..lib.config import PROCESSED, cfg, get_logger
from ..lib.meta import write_meta

log = get_logger("gh_id_dates")
URL = "https://data.gharchive.org/{d:%Y-%m-%d}-{h}.json.gz"
MAP = PROCESSED / "gh_id_date_map.csv"


def scan_hour(session: requests.Session, ts: datetime, hour: int) -> dict | None:
    url = URL.format(d=ts, h=hour)
    for attempt in range(4):
        try:
            with session.get(url, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                max_id, n = 0, 0
                dec = zlib.decompressobj(16 + zlib.MAX_WBITS)       # gzip stream, decoded chunk by chunk
                buf = b""

                def consume(lines):
                    nonlocal max_id, n
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        n += 1
                        for k in ("actor", "org"):
                            v = ev.get(k)
                            if isinstance(v, dict) and isinstance(v.get("id"), int):
                                max_id = max(max_id, v["id"])

                for chunk in r.iter_content(1 << 20):
                    buf += dec.decompress(chunk)
                    *lines, buf = buf.split(b"\n")
                    consume(lines)
                buf += dec.flush()
                consume(buf.split(b"\n"))
                return {"hour_ts": ts.replace(hour=hour).isoformat(), "max_id": max_id, "n_events": n}
        except (requests.RequestException, OSError, EOFError) as e:
            log.warning("retry %d %s: %r", attempt + 1, url, e)
            time.sleep(2 ** attempt)
    return None


def build_map(start: str = "2015-01-01", end: str | None = None, hour: int = 4, freq: str = "QS") -> pd.DataFrame:
    end = end or cfg()["data_cutoff"]
    months = pd.date_range(start, end, freq=freq, tz="UTC")
    done = pd.read_csv(MAP) if MAP.exists() else pd.DataFrame(columns=["hour_ts", "max_id", "n_events"])
    have = set(done["hour_ts"])
    s = requests.Session()
    s.headers["User-Agent"] = cfg()["http"]["user_agent"]
    rows = done.to_dict("records")
    for m in months:
        key = m.to_pydatetime().replace(hour=hour).isoformat()
        if key in have:
            continue
        rec = scan_hour(s, m.to_pydatetime(), hour)
        if rec:
            rows.append(rec)
            log.info("id map %s max_id=%d n=%d", rec["hour_ts"], rec["max_id"], rec["n_events"])
            pd.DataFrame(rows).sort_values("hour_ts").to_csv(MAP, index=False)    # resumable
        time.sleep(cfg()["http"]["min_interval_s"].get("data.gharchive.org", 1.0))
    df = pd.DataFrame(rows).sort_values("hour_ts").reset_index(drop=True)
    df["max_id"] = df["max_id"].cummax()               # ids are monotone in time; guard against a sparse hour
    df.to_csv(MAP, index=False)
    write_meta(MAP, "scripts/sources/gh_id_dates.py", rows=len(df), sources=["data.gharchive.org"],
               params={"start": start, "end": end, "hour_utc": hour, "freq": freq},
               notes="Max actor/org id seen in one hourly GH Archive file per month; streamed, nothing else stored.")
    return df


def creation_bounds(uuids, id_map: pd.DataFrame) -> pd.DataFrame:
    """For each GitHub id: created in (lower, upper]. lower=NaT if created before the first sampled hour."""
    m = id_map.sort_values("hour_ts")
    ts = pd.to_datetime(m["hour_ts"], utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    mx = m["max_id"].to_numpy()
    u = np.asarray(pd.to_numeric(pd.Series(uuids), errors="coerce"), dtype=float)
    idx = np.searchsorted(mx, u, side="left")          # first hour whose max_id >= uuid
    lower = np.array([ts[i - 1] if 0 < i <= len(ts) else np.datetime64("NaT") for i in idx], dtype="datetime64[ns]")
    upper = np.array([ts[i] if i < len(ts) else np.datetime64("NaT") for i in idx], dtype="datetime64[ns]")
    lower[np.isnan(u)] = np.datetime64("NaT")
    upper[np.isnan(u)] = np.datetime64("NaT")
    return pd.DataFrame({"uuid": uuids, "created_after": pd.to_datetime(lower, utc=True),
                         "created_by": pd.to_datetime(upper, utc=True)})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--hour", type=int, default=4, help="UTC hour sampled each period (night = smaller files)")
    ap.add_argument("--freq", default="QS", help="pandas frequency of sampled hours (QS = quarterly, MS = monthly)")
    a = ap.parse_args(argv)
    df = build_map(a.start, a.end, a.hour, a.freq)
    print(df.tail(3).to_string(index=False), f"\n{len(df)} hours")


if __name__ == "__main__":
    main()
