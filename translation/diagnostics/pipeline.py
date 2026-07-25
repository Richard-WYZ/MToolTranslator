from __future__ import annotations

from typing import Any

from translation.workflow.pipeline import TranslationPipeline
from translation.terminology import Glossary


def build_diagnostic_pipeline(
    *,
    model: str | None = None,
    glossary_path: str | None = None,
) -> TranslationPipeline:
    """Build a pipeline facade for profiling and benchmark diagnostics."""
    glossary = Glossary(file_path=glossary_path) if glossary_path else None
    return TranslationPipeline(model=model, glossary=glossary)


def diagnostic_glossary(pipeline: TranslationPipeline) -> Glossary:
    """Return the glossary attached to a diagnostic pipeline."""
    return pipeline.glossary


def diagnostic_batch_translator(pipeline: TranslationPipeline):
    """Return the batch-call adapter used by throughput diagnostics."""
    return pipeline._batch_translate_call


def finish_diagnostic_batch_translation(
    pipeline: TranslationPipeline,
    candidate: dict[str, Any],
    translated: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Run batch-output finishing for diagnostic reports."""
    return pipeline._finish_batch_translation(candidate, translated)


__all__ = [
    "build_diagnostic_pipeline",
    "diagnostic_batch_translator",
    "diagnostic_glossary",
    "finish_diagnostic_batch_translation",
]
