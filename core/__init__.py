"""Shared platform infrastructure for AQAT.

The modules in this package deliberately contain no Flask route definitions.
They provide small, testable building blocks used by the API layer:

* :mod:`core.cache`          — bounded in-memory TTL cache
* :mod:`core.errors`         — consistent, user-facing API errors
* :mod:`core.sessions`       — thread-safe session store with history
* :mod:`core.serialization`  — JSON-safe dataframe payloads & profiling
* :mod:`core.dataio`         — robust CSV / Excel ingestion
"""

from core.cache import CACHE, TTLCache, cache_key  # noqa: F401
from core.errors import ApiError  # noqa: F401
from core.sessions import STORE, SessionStore  # noqa: F401
