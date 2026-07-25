from __future__ import annotations

import translation.checkpoint as checkpoint
from translation.context import TranslationRequest, TranslationResult, TranslationWorkflowContext
from translation.models import get_system_prompts
from translation.terminology import Glossary
from translation.workflow.pipeline import TranslationPipeline


PIPELINE_RESOURCE = "pipeline"


def build_pipeline(
    request: TranslationRequest,
    *,
    glossary_path: str | None = None,
) -> TranslationPipeline:
    """Build the runtime pipeline owned by the translation workflow."""
    prompts = get_system_prompts()
    system_prompt = prompts.get(request.prompt_style, prompts.get("professional", ""))
    resolved_glossary_path = glossary_path or request.glossary_path or checkpoint.get_glossary_path(request.file_path)
    return TranslationPipeline(
        model=request.model,
        system_prompt=system_prompt,
        glossary=Glossary(file_path=resolved_glossary_path),
        prompt_style=request.prompt_style,
        task_id=request.task_id,
        batch_config_override=request.batch_config_override,
    )


class PipelineBuildStage:
    """Create runtime translation services after input analysis."""

    name = "pipeline_build"

    def build_pipeline(self, context: TranslationWorkflowContext) -> TranslationPipeline:
        glossary_path = context.analysis.get("glossary_path")
        return build_pipeline(context.request, glossary_path=glossary_path)

    def run(self, context: TranslationWorkflowContext) -> TranslationWorkflowContext:
        if PIPELINE_RESOURCE not in context.resources:
            context.resources[PIPELINE_RESOURCE] = self.build_pipeline(context)
        return context


class TranslationStage:
    """Execute translation using the pipeline prepared by the workflow."""

    name = "translation"

    def build_pipeline(self, request: TranslationRequest) -> TranslationPipeline:
        return build_pipeline(request)

    def translate(self, request: TranslationRequest, pipeline: TranslationPipeline | None = None) -> TranslationResult:
        pipeline = pipeline or self.build_pipeline(request)
        items = pipeline.translate_file(
            request.file_path,
            output_path=request.output_path,
            progress_callback=request.progress_callback,
            translate_columns=request.translate_columns,
        )
        return TranslationResult(file_path=request.file_path, output_path=request.output_path, items=items)

    def run(self, context: TranslationWorkflowContext) -> TranslationWorkflowContext:
        pipeline = context.resources.get(PIPELINE_RESOURCE)
        context.result = self.translate(context.request, pipeline=pipeline)
        return context


__all__ = ["PIPELINE_RESOURCE", "PipelineBuildStage", "TranslationStage", "build_pipeline"]
