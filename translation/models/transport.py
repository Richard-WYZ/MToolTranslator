"""Small, explicit request-session scopes used by model transports.

The default path keeps calling ``requests.get/post`` so existing callers that
patch those functions remain compatible. Translation runs can opt into a
scope, which owns one session per provider per thread and closes all at exit.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import threading
from typing import Any, Iterator

import requests


@dataclass(eq=False)
class _SessionScope:
    """Own one provider session per calling thread.

    A translation scope normally surrounds a pipeline that may use a thread
    pool.  Keeping a single ``requests.Session`` in that case makes connection
    state cross thread boundaries.  The scope therefore lazily creates a
    provider session for each thread and closes every one at scope exit.
    """

    sessions: dict[tuple[int, str], requests.Session]
    lock: threading.Lock

    def session_for(self, provider: str) -> requests.Session:
        key = (threading.get_ident(), provider)
        with self.lock:
            session = self.sessions.get(key)
            if session is None:
                session = requests.Session()
                self.sessions[key] = session
            return session

    @property
    def api(self) -> requests.Session:
        """Compatibility view of the current thread's API session."""
        return self.session_for("api")

    @property
    def ollama(self) -> requests.Session:
        """Compatibility view of the current thread's Ollama session."""
        return self.session_for("ollama")

    def close(self) -> None:
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                # Closing one provider connection must not prevent the others
                # from being released.
                continue


_current_scope: ContextVar[_SessionScope | None] = ContextVar("model_transport_scope", default=None)


@contextmanager
def connection_scope() -> Iterator[_SessionScope]:
    """Own provider sessions for one run and close them deterministically."""
    existing = _current_scope.get()
    if existing is not None:
        yield existing
        return
    scope = _SessionScope(sessions={}, lock=threading.Lock())
    token = _current_scope.set(scope)
    try:
        yield scope
    finally:
        _current_scope.reset(token)
        scope.close()


def request(provider: str, method: str, url: str, **kwargs: Any) -> requests.Response:
    """Issue a request through the active scope, or legacy requests helpers."""
    scope = _current_scope.get()
    if scope is None:
        return getattr(requests, method)(url, **kwargs)
    session = scope.session_for(provider)
    return getattr(session, method)(url, **kwargs)


__all__ = ["connection_scope", "request"]
