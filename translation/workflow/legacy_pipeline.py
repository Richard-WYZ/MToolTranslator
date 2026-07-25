"""Compatibility aliases for workflow names used before the package split."""

from translation.workflow.execution import PIPELINE_RESOURCE, PipelineBuildStage, TranslationStage, build_pipeline
from translation.workflow.pipeline import TranslationCancelled


LEGACY_PIPELINE_RESOURCE = PIPELINE_RESOURCE
LegacyPipelineBuildStage = PipelineBuildStage
LegacyPipelineStage = TranslationStage
build_legacy_pipeline = build_pipeline


__all__ = [
    "LEGACY_PIPELINE_RESOURCE",
    "LegacyPipelineBuildStage",
    "LegacyPipelineStage",
    "TranslationCancelled",
    "build_legacy_pipeline",
]
