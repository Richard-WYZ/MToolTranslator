from __future__ import annotations

from dataclasses import replace
from typing import Any

from translation.context import TranslationRequest, TranslationResult, TranslationWorkflowContext
from translation.translate import build_pipeline, build_workflow
from translation.workflow.execution import PIPELINE_RESOURCE


class TranslationRuntime:
    """Runtime control adapter for an active translation pipeline.

    Application tasks need pause/resume/cancel/progress coordination, but they
    should not depend on the concrete pipeline API or private methods.
    """

    def __init__(self, request: TranslationRequest) -> None:
        self.request = request
        self._pipeline = build_pipeline(request)

    def translate_file(self, *, progress_callback, translate_columns: list[int] | None) -> TranslationResult:
        request = replace(
            self.request,
            progress_callback=progress_callback,
            translate_columns=translate_columns,
        )
        context = TranslationWorkflowContext(
            request=request,
            resources={PIPELINE_RESOURCE: self._pipeline},
        )
        completed = build_workflow().run(context)
        if completed.result is None:
            raise RuntimeError("Translation workflow completed without a result")
        return completed.result

    def token_usage(self) -> dict:
        # Progress reads must never persist the checkpoint. The translation
        # workflow performs the final durable usage write after model work.
        return self._pipeline.token_usage()

    def pause(self) -> None:
        self._pipeline.pause()

    def resume(self) -> None:
        self._pipeline.resume()

    def cancel(self) -> None:
        self._pipeline.cancel()

    def flush_writer(self) -> None:
        self._pipeline.flush_writer()

    def update_output_cell(self, row: int, col: int, text: str) -> bool:
        return bool(self._pipeline.update_output_cell(row, col, text))

    def replace_glossary(self, glossary: Any) -> None:
        self._pipeline.replace_glossary(glossary)


__all__ = ["TranslationRuntime"]
