"""Server-side AI usage guard.

Puter.js is still the only AI layer: this module never calls a provider, stores
a credential, or proxies a model request.  It keeps a small in-memory ledger so
the browser cannot silently issue unlimited AI requests.  The assistant UI must
ask for a reservation before a model call; when a session/hour/day threshold is
crossed the ledger returns a cooldown and the browser ends its Puter session.

Configuration is environment-driven (see ``configure_from_env``) and contains
no secrets.  The counters are intentionally process-local: AQACT is a
local-first single-user workspace, and no database or account system exists.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("aqat.quota")

DEFAULT_LIMITS = {
    # Requests allowed in one browser page session, UTC hour, and UTC day.
    "session": 40,
    "hour": 120,
    "day": 500,
    # Seconds the AI session stays locked out after a threshold sign-out.
    "cooldown_seconds": 900,
    # Kept for the existing token warning in the assistant panel.
    "token_warning": 10000,
}

_ENV_NAMES = {
    "session": "AQAT_AI_SESSION_LIMIT",
    "hour": "AQAT_AI_HOUR_LIMIT",
    "day": "AQAT_AI_DAY_LIMIT",
    "cooldown_seconds": "AQAT_AI_COOLDOWN_SECONDS",
    "token_warning": "AQAT_AI_TOKEN_WARNING",
}


class QuotaError(Exception):
    """A friendly, user-facing quota decision with an HTTP status and code."""

    def __init__(self, message: str, *, code: str, status: int = 429, payload=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.payload = payload or {}


@dataclass
class _LedgerEntry:
    authenticated: bool = False
    session_count: int = 0
    hour_key: str = ""
    hour_count: int = 0
    day_key: str = ""
    day_count: int = 0
    blocked_until: float = 0.0
    last_limit: str = ""

    def as_dict(self, now: float, config: dict) -> dict:
        retry = max(0.0, self.blocked_until - now)
        return {
            "authenticated": self.authenticated,
            "counts": {
                "session": self.session_count,
                "hour": self.hour_count,
                "day": self.day_count,
            },
            "limits": {
                "session": config["session"],
                "hour": config["hour"],
                "day": config["day"],
            },
            "remaining": {
                "session": max(0, config["session"] - self.session_count),
                "hour": max(0, config["hour"] - self.hour_count),
                "day": max(0, config["day"] - self.day_count),
            },
            "blocked": bool(retry > 0),
            "retry_after_seconds": int(retry + 0.999),
            "cooldown_seconds": config["cooldown_seconds"],
            "last_limit": self.last_limit,
        }


class _QuotaManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config = dict(DEFAULT_LIMITS)
        self._entries: dict[str, _LedgerEntry] = {}

    # ── Configuration ──────────────────────────────────────────────────────
    def configure(self, values: dict | None = None) -> dict:
        if values:
            for key in DEFAULT_LIMITS:
                if key in values and values[key] is not None:
                    self._config[key] = self._coerce(key, values[key])
        return self.config()

    def configure_from_env(self) -> dict:
        return self.configure(
            {key: os.environ.get(env) for key, env in _ENV_NAMES.items() if os.environ.get(env)}
        )

    @staticmethod
    def _coerce(key: str, value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return DEFAULT_LIMITS[key]
        minimum = 1 if key != "cooldown_seconds" else 0
        return max(minimum, number)

    def config(self) -> dict:
        with self._lock:
            return dict(self._config)

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def _entry(self, session_id: str) -> _LedgerEntry:
        sid = str(session_id or "anonymous")
        entry = self._entries.get(sid)
        if entry is None:
            entry = _LedgerEntry()
            self._entries[sid] = entry
        return entry

    def mark_authenticated(self, session_id: str) -> dict:
        """Record a successful browser sign-in and start a fresh session count.

        Hour/day counters and any active cooldown are deliberately preserved so
        re-signing-in cannot be used to bypass an hourly/daily limit.
        """
        now = time.time()
        with self._lock:
            entry = self._entry(session_id)
            entry.authenticated = True
            entry.session_count = 0
            if entry.blocked_until and entry.blocked_until <= now:
                entry.blocked_until = 0.0
                entry.last_limit = ""
            return entry.as_dict(now, self._config)

    def mark_signed_out(self, session_id: str) -> dict:
        """End AI access without erasing rolling counters or cooldown state."""
        now = time.time()
        with self._lock:
            entry = self._entry(session_id)
            entry.authenticated = False
            return entry.as_dict(now, self._config)

    def status(self, session_id: str) -> dict:
        now = time.time()
        with self._lock:
            entry = self._entry(session_id)
            if entry.blocked_until and entry.blocked_until <= now:
                entry.blocked_until = 0.0
                entry.last_limit = ""
            return entry.as_dict(now, self._config)

    def consume(self, session_id: str, *, model: str = "", task: str = "") -> dict:
        """Reserve one AI request, or return a blocked decision.

        Raises ``QuotaError`` with code ``ai_auth_required`` when the browser has
        not completed Puter sign-in for this AQACT session.
        """
        now = time.time()
        config = self.config()
        hour_key = time.strftime("%Y-%m-%dT%H", time.gmtime(now))
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
        sid = str(session_id or "anonymous")
        safe_sid = sid[:8] if sid else "anonymous"

        with self._lock:
            entry = self._entry(sid)
            if not entry.authenticated:
                raise QuotaError(
                    "Sign in to Puter before using AI features.",
                    code="ai_auth_required",
                    status=401,
                )

            if entry.hour_key != hour_key:
                entry.hour_key = hour_key
                entry.hour_count = 0
            if entry.day_key != day_key:
                entry.day_key = day_key
                entry.day_count = 0

            if entry.blocked_until > now:
                retry = int(entry.blocked_until - now + 0.999)
                raise QuotaError(
                    (
                        f"AI access is paused for another {retry} seconds after the "
                        f"{entry.last_limit or 'usage'} limit was reached. Sign-in is available, "
                        "but model requests stay blocked until the cooldown ends."
                    ),
                    code="ai_quota_cooldown",
                    status=429,
                    payload=entry.as_dict(now, config),
                )

            entry.session_count += 1
            entry.hour_count += 1
            entry.day_count += 1

            exceeded = None
            if entry.session_count > config["session"]:
                exceeded = "session"
            elif entry.hour_count > config["hour"]:
                exceeded = "hour"
            elif entry.day_count > config["day"]:
                exceeded = "day"

            if exceeded:
                entry.blocked_until = now + config["cooldown_seconds"]
                entry.last_limit = exceeded
                entry.authenticated = False
                retry = config["cooldown_seconds"]
                log.warning(
                    "AI quota sign-out: limit=%s sid=%s model=%s task=%s counts=%s cooldown=%ss",
                    exceeded,
                    safe_sid,
                    str(model or "auto")[:32],
                    str(task or "chat")[:32],
                    entry.as_dict(now, config)["counts"],
                    retry,
                )
                raise QuotaError(
                    (
                        f"The AI {exceeded} request limit was reached. Your AI session has been "
                        f"signed out and paused for {retry} seconds. You can sign in again, but "
                        "AI requests stay paused until the cooldown ends. Local analysis is still available."
                    ),
                    code="ai_quota_exceeded",
                    status=429,
                    payload=entry.as_dict(now, config),
                )

            return entry.as_dict(now, config)

    def clear(self, session_id: str | None = None) -> None:
        """Testing helper: remove all ledger state (or one session's state)."""
        with self._lock:
            if session_id is None:
                self._entries.clear()
            else:
                self._entries.pop(str(session_id), None)


MANAGER = _QuotaManager()

# Backwards-friendly module-level aliases used by routes and tests.
configure = MANAGER.configure
configure_from_env = MANAGER.configure_from_env
config = MANAGER.config
status = MANAGER.status
consume = MANAGER.consume
mark_authenticated = MANAGER.mark_authenticated
mark_signed_out = MANAGER.mark_signed_out
clear = MANAGER.clear
