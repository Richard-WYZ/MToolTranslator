"""Private recovery copies for editor drafts, separate from applied translations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from common.paths import runtime_base_dir


_KINDS = {"review", "glossary"}
_LOCKS = {kind: threading.RLock() for kind in _KINDS}
MAX_DRAFTS = 200_000
MAX_STORE_BYTES = 64 * 1024 * 1024


def _path(kind: str) -> Path:
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("Unknown workspace draft kind")
    # Checkpoint runtime data is already excluded from Git and public packages.
    return runtime_base_dir() / ".checkpoints" / f"workspace-drafts-{kind}.json"


def _validated(kind: str, drafts: Any) -> list[dict[str, Any]]:
    _path(kind)
    if not isinstance(drafts, list):
        raise ValueError("Workspace drafts must be a list")
    if len(drafts) > MAX_DRAFTS:
        raise ValueError("Too many workspace drafts; existing recovery data was retained")
    required = {"path", "row", "text", "original"} if kind == "review" else {"file", "source", "target"}
    allowed = required | ({"base_text"} if kind == "review" else {"initialTarget"})
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for draft in drafts:
        if not isinstance(draft, dict) or not required.issubset(draft) or set(draft) - allowed:
            raise ValueError("Workspace draft fields are invalid")
        for key, value in draft.items():
            if key == "row":
                if type(value) is not int or not 0 <= value <= 2**53 - 1:
                    raise ValueError("Workspace draft row must be a non-negative safe integer")
            elif not isinstance(value, str) or len(value) > MAX_STORE_BYTES:
                raise ValueError("Workspace draft text is invalid or too large")
        file_key = "path" if kind == "review" else "file"
        if not draft[file_key].strip() or "\x00" in draft[file_key]:
            raise ValueError("Workspace draft project path is invalid")
        if kind == "glossary" and not draft["source"]:
            raise ValueError("Workspace draft source term is required")
        key = (draft[file_key], draft["row"] if kind == "review" else draft["source"])
        unique[key] = dict(draft)
    return list(unique.values())


def get_workspace_drafts(kind: str) -> list[dict[str, Any]]:
    """Return one editor's recovery copy without touching its actual project files."""
    path = _path(kind)
    with _LOCKS[kind]:
        try:
            if path.stat().st_size > MAX_STORE_BYTES:
                raise ValueError("Workspace draft recovery file is too large")
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return []
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("Workspace draft recovery format is invalid")
        return _validated(kind, payload.get("drafts"))


def set_workspace_drafts(kind: str, drafts: Any) -> dict[str, Any]:
    """Atomically replace only this editor's snapshot; an empty list clears it."""
    path = _path(kind)
    normalized = _validated(kind, drafts)
    serialized = json.dumps({"version": 1, "drafts": normalized}, ensure_ascii=False, separators=(",", ":")) + "\n"
    if len(serialized.encode("utf-8")) > MAX_STORE_BYTES:
        raise ValueError("Workspace drafts are too large; existing recovery data was retained")
    with _LOCKS[kind]:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(serialized)
            os.replace(temporary, path)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
    return {"ok": True, "count": len(normalized)}
