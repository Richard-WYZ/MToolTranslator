"""Bounded in-memory discovery caching; transport and translation stay in clients."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable


@dataclass
class _Entry:
    models: list[dict[str, Any]] = field(default_factory=list)
    checked_at: float | None = None
    expires_at: float = 0.0
    error: Exception | None = None
    pending: threading.Event | None = None


class CatalogCache:
    """Coalesce concurrent discovery calls and briefly cache both success and failure."""

    def __init__(self, *, ttl: float = 30.0, failure_ttl: float = 5.0, wait_timeout: float = 4.0):
        self.ttl = ttl
        self.failure_ttl = failure_ttl
        self.wait_timeout = wait_timeout
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def snapshot(self, key: str) -> dict[str, Any]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return {"models": [], "checked": False, "available": False, "stale": False, "pending": False, "error": ""}
            fresh = time.monotonic() < entry.expires_at
            available = fresh and entry.checked_at is not None and entry.error is None
            return {
                "models": deepcopy(entry.models), "checked": entry.checked_at is not None,
                "available": available, "stale": bool(entry.models) and not available,
                "pending": entry.pending is not None, "error": str(entry.error or ""),
            }

    def get(self, key: str, loader: Callable[[], list[dict[str, Any]]], *, refresh: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            entry = self._entries.setdefault(key, _Entry())
            pending = entry.pending
            if pending is None and not refresh and time.monotonic() < entry.expires_at:
                if entry.error is not None:
                    raise entry.error
                return deepcopy(entry.models)
            owner = pending is None
            if owner:
                pending = entry.pending = threading.Event()
        if not owner:
            if not pending.wait(self.wait_timeout):
                raise TimeoutError("Model discovery is still running; try refreshing again")
            with self._lock:
                if entry.error is not None:
                    raise entry.error
                return deepcopy(entry.models)
        try:
            models = loader()
        except Exception as exc:
            with self._lock:
                entry.error = exc.with_traceback(None)
                entry.checked_at = time.monotonic()
                entry.expires_at = entry.checked_at + self.failure_ttl
            raise
        else:
            with self._lock:
                entry.models = deepcopy(models)
                entry.error = None
                entry.checked_at = time.monotonic()
                entry.expires_at = entry.checked_at + self.ttl
            return deepcopy(models)
        finally:
            with self._lock:
                entry.pending = None
                pending.set()
                # Retire unused hosts without removing in-flight work.
                for old_key in list(self._entries):
                    if len(self._entries) <= 16:
                        break
                    if old_key != key and self._entries[old_key].pending is None:
                        self._entries.pop(old_key)
