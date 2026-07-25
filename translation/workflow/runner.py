from __future__ import annotations

from collections.abc import Iterable

from translation.context import TranslationWorkflowContext
from translation.workflow.stage import Stage


class TranslationWorkflow:
    """Sequential workflow runner for translation stages."""

    def __init__(self, stages: Iterable[Stage[TranslationWorkflowContext]]) -> None:
        self.stages = list(stages)

    def run(self, context: TranslationWorkflowContext) -> TranslationWorkflowContext:
        current = context
        for stage in self.stages:
            current = stage.run(current)
        return current


__all__ = ["TranslationWorkflow"]
