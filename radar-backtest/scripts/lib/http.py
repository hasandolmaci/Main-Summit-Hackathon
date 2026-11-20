"""Cached, polite, resumable HTTP client.

- Every response is cached under data/cache/<host>/ keyed by sha256(method, url, params, body).
  Re-running a collector replays from cache, so collection is resumable.
- Exponential backoff on 429 / 5xx / connection errors; per-host minimum interval.
- Tokens come from environment variables only; nothing secret is written to disk.
- Every network call is logged to logs/collection.log.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import CACHE, cfg, get_logger

log = get_logger("http")


@dataclass
class CachedResponse:
    url: str
    status: int
    text: str
    fetched_at: str
    from_cache: bool

    def json(self):
        return json.loads(self.text)


def _key(method: str, url: str, params: dict | None, body: str | bytes | None) -> str:
    h = hashlib.sha256()
    h.update(method.upper().encode())
    h.update(url.encode())
    h.update(json.dumps(params or {}, sort_keys=True).encode())
    if body is not None:
        h.update(body if isinstance(body, bytes) else body.encode())
    return h.hexdigest()


class Client:
    def __init__(self, cache_dir: Path = CACHE, user_agent: str | None = None, headers: dict | None = None):
        c = cfg()["http"]
        self.cache_dir = Path(cache_dir)
        self.timeout = c["timeout_s"]
        self.max_retries = c["max_retries"]
        self.intervals = c["min_interval_s"]
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or c["user_agent"]
        if headers:
            self.session.headers.update(headers)
        self._last: dict[str, float] = {}

    def _cache_path(self, host: str, key: str) -> Path:
        return self.cache_dir / host / key[:2] / f"{key}.json"

    def _wait(self, host: str):
        gap = self.intervals.get(host, self.intervals["default"])
        dt = time.monotonic() - self._last.get(host, 0.0)
        if dt < gap:
            time.sleep(gap - dt)
        self._last[host] = time.monotonic()

    def request(self, method: str, url: str, params: dict | None = None, data: str | bytes | None = None,
                json_body: dict | None = None, headers: dict | None = None, use_cache: bool = True,
                cache_statuses=(200, 404), stream_to: Path | None = None) -> CachedResponse:
        body = json.dumps(json_body, sort_keys=True) if json_body is not None else data
        host = urlparse(url).netloc
        key = _key(method, url, params, body)
        path = self._cache_path(host, key)
        if use_cache and path.exists() and stream_to is None:
            rec = json.loads(path.read_text())
            return CachedResponse(rec["url"], rec["status"], rec["text"], rec["fetched_at"], True)
        if stream_to is not None and use_cache and Path(stream_to).exists():
            return CachedResponse(url, 200, "", "", True)

        attempt = 0
        while True:
            self._wait(host)
            try:
                r = self.session.request(method, url, params=params, data=body, headers=headers,
                                         timeout=self.timeout, stream=stream_to is not None)
                status = r.status_code
            except requests.RequestException as e:  # connection reset, timeout ...
                status, r = -1, None
                err = repr(e)
            rate_limited_403 = status == 403 and r is not None and "rate limit" in r.text.lower()
            retryable = status == -1 or status == 429 or status >= 500 or rate_limited_403
            if not retryable:
                break
            attempt += 1
            if attempt > self.max_retries:
                log.error("giving up %s %s status=%s", method, url, status)
                break
            retry_after = None
            if r is not None:
                retry_after = r.headers.get("Retry-After")
                reset = r.headers.get("X-RateLimit-Reset")
                if status in (403, 429) and reset and r.headers.get("X-RateLimit-Remaining") == "0":
                    retry_after = max(1, int(reset) - int(time.time()) + 2)
            sleep = float(retry_after) if retry_after else min(300, 2 ** attempt + random.random())
            log.warning("retry %d %s %s status=%s sleep=%.1fs %s", attempt, method, url, status, sleep,
                        "" if r is not None else err)
            time.sleep(sleep)

        fetched_at = datetime.now(timezone.utc).isoformat()
        if stream_to is not None and r is not None and status == 200:
            tmp = Path(str(stream_to) + ".part")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.rename(stream_to)
            log.info("%s %s -> %s (%d bytes)", method, url, stream_to, Path(stream_to).stat().st_size)
            return CachedResponse(url, status, "", fetched_at, False)
        text = r.text if r is not None else ""
        log.info("%s %s params=%s status=%s bytes=%d", method, url, params, status, len(text))
        if use_cache and status in cache_statuses:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "params": params, "status": status,
                                        "fetched_at": fetched_at, "text": text}))
        return CachedResponse(url, status, text, fetched_at, False)

    def get(self, url, **kw) -> CachedResponse:
        return self.request("GET", url, **kw)

    def post(self, url, **kw) -> CachedResponse:
        return self.request("POST", url, **kw)


def env_tokens(*names: str) -> list[str]:
    """Collect tokens from env vars (NAME, NAME_2, NAME_3 ...). Values are never logged."""
    out = []
    for n in names:
        for suffix in ["", "_2", "_3", "_4"]:
            v = os.environ.get(n + suffix)
            if v and v not in out:
                out.append(v)
    return out
