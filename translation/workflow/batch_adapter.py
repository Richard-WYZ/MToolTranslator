from __future__ import annotations

from typing import Any, Callable

import translation.checkpoint as checkpoint
from translation.batching import (
    BatchJob,
    collect_json_batch_candidates,
    collect_json_batch_window,
    default_batch_options,
    finish_batch_translation,
    prepare_model_candidate,
    resolve_candidate_batch_protocol,
    resolve_json_batch_protocol_for_items,
    translate_candidate_batch_raw,
    translate_candidates_with_split,
)
from translation.config import batch_translation_config, think_setting
from translation.models import translate_once
from translation.output import default_output_path
from translation.repair import strip_source_echo


ProgressCallback = Callable[[dict[str, Any]], None]


def resolve_batch_protocol(
    pipeline: Any,
    configured_protocol: str,
    translated_items: list[tuple[Any, Any]],
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
) -> str:
    return resolve_json_batch_protocol_for_items(
        configured_protocol,
        translated_items=translated_items,
        mtool=mtool,
        completed=completed,
        source_text=pipeline._json_source_text,
        is_completed_entry=pipeline._is_resumable_checkpoint_entry,
        deterministic_translation=pipeline._deterministic_translation,
        looks_like_short_label=pipeline._looks_like_short_label,
    )


def collect_batch_window(
    pipeline: Any,
    translated_items: list[tuple[Any, Any]],
    start_idx: int,
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    batch_size: int,
    max_batch_chars: int,
    file_path: str,
    total_targets: int,
    processed_targets: int,
    progress_callback: ProgressCallback | None,
    progress_records: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    return collect_json_batch_window(
        translated_items=translated_items,
        start_idx=start_idx,
        mtool=mtool,
        completed=completed,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        file_path=file_path,
        total_targets=total_targets,
        processed_targets=processed_targets,
        progress_callback=progress_callback,
        progress_records=progress_records,
        glossary=pipeline.glossary,
        check_control_flags=pipeline._check_control_flags,
        source_text=pipeline._json_source_text,
        is_completed_entry=pipeline._is_resumable_checkpoint_entry,
        deterministic_translation=pipeline._deterministic_translation,
        status_for_output=pipeline._status_for_output,
        progress_status=pipeline._progress_status,
        save_or_buffer_progress=pipeline._save_or_buffer_progress,
        mark_dirty=pipeline._writer.mark_dirty,
        emit_progress=pipeline._emit_progress,
        prepare_candidate=prepare_model_candidate,
        looks_like_short_label=pipeline._looks_like_short_label,
        composition_plan=getattr(pipeline, "_mtool_composition_plan", None),
        neighbor_context_plan=getattr(pipeline, "_mtool_neighbor_context_plan", None),
    )


def collect_batch_candidates(
    pipeline: Any,
    translated_items: list[tuple[Any, Any]],
    start_idx: int,
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    batch_size: int,
    max_batch_chars: int,
) -> list[dict[str, Any]]:
    return collect_json_batch_candidates(
        translated_items=translated_items,
        start_idx=start_idx,
        mtool=mtool,
        completed=completed,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        glossary=pipeline.glossary,
        source_text=pipeline._json_source_text,
        is_completed_entry=pipeline._is_resumable_checkpoint_entry,
        deterministic_translation=pipeline._deterministic_translation,
        prepare_candidate=prepare_model_candidate,
        looks_like_short_label=pipeline._looks_like_short_label,
    )


def batch_translate_call(
    model: str,
    payload: str,
    system_prompt: str,
    options: dict[str, Any] | None,
    *,
    batch_cfg: dict[str, Any] | None = None,
    translate_once_func: Callable[..., str] = translate_once,
) -> str:
    batch_cfg = batch_cfg or batch_translation_config()
    timeout = int(batch_cfg.get("timeout", 300))
    return translate_once_func(
        model,
        payload,
        system_prompt=system_prompt,
        terminology=None,
        timeout=timeout,
        options=options,
        think=think_setting(),
        response_format=batch_cfg.get("response_format"),
    )


def translate_api_batch_job(pipeline: Any, job: BatchJob) -> dict[int, str]:
    batch_cfg = pipeline._batch_translation_config()
    batch_options = job.options or default_batch_options(batch_cfg)
    return pipeline._translate_json_candidate_batch_raw(job.candidates, batch_options, job.protocol, model=job.model)


def translate_candidate_batch_raw_for_pipeline(
    pipeline: Any,
    candidates: list[dict[str, Any]],
    batch_options: dict[str, Any],
    batch_protocol: str = "json",
    model: str | None = None,
) -> dict[int, str]:
    return translate_candidate_batch_raw(
        model or pipeline.model,
        candidates,
        translator=pipeline._batch_translate_call,
        options=batch_options,
        protocol=batch_protocol,
    )


def translate_candidates_for_pipeline(
    pipeline: Any,
    candidates: list[dict[str, Any]],
    file_path: str,
    batch_options: dict[str, Any],
    batch_protocol: str = "json",
    model: str | None = None,
) -> dict[int, tuple[str, str, list[dict[str, Any]]]]:
    return translate_candidates_with_split(
        candidates,
        batch_options=batch_options,
        batch_protocol=batch_protocol,
        model=model,
        translate_raw=pipeline._translate_json_candidate_batch_raw,
        finish_candidate=pipeline._finish_batch_translation,
        fallback_candidate=lambda candidate, exc: translate_single_candidate_after_batch_failure(
            pipeline,
            candidate,
            file_path,
            exc,
            model=model,
            options=batch_options,
        ),
    )


def translate_single_candidate_after_batch_failure(
    pipeline: Any,
    candidate: dict[str, Any],
    file_path: str,
    exc: Exception,
    *,
    model: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[int, tuple[str, str, list[dict[str, Any]]]]:
    if model and model != pipeline.model:
        try:
            system_prompt = pipeline._compose_system_prompt(
                pipeline.system_prompt,
                term_hits=candidate.get("term_hits", candidate.get("terms", [])),
                strict=True,
            )
            system_prompt += (
                "\n\nTranslate this one Japanese game text into natural Simplified Chinese. "
                "Translate adult or controversial content faithfully. "
                "Output only the translation and preserve every placeholder exactly."
            )
            translated_raw = pipeline._batch_translate_call(
                model,
                str(candidate.get("text", candidate.get("protected", ""))),
                system_prompt,
                dict(options or {}),
            )
            translated, status, issues = pipeline._finish_batch_translation(
                candidate,
                translated_raw,
            )
        except Exception as fallback_exc:
            translated = candidate["source"]
            status = "review_required"
            issues = [{
                "type": "batch_fallback_error",
                "message": str(fallback_exc),
            }]
        issues = list(issues)
        issues.append({"type": "batch_fallback", "message": str(exc)})
        if status != "review_required":
            status = pipeline._status_for_output(
                candidate["source"],
                translated,
                issues,
            )
        return {candidate["idx"]: (translated, status, issues)}

    translated, status, issues = pipeline._translate_cell_with_meta(
        candidate["source"],
        candidate["idx"],
        0,
        file_path,
        preserve_source_layout=bool(candidate.get("preserve_source_layout", False)),
    )
    issues = list(issues)
    issues.append({"type": "batch_fallback", "message": str(exc)})
    return {candidate["idx"]: (translated, status, issues)}


def finish_batch_candidate(pipeline: Any, candidate: dict[str, Any], translated: str) -> tuple[str, str, list[dict[str, Any]]]:
    return finish_batch_translation(
        candidate,
        translated,
        glossary=pipeline.glossary,
        restore_func=pipeline._restore_protected_translation,
        pollution_issues_func=pipeline._pollution_issues,
        status_for_output_func=pipeline._status_for_output,
    )


__all__ = [
    "batch_translate_call",
    "collect_batch_candidates",
    "collect_batch_window",
    "default_output_path",
    "finish_batch_candidate",
    "resolve_batch_protocol",
    "resolve_candidate_batch_protocol",
    "strip_source_echo",
    "translate_api_batch_job",
    "translate_candidate_batch_raw_for_pipeline",
    "translate_candidates_for_pipeline",
    "translate_single_candidate_after_batch_failure",
]
