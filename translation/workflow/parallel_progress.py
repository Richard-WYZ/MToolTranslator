"""Shared acceptance and durable finalization for both parallel schedulers."""
from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from translation import checkpoint
from translation.batching import apply_batch_translation_results
from translation.workflow.composition import finalize_mtool_compositions

if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


def apply_parallel_results(
    *,
    pipeline: TranslationPipeline,
    candidates: list[dict[str, Any]],
    translated_payloads: dict[int, tuple[str, str, list[dict[str, Any]]]],
    translated_items: list[tuple[Any, Any]],
    processed_targets: int,
    total_targets: int,
    progress_callback: Callable | None,
    file_path: str,
    mtool: bool,
    progress_records: list[dict[str, Any]],
    deferred_confirmed_terms: dict[tuple[str, str], dict[str, Any]],
    batch_id: str,
    model_identifier: str,
    retry_count: int | None = None,
) -> int:
    def defer_terms(_path: str, terms: list[dict[str, Any]]) -> None:
        for term in terms:
            deferred_confirmed_terms[(str(term.get("source", "")), str(term.get("target", "")))] = term

    processed_targets, glossary_changed = apply_batch_translation_results(
        candidates=candidates, translated_payloads=translated_payloads,
        translated_items=translated_items, processed_targets=processed_targets,
        total_targets=total_targets, progress_callback=progress_callback,
        file_path=file_path, mtool=mtool, progress_records=progress_records,
        glossary=pipeline.glossary, mark_dirty=pipeline._writer.mark_dirty,
        emit_progress=pipeline._emit_progress, progress_status=pipeline._progress_status,
        apply_confirmed_terms_to_outputs=defer_terms,
        batch_id=batch_id, model_identifier=model_identifier, retry_count=retry_count,
    )
    if len(progress_records) >= 1000:
        checkpoint.save_progress_many(file_path, progress_records)
        progress_records.clear()
    if glossary_changed:
        pipeline.glossary.save()
    return processed_targets


def finalize_parallel_run(
    pipeline: TranslationPipeline, *, file_path: str,
    translated_items: list[tuple[Any, Any]], processed_targets: int,
    total_targets: int, progress_callback: Callable | None,
    collection_records: list[dict[str, Any]], result_records: list[dict[str, Any]],
    deferred_confirmed_terms: dict[tuple[str, str], dict[str, Any]],
) -> int:
    # Writer termination is unconditional even if checkpoint/report persistence
    # fails. Task cleanup can then rely on worker termination as a write barrier.
    try:
        checkpoint.save_progress_many(file_path, collection_records)
        collection_records.clear()
        checkpoint.save_progress_many(file_path, result_records)
        result_records.clear()
        if deferred_confirmed_terms:
            pipeline._apply_confirmed_terms_to_outputs(file_path, list(deferred_confirmed_terms.values()))
            pipeline.glossary.save()
        processed_targets = finalize_mtool_compositions(
            pipeline, file_path=file_path, translated_items=translated_items,
            processed_targets=processed_targets, total_targets=total_targets,
            progress_callback=progress_callback,
        )
        checkpoint.set_glossary_version(file_path, pipeline.glossary.version(), update_entries=True)
        pipeline._update_token_usage(file_path)
        return processed_targets
    finally:
        if pipeline._writer:
            pipeline._writer.stop()
