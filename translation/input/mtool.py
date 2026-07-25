from __future__ import annotations

from typing import Any


def is_mtool_items(items: list[tuple[Any, Any]]) -> bool:
    """Return whether parsed JSON items match the flat MTool mapping contract."""
    if not items:
        return False
    return all(isinstance(key, str) and isinstance(value, str) for key, value in items)


def source_text(key: Any, value: Any, *, mtool: bool) -> str:
    """Return the authoritative source text for a parsed JSON entry."""
    if mtool and isinstance(key, str):
        return key
    if isinstance(value, str):
        return value
    return ""


def original_text(key: Any, value: Any, cp_entry: dict | None = None, *, mtool: bool = False) -> str:
    """Return source text for review, preferring checkpoint source when needed."""
    if mtool and isinstance(key, str) and isinstance(value, str):
        return key
    if isinstance(cp_entry, dict) and cp_entry.get("original"):
        return str(cp_entry.get("original", ""))
    if isinstance(value, str) and value.strip():
        return value
    return value if isinstance(value, str) else ""


__all__ = ["is_mtool_items", "original_text", "source_text"]
