"""Thread-safe in-memory session store.

AQAT is a single-user, local-first desktop-style application, so dataframes are
kept in process memory rather than a database.  This module centralises that
state so routes do not mutate shared dictionaries directly.

Key properties
--------------
* Every mutation bumps a monotonic ``version``.  Caches keyed by version become
  unreachable automatically.
* A small undo history is kept for destructive dataframe operations.
* Sessions expire after a configurable TTL and the store is size-bounded.
"""

from __future__ import annotations

import copy
import secrets
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

import pandas as pd


def _new_id() -> str:
    return secrets.token_hex(8)


class SessionStore:
    def __init__(self, *, ttl_seconds: int = 12 * 3600, max_sessions: int = 32,
                 max_batches: int = 16, history_depth: int = 8) -> None:
        self.ttl_seconds = int(ttl_seconds)
        self.max_sessions = max(1, int(max_sessions))
        self.max_batches = max(1, int(max_batches))
        self.history_depth = max(0, int(history_depth))
        self._sessions: Dict[str, dict] = {}
        self._batches: Dict[str, dict] = {}
        self._lock = threading.RLock()

    # ── internal helpers ────────────────────────────────────────────────────
    def _prune_locked(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._sessions.items()
                   if now - s.get("updated_at", now) > self.ttl_seconds]
        for sid in expired:
            self._sessions.pop(sid, None)
        expired_b = [bid for bid, b in self._batches.items()
                     if now - b.get("updated_at", now) > self.ttl_seconds]
        for bid in expired_b:
            self._batches.pop(bid, None)

        while len(self._sessions) > self.max_sessions:
            oldest = min(self._sessions, key=lambda k: self._sessions[k]["updated_at"])
            self._sessions.pop(oldest, None)
        while len(self._batches) > self.max_batches:
            oldest = min(self._batches, key=lambda k: self._batches[k]["updated_at"])
            self._batches.pop(oldest, None)

    def _touch_locked(self, session: dict) -> None:
        session["updated_at"] = time.time()

    def _bump_locked(self, session: dict, note: Optional[str] = None) -> None:
        session["version"] = int(session.get("version", 0)) + 1
        session["updated_at"] = time.time()
        if note:
            session["last_action"] = note

    # ── sessions ────────────────────────────────────────────────────────────
    def create(self, raw: pd.DataFrame, files: Optional[list] = None,
               provider: Optional[str] = None, **meta: Any) -> dict:
        sid = _new_id()
        now = time.time()
        session = {
            "id": sid,
            "raw": raw,
            "cleaned": None,
            "files": list(files or []),
            "provider": provider,
            "created_at": now,
            "updated_at": now,
            "version": 1,
            "history": deque(maxlen=self.history_depth),
            "last_analysis": None,
            "last_plot": None,
            "chat": deque(maxlen=40),
            "meta": dict(meta),
            "last_action": "upload",
        }
        with self._lock:
            self._prune_locked()
            self._sessions[sid] = session
        return session

    def get(self, sid: Optional[str]) -> Optional[dict]:
        if not sid:
            return None
        with self._lock:
            self._prune_locked()
            return self._sessions.get(sid)

    def require(self, sid: Optional[str]) -> dict:
        from core.errors import ApiError

        session = self.get(sid)
        if session is None:
            raise ApiError(
                "Session not found. It may have expired after a period of inactivity.",
                404,
                code="session_not_found",
                hint="Upload your data again, or use the demo dataset to restart quickly.",
            )
        return session

    def active_frame(self, sid: Optional[str]):
        session = self.require(sid)
        return session["cleaned"] if session.get("cleaned") is not None else session["raw"]

    def version(self, sid: Optional[str]) -> int:
        session = self.get(sid)
        return int(session.get("version", 0)) if session else 0

    def clear(self) -> None:
        """Discard all in-memory sessions and batches for a clean restart."""
        with self._lock:
            self._sessions.clear()
            self._batches.clear()

    def bump(self, sid: str, note: Optional[str] = None) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if session:
                self._bump_locked(session, note)

    def snapshot(self, sid: str, label: str = "edit") -> None:
        """Remember the current active dataframe before a destructive edit."""
        with self._lock:
            session = self._sessions.get(sid)
            if not session:
                return
            frame = session["cleaned"] if session.get("cleaned") is not None else session["raw"]
            session["history"].append({
                "label": label,
                "cleaned": copy.deepcopy(session.get("cleaned")),
                "raw": None,  # raw is not normally mutated; keep history lean
                "active": "cleaned" if session.get("cleaned") is not None else "raw",
                "frame": frame.copy(deep=True),
                "version": session.get("version", 0),
            })

    def set_raw(self, sid: str, df: pd.DataFrame, *, note: str = "raw update") -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if not session:
                return
            session["raw"] = df
            session["last_analysis"] = None
            session["last_plot"] = None
            self._bump_locked(session, note)

    def set_cleaned(self, sid: str, df: pd.DataFrame, *, provider: Optional[str] = None,
                    note: str = "clean") -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if not session:
                return
            session["cleaned"] = df
            if provider:
                session["provider"] = provider
            session["last_analysis"] = None
            session["last_plot"] = None
            self._bump_locked(session, note)

    def undo(self, sid: str) -> Optional[pd.DataFrame]:
        with self._lock:
            session = self._sessions.get(sid)
            if not session or not session["history"]:
                return None
            entry = session["history"].pop()
            target = entry.get("active", "cleaned")
            if target == "cleaned":
                session["cleaned"] = entry["frame"]
            else:
                session["raw"] = entry["frame"]
            session["last_analysis"] = None
            session["last_plot"] = None
            self._bump_locked(session, f"undo:{entry.get('label', 'edit')}")
            return session["cleaned"] if session.get("cleaned") is not None else session["raw"]

    def history_labels(self, sid: str) -> list:
        with self._lock:
            session = self._sessions.get(sid)
            if not session:
                return []
            return [h.get("label", "edit") for h in session["history"]]

    def record_analysis(self, sid: str, analysis_type: str, result: dict,
                        params: Optional[dict] = None) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if not session:
                return
            session["last_analysis"] = {
                "type": analysis_type,
                "result": result,
                "params": params or {},
                "at": time.time(),
                "version": session.get("version", 0),
            }

    def record_plot(self, sid: str, plot_type: str, params: dict, meta: dict) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if not session:
                return
            session["last_plot"] = {
                "plot_type": plot_type,
                "params": params or {},
                "meta": meta or {},
                "at": time.time(),
                "version": session.get("version", 0),
            }

    def append_chat(self, sid: str, role: str, text: str) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if session:
                session["chat"].append({"role": role, "text": text, "at": time.time()})

    def delete(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    # ── multi-station batches ──────────────────────────────────────────────
    def create_batch(self, stations: Dict[str, pd.DataFrame], *, provider: str = "generic",
                     file_names: Optional[list] = None) -> dict:
        bid = _new_id()
        now = time.time()
        batch = {
            "id": bid,
            "stations_raw": stations,
            "stations_cleaned": {},
            "provider": provider,
            "file_names": list(file_names or []),
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
        with self._lock:
            self._prune_locked()
            self._batches[bid] = batch
        return batch

    def get_batch(self, bid: Optional[str]) -> Optional[dict]:
        if not bid:
            return None
        with self._lock:
            self._prune_locked()
            return self._batches.get(bid)

    def require_batch(self, bid: Optional[str]) -> dict:
        from core.errors import ApiError

        batch = self.get_batch(bid)
        if batch is None:
            raise ApiError(
                "Multi-station session not found or expired.",
                404,
                code="batch_not_found",
                hint="Upload the station files again from the Multi-Station workspace.",
            )
        return batch

    def set_batch_cleaned(self, bid: str, stations: Dict[str, pd.DataFrame], provider: str) -> None:
        with self._lock:
            batch = self._batches.get(bid)
            if not batch:
                return
            batch["stations_cleaned"] = stations
            batch["provider"] = provider
            batch["version"] = int(batch.get("version", 0)) + 1
            batch["updated_at"] = time.time()

    def record_batch_analysis(self, bid: str, analysis_type: str, result: dict,
                              params: Optional[dict] = None) -> None:
        with self._lock:
            batch = self._batches.get(bid)
            if batch:
                batch["last_analysis"] = {
                    "type": analysis_type,
                    "result": result,
                    "params": params or {},
                    "at": time.time(),
                }

    # ── introspection ──────────────────────────────────────────────────────
    def stats(self) -> dict:
        with self._lock:
            self._prune_locked()
            return {
                "sessions": len(self._sessions),
                "batches": len(self._batches),
                "max_sessions": self.max_sessions,
                "ttl_seconds": self.ttl_seconds,
            }


STORE = SessionStore()
