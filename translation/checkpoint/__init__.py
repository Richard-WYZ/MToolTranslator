"""Checkpoint persistence and resume helpers."""

from typing import Any

from translation.checkpoint import store as _store
from translation.checkpoint.context import build_prompt_version, build_resume_model_configuration, stable_fingerprint


CHECKPOINT_DIR = _store.CHECKPOINT_DIR
VERSION = _store.VERSION
FINAL_STATUSES = _store.FINAL_STATUSES
COMPLETED_STATUSES = _store.COMPLETED_STATUSES
LEGACY_STATUS_MAP = _store.LEGACY_STATUS_MAP

_SYNCED_CHECKPOINT_DIR = CHECKPOINT_DIR


def _sync_checkpoint_dir() -> None:
    global CHECKPOINT_DIR, _SYNCED_CHECKPOINT_DIR
    facade_dir = CHECKPOINT_DIR
    store_dir = _store.CHECKPOINT_DIR
    if facade_dir != _SYNCED_CHECKPOINT_DIR:
        _store.CHECKPOINT_DIR = facade_dir
        _SYNCED_CHECKPOINT_DIR = facade_dir
        return
    if store_dir != _SYNCED_CHECKPOINT_DIR:
        CHECKPOINT_DIR = store_dir
        _SYNCED_CHECKPOINT_DIR = store_dir
        return
    if store_dir != facade_dir:
        _store.CHECKPOINT_DIR = facade_dir
    _SYNCED_CHECKPOINT_DIR = facade_dir


normalize_status = _store.normalize_status
is_completed_status = _store.is_completed_status
is_completed_entry = _store.is_completed_entry
is_resumable_entry = _store.is_resumable_entry


def get_checkpoint_path(file_path: str) -> str:
    _sync_checkpoint_dir()
    return _store.get_checkpoint_path(file_path)


def get_glossary_path(file_path: str) -> str:
    _sync_checkpoint_dir()
    return _store.get_glossary_path(file_path)


def get_checkpoint_journal_path(file_path: str) -> str:
    _sync_checkpoint_dir()
    return _store.get_checkpoint_journal_path(file_path)


def load_checkpoint(file_path: str) -> dict[str, Any]:
    _sync_checkpoint_dir()
    return _store.load_checkpoint(file_path)


def save_checkpoint(file_path: str, data: dict[str, Any]) -> None:
    _sync_checkpoint_dir()
    _store.save_checkpoint(file_path, data)


def init_checkpoint(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _sync_checkpoint_dir()
    return _store.init_checkpoint(*args, **kwargs)


def set_token_usage(file_path: str, token_usage: dict[str, Any]) -> None:
    _sync_checkpoint_dir()
    _store.set_token_usage(file_path, token_usage)


def set_glossary_version(file_path: str, glossary_version: str, *, update_entries: bool = False) -> None:
    _sync_checkpoint_dir()
    _store.set_glossary_version(file_path, glossary_version, update_entries=update_entries)


def save_progress(*args: Any, **kwargs: Any) -> None:
    _sync_checkpoint_dir()
    _store.save_progress(*args, **kwargs)


def save_progress_many(file_path: str, records: list[dict[str, Any]]) -> None:
    _sync_checkpoint_dir()
    _store.save_progress_many(file_path, records)


def load_progress(file_path: str) -> dict[tuple[int, int], dict[str, Any]]:
    _sync_checkpoint_dir()
    return _store.load_progress(file_path)


def get_entry(file_path: str, row: int, col: int) -> dict[str, Any] | None:
    _sync_checkpoint_dir()
    return _store.get_entry(file_path, row, col)


def is_translated(file_path: str, row: int, col: int) -> bool:
    _sync_checkpoint_dir()
    return _store.is_translated(file_path, row, col)


def get_translated_count(file_path: str) -> int:
    _sync_checkpoint_dir()
    return _store.get_translated_count(file_path)


def get_total_count(file_path: str) -> int:
    _sync_checkpoint_dir()
    return _store.get_total_count(file_path)


def clear_checkpoint(file_path: str, include_glossary: bool = True) -> list[str]:
    _sync_checkpoint_dir()
    return _store.clear_checkpoint(file_path, include_glossary=include_glossary)


def merge_checkpoints(file_path: str, new_data: dict[tuple[int, int], dict[str, Any]]) -> None:
    _sync_checkpoint_dir()
    _store.merge_checkpoints(file_path, new_data)


def list_recovery_sessions() -> list[dict[str, Any]]:
    _sync_checkpoint_dir()
    return _store.list_recovery_sessions()


def list_translation_sessions(*, include_completed: bool = True) -> list[dict[str, Any]]:
    _sync_checkpoint_dir()
    return _store.list_translation_sessions(include_completed=include_completed)


def save_or_buffer_progress(
    file_path: str,
    progress_records: list[dict[str, Any]] | None,
    **record: Any,
) -> None:
    """Append a progress record to a buffer or write it immediately."""
    if progress_records is not None:
        progress_records.append(record)
        return
    _sync_checkpoint_dir()
    _store.save_progress(
        file_path,
        int(record["row"]),
        int(record["col"]),
        str(record["original"]),
        str(record["translated"]),
        status=str(record.get("status", "translated")),
        issues=record.get("issues", []) or [],
        **{
            key: value
            for key, value in record.items()
            if key not in {"row", "col", "original", "translated", "status", "issues"}
        },
    )


__all__ = [
    "CHECKPOINT_DIR",
    "COMPLETED_STATUSES",
    "FINAL_STATUSES",
    "LEGACY_STATUS_MAP",
    "VERSION",
    "clear_checkpoint",
    "build_prompt_version",
    "build_resume_model_configuration",
    "get_checkpoint_path",
    "get_checkpoint_journal_path",
    "get_entry",
    "get_glossary_path",
    "get_total_count",
    "get_translated_count",
    "init_checkpoint",
    "is_completed_entry",
    "is_resumable_entry",
    "is_completed_status",
    "is_translated",
    "list_recovery_sessions",
    "load_checkpoint",
    "load_progress",
    "list_translation_sessions",
    "merge_checkpoints",
    "normalize_status",
    "save_checkpoint",
    "save_or_buffer_progress",
    "save_progress",
    "save_progress_many",
    "set_token_usage",
    "set_glossary_version",
    "stable_fingerprint",
]
