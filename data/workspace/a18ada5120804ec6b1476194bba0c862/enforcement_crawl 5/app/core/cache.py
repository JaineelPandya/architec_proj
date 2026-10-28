"""
app/core/cache.py
==================
Thread-safe in-process TTL cache.

Uses only Python stdlib (hashlib, time, threading.Lock) — no external
dependencies.  Entries are evicted lazily on access once their TTL expires.

Usage
-----
    from app.core.cache import TTLCache

    _cache: TTLCache = TTLCache(default_ttl_seconds=600)

    key = TTLCache.make_key("nse_circulars", from_date.isoformat(), to_date.isoformat())

    cached = _cache.get(key)
    if cached is not None:
        return cached          # cache hit

    result = expensive_fetch()
    _cache.set(key, result, ttl_seconds=600)
    return result
"""

import hashlib
import logging
import time
import threading

logger = logging.getLogger(__name__)


class TTLCache:
    """
    In-process key/value cache with per-entry time-to-live.

    - Thread-safe: all reads and writes are protected by a single Lock.
    - Lazy eviction: expired entries are removed when they are next accessed
      via get(); no background sweep thread is started.
    - Keys are opaque strings; use make_key() to build them from typed parts.
    """

    def __init__(self, default_ttl_seconds: int = 600) -> None:
        """
        Parameters
        ----------
        default_ttl_seconds:
            Fallback TTL (in whole seconds) used by set() when ttl_seconds is
            not provided.  Defaults to 600 (10 minutes).
        """
        self._default_ttl = default_ttl_seconds
        self._store: dict[str, tuple[object, float]] = {}  # key -> (value, expires_at)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(*parts: str) -> str:
        """
        Build a cache key by joining *parts* with ':' and returning the
        SHA-256 hex digest.

        Examples
        --------
            TTLCache.make_key("nse_circulars", "2025-01-01", "2025-06-30")
            TTLCache.make_key("sat_orders", "sebi", "2025-01-01", "2025-06-30")
        """
        raw = ":".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> object | None:
        """
        Return the cached value for *key*, or ``None`` on a miss.

        An entry whose TTL has elapsed is treated as a miss and evicted
        inline before returning ``None``.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() >= expires_at:
                del self._store[key]
                logger.debug("TTLCache evicted expired key=%s", key[:16])
                return None
            return value

    def set(self, key: str, value: object, ttl_seconds: int | None = None) -> None:
        """
        Store *value* under *key* for *ttl_seconds* seconds.

        Falls back to ``default_ttl_seconds`` when *ttl_seconds* is ``None``.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = time.monotonic() + ttl
        with self._lock:
            self._store[key] = (value, expires_at)
        logger.debug("TTLCache set key=%s ttl=%ss", key[:16], ttl)
