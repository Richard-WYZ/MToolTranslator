from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DesktopSource:
    token: str
    path: str
    filename: str
    size: int
    created_at: float

    def public(self) -> dict[str, object]:
        return {
            "token": self.token,
            "filename": self.filename,
            "size": self.size,
        }


class DesktopSourceRegistry:
    """Pass trusted desktop file paths to the local API through one-time tokens."""

    def __init__(self, *, ttl_seconds: float = 120.0) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._condition = threading.Condition()
        self._records: dict[str, DesktopSource] = {}
        self._pending: list[str] = []

    def _prune_locked(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        expired = [token for token, record in self._records.items() if record.created_at < cutoff]
        for token in expired:
            self._records.pop(token, None)
        if expired:
            expired_set = set(expired)
            self._pending = [token for token in self._pending if token not in expired_set]

    def register(self, path: str) -> DesktopSource:
        source = Path(path).resolve(strict=True)
        if not source.is_file():
            raise FileNotFoundError(f"Desktop source is not a file: {source}")
        record = DesktopSource(
            token=uuid.uuid4().hex,
            path=str(source),
            filename=source.name,
            size=source.stat().st_size,
            created_at=time.monotonic(),
        )
        with self._condition:
            self._prune_locked()
            self._records[record.token] = record
            self._pending.append(record.token)
            self._condition.notify_all()
        return record

    def wait_for_match(self, filename: str, size: int, *, timeout: float = 2.0) -> DesktopSource | None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                self._prune_locked()
                for index, token in enumerate(self._pending):
                    record = self._records.get(token)
                    if record and record.filename == filename and record.size == int(size):
                        self._pending.pop(index)
                        return record
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def consume(self, token: str) -> DesktopSource | None:
        with self._condition:
            self._prune_locked()
            record = self._records.pop(str(token or ""), None)
            if record:
                self._pending = [pending for pending in self._pending if pending != record.token]
            return record

    def clear(self) -> None:
        with self._condition:
            self._records.clear()
            self._pending.clear()
            self._condition.notify_all()


desktop_source_registry = DesktopSourceRegistry()


def validate_desktop_source(record: DesktopSource, filename: str, content: bytes) -> str:
    source = Path(record.path)
    if Path(filename).name != record.filename:
        raise ValueError("The dropped source filename does not match the uploaded file")
    if len(content) != record.size:
        raise ValueError("The dropped source size does not match the uploaded file")
    try:
        disk_content = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"Unable to read the dropped source file: {exc}") from exc
    if disk_content != content:
        raise ValueError("The dropped source content changed before import")
    return os.path.abspath(record.path)


__all__ = [
    "DesktopSource",
    "DesktopSourceRegistry",
    "desktop_source_registry",
    "validate_desktop_source",
]
