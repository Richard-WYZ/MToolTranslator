from __future__ import annotations

import os
import hashlib
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from common.files import apply_translated_output, detect_encoding, is_path_inside
from translation.input import is_mtool_items as translation_is_mtool_items
from translation.input import load_json_items
from translation.input import original_text as translation_original_text
from translation.output import default_output_path


SESSION_METADATA_FILENAME = ".lgt-session.json"


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def session_metadata_path(file_path: str | Path) -> Path:
    return Path(file_path).resolve().parent / SESSION_METADATA_FILENAME


def load_session_metadata(file_path: str | Path) -> dict[str, Any]:
    path = session_metadata_path(file_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    working_path = str(payload.get("working_path") or "")
    if working_path and os.path.abspath(working_path) != os.path.abspath(str(file_path)):
        return {}
    return payload


def save_session_metadata(file_path: str | Path, metadata: dict[str, Any]) -> Path:
    destination = session_metadata_path(file_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            os.remove(temp_path)
    return destination


def session_project_info(file_path: str | Path) -> dict[str, Any]:
    metadata = load_session_metadata(file_path)
    original_path = str(metadata.get("original_path") or "")
    custom_name = str(metadata.get("project_name") or "").strip()
    session_id = str(metadata.get("session_id") or Path(file_path).resolve().parent.name)
    if custom_name:
        display_name = custom_name
        name_source = "custom"
    elif original_path:
        parent_name = Path(original_path).parent.name.strip()
        display_name = parent_name or Path(original_path).stem or f"项目 {session_id[:6]}"
        name_source = "directory"
    else:
        display_name = f"项目 {session_id[:6]}" if session_id else "未命名项目"
        name_source = "session"
    return {
        "project_name": custom_name,
        "project_display_name": display_name,
        "project_name_source": name_source,
        "original_path": original_path,
        "session_id": session_id,
        "created_at": str(metadata.get("created_at") or ""),
    }


def update_session_project_name(file_path: str | Path, project_name: str) -> dict[str, Any]:
    clean_name = str(project_name or "").strip()
    if len(clean_name) > 80:
        raise HTTPException(status_code=400, detail="Project name must be 80 characters or fewer")
    metadata = load_session_metadata(file_path)
    if not metadata:
        source = Path(file_path).resolve()
        if not source.is_file():
            raise HTTPException(status_code=404, detail="Session source file does not exist")
        metadata = {
            "version": 1,
            "session_id": source.parent.name,
            "working_path": str(source),
            "original_path": "",
            "original_import_sha256": "",
            "original_last_known_sha256": "",
            "last_exported_sha256": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    metadata["project_name"] = clean_name
    save_session_metadata(file_path, metadata)
    return session_project_info(file_path)


def _atomic_copy(source: str | Path, destination: str | Path) -> None:
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path == destination_path:
        return
    if not destination_path.parent.is_dir():
        raise FileNotFoundError(f"Export destination directory does not exist: {destination_path.parent}")
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
        dir=destination_path.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    handle.close()
    try:
        shutil.copy2(source_path, temp_path)
        os.replace(temp_path, destination_path)
    finally:
        if temp_path.exists():
            os.remove(temp_path)


def translated_path(file_path: str) -> str:
    return default_output_path(file_path)


def translation_output_state(file_path: str) -> dict[str, Any]:
    """Describe whether a structurally safe snapshot exists and differs from the last export."""
    output_path = translated_path(file_path)
    metadata = load_session_metadata(file_path)
    original_path = str(metadata.get("original_path") or "")
    if not os.path.isfile(file_path) or not os.path.isfile(output_path):
        return {
            "ready": False,
            "dirty": False,
            "output_path": output_path,
            "original_path": original_path,
            "reason": "missing_output",
        }
    try:
        source_items = load_json_items(file_path)
        output_items = load_json_items(output_path)
    except Exception:
        return {
            "ready": False,
            "dirty": False,
            "output_path": output_path,
            "original_path": original_path,
            "reason": "invalid_json",
        }
    source_keys = [key for key, _value in source_items]
    output_keys = [key for key, _value in output_items]
    ready = len(source_items) == len(output_items) and source_keys == output_keys
    output_hash = _sha256_file(output_path) if ready else ""
    last_exported_hash = str(metadata.get("last_exported_sha256") or "")
    if metadata:
        dirty = bool(ready and output_hash != last_exported_hash)
    else:
        dirty = bool(ready and source_items != output_items)
    return {
        "ready": ready,
        "dirty": dirty,
        "output_path": output_path,
        "output_sha256": output_hash,
        "last_exported_sha256": last_exported_hash,
        "original_path": original_path,
        "reason": "ready" if ready else "structure_mismatch",
    }


def save_mtool_upload(
    upload_dir: str | Path,
    filename: str,
    content: bytes,
    *,
    original_path: str | None = None,
) -> dict[str, Any]:
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

    content_hash = _sha256_bytes(content)
    metadata = {
        "version": 1,
        "session_id": session_id,
        "working_path": str(saved_path.resolve()),
        "original_path": os.path.abspath(original_path) if original_path else "",
        "original_import_sha256": content_hash if original_path else "",
        "original_last_known_sha256": content_hash if original_path else "",
        "last_exported_sha256": "",
        "project_name": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_session_metadata(saved_path, metadata)

    return {
        "session_id": session_id,
        "filename": filename,
        "file_type": "json",
        "file_size": saved_path.stat().st_size,
        "saved_path": str(saved_path),
        "original_path": metadata["original_path"],
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
    *,
    overwrite_original: bool = False,
) -> dict[str, Any]:
    if not Path(session_dir).is_dir():
        raise HTTPException(status_code=404, detail="Session does not exist")
    if not os.path.isfile(source_path):
        raise HTTPException(status_code=404, detail=f"Source file does not exist: {source_path}")
    require_mtool_json_file(source_path)

    translated_output_path = translated_path(source_path)
    if not os.path.isfile(translated_output_path):
        raise HTTPException(status_code=409, detail="Translated output does not exist")
    try:
        source_items = load_json_items(source_path)
        output_items = load_json_items(translated_output_path)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Unable to validate translated output: {exc}") from exc
    if [key for key, _value in source_items] != [key for key, _value in output_items]:
        raise HTTPException(status_code=409, detail="Translated output keys or key order do not match the source")

    metadata = load_session_metadata(source_path)
    if not metadata:
        metadata = {
            "version": 1,
            "session_id": Path(session_dir).name,
            "working_path": str(Path(source_path).resolve()),
            "original_path": "",
            "original_import_sha256": "",
            "original_last_known_sha256": "",
            "last_exported_sha256": "",
        }
    output_hash = _sha256_file(translated_output_path)
    backup_path = ""

    if overwrite_original:
        if output_path or output_dir:
            raise HTTPException(status_code=400, detail="Overwrite-source export cannot also specify another destination")
        original_path = str(metadata.get("original_path") or "")
        if not original_path:
            raise HTTPException(status_code=409, detail="The original desktop file path is unavailable; use Save As")
        original = Path(original_path)
        if not original.is_file():
            raise HTTPException(status_code=409, detail="The original source file no longer exists; use Save As")
        expected_hash = str(metadata.get("original_last_known_sha256") or "")
        try:
            current_hash = _sha256_file(original)
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"Unable to read the original source file: {exc}") from exc
        if expected_hash and current_hash != expected_hash:
            raise HTTPException(
                status_code=409,
                detail="The original source file changed after import and will not be overwritten; use Save As",
            )
        backup = Path(f"{original_path}.bak")
        try:
            if not backup.exists():
                shutil.copy2(original, backup)
            _atomic_copy(translated_output_path, original)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Unable to overwrite the original source file: {exc}") from exc
        backup_path = str(backup)
        export_path = str(original)
        metadata["original_last_known_sha256"] = output_hash
    elif output_path:
        destination = Path(output_path)
        if destination.exists() and destination.is_dir():
            raise HTTPException(status_code=400, detail="Export destination must be a file, not a directory")
        if not destination.parent.is_dir():
            raise HTTPException(status_code=400, detail="Export destination directory does not exist")
        try:
            _atomic_copy(translated_output_path, destination)
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
        destination = output_dir_path / Path(source_path).name
        _atomic_copy(translated_output_path, destination)
        export_path = str(destination)
    else:
        export_path = translated_output_path

    metadata["last_exported_sha256"] = output_hash
    save_session_metadata(source_path, metadata)

    return {
        "ok": True,
        "backup_path": backup_path,
        "export_path": export_path,
        "filename": Path(source_path).name if not output_path and not output_dir else Path(export_path).name,
        "overwrote_original": bool(overwrite_original),
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
