import numpy as np
import pandas as pd

from scripts.sources import pool_sample as ps


def test_choose_m():
    assert ps.choose_m(np.array([0, 0, 1, 3, 3, 3]), target=3, pilot_m=10) == (2, True)
    assert ps.choose_m(np.array([0, 5]), target=5, pilot_m=10) == (10, False)


def test_incremental_scan_is_exact_per_year_and_stops_early():
    rows = []
    for y in (2014, 2015):
        for b in range(10):
            for k in range(4):
                rows.append({"login": f"o{y}-{b}-{k}", "hash_bucket": b, "first_year": y,
                             "first_event_ts": pd.Timestamp(f"{y}-02-01", tz="UTC")})
    listing = pd.DataFrame(rows)
    calls = []

    def fake_fya(part, ch, chunk=0):
        calls.append(len(part))
        ok = part["login"].str.endswith("-0") | (part["first_year"] == 2015) & part["login"].str.endswith("-1")
        return pd.DataFrame({"login": part["login"], "n_act_12m": np.where(ok, 50, 0), "n_pushers_12m": np.where(ok, 3, 0)})

    scanned, m = ps.incremental_entry_scan(listing, None, target=3, pilot_m=10, min_act=10, min_pushers=2,
                                           chunk=8, fya=fake_fya)
    # 2015 passes 2 owners per bucket -> needs buckets 0,1 -> m=2; 2014 passes 1 per bucket -> m=3
    assert m[2015] == (2, True) and m[2014] == (3, True)
    for y, (mm, _) in m.items():                         # every owner below m_Y was scanned
        need = set(listing.loc[(listing.first_year == y) & (listing.hash_bucket < mm), "login"])
        assert need <= set(scanned["login"])
    assert len(scanned) < len(listing)                   # stopped early
