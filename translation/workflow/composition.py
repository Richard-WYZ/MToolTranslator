from __future__ import annotations

from typing import Any, Callable

import translation.checkpoint as checkpoint
from translation.analysis import apply_mtool_compositions


def finalize_mtool_compositions(
    pipeline: Any,
    *,
    file_path: str,
    translated_items: list[tuple[Any, Any]],
    processed_targets: int,
    total_targets: int,
    progress_callback: Callable[[dict[str, Any]], None] | None,
) -> int:
    """Apply deterministic multiline compositions after child outputs are final."""
    plan = getattr(pipeline, "_mtool_composition_plan", None)
    if plan is None or not plan.entries:
        return processed_targets

    records: list[dict[str, Any]] = []
    processed_targets = apply_mtool_compositions(
        plan,
        translated_items=translated_items,
        checkpoint_entries=checkpoint.load_progress(file_path),
        file_path=file_path,
        progress_records=records,
        processed_targets=processed_targets,
        total_targets=total_targets,
        progress_callback=progress_callback,
        save_record=pipeline._save_or_buffer_progress,
        mark_dirty=pipeline._writer.mark_dirty,
        emit_progress=pipeline._emit_progress,
        progress_status=pipeline._progress_status,
    )
    checkpoint.save_progress_many(file_path, records)
    return processed_targets


__all__ = ["finalize_mtool_compositions"]
