"""Select a scheduler; policies and persistence are shared by both paths."""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from translation.workflow.parallel_event import _translate_json_batched_event_workflow
from translation.workflow.parallel_phased import translate_phased_batches

# Stable imports for existing diagnostics and regression callers.
from translation.workflow.parallel_support import (
    _finish_api_batch_result,
    _finish_parent_first_result,
    _fast_fallback_group,
    _parent_repair_child_payload,
)
from translation.workflow.parallel_repair import _event_quality_followups

if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline

ProgressCallback = Callable[[dict[str, Any]], None]


def translate_json_batched_parallel_workflow(
    pipeline: TranslationPipeline,
    file_path: str,
    translated_items: list[tuple[Any, Any]],
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    target_path: str,
    total_targets: int,
    progress_callback: ProgressCallback | None,
    batch_size: int,
    max_batch_chars: int,
    batch_options: dict[str, Any],
    configured_protocol: str,
    batch_protocol: str,
    batch_cfg: dict[str, Any],
) -> list[tuple[Any, Any]]:
    args = (pipeline, file_path, translated_items, mtool, completed, target_path,
            total_targets, progress_callback, batch_size, max_batch_chars,
            batch_options, configured_protocol, batch_protocol, batch_cfg)
    if batch_cfg.get("api_event_driven_enabled", False):
        return _translate_json_batched_event_workflow(*args)
    return translate_phased_batches(*args)


__all__ = ["translate_json_batched_parallel_workflow"]
