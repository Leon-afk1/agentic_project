"""Polite HTTP layer: SQLite response cache, rate limit, bounded retries.

Every request to the portal goes through `CachedClient`, so the three rules
(cache, at most one request per second, identifiable User-Agent) are enforced
in one place and tested once.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from oabench import __version__

USER_AGENT = f"oabench/{__version__} (independent open-data research project; read-only)"
RETRY_DELAYS_S = (2.0, 5.0)


class PortalError(RuntimeError):
    """The portal answered with an error, or could not be reached."""


class RateLimiter:
    """Spaces calls by at least `min_interval` seconds.

    Clock and sleep are injected so tests run instantly and deterministically.
    """

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next_allowed = float("-inf")

    def wait(self) -> float:
        """Block until the next call is allowed. Returns the time slept."""
        now = self._clock()
        delay = max(0.0, self._next_allowed - now)
        if delay > 0:
            self._sleep(delay)
        self._next_allowed = max(now, self._next_allowed) + self._min_interval
        return delay


class ResponseCache:
    """SQLite cache of successful JSON responses, keyed by the full request."""

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._clock = clock
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses ("
            "key TEXT PRIMARY KEY, url TEXT NOT NULL, body TEXT NOT NULL, fetched_at REAL NOT NULL)"
        )

    @staticmethod
    def key(method: str, url: str, payload: Any) -> str:
        raw = json.dumps([method, url, payload], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str, ttl_s: float | None) -> str | None:
        row = self._db.execute(
            "SELECT body, fetched_at FROM responses WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        body, fetched_at = row
        if ttl_s is not None and self._clock() - fetched_at > ttl_s:
            return None
        return str(body)

    def put(self, key: str, url: str, body: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO responses (key, url, body, fetched_at) VALUES (?, ?, ?, ?)",
            (key, url, body, self._clock()),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


@dataclass
class ClientStats:
    network_calls: int = 0
    cache_hits: int = 0
    retries: int = 0


class CachedClient:
    """POST JSON, get JSON back. Cache first, then rate-limited network."""

    def __init__(
        self,
        cache: ResponseCache,
        limiter: RateLimiter,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 90.0,
        ttl_s: float | None = 24 * 3600,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cache = cache
        self._limiter = limiter
        self._ttl_s = ttl_s
        self._sleep = sleep
        self.stats = ClientStats()
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout_s,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def post_json(self, url: str, payload: dict[str, Any], *, refresh: bool = False) -> Any:
        key = ResponseCache.key("POST", url, payload)
        if not refresh:
            cached = self._cache.get(key, self._ttl_s)
            if cached is not None:
                self.stats.cache_hits += 1
                return json.loads(cached)
        body = self._fetch(url, payload)
        self._cache.put(key, url, body)
        return json.loads(body)

    def _fetch(self, url: str, payload: dict[str, Any]) -> str:
        attempts = len(RETRY_DELAYS_S) + 1
        last_error = ""
        for attempt in range(attempts):
            if attempt > 0:
                self.stats.retries += 1
                self._sleep(RETRY_DELAYS_S[attempt - 1])
            self._limiter.wait()
            self.stats.network_calls += 1
            try:
                response = self._http.post(url, json=payload)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code != 200:
                # Client errors are our fault: retrying would only add load.
                raise PortalError(f"HTTP {response.status_code} for {url}")
            return response.text
        raise PortalError(f"giving up on {url} after {attempts} attempts ({last_error})")

    def close(self) -> None:
        self._http.close()
        self._cache.close()
