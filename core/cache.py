"""A tiny dependency-free TTL + LRU cache.

The application performs a handful of comparatively expensive operations
(dataset profiling, insight generation, descriptive statistics).  Results only
change when a session is mutated, so a bounded cache keyed by
``(session_id, version, scope)`` gives a large speed-up without serving stale
data.

The cache is process-local and intentionally simple.  It never stores uploaded
data in a shared location and never writes to disk.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Optional


def cache_key(*parts: Any) -> str:
    """Build a stable cache key from arbitrary JSON-serialisable parts."""
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class TTLCache:
    """Thread-safe, size-bounded cache with per-entry expiry."""

    def __init__(self, maxsize: int = 256, ttl: float = 300.0) -> None:
        self.maxsize = max(1, int(maxsize))
        self.default_ttl = float(ttl)
        self._data: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if expires_at < now:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        expires_at = time.monotonic() + (self.default_ttl if ttl is None else ttl)
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def get_or_set(self, key: str, factory, ttl: Optional[float] = None) -> Any:
        """Return a cached value or call ``factory`` once and store it."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = factory()
        self.set(key, value, ttl=ttl)
        return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._data),
                "maxsize": self.maxsize,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
            }


# Shared application cache.  Session mutations bump the session version, which
# automatically makes old keys unreachable.
CACHE = TTLCache(maxsize=384, ttl=600)
