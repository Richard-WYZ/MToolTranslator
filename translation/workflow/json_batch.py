from __future__ import annotations

from typing import Any, Callable

import translation.checkpoint as checkpoint
from translation.batching import apply_batch_translation_results, default_batch_options
from translation.config import batch_translation_config
from translation.output import write_json_items
from translation.workflow.composition import finalize_mtool_compositions


ProgressCallback = Callable[[dict[str, Any]], None]


def translate_json_batched_workflow(
    pipeline: Any,
    file_path: str,
    translated_items: list[tuple[Any, Any]],
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    target_path: str,
    total_targets: int,
    progress_callback: ProgressCallback | None,
) -> list[tuple[Any, Any]]:
    processed_targets = 0
    deferred_confirmed_terms: dict[tuple[str, str], dict[str, Any]] = {}
    batch_cfg = pipeline._batch_translation_config()
    batch_size = max(1, int(batch_cfg.get("json_batch_size", 40)))
    max_batch_chars = max(500, int(batch_cfg.get("max_batch_chars", 12000)))
    batch_options = default_batch_options(batch_cfg)
    configured_protocol = str(batch_cfg.get("protocol", "json")).lower()
    batch_protocol = pipeline._resolve_batch_protocol(
        configured_protocol,
        translated_items,
        mtool,
        completed,
    )
    if pipeline._uses_api_parallel_batches(batch_cfg):
        return pipeline._translate_json_batched_parallel(
            file_path,
            translated_items,
            mtool,
            completed,
            target_path,
            total_targets,
            progress_callback,
            batch_size,
            max_batch_chars,
            batch_options,
            configured_protocol,
            batch_protocol,
            batch_cfg,
        )

    try:
        idx = 0
        while idx < len(translated_items):
            pipeline._check_control_flags()
            candidates, next_idx, processed_targets = pipeline._collect_json_batch_window(
                translated_items,
                idx,
                mtool,
                completed,
                batch_size,
                max_batch_chars,
                file_path,
                total_targets,
                processed_targets,
                progress_callback,
            )
            if not candidates:
                idx = next_idx
                continue
            for candidate in candidates:
                pipeline._emit_progress(
                    progress_callback,
                    file_path,
                    candidate["idx"],
                    0,
                    "translating",
                    processed_targets,
                    total_targets,
                    original_text=candidate["source"],
                )

            translated_payloads = pipeline._translate_json_candidates(
                candidates,
                file_path,
                batch_options,
                pipeline._resolve_candidate_batch_protocol(configured_protocol, batch_protocol, candidates),
            )

            progress_records: list[dict[str, Any]] = []
            processed_targets, glossary_changed = apply_batch_translation_results(
                candidates=candidates,
                translated_payloads=translated_payloads,
                translated_items=translated_items,
                processed_targets=processed_targets,
                total_targets=total_targets,
                progress_callback=progress_callback,
                file_path=file_path,
                mtool=mtool,
                progress_records=progress_records,
                glossary=pipeline.glossary,
                mark_dirty=pipeline._writer.mark_dirty,
                emit_progress=pipeline._emit_progress,
                progress_status=pipeline._progress_status,
                apply_confirmed_terms_to_outputs=lambda _file_path, terms: deferred_confirmed_terms.update({
                    (str(term.get("source", "")), str(term.get("target", ""))): term
                    for term in terms
                }),
            )

            checkpoint.save_progress_many(file_path, progress_records)
            if glossary_changed:
                pipeline.glossary.save()
            idx = next_idx
    finally:
        if deferred_confirmed_terms:
            pipeline._apply_confirmed_terms_to_outputs(file_path, list(deferred_confirmed_terms.values()))
            pipeline.glossary.save()
        processed_targets = finalize_mtool_compositions(
            pipeline,
            file_path=file_path,
            translated_items=translated_items,
            processed_targets=processed_targets,
            total_targets=total_targets,
            progress_callback=progress_callback,
        )
        checkpoint.set_glossary_version(file_path, pipeline.glossary.version(), update_entries=True)
        pipeline._update_token_usage(file_path)
        if pipeline._writer:
            pipeline._writer.stop()

    write_json_items(translated_items, target_path)
    return translated_items


__all__ = ["translate_json_batched_workflow"]
