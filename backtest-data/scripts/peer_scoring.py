"""Sector/age peer percentiles for already computed startup merit scores.

Callers must apply eligibility gates before fitting/scoring. This module neither
computes merit nor reads model weights, so weights can change independently.
Missing raw scores count as zero. Percentiles use the empirical midrank:
100 * (number below + 0.5 * number equal) / reference size.
"""

from __future__ import annotations

import csv
import json
import math
import re
from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from pathlib import Path


SECTORS = frozenset({"deep_tech", "digital_tech", "life_science", "non_tech", "unclear"})
BANDS = ("<=18m", "19-36m", ">36m_or_unknown")
KINDS = ("quality", "opportunity")
MIN_PEERS = 10


def normalise_name(name: str) -> str:
    """Lowercase, remove legal-form tokens, then remove non-alphanumerics.

    Legal forms are removed as whole tokens, preserving names such as 'Agile'.
    Unicode letters/digits are retained; underscores are removed.
    """
    text = re.sub(r"\b(?:gmbh|ug|ag)\b", "", str(name or "").lower())
    return "".join(char for char in text if char.isalnum())


def _sector(sector) -> str:
    value = str(sector or "").strip().lower()
    return value if value in SECTORS else "unclear"


def sector_lookup(path="raw/relevance_v2.csv") -> dict[str, str]:
    """Read name -> sector; conflicting normalized-name categories are unclear.

    Missing files and missing required columns raise errors instead of silently
    making every company unclear. Empty company names are ignored.
    """
    lookup = {}
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not {"name", "category"}.issubset(reader.fieldnames or []):
            raise ValueError("Sector CSV requires name and category columns")
        for row in reader:
            name = normalise_name(row["name"])
            if not name:
                continue
            category = _sector(row["category"])
            if name in lookup and lookup[name] != category:
                category = "unclear"
            lookup[name] = category
    return lookup


def age_band(f_age_months) -> str:
    """Use [0,18], (18,36], and older/unknown (including invalid ages)."""
    try:
        age = float(f_age_months)
    except (ValueError, TypeError, OverflowError):
        return BANDS[2]
    if not math.isfinite(age) or age < 0:
        return BANDS[2]
    return BANDS[0] if age <= 18 else BANDS[1] if age <= 36 else BANDS[2]


def peer_group(sector, band) -> str:
    """Return a stable, JSON-safe sector|age-band group key."""
    return f"{_sector(sector)}|{band if band in BANDS else BANDS[2]}"


def _raw_value(value) -> float:
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _empty_scores() -> dict:
    return {kind: [] for kind in KINDS}


def fit_reference(raw_scores: list[dict]) -> dict:
    """Store sorted distributions at peer-group, sector, and global levels.

    Rows contain name, sector, band, quality_raw, opportunity_raw. Each row is
    one member (names are informational; repeated observations are not deduped).
    Missing/invalid sectors become unclear and missing/invalid bands become
    >36m_or_unknown. The returned object is directly JSON serializable.
    """
    ref = {"version": 1, "peer_groups": {}, "sectors": {}, "global": _empty_scores()}
    for row in raw_scores:
        sector = _sector(row.get("sector"))
        group = peer_group(sector, row.get("band"))
        peers = ref["peer_groups"].setdefault(group, _empty_scores())
        sectors = ref["sectors"].setdefault(sector, _empty_scores())
        for kind in KINDS:
            value = _raw_value(row.get(f"{kind}_raw"))
            for bucket in (peers, sectors, ref["global"]):
                bucket[kind].append(value)
    for bucket in [*ref["peer_groups"].values(), *ref["sectors"].values(), ref["global"]]:
        for values in bucket.values():
            values.sort()
    return ref


def percentile(ref, value, sector, band, kind) -> tuple[float, str]:
    """Return (0..100 percentile, 'peer_group' | 'sector' | 'global').

    kind accepts quality/opportunity, or their *_raw field names. Use a peer
    group with >=10 members, then a sector with >=10, then global regardless
    of size. An empty global reference returns (0.0, 'global').
    """
    kind = str(kind).removesuffix("_raw")
    if kind not in KINDS:
        raise ValueError(f"Unknown score kind: {kind!r}")
    sector = _sector(sector)
    candidates = (
        ("peer_group", ref["peer_groups"].get(peer_group(sector, band), {}).get(kind, [])),
        ("sector", ref["sectors"].get(sector, {}).get(kind, [])),
        ("global", ref["global"][kind]),
    )
    for level, values in candidates:
        if level != "global" and len(values) < MIN_PEERS:
            continue
        if not values:
            return 0.0, level
        score = _raw_value(value)
        lower = bisect_left(values, score)
        upper = bisect_right(values, score)
        return 100.0 * (lower + upper) / (2 * len(values)), level
    raise AssertionError("Global fallback must always be available")


def _weight_mass(weights) -> float:
    """Accept a scalar mass, iterable of weights, or feature-name mapping."""
    if isinstance(weights, Mapping):
        weights = weights.values()
    elif isinstance(weights, (str, bytes)) or not hasattr(weights, "__iter__"):
        weights = [weights]
    values = [float(weight) for weight in weights]
    if any(not math.isfinite(weight) for weight in values):
        raise ValueError("Coverage weights must be finite")
    return math.fsum(abs(weight) for weight in values)


def coverage(observed_weights, total_weights) -> float:
    """Observed absolute weight mass / total absolute weight mass, as 0..100.

    Accept sums, weight iterables, or mappings keyed by feature name. Callers
    select observed features from actual presence, including observed zeros.
    Zero total mass gives zero coverage; excess observed mass is capped at 100.
    """
    observed = _weight_mass(observed_weights)
    total = _weight_mass(total_weights)
    return min(100.0, 100.0 * observed / total) if total else 0.0


def save_reference(ref, path="models/peer_reference.json") -> None:
    """Explicitly save a reference to a path or writable text stream.

    No file is written by fitting, importing, or running the self-test. Parent
    directories must already exist; the integrator controls persistence.
    """
    if hasattr(path, "write"):
        json.dump(ref, path, indent=2, allow_nan=False)
        return
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(ref, stream, indent=2, allow_nan=False)
        stream.write("\n")


def load_reference(path="models/peer_reference.json") -> dict:
    """Load a reference from a path or readable text stream; check its schema."""
    if hasattr(path, "read"):
        ref = json.load(path)
    else:
        with Path(path).open(encoding="utf-8") as stream:
            ref = json.load(stream)
    if ref.get("version") != 1:
        raise ValueError("Unsupported peer reference version")
    buckets = [ref["global"], *ref["peer_groups"].values(), *ref["sectors"].values()]
    for bucket in buckets:
        for kind in KINDS:
            values = bucket[kind]
            if (not isinstance(values, list)
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
                    or values != sorted(values)):
                raise ValueError("Reference distributions must be sorted finite numbers")
        if len(bucket["quality"]) != len(bucket["opportunity"]):
            raise ValueError("Score distributions must have equal member counts")
    return ref


def _self_test() -> None:
    from io import StringIO

    assert normalise_name("ACME GmbH / UG AG") == "acme"
    assert normalise_name("Agile GmbH") == "agile"
    for age, expected in [(0, BANDS[0]), (18, BANDS[0]), (18.5, BANDS[1]),
                          (36, BANDS[1]), (37, BANDS[2]), (None, BANDS[2]),
                          ("", BANDS[2]), (-1, BANDS[2]), (float("nan"), BANDS[2])]:
        assert age_band(age) == expected
    rows = [dict(name=f"A{i}", sector="deep_tech", band=BANDS[0],
                 quality_raw=i // 2, opportunity_raw=9 - i) for i in range(10)]
    rows += [dict(name=f"B{i}", sector="digital_tech", band=BANDS[i % 2],
                  quality_raw=i, opportunity_raw=i) for i in range(10)]
    rows += [dict(name="C", sector="life_science", band=BANDS[0],
                  quality_raw=None, opportunity_raw=0)]
    ref = fit_reference(rows)
    assert percentile(ref, 2, "deep_tech", BANDS[0], "quality") == (50.0, "peer_group")
    assert percentile(ref, 4.5, "digital_tech", BANDS[0], "quality") == (50.0, "sector")
    assert percentile(ref, 3, "life_science", BANDS[0], "quality")[1] == "global"
    assert percentile(ref, -1, "deep_tech", BANDS[0], "quality")[0] == 0
    assert percentile(ref, 100, "deep_tech", BANDS[0], "quality")[0] == 100
    assert percentile(ref, 4.5, "deep_tech", BANDS[0], "opportunity_raw") == (50, "peer_group")
    assert percentile(fit_reference([]), 0, None, None, "quality") == (0, "global")
    assert percentile(fit_reference([rows[-1]]), 0, "life_science", BANDS[0], "quality") == (50, "global")
    assert percentile(fit_reference(rows[:9]), 0, "deep_tech", BANDS[0], "quality")[1] == "global"
    assert coverage(7, 10) == 70
    assert coverage({"feature_a": -3}, {"feature_a": -3, "feature_b": 1}) == 75
    assert coverage([], []) == 0
    assert coverage([2], [1]) == 100
    stream = StringIO()
    save_reference(ref, stream)
    stream.seek(0)
    assert load_reference(stream) == ref
    assert ref["global"]["quality"] == sorted(ref["global"]["quality"])
    print("peer_scoring self-test passed (no files written)")


if __name__ == "__main__":
    _self_test()
