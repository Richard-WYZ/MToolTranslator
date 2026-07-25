from __future__ import annotations

import os
import tempfile
import threading
from typing import Any

from translation.output.json_io import serialize_json_items


class TranslationWriter:
    """Background writer that atomically rewrites the current translated file."""

    def __init__(
        self,
        file_type: str,
        data_ref: Any,
        output_path: str,
        encoding: str = "utf-8",
        flush_interval: float = 0.5,
        json_every: int = 5,
        periodic_enabled: bool = True,
        flush_on_stop: bool = True,
    ):
        self.file_type = file_type
        self.data_ref = data_ref
        self.output_path = output_path
        self.encoding = encoding
        self.flush_interval = flush_interval
        self.json_every = max(1, json_every)
        self.periodic_enabled = bool(periodic_enabled)
        self.flush_on_stop = bool(flush_on_stop)
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._updates = 0

    def start(self) -> None:
        if not self.periodic_enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def mark_dirty(self) -> None:
        with self._lock:
            self._updates += 1
        self._dirty.set()

    def update_cell(self, row_idx: int, col_idx: int, text: str) -> bool:
        """Update one output cell and mark the writer dirty."""
        updated = False
        with self._lock:
            if self.file_type == "json":
                items = self.data_ref
                if 0 <= row_idx < len(items):
                    key, _ = items[row_idx]
                    items[row_idx] = (key, text)
                    updated = True
            else:
                rows = self.data_ref.get("rows", [])
                if 0 <= row_idx < len(rows) and 0 <= col_idx < len(rows[row_idx]):
                    rows[row_idx][col_idx] = text
                    updated = True
            if updated:
                self._updates += 1
                self._dirty.set()
        return updated

    def flush(self) -> None:
        with self._lock:
            snapshot = list(self.data_ref)
            self._updates = 0
            self._dirty.clear()
        with self._flush_lock:
            self._write_atomic(snapshot)

    def stop(self) -> None:
        self._stop.set()
        self._dirty.set()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                return
        if self.flush_on_stop:
            self.flush()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._dirty.wait(self.flush_interval)
            if not self._dirty.is_set():
                continue
            if self.file_type == "json":
                with self._lock:
                    should_flush = self._updates >= self.json_every
                if not should_flush and not self._stop.is_set():
                    continue
            self.flush()

    def _write_atomic(self, data_ref: Any | None = None) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)) or ".", exist_ok=True)
        if self.file_type != "json":
            raise ValueError("TranslationWriter only supports MTool JSON output")
        content = serialize_json_items(
            self.data_ref if data_ref is None else data_ref
        )

        fd, tmp_path = tempfile.mkstemp(
            prefix=".tmp_translation_",
            suffix=os.path.splitext(self.output_path)[1] or ".tmp",
            dir=os.path.dirname(os.path.abspath(self.output_path)) or ".",
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding=self.encoding, newline="") as f:
                f.write(content)
            os.replace(tmp_path, self.output_path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


__all__ = ["TranslationWriter"]
