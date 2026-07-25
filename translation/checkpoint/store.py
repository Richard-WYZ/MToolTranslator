from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


CHECKPOINT_DIR = ".checkpoints"
VERSION = 2
FINAL_STATUSES = {"translated", "preserved", "translated_needs_review", "review_required"}
COMPLETED_STATUSES = {"translated", "preserved", "translated_needs_review", "review_required"}
LEGACY_STATUS_MAP = {
    "done": "translated",
    "skipped": "preserved",
    "failed_refusal": "review_required",
    "failed_untranslated": "review_required",
}
_JOURNAL_LOCK = threading.Lock()
_CONTEXT_CACHE: dict[str, dict[str, Any]] = {}


def _ensure_dir() -> None:
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _source_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _normalized_source(text: str) -> str:
    return " ".join((text or "").split())


def normalize_status(
    status: str,
    issues: list[dict[str, Any]] | None = None,
    *,
    translated: str = "",
    original: str = "",
) -> str:
    normalized = LEGACY_STATUS_MAP.get(status, status)
    if normalized not in FINAL_STATUSES:
        normalized = "review_required" if status else "translated"
    if normalized == "translated" and (original or translated):
        from translation.quality.status import status_for_output

        return status_for_output(original, translated, issues)
    if normalized == "translated" and issues:
        return "translated_needs_review"
    return normalized


def is_completed_status(status: str) -> bool:
    return status in FINAL_STATUSES or status in LEGACY_STATUS_MAP


def is_completed_entry(entry: dict[str, Any] | None) -> bool:
    return bool(entry and is_completed_status(str(entry.get("status", ""))))


def is_resumable_entry(
    entry: dict[str, Any] | None,
    *,
    source: str,
    translation_direction: str,
    prompt_version: str,
    glossary_version: str,
    model_configuration: dict[str, Any],
) -> bool:
    """Return whether a completed entry is valid for the exact current run context."""
    if not is_completed_entry(entry):
        return False
    assert entry is not None
    if str(entry.get("source_hash", "")) != _source_hash(source):
        return False
    if str(entry.get("translation_direction", "")) != translation_direction:
        return False
    if str(entry.get("prompt_version", "")) != prompt_version:
        return False
    if str(entry.get("glossary_version", "")) != glossary_version:
        return False
    return _canonical_json(entry.get("model_configuration", {})) == _canonical_json(model_configuration)


def _canonical_json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_name(file_path: str) -> str:
    name = Path(file_path).stem or "translation"
    return "".join(c if c.isalnum() else "_" for c in name)


def _stable_hash(file_path: str) -> str:
    abs_path = os.path.abspath(file_path)
    return hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:12]


def get_checkpoint_path(file_path: str) -> str:
    _ensure_dir()
    return os.path.join(CHECKPOINT_DIR, f"{_safe_name(file_path)}_{_stable_hash(file_path)}.json")


def get_glossary_path(file_path: str) -> str:
    cp = get_checkpoint_path(file_path)
    return cp[:-5] + ".glossary.json"


def get_checkpoint_journal_path(file_path: str) -> str:
    return get_checkpoint_path(file_path) + ".journal.jsonl"


def _empty_checkpoint(file_path: str) -> dict[str, Any]:
    now = _now_iso()
    ext = Path(file_path).suffix.lower().lstrip(".") or "json"
    return {
        "version": VERSION,
        "file_path": os.path.abspath(file_path),
        "file_name": os.path.basename(file_path),
        "file_type": ext,
        "task_id": "",
        "model": "",
        "model_configuration": {},
        "translation_direction": "ja-Hans",
        "prompt_version": "default",
        "glossary_version": "0",
        "prompt_style": "",
        "translate_columns": [],
        "created_at": now,
        "updated_at": now,
        "entries": {},
        "stats": {"total": 0, "completed": 0, "failed": 0},
    }


def load_checkpoint(file_path: str) -> dict[str, Any]:
    cp_path = get_checkpoint_path(file_path)
    data: dict[str, Any]
    if os.path.exists(cp_path):
        with open(cp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != VERSION:
            data = _empty_checkpoint(file_path)
    else:
        data = _empty_checkpoint(file_path)
    _replay_journal(cp_path, data)
    _normalize_loaded_entries(data)
    _update_checkpoint_stats(data)
    return data


def _replay_journal(cp_path: str, data: dict[str, Any]) -> None:
    journal_path = cp_path + ".journal.jsonl"
    if not os.path.exists(journal_path):
        return
    entries = data.setdefault("entries", {})
    with _JOURNAL_LOCK:
        try:
            with open(journal_path, "r", encoding="utf-8") as stream:
                for line in stream:
                    rendered = line.strip()
                    if not rendered:
                        continue
                    try:
                        event = json.loads(rendered)
                    except json.JSONDecodeError:
                        continue
                    key = str(event.get("key", ""))
                    entry = event.get("entry")
                    if key and isinstance(entry, dict):
                        entries[key] = entry
        except OSError:
            return


def _update_checkpoint_stats(data: dict[str, Any]) -> None:
    entries = data.setdefault("entries", {})
    stats = data.setdefault("stats", {})
    stats["completed"] = sum(
        1
        for entry in entries.values()
        if is_completed_status(str(entry.get("status", "")))
    )
    stats["failed"] = sum(
        1
        for entry in entries.values()
        if normalize_status(str(entry.get("status", ""))) == "review_required"
    )


def _checkpoint_context(file_path: str) -> dict[str, Any]:
    cp_path = get_checkpoint_path(file_path)
    cached = _CONTEXT_CACHE.get(cp_path)
    if cached is not None:
        return cached
    if os.path.exists(cp_path):
        try:
            with open(cp_path, "r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, json.JSONDecodeError):
            data = _empty_checkpoint(file_path)
    else:
        data = _empty_checkpoint(file_path)
    context = {
        "model": data.get("model", ""),
        "model_configuration": data.get(
            "model_configuration",
            {"model": data.get("model", "")},
        ),
        "translation_direction": data.get(
            "translation_direction",
            "ja-Hans",
        ),
        "prompt_version": data.get("prompt_version", "default"),
        "glossary_version": data.get("glossary_version", "0"),
    }
    _CONTEXT_CACHE[cp_path] = context
    return context


def _normalize_loaded_entries(data: dict[str, Any]) -> None:
    for entry in data.get("entries", {}).values():
        if not isinstance(entry, dict):
            continue
        issues = entry.get("issues", [])
        if "validation_issues" not in entry:
            entry["validation_issues"] = issues if isinstance(issues, list) else []
        original = str(entry.get("original", ""))
        translated = str(entry.get("translated", ""))
        entry["status"] = normalize_status(
            str(entry.get("status", "")),
            entry.get("issues", []) if isinstance(entry.get("issues"), list) else [],
            translated=translated,
            original=original,
        )
        entry.setdefault("source_key", entry.get("json_key", original))
        entry.setdefault("source_hash", _source_hash(original))
        entry.setdefault("normalized_source", _normalized_source(original))
        entry.setdefault("output_translation", translated)
        entry.setdefault("entry_classification", "")
        entry.setdefault("batch_id", "")
        entry.setdefault("model_identifier", data.get("model", ""))
        entry.setdefault("model_configuration", data.get("model_configuration", {"model": data.get("model", "")}))
        entry.setdefault("translation_direction", data.get("translation_direction", "ja-Hans"))
        entry.setdefault("prompt_version", data.get("prompt_version", "default"))
        entry.setdefault("glossary_version", data.get("glossary_version", "0"))
        entry.setdefault("retry_count", 0)
        entry.setdefault("review_reasons", _review_reasons(entry["status"], entry.get("issues", [])))
        entry.setdefault("updated_at", data.get("updated_at", ""))


def save_checkpoint(file_path: str, data: dict[str, Any]) -> None:
    _ensure_dir()
    data["version"] = VERSION
    data["file_path"] = os.path.abspath(file_path)
    data["file_name"] = os.path.basename(file_path)
    data["updated_at"] = _now_iso()
    _update_checkpoint_stats(data)
    cp_path = get_checkpoint_path(file_path)
    journal_path = cp_path + ".journal.jsonl"
    fd, temp_path = tempfile.mkstemp(
        prefix=".checkpoint_",
        suffix=".tmp",
        dir=os.path.dirname(os.path.abspath(cp_path)) or ".",
        text=True,
    )
    with _JOURNAL_LOCK:
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
            os.replace(temp_path, cp_path)
            if os.path.exists(journal_path):
                os.remove(journal_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    _CONTEXT_CACHE[cp_path] = {
        "model": data.get("model", ""),
        "model_configuration": data.get(
            "model_configuration",
            {"model": data.get("model", "")},
        ),
        "translation_direction": data.get(
            "translation_direction",
            "ja-Hans",
        ),
        "prompt_version": data.get("prompt_version", "default"),
        "glossary_version": data.get("glossary_version", "0"),
    }


def init_checkpoint(
    file_path: str,
    total: int = 0,
    task_id: str = "",
    model: str = "",
    prompt_style: str = "",
    translate_columns: list[int] | None = None,
    file_type: str | None = None,
    model_configuration: dict[str, Any] | None = None,
    translation_direction: str = "ja-Hans",
    prompt_version: str = "default",
    glossary_version: str = "0",
) -> dict[str, Any]:
    data = load_checkpoint(file_path)
    data["task_id"] = task_id or data.get("task_id", "")
    data["model"] = model or data.get("model", "")
    data["model_configuration"] = model_configuration or data.get("model_configuration", {}) or {"model": data["model"]}
    data["translation_direction"] = translation_direction
    data["prompt_version"] = prompt_version
    data["glossary_version"] = glossary_version
    data["prompt_style"] = prompt_style or data.get("prompt_style", "")
    data["translate_columns"] = translate_columns if translate_columns is not None else data.get("translate_columns", [])
    if file_type:
        data["file_type"] = file_type
    if total:
        data.setdefault("stats", {})["total"] = total
    save_checkpoint(file_path, data)
    return data


def set_glossary_version(file_path: str, glossary_version: str, *, update_entries: bool = False) -> None:
    data = load_checkpoint(file_path)
    data["glossary_version"] = glossary_version
    if update_entries:
        for entry in data.get("entries", {}).values():
            if isinstance(entry, dict):
                entry["glossary_version"] = glossary_version
    save_checkpoint(file_path, data)


def set_token_usage(file_path: str, token_usage: dict[str, Any]) -> None:
    data = load_checkpoint(file_path)
    data["token_usage"] = token_usage or {}
    save_checkpoint(file_path, data)


def save_progress(
    file_path: str,
    row: int,
    col: int,
    original: str,
    translated: str,
    status: str = "translated",
    issues: list[dict[str, Any]] | None = None,
    error: str | None = None,
    **metadata: Any,
) -> None:
    data = load_checkpoint(file_path)
    key = f"{row}_{col}"
    issue_list = issues or []
    normalized_status = normalize_status(status, issue_list, translated=translated, original=original)
    now = _now_iso()
    entry = {
        "row": row,
        "col": col,
        "original": original,
        "translated": translated,
        "source_key": metadata.get("json_key", original),
        "source_hash": _source_hash(original),
        "normalized_source": _normalized_source(original),
        "output_translation": translated,
        "entry_classification": metadata.get("entry_classification", metadata.get("classification", "")),
        "status": normalized_status,
        "batch_id": metadata.get("batch_id", ""),
        "model_identifier": metadata.get("model_identifier", data.get("model", "")),
        "model_configuration": metadata.get("model_configuration", data.get("model_configuration", {"model": data.get("model", "")})),
        "translation_direction": metadata.get("translation_direction", data.get("translation_direction", "ja-Hans")),
        "prompt_version": metadata.get("prompt_version", data.get("prompt_version", "default")),
        "glossary_version": metadata.get("glossary_version", data.get("glossary_version", "0")),
        "retry_count": int(metadata.get("retry_count", 0) or 0),
        "issues": issue_list,
        "validation_issues": issue_list,
        "review_reasons": metadata.get("review_reasons", _review_reasons(normalized_status, issue_list)),
        "updated_at": now,
    }
    if error:
        entry["error"] = error
    entry.update({k: v for k, v in metadata.items() if v is not None})
    data.setdefault("entries", {})[key] = entry
    save_checkpoint(file_path, data)


def save_progress_many(file_path: str, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    data = _checkpoint_context(file_path)
    journal_entries: list[tuple[str, dict[str, Any]]] = []
    now = _now_iso()
    for record in records:
        row = int(record.get("row", 0))
        col = int(record.get("col", 0))
        original = str(record.get("original", ""))
        translated = str(record.get("translated", ""))
        issue_list = record.get("issues", []) or []
        metadata = {k: v for k, v in record.items() if k not in {"row", "col", "original", "translated", "status", "issues", "error"}}
        normalized_status = normalize_status(str(record.get("status", "translated")), issue_list, translated=translated, original=original)
        entry = {
            "row": row,
            "col": col,
            "original": original,
            "translated": translated,
            "source_key": metadata.get("json_key", original),
            "source_hash": _source_hash(original),
            "normalized_source": _normalized_source(original),
            "output_translation": translated,
            "entry_classification": metadata.get("entry_classification", metadata.get("classification", "")),
            "status": normalized_status,
            "batch_id": metadata.get("batch_id", ""),
            "model_identifier": metadata.get("model_identifier", data.get("model", "")),
            "model_configuration": metadata.get("model_configuration", data.get("model_configuration", {"model": data.get("model", "")})),
            "translation_direction": metadata.get("translation_direction", data.get("translation_direction", "ja-Hans")),
            "prompt_version": metadata.get("prompt_version", data.get("prompt_version", "default")),
            "glossary_version": metadata.get("glossary_version", data.get("glossary_version", "0")),
            "retry_count": int(metadata.get("retry_count", 0) or 0),
            "issues": issue_list,
            "validation_issues": issue_list,
            "review_reasons": metadata.get("review_reasons", _review_reasons(normalized_status, issue_list)),
            "updated_at": now,
        }
        if record.get("error"):
            entry["error"] = record["error"]
        entry.update({k: v for k, v in metadata.items() if v is not None})
        journal_entries.append((f"{row}_{col}", entry))
    _append_progress_journal(file_path, journal_entries)


def _append_progress_journal(
    file_path: str,
    entries: list[tuple[str, dict[str, Any]]],
) -> None:
    if not entries:
        return
    journal_path = get_checkpoint_journal_path(file_path)
    with _JOURNAL_LOCK:
        with open(journal_path, "a", encoding="utf-8", newline="") as stream:
            for key, entry in entries:
                stream.write(json.dumps(
                    {"key": key, "entry": entry},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ))
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())


def _review_reasons(status: str, issues: list[dict[str, Any]] | None) -> list[str]:
    reasons = [str(issue.get("type", "translation_issue")) for issue in issues or [] if isinstance(issue, dict)]
    if status == "review_required" and not reasons:
        reasons.append("review_required")
    if status == "translated_needs_review" and not reasons:
        reasons.append("validation_issue")
    return reasons


def load_progress(file_path: str) -> dict[tuple[int, int], dict[str, Any]]:
    data = load_checkpoint(file_path)
    result: dict[tuple[int, int], dict[str, Any]] = {}
    for key, entry in data.get("entries", {}).items():
        try:
            r, c = key.split("_", 1)
            result[(int(r), int(c))] = dict(entry)
        except (ValueError, TypeError):
            continue
    return result


def get_entry(file_path: str, row: int, col: int) -> dict[str, Any] | None:
    return load_checkpoint(file_path).get("entries", {}).get(f"{row}_{col}")


def is_translated(file_path: str, row: int, col: int) -> bool:
    entry = get_entry(file_path, row, col) or {}
    return is_completed_status(str(entry.get("status", "")))


def get_translated_count(file_path: str) -> int:
    return load_checkpoint(file_path).get("stats", {}).get("completed", 0)


def get_total_count(file_path: str) -> int:
    return load_checkpoint(file_path).get("stats", {}).get("total", 0)


def clear_checkpoint(file_path: str, include_glossary: bool = True) -> list[str]:
    deleted: list[str] = []
    checkpoint_path = get_checkpoint_path(file_path)
    for path in [
        checkpoint_path,
        get_checkpoint_journal_path(file_path),
        get_glossary_path(file_path) if include_glossary else "",
    ]:
        if path and os.path.exists(path):
            os.remove(path)
            deleted.append(path)
    _CONTEXT_CACHE.pop(checkpoint_path, None)
    return deleted


def merge_checkpoints(file_path: str, new_data: dict[tuple[int, int], dict[str, Any]]) -> None:
    data = load_checkpoint(file_path)
    for (row, col), entry in new_data.items():
        data.setdefault("entries", {})[f"{row}_{col}"] = {
            "row": row,
            "col": col,
            "original": entry.get("original", ""),
            "translated": entry.get("translated", ""),
            "status": entry.get("status", "done"),
            "issues": entry.get("issues", []),
        }
    save_checkpoint(file_path, data)


def list_translation_sessions(*, include_completed: bool = True) -> list[dict[str, Any]]:
    _ensure_dir()
    sessions: list[dict[str, Any]] = []
    for path in Path(CHECKPOINT_DIR).glob("*.json"):
        if path.name.endswith(".glossary.json"):
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("version") != VERSION:
            continue
        _replay_journal(str(path), data)
        _normalize_loaded_entries(data)
        _update_checkpoint_stats(data)
        stats = data.get("stats", {})
        total = int(stats.get("total") or 0)
        completed = int(stats.get("completed") or 0)
        is_complete = bool(total and completed >= total)
        if is_complete and not include_completed:
            continue
        file_path = data.get("file_path", "")
        sessions.append(
            {
                "checkpoint_path": str(path),
                "file_path": file_path,
                "file_exists": bool(file_path and os.path.exists(file_path)),
                "file_name": data.get("file_name") or os.path.basename(file_path),
                "file_type": data.get("file_type", ""),
                "task_id": data.get("task_id", ""),
                "model": data.get("model", ""),
                "prompt_style": data.get("prompt_style", ""),
                "translate_columns": data.get("translate_columns", []),
                "completed": completed,
                "total": total,
                "updated_at": data.get("updated_at", ""),
                "status": "completed" if is_complete else "incomplete",
                "review_required": int(stats.get("failed") or 0),
                "token_usage": data.get("token_usage", {}),
            }
        )
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions


def list_recovery_sessions() -> list[dict[str, Any]]:
    return list_translation_sessions(include_completed=False)
