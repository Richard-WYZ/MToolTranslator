from __future__ import annotations

import os
from typing import Any, Callable, TYPE_CHECKING

import translation.usage as token_usage
from translation.input import load_json_items
from translation.models.transport import connection_scope


if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


ProgressCallback = Callable[[dict[str, Any]], None]


def translate_file_for_pipeline(
    pipeline: TranslationPipeline,
    file_path: str,
    output_path: str | None = None,
    progress_callback: ProgressCallback | None = None,
    translate_columns: list[int] | None = None,
) -> Any:
    # Controls may already be requested while Runtime is being constructed.
    # A new pipeline starts with clear events; entry must not erase requests
    # delivered between construction and the first translation stage.
    pipeline._check_control_flags()
    pipeline._usage_tracker.reset()
    pipeline._token_usage = pipeline._usage_tracker.snapshot()

    ext = os.path.splitext(file_path)[1].lower()
    if ext != ".json":
        raise ValueError("Only MTool-style JSON files are supported")
    items = load_json_items(file_path)
    if not pipeline._is_mtool_json(items):
        raise ValueError("Only flat MTool-style JSON mappings are supported")
    with token_usage.use_tracker(pipeline._usage_tracker), connection_scope():
        glossary_was_frozen = pipeline.glossary.frozen
        try:
            return pipeline._translate_json(file_path, output_path, progress_callback)
        finally:
            # Also cover failures during workflow setup, before the stage's
            # own finalization block has been entered.
            try:
                if pipeline._writer:
                    pipeline._writer.stop()
            finally:
                if not glossary_was_frozen:
                    pipeline.glossary.thaw()


__all__ = ["translate_file_for_pipeline"]
