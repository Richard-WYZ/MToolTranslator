from __future__ import annotations

from typing import Any, Callable


ProgressCallback = Callable[..., None]


def build_progress_payload(
    *,
    file_path: str,
    row_idx: int,
    col_idx: int,
    status: str,
    processed: int,
    total: int,
    original_text: str = "",
    translated_text: str = "",
) -> dict[str, Any]:
    """Build the standard progress payload shared by workflow runners."""
    return {
        "file": file_path,
        "row": row_idx,
        "col": col_idx,
        "status": status,
        "processed": processed,
        "total": total,
        "percent": 100 if total == 0 else round(processed * 100 / total, 2),
        "original_text": original_text[:200] if original_text else "",
        "translated_text": translated_text[:200] if translated_text else "",
    }


def emit_progress(
    progress_callback: ProgressCallback | None,
    *,
    file_path: str,
    row_idx: int,
    col_idx: int,
    status: str,
    processed: int,
    total: int,
    original_text: str = "",
    translated_text: str = "",
) -> None:
    """Emit progress while preserving the legacy positional callback fallback."""
    if not progress_callback:
        return
    payload = build_progress_payload(
        file_path=file_path,
        row_idx=row_idx,
        col_idx=col_idx,
        status=status,
        processed=processed,
        total=total,
        original_text=original_text,
        translated_text=translated_text,
    )
    try:
        progress_callback(payload)
    except TypeError:
        progress_callback(row_idx, col_idx, status)


__all__ = ["ProgressCallback", "build_progress_payload", "emit_progress"]
