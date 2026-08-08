from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from common.files import apply_translated_output, detect_encoding, is_path_inside
from translation.input import is_mtool_items as translation_is_mtool_items
from translation.input import load_json_items
from translation.input import original_text as translation_original_text
from translation.output import default_output_path


def translated_path(file_path: str) -> str:
    return default_output_path(file_path)


def translation_output_state(file_path: str) -> dict[str, Any]:
    """Describe whether a structurally safe snapshot exists and differs from the last export."""
    output_path = translated_path(file_path)
    if not os.path.isfile(file_path) or not os.path.isfile(output_path):
        return {"ready": False, "dirty": False, "output_path": output_path, "reason": "missing_output"}
    try:
        source_items = load_json_items(file_path)
        output_items = load_json_items(output_path)
    except Exception:
        return {"ready": False, "dirty": False, "output_path": output_path, "reason": "invalid_json"}
    source_keys = [key for key, _value in source_items]
    output_keys = [key for key, _value in output_items]
    ready = len(source_items) == len(output_items) and source_keys == output_keys
    return {
        "ready": ready,
        "dirty": bool(ready and source_items != output_items),
        "output_path": output_path,
        "reason": "ready" if ready else "structure_mismatch",
    }


def save_mtool_upload(upload_dir: str | Path, filename: str, content: bytes) -> dict[str, Any]:
    ext = Path(filename).suffix.lower()
    if ext != ".json":
        raise HTTPException(status_code=400, detail=f"Unsupported file format: {ext}; only MTool JSON is supported")

    session_id = uuid.uuid4().hex[:12]
    session_dir = Path(upload_dir) / session_id
    session_dir.mkdir(exist_ok=True)

    saved_path = session_dir / filename
    with open(saved_path, "wb") as handle:
        handle.write(content)
    require_mtool_json_file(str(saved_path))

    return {
        "session_id": session_id,
        "filename": filename,
        "file_type": "json",
        "file_size": saved_path.stat().st_size,
        "saved_path": str(saved_path),
        "internal_files": [],
    }


def preview_mtool_json(path: str, limit: int = 10) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File does not exist: {path}")
    ext = Path(path).suffix.lower()
    if ext != ".json":
        raise HTTPException(status_code=400, detail=f"Unsupported preview file type: {ext}; only MTool JSON is supported")

    try:
        pairs = load_json_items(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"JSON parse failed: {exc}") from exc
    if not is_mtool_items(pairs):
        raise HTTPException(status_code=400, detail="Only flat MTool JSON mappings are supported")

    return {
        "header": ["Key", "Value"],
        "rows": [[str(key), str(value)] for key, value in pairs[:limit]],
        "column_count": 2,
        "total_rows": len(pairs),
        "encoding": "utf-8",
        "file_type": "json",
    }


def export_mtool_json(
    session_dir: str | Path,
    source_path: str,
    output_dir: str | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    if not Path(session_dir).is_dir():
        raise HTTPException(status_code=404, detail="Session does not exist")
    if not os.path.isfile(source_path):
        raise HTTPException(status_code=404, detail=f"Source file does not exist: {source_path}")
    require_mtool_json_file(source_path)

    backup_path = source_path + ".bak"
    export_path = source_path

    if not os.path.exists(backup_path):
        shutil.copy2(source_path, backup_path)

    translated_output_path = translated_path(source_path)
    if os.path.isfile(translated_output_path):
        try:
            # Keep the working snapshot so review can continue after an early export.
            shutil.copy2(translated_output_path, source_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Unable to apply translated file: {exc}") from exc

    if output_path:
        destination = Path(output_path)
        if destination.exists() and destination.is_dir():
            raise HTTPException(status_code=400, detail="Export destination must be a file, not a directory")
        if not destination.parent.is_dir():
            raise HTTPException(status_code=400, detail="Export destination directory does not exist")
        try:
            if destination.resolve() != Path(export_path).resolve():
                shutil.copy2(export_path, destination)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Unable to save exported file: {exc}") from exc
        export_path = str(destination)
    elif output_dir:
        output_dir_path = Path(output_dir)
        if not output_dir_path.is_dir():
            try:
                output_dir_path.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Unable to create export directory: {exc}") from exc
        destination = output_dir_path / Path(export_path).name
        shutil.copy2(export_path, str(destination))
        export_path = str(destination)

    return {
        "ok": True,
        "backup_path": backup_path,
        "export_path": export_path,
        "filename": Path(export_path).name,
    }


def is_mtool_items(items: list[tuple[Any, Any]]) -> bool:
    return translation_is_mtool_items(items)


def require_mtool_json_file(file_path: str) -> None:
    if Path(file_path).suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Only MTool JSON files are supported")
    try:
        items = load_json_items(file_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {exc}") from exc
    if not is_mtool_items(items):
        raise HTTPException(status_code=400, detail="Only flat MTool JSON mappings are supported")


def json_original_text(key: Any, value: Any, cp_entry: dict | None = None, mtool: bool = False) -> str:
    return translation_original_text(key, value, cp_entry, mtool=mtool)
