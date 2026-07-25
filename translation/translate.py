from __future__ import annotations

from typing import TYPE_CHECKING

from translation.context import TranslationRequest, TranslationResult, TranslationWorkflowContext
from translation.workflow.analysis import MToolAnalysisStage
from translation.workflow.execution import PipelineBuildStage, TranslationStage, build_pipeline as build_runtime_pipeline
from translation.workflow.pipeline import TranslationCancelled
from translation.workflow.review import ReviewPreparationStage
from translation.workflow.runner import TranslationWorkflow

if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


def build_pipeline(request: TranslationRequest) -> TranslationPipeline:
    """Build the concrete pipeline for a translation workflow request."""
    return build_runtime_pipeline(request)


def build_workflow() -> TranslationWorkflow:
    """Build the default translation workflow."""
    return TranslationWorkflow(
        [MToolAnalysisStage(), PipelineBuildStage(), TranslationStage(), ReviewPreparationStage()]
    )


def translate(request: TranslationRequest) -> TranslationResult:
    """Translation domain entrypoint.

    This function is intentionally a workflow facade. Workflow stages are added
    behind this entrypoint instead of being wired into UI or application code.
    """
    workflow = build_workflow()
    context = workflow.run(TranslationWorkflowContext(request=request))
    if context.result is None:
        raise RuntimeError("Translation workflow completed without a result")
    return context.result


__all__ = [
    "TranslationCancelled",
    "TranslationRequest",
    "TranslationResult",
    "TranslationWorkflow",
    "TranslationWorkflowContext",
    "build_pipeline",
    "build_workflow",
    "translate",
]
