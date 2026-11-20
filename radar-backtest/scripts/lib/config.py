"""Project configuration and paths. Every script imports from here."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
LABELS = DATA / "labels"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
CACHE = DATA / "cache"
RESULTS = ROOT / "results"
FIGURES = RESULTS / "figures"
LOGS = ROOT / "logs"
PAPERS = ROOT / "papers"


@lru_cache(maxsize=1)
def cfg() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def seed() -> int:
    return int(cfg()["seed"])


def utc(d) -> datetime:
    """Coerce a date/datetime/ISO string to a tz-aware UTC datetime."""
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    if isinstance(d, date) and not isinstance(d, datetime):
        d = datetime(d.year, d.month, d.day)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def as_of_date(year: int) -> datetime:
    m, d = cfg()["years"]["as_of_month_day"]
    return datetime(year, m, d, tzinfo=timezone.utc)


def data_cutoff() -> datetime:
    return utc(cfg()["data_cutoff"])


def selection_years() -> list[int]:
    a, b = cfg()["years"]["selection"]
    return list(range(a, b + 1))


def locked_years() -> list[int]:
    a, b = cfg()["years"]["locked_test"]
    return list(range(a, b + 1))


class LockedYearError(RuntimeError):
    pass


def guard_years(years, allow_locked: bool = False) -> list[int]:
    """Refuse to score locked test years unless explicitly unlocked by the user.

    Unlock only with allow_locked=True AND env RADAR_UNLOCK_TEST=1 (set when the user says so).
    """
    years = [int(y) for y in years]
    locked = set(locked_years()) & set(years)
    if locked and not (allow_locked and os.environ.get("RADAR_UNLOCK_TEST") == "1"):
        raise LockedYearError(f"Locked test years {sorted(locked)} may not be evaluated yet.")
    return years


def get_logger(name: str) -> logging.Logger:
    LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fh = logging.FileHandler(LOGS / "collection.log")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger
