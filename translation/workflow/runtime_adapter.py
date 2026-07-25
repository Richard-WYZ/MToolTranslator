from __future__ import annotations

from typing import Any, Callable

import translation.checkpoint as checkpoint
import translation.usage as token_usage
from translation.control import check_control_flags
from translation.output import default_output_path
from translation.progress import emit_progress
from translation.terminology import backfill_confirmed_terms_to_outputs


ProgressCallback = Callable[[dict[str, Any]], None]


def update_token_usage(pipeline: Any, file_path: str | None = None) -> dict[str, Any]:
    pipeline._token_usage = token_usage.snapshot()
    if file_path:
        checkpoint.set_token_usage(file_path, pipeline._token_usage)
    return pipeline._token_usage


def save_or_buffer_progress(
    file_path: str,
    progress_records: list[dict[str, Any]] | None,
    **record: Any,
) -> None:
    checkpoint.save_or_buffer_progress(file_path, progress_records, **record)


def is_resumable_checkpoint_entry(pipeline: Any, entry: dict[str, Any] | None, source: str) -> bool:
    return checkpoint.is_resumable_entry(entry, source=source, **pipeline._resume_context)


def pause(pipeline: Any) -> None:
    pipeline._pause_event.set()


def resume(pipeline: Any) -> None:
    pipeline._pause_event.clear()


def cancel(pipeline: Any) -> None:
    pipeline._cancel_event.set()
    pipeline._pause_event.clear()


def flush_writer(pipeline: Any) -> None:
    if pipeline._writer:
        pipeline._writer.flush()


def update_output_cell(pipeline: Any, row_idx: int, col_idx: int, text: str) -> bool:
    writer = pipeline._writer
    if not writer:
        return False
    return writer.update_cell(row_idx, col_idx, text)


def apply_confirmed_terms_to_outputs(pipeline: Any, file_path: str, confirmed_terms: list[dict[str, Any]]) -> None:
    backfill_confirmed_terms_to_outputs(
        file_path,
        confirmed_terms,
        update_output_cell=pipeline.update_output_cell,
        glossary_mappings=pipeline._glossary_mappings_for_quality(),
    )


def check_pipeline_control_flags(pipeline: Any, cancelled_factory: Callable[[], Exception]) -> None:
    check_control_flags(
        is_cancelled=pipeline._cancel_event.is_set,
        is_paused=pipeline._pause_event.is_set,
        cancelled_factory=cancelled_factory,
    )


def emit_pipeline_progress(
    progress_callback: ProgressCallback | None,
    file_path: str,
    row_idx: int,
    col_idx: int,
    status: str,
    processed: int,
    total: int,
    original_text: str = "",
    translated_text: str = "",
) -> None:
    emit_progress(
        progress_callback,
        file_path=file_path,
        row_idx=row_idx,
        col_idx=col_idx,
        status=status,
        processed=processed,
        total=total,
        original_text=original_text,
        translated_text=translated_text,
    )


__all__ = [
    "apply_confirmed_terms_to_outputs",
    "cancel",
    "check_pipeline_control_flags",
    "default_output_path",
    "emit_pipeline_progress",
    "flush_writer",
    "is_resumable_checkpoint_entry",
    "pause",
    "resume",
    "save_or_buffer_progress",
    "update_output_cell",
    "update_token_usage",
]
