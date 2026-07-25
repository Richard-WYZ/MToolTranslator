from __future__ import annotations

import os
from typing import Any, Callable

import translation.usage as token_usage
from translation.input import load_json_items


ProgressCallback = Callable[[dict[str, Any]], None]


def translate_file_for_pipeline(
    pipeline: Any,
    file_path: str,
    output_path: str | None = None,
    progress_callback: ProgressCallback | None = None,
    translate_columns: list[int] | None = None,
) -> Any:
    pipeline._cancel_event.clear()
    pipeline._pause_event.clear()
    token_usage.reset()
    pipeline._token_usage = token_usage.snapshot()

    ext = os.path.splitext(file_path)[1].lower()
    if ext != ".json":
        raise ValueError("Only MTool-style JSON files are supported")
    items = load_json_items(file_path)
    if not pipeline._is_mtool_json(items):
        raise ValueError("Only flat MTool-style JSON mappings are supported")
    return pipeline._translate_json(file_path, output_path, progress_callback)


__all__ = ["translate_file_for_pipeline"]
