from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

import translation.checkpoint as checkpoint
import translation.usage as token_usage
from translation.batching import (
    BatchJob,
    BatchTranslationError,
    ModelAdmissionPolicy,
    apply_batch_translation_results,
    candidate_needs_quality_model_retry,
    candidate_needs_sensitive_repair,
    pack_api_candidate_batches,
    prepare_model_candidate,
    reindex_candidates,
)
from translation.output import write_json_items
from translation.protection import protect_symbols
from translation.quality import new_issues, translation_issues
from translation.workflow.composition import finalize_mtool_compositions


ProgressCallback = Callable[[dict[str, Any]], None]
_STRUCTURAL_QUALITY_ISSUE_TYPES = frozenset({
    "line_break_preservation",
    "length_expansion",
    "short_label_expansion",
})


def _model_concurrency_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(model): max(1, int(limit))
        for model, limit in value.items()
        if str(model)
    }


def _build_model_admission_policy(
    batch_cfg: dict[str, Any],
) -> ModelAdmissionPolicy | None:
    if not batch_cfg.get("api_adaptive_concurrency_enabled", False):
        return None
    default_initial = max(1, int(batch_cfg.get("api_concurrency", 1)))
    default_maximum = max(
        default_initial,
        int(batch_cfg.get("api_adaptive_default_maximum", default_initial)),
    )
    return ModelAdmissionPolicy(
        initial_by_model=_model_concurrency_map(
            batch_cfg.get("api_model_concurrency_initial", {}),
        ),
        maximum_by_model=_model_concurrency_map(
            batch_cfg.get("api_model_concurrency_max", {}),
        ),
        maximum_inflight_chars_by_model=_model_concurrency_map(
            batch_cfg.get("api_model_inflight_chars_max", {}),
        ),
        default_initial=default_initial,
        default_maximum=default_maximum,
        default_maximum_inflight_chars=max(
            1,
            int(
                batch_cfg.get(
                    "api_default_inflight_chars_max",
                    40000,
                )
            ),
        ),
        increase_every=max(
            1,
            int(batch_cfg.get("api_concurrency_increase_every", 8)),
        ),
        decrease_factor=float(
            batch_cfg.get("api_concurrency_decrease_factor", 0.5),
        ),
    )


def _sensitive_repair_candidate(
    candidate: dict[str, Any],
    translated: str,
    issues: list[dict[str, Any]],
    *,
    model: str,
    repair_round: int,
    prior_retry_count: int,
) -> dict[str, Any]:
    """Attach bounded review metadata while preserving the original protected candidate."""
    previous = "" if repair_round >= 2 else translated
    return dict(
        candidate,
        quality_retry={
            "previous": previous,
            "issues": [
                str(issue.get("type", ""))
                for issue in issues
                if isinstance(issue, dict) and str(issue.get("type", ""))
            ],
        },
        sensitive_repair_model=model,
        sensitive_repair_round=max(1, int(repair_round)),
        sensitive_repair_prior_retry_count=max(0, int(prior_retry_count)),
    )


def _build_sensitive_repair_jobs(
    candidates: list[dict[str, Any]],
    *,
    repair_round: int,
    batch_options: dict[str, Any],
    batch_cfg: dict[str, Any],
) -> tuple[list[BatchJob], dict[str, BatchJob]]:
    """Pack one bounded same-model sensitive repair round."""
    jobs: list[BatchJob] = []
    job_map: dict[str, BatchJob] = {}
    batch_size = (
        max(1, int(batch_cfg.get("api_sensitive_repair_batch_size", 5)))
        if repair_round == 1
        else 1
    )
    max_batch_chars = max(
        1,
        int(batch_cfg.get("api_sensitive_repair_max_batch_chars", 1000)),
    )
    candidates_by_model: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        model = str(
            candidate.get("sensitive_repair_model")
            or batch_cfg.get("api_sensitive_model")
            or ""
        )
        if not model:
            continue
        candidates_by_model.setdefault(model, []).append(candidate)

    for model, model_candidates in candidates_by_model.items():
        for packed in pack_api_candidate_batches(
            model_candidates,
            batch_size=batch_size,
            max_batch_chars=max_batch_chars,
            batch_cfg=batch_cfg,
        ):
            batch_id = (
                f"api_sensitive_repair_r{repair_round}_{len(jobs):06d}"
            )
            job = BatchJob(
                batch_id=batch_id,
                candidates=packed,
                protocol="json",
                model=model,
                options=dict(batch_options),
            )
            jobs.append(job)
            job_map[batch_id] = job
    return jobs, job_map


def _repair_retry_count(candidate: dict[str, Any], attempts: int) -> int:
    """Count logical repair requests plus scheduler retries for checkpoint audit."""
    return (
        max(0, int(candidate.get("sensitive_repair_prior_retry_count", 0) or 0))
        + 1
        + max(0, int(attempts) - 1)
    )


def _sensitive_single_retry_model(
    batch_cfg: dict[str, Any],
    current_model: str,
) -> str:
    """Use the quality model for the final isolated retry when available."""
    if batch_cfg.get("api_sensitive_cross_model_retry_enabled", True):
        quality_model = str(batch_cfg.get("api_quality_model") or "")
        if quality_model and quality_model != current_model:
            return quality_model
    return current_model


def _build_sensitive_parent_repair_jobs(
    pipeline: Any,
    pending: dict[tuple[int, str], list[dict[str, Any]]],
    *,
    batch_options: dict[str, Any],
) -> tuple[list[BatchJob], dict[str, BatchJob]]:
    """Build one same-model structured request per unique multiline parent."""
    jobs: list[BatchJob] = []
    job_map: dict[str, BatchJob] = {}
    for (parent_index, model), requests in pending.items():
        entry = requests[0]["parent_entry"]
        parent_candidate = prepare_model_candidate(
            batch_i=0,
            idx=parent_index,
            source=entry.source,
            glossary=pipeline.glossary,
            short_label=False,
        )
        parent_candidate.update({
            "preserve_source_layout": True,
            "entry_classification": "composed_parent_repair",
            "quality_retry": {
                "previous": "",
                "issues": ["composed_child_repair"],
            },
            "sensitive_parent_target_rows": sorted({
                int(request["candidate"]["idx"])
                for request in requests
            }),
            "_composition_entry": entry,
        })
        batch_id = f"api_sensitive_parent_repair_{len(jobs):06d}"
        job = BatchJob(
            batch_id=batch_id,
            candidates=[parent_candidate],
            protocol="json",
            model=model,
            options=dict(batch_options),
        )
        jobs.append(job)
        job_map[batch_id] = job
    return jobs, job_map


def _parent_repair_child_payload(
    pipeline: Any,
    candidate: dict[str, Any],
    translated: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Validate an extracted parent line as an ordinary child translation."""
    source = str(candidate["source"])
    translated = pipeline.glossary.apply_post_translation(source, str(translated))
    issues = translation_issues(
        source,
        translated,
        short_label=bool(candidate.get("short_label", False)),
    )
    source_symbols = [token.symbol for token in protect_symbols(source)[1]]
    target_symbols = [token.symbol for token in protect_symbols(translated)[1]]
    if source_symbols != target_symbols:
        issues.append({
            "type": "symbol_preservation",
            "message": "Parent repair changed the child line's protected symbol sequence.",
        })
    issues.extend(new_issues(issues, pipeline._pollution_issues(source, translated)))
    return translated, pipeline._status_for_output(source, translated, issues), issues


def _failed_parent_repair_payload(
    fallback: tuple[str, str, list[dict[str, Any]]],
    *,
    message: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    translated, _status, issues = fallback
    final_issues = list(issues)
    final_issues.append({
        "type": "sensitive_parent_repair_failed",
        "message": message,
    })
    return translated, "review_required", final_issues


def _build_parent_first_jobs(
    pipeline: Any,
    candidates: list[dict[str, Any]],
    translated_items: list[tuple[Any, Any]],
    *,
    batch_size: int,
    max_batch_chars: int,
    batch_options: dict[str, Any],
    batch_cfg: dict[str, Any],
) -> tuple[list[BatchJob], dict[str, BatchJob], list[dict[str, Any]]]:
    """Divert composition children into line-ID scene jobs."""
    plan = getattr(pipeline, "_mtool_composition_plan", None)
    if (
        plan is None
        or not batch_cfg.get("mtool_parent_first_enabled", False)
    ):
        return [], {}, candidates

    max_parent_chars = max(
        1,
        int(batch_cfg.get("mtool_parent_first_max_chars", 2400)),
    )
    candidates_by_index = {
        int(candidate["idx"]): candidate
        for candidate in candidates
    }
    targets_by_parent: dict[int, list[dict[str, Any]]] = {}
    remaining: list[dict[str, Any]] = []
    for candidate in candidates:
        entry = plan.repair_parent_for_child(int(candidate["idx"]))
        if entry is None or len(entry.source) > max_parent_chars:
            remaining.append(candidate)
            continue
        targets_by_parent.setdefault(entry.parent_index, []).append(candidate)

    parent_candidates_by_model: dict[str, list[dict[str, Any]]] = {}
    for parent_index, targets in targets_by_parent.items():
        entry = plan.entries[parent_index]
        target_indexes = {int(candidate["idx"]) for candidate in targets}
        emitted_targets: set[int] = set()
        scene_lines: list[dict[str, Any]] = []
        for piece in entry.pieces:
            if piece.child_index is None:
                continue
            child_index = int(piece.child_index)
            target = (
                child_index in target_indexes
                and child_index not in emitted_targets
            )
            if target:
                emitted_targets.add(child_index)
            context_candidate = candidates_by_index.get(child_index)
            scene_lines.append({
                "i": child_index,
                "text": (
                    str(context_candidate["text"])
                    if context_candidate is not None
                    else str(translated_items[child_index][0])
                ),
                "target": target,
            })

        parent_candidate = {
            "i": 0,
            "idx": int(parent_index),
            "source": entry.source,
            "text": entry.source,
            "protected": entry.source,
            "short_label": False,
            "entry_classification": "composed_parent_first",
            "scene_lines": scene_lines,
            "scene_targets": [
                dict(
                    candidate,
                    parent_first=True,
                    parent_first_index=int(parent_index),
                )
                for candidate in targets
            ],
        }
        model = pipeline._select_api_job_model(targets, batch_cfg)
        parent_candidates_by_model.setdefault(model, []).append(parent_candidate)

    jobs: list[BatchJob] = []
    job_map: dict[str, BatchJob] = {}
    for model, parent_candidates in parent_candidates_by_model.items():
        packed_parent_batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_target_lines = 0
        current_chars = 0
        for parent_candidate in parent_candidates:
            target_lines = len(parent_candidate.get("scene_targets", []) or [])
            parent_chars = len(str(parent_candidate.get("source", "")))
            if current and (
                current_target_lines + target_lines > max(1, int(batch_size))
                or current_chars + parent_chars > max(1, int(max_batch_chars))
            ):
                packed_parent_batches.append(reindex_candidates(current))
                current = []
                current_target_lines = 0
                current_chars = 0
            current.append(parent_candidate)
            current_target_lines += target_lines
            current_chars += parent_chars
        if current:
            packed_parent_batches.append(reindex_candidates(current))

        for packed in packed_parent_batches:
            target_candidates = [
                target
                for parent in packed
                for target in parent.get("scene_targets", []) or []
            ]
            options = pipeline._select_api_job_options(
                target_candidates,
                batch_options,
                batch_cfg,
            )
            batch_id = f"api_parent_first_{len(jobs):06d}"
            job = BatchJob(
                batch_id=batch_id,
                candidates=packed,
                protocol="parent_json",
                model=model,
                options=options,
            )
            jobs.append(job)
            job_map[batch_id] = job
    return jobs, job_map, remaining


def _finish_parent_first_result(
    pipeline: Any,
    job: BatchJob,
    result: Any,
) -> tuple[
    list[dict[str, Any]],
    dict[int, tuple[str, str, list[dict[str, Any]]]],
    list[dict[str, Any]],
]:
    """Accept valid scene lines and return only failed lines to the old pipeline."""
    accepted: list[dict[str, Any]] = []
    payloads: dict[int, tuple[str, str, list[dict[str, Any]]]] = {}
    fallback: list[dict[str, Any]] = []
    for parent in job.candidates:
        targets = list(parent.get("scene_targets", []) or [])
        if result.error:
            fallback.extend(targets)
            continue
        raw_mapping = result.translations.get(int(parent["i"]))
        try:
            parsed_mapping = json.loads(str(raw_mapping or "{}"))
        except (TypeError, ValueError):
            parsed_mapping = {}
        if not isinstance(parsed_mapping, dict):
            parsed_mapping = {}
        for candidate in targets:
            child_index = int(candidate["idx"])
            raw_translation = parsed_mapping.get(str(child_index))
            if not isinstance(raw_translation, str) or not raw_translation.strip():
                fallback.append(candidate)
                continue
            payload = pipeline._finish_batch_translation(
                candidate,
                raw_translation,
            )
            if payload[1] != "translated" or payload[2]:
                fallback.append(candidate)
                continue
            accepted.append(candidate)
            payloads[child_index] = payload
    return accepted, payloads, fallback


def _is_content_rejection(error: Exception) -> bool:
    return bool(getattr(error, "content_rejected", False)) or (
        getattr(error, "retryable", True) is False
        and int(getattr(error, "status_code", 0) or 0) in {400, 422}
    )


def _fast_fallback_group(
    pipeline: Any,
    candidates: list[dict[str, Any]],
    file_path: str,
    options: dict[str, Any],
    fast_model: str,
    error: Exception,
) -> tuple[
    dict[int, tuple[str, str, list[dict[str, Any]]]],
    dict[int, Exception],
]:
    fallback_candidates = [
        {key: value for key, value in candidate.items() if key != "contexts"}
        for candidate in candidates
    ]
    payloads = pipeline._translate_json_candidates(
        fallback_candidates,
        file_path,
        options,
        "json",
        model=fast_model,
    )
    return payloads, {candidate["idx"]: error for candidate in candidates}


def _retry_content_group(
    pipeline: Any,
    candidates: list[dict[str, Any]],
    file_path: str,
    options: dict[str, Any],
    quality_model: str,
    fast_model: str,
    *,
    depth: int,
    max_depth: int,
) -> tuple[
    dict[int, tuple[str, str, list[dict[str, Any]]]],
    dict[int, Exception],
]:
    try:
        raw = pipeline._translate_json_candidate_batch_raw(
            candidates,
            options,
            "json",
            model=quality_model,
        )
    except Exception as exc:
        if _is_content_rejection(exc) and len(candidates) > 1 and depth < max_depth:
            mid = len(candidates) // 2
            payloads: dict[int, tuple[str, str, list[dict[str, Any]]]] = {}
            fallback_errors: dict[int, Exception] = {}
            for group in (candidates[:mid], candidates[mid:]):
                group_payloads, group_errors = _retry_content_group(
                    pipeline,
                    group,
                    file_path,
                    options,
                    quality_model,
                    fast_model,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
                payloads.update(group_payloads)
                fallback_errors.update(group_errors)
            return payloads, fallback_errors
        return _fast_fallback_group(
            pipeline,
            candidates,
            file_path,
            options,
            fast_model,
            exc,
        )
    return {
        candidate["idx"]: pipeline._finish_batch_translation(candidate, raw[candidate["i"]])
        for candidate in candidates
    }, {}


def _isolate_content_rejected_batch(
    pipeline: Any,
    candidates: list[dict[str, Any]],
    file_path: str,
    options: dict[str, Any],
    quality_model: str,
    fast_model: str,
    *,
    max_depth: int,
    original_error: Exception,
) -> tuple[
    dict[int, tuple[str, str, list[dict[str, Any]]]],
    dict[int, Exception],
]:
    if len(candidates) <= 1 or max_depth <= 0:
        return _fast_fallback_group(
            pipeline,
            candidates,
            file_path,
            options,
            fast_model,
            original_error,
        )
    mid = len(candidates) // 2
    payloads: dict[int, tuple[str, str, list[dict[str, Any]]]] = {}
    fallback_errors: dict[int, Exception] = {}
    for group in (candidates[:mid], candidates[mid:]):
        group_payloads, group_errors = _retry_content_group(
            pipeline,
            group,
            file_path,
            options,
            quality_model,
            fast_model,
            depth=1,
            max_depth=max_depth,
        )
        payloads.update(group_payloads)
        fallback_errors.update(group_errors)
    return payloads, fallback_errors


def _finish_api_batch_result(
    pipeline: Any,
    job: BatchJob,
    result: Any,
    file_path: str,
    default_options: dict[str, Any],
    batch_cfg: dict[str, Any] | None = None,
) -> dict[int, tuple[str, str, list[dict[str, Any]]]]:
    if not result.error:
        return {
            candidate["idx"]: pipeline._finish_batch_translation(candidate, result.translations[candidate["i"]])
            for candidate in job.candidates
        }

    translated_payloads: dict[int, tuple[str, str, list[dict[str, Any]]]] = {}
    retry_candidates = job.candidates
    if isinstance(result.error, BatchTranslationError) and result.error.partial_results:
        translated_payloads.update({
            candidate["idx"]: pipeline._finish_batch_translation(
                candidate,
                result.error.partial_results[candidate["i"]],
            )
            for candidate in job.candidates
            if candidate["i"] in result.error.partial_results
        })
        retry_candidates = [
            candidate
            for candidate in job.candidates
            if candidate["i"] in result.error.retry_indexes
        ]
    routing_cfg = batch_cfg or {}
    fast_model = str(routing_cfg.get("api_fast_model") or "")
    quality_model = str(routing_cfg.get("api_quality_model") or "")
    rejected_request = _is_content_rejection(result.error)
    use_content_fallback = (
        rejected_request
        and bool(routing_cfg.get("api_model_routing_enabled", False))
        and fast_model
        and quality_model
        and fast_model != quality_model
        and job.model == quality_model
    )
    structural_failure = isinstance(result.error, BatchTranslationError)
    terminal_transport_failure = bool(retry_candidates) and not structural_failure and not use_content_fallback
    content_fallback_errors: dict[int, Exception] = {}
    if retry_candidates and use_content_fallback:
        isolated, content_fallback_errors = _isolate_content_rejected_batch(
            pipeline,
            retry_candidates,
            file_path,
            job.options or default_options,
            quality_model,
            fast_model,
            max_depth=max(0, int(routing_cfg.get("api_content_split_max_depth", 3))),
            original_error=result.error,
        )
        translated_payloads.update(isolated)
    elif retry_candidates and structural_failure:
        translated_payloads.update(pipeline._translate_json_candidates(
            retry_candidates,
            file_path,
            job.options or default_options,
            job.protocol,
            model=job.model,
        ))
    elif terminal_transport_failure:
        issue_type = (
            "api_quota_exhausted"
            if bool(getattr(result.error, "quota_exhausted", False))
            else "api_batch_transport_error"
        )
        translated_payloads.update({
            candidate["idx"]: (
                candidate["source"],
                "review_required",
                [{"type": issue_type, "message": str(result.error)}],
            )
            for candidate in retry_candidates
        })
    for idx_key, (translated, status, issues) in list(translated_payloads.items()):
        issues = list(issues)
        source = next((candidate["source"] for candidate in job.candidates if candidate["idx"] == idx_key), "")
        if (issues or status == "review_required") and not terminal_transport_failure:
            issues.append({
                "type": "api_parallel_batch_retry_failed",
                "message": str(result.error),
            })
        if idx_key in content_fallback_errors:
            fallback_issue_type = (
                "api_content_filter_fallback"
                if _is_content_rejection(content_fallback_errors[idx_key])
                else "api_request_fallback"
            )
            issues.append({
                "type": fallback_issue_type,
                "message": (
                    f"Quality route rejected the isolated item group; "
                    f"translated with fallback model {fast_model}."
                ),
            })
        if status != "review_required":
            status = pipeline._status_for_output(source, translated, issues)
        translated_payloads[idx_key] = (translated, status, issues)
    return translated_payloads


def translate_json_batched_parallel_workflow(
    pipeline: Any,
    file_path: str,
    translated_items: list[tuple[Any, Any]],
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    target_path: str,
    total_targets: int,
    progress_callback: ProgressCallback | None,
    batch_size: int,
    max_batch_chars: int,
    batch_options: dict[str, Any],
    configured_protocol: str,
    batch_protocol: str,
    batch_cfg: dict[str, Any],
) -> list[tuple[Any, Any]]:
    if batch_cfg.get("api_event_driven_enabled", False):
        return _translate_json_batched_event_workflow(
            pipeline,
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
    processed_targets = 0
    jobs: list[BatchJob] = []
    job_map: dict[str, BatchJob] = {}
    model_candidates: list[dict[str, Any]] = []
    collection_records: list[dict[str, Any]] = []
    result_records: list[dict[str, Any]] = []
    deferred_confirmed_terms: dict[tuple[str, str], dict[str, Any]] = {}
    pending_quality_candidates: list[dict[str, Any]] = []
    pending_sensitive_repair_candidates: list[dict[str, Any]] = []
    pending_sensitive_parent_repairs: dict[
        tuple[int, str],
        list[dict[str, Any]],
    ] = {}

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
                progress_records=collection_records,
            )
            if len(collection_records) >= 1000:
                checkpoint.save_progress_many(file_path, collection_records)
                collection_records.clear()
            if not candidates:
                idx = next_idx
                continue
            for candidate in candidates:
                pipeline._emit_progress(
                    progress_callback,
                    file_path,
                    candidate["idx"],
                    0,
                    "queued",
                    processed_targets,
                    total_targets,
                    original_text=candidate["source"],
                )
            model_candidates.extend(candidates)
            idx = next_idx

        checkpoint.save_progress_many(file_path, collection_records)
        collection_records.clear()
        worker_count = max(1, int(batch_cfg.get("api_concurrency", 1)))
        max_retries = max(0, int(batch_cfg.get("api_max_retries", 2)))
        retry_backoff = [
            float(item)
            for item in batch_cfg.get(
                "api_retry_backoff_seconds",
                [2, 5, 15],
            )
        ]

        parent_jobs, parent_job_map, model_candidates = _build_parent_first_jobs(
            pipeline,
            model_candidates,
            translated_items,
            batch_size=batch_size,
            max_batch_chars=max_batch_chars,
            batch_options=batch_options,
            batch_cfg=batch_cfg,
        )
        parent_fallback_candidates: list[dict[str, Any]] = []
        for result in pipeline._run_concurrent_batches(
            parent_jobs,
            worker_count,
            pipeline._translate_api_batch_job,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff,
            check_stop=pipeline._check_control_flags,
        ):
            pipeline._check_control_flags()
            job = parent_job_map[result.batch_id]
            accepted, parent_payloads, fallback = _finish_parent_first_result(
                pipeline,
                job,
                result,
            )
            parent_fallback_candidates.extend(fallback)
            if not accepted:
                continue
            processed_targets, glossary_changed = apply_batch_translation_results(
                candidates=accepted,
                translated_payloads=parent_payloads,
                translated_items=translated_items,
                processed_targets=processed_targets,
                total_targets=total_targets,
                progress_callback=progress_callback,
                file_path=file_path,
                mtool=mtool,
                progress_records=result_records,
                glossary=pipeline.glossary,
                mark_dirty=pipeline._writer.mark_dirty,
                emit_progress=pipeline._emit_progress,
                progress_status=pipeline._progress_status,
                apply_confirmed_terms_to_outputs=lambda _file_path, terms: deferred_confirmed_terms.update({
                    (str(term.get("source", "")), str(term.get("target", ""))): term
                    for term in terms
                }),
                batch_id=result.batch_id,
                model_identifier=str(job.model or pipeline.model),
                retry_count=max(0, int(result.attempts) - 1),
            )
            if len(result_records) >= 1000:
                checkpoint.save_progress_many(file_path, result_records)
                result_records.clear()
            if glossary_changed:
                pipeline.glossary.save()
        model_candidates.extend(parent_fallback_candidates)

        for candidates in pack_api_candidate_batches(
            model_candidates,
            batch_size=batch_size,
            max_batch_chars=max_batch_chars,
            batch_cfg=batch_cfg,
        ):
            protocol = pipeline._resolve_parallel_candidate_protocol(
                configured_protocol,
                batch_protocol,
                candidates,
                batch_cfg,
            )
            job_model = pipeline._select_api_job_model(candidates, batch_cfg)
            job_options = pipeline._select_api_job_options(candidates, batch_options, batch_cfg)
            batch_id = f"api_batch_{len(jobs):06d}"
            job = BatchJob(batch_id=batch_id, candidates=candidates, protocol=protocol, model=job_model, options=job_options)
            jobs.append(job)
            job_map[batch_id] = job

        for result in pipeline._run_concurrent_batches(
            jobs,
            worker_count,
            pipeline._translate_api_batch_job,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff,
            check_stop=pipeline._check_control_flags,
        ):
            pipeline._check_control_flags()
            job = job_map[result.batch_id]
            translated_payloads = _finish_api_batch_result(
                pipeline,
                job,
                result,
                file_path,
                batch_options,
                batch_cfg,
            )

            accepted_candidates = job.candidates
            fast_model = str(batch_cfg.get("api_fast_model") or "")
            quality_model = str(batch_cfg.get("api_quality_model") or "")
            if (
                batch_cfg.get("api_model_routing_enabled", False)
                and fast_model
                and quality_model
                and fast_model != quality_model
                and job.model in {fast_model, quality_model}
            ):
                pending_ids = {
                    candidate["idx"]
                    for candidate in job.candidates
                    if candidate_needs_quality_model_retry(
                        candidate,
                        translated_payloads[candidate["idx"]][1],
                        translated_payloads[candidate["idx"]][2],
                        batch_cfg,
                    )
                }
                if pending_ids:
                    for candidate in job.candidates:
                        if candidate["idx"] not in pending_ids:
                            continue
                        translated, _status, issues = translated_payloads[candidate["idx"]]
                        pending_quality_candidates.append(dict(
                            candidate,
                            quality_retry={
                                "previous": translated,
                                "issues": [
                                    str(issue.get("type", ""))
                                    for issue in issues
                                    if str(issue.get("type", ""))
                                ],
                            },
                        ))
                    accepted_candidates = [
                        candidate for candidate in job.candidates if candidate["idx"] not in pending_ids
                    ]
                    translated_payloads = {
                        idx_key: payload
                        for idx_key, payload in translated_payloads.items()
                        if idx_key not in pending_ids
                    }

            sensitive_pending_ids = {
                candidate["idx"]
                for candidate in accepted_candidates
                if candidate_needs_sensitive_repair(
                    candidate,
                    translated_payloads[candidate["idx"]][1],
                    translated_payloads[candidate["idx"]][2],
                    batch_cfg,
                    repair_round=1,
                )
            }
            if sensitive_pending_ids:
                for candidate in accepted_candidates:
                    if candidate["idx"] not in sensitive_pending_ids:
                        continue
                    translated, _status, issues = translated_payloads[candidate["idx"]]
                    pending_sensitive_repair_candidates.append(
                        _sensitive_repair_candidate(
                            candidate,
                            translated,
                            issues,
                            model=str(job.model or pipeline.model),
                            repair_round=1,
                            prior_retry_count=max(0, int(result.attempts) - 1),
                        )
                    )
                accepted_candidates = [
                    candidate
                    for candidate in accepted_candidates
                    if candidate["idx"] not in sensitive_pending_ids
                ]
                translated_payloads = {
                    idx_key: payload
                    for idx_key, payload in translated_payloads.items()
                    if idx_key not in sensitive_pending_ids
                }

            if not accepted_candidates:
                continue

            processed_targets, glossary_changed = apply_batch_translation_results(
                candidates=accepted_candidates,
                translated_payloads=translated_payloads,
                translated_items=translated_items,
                processed_targets=processed_targets,
                total_targets=total_targets,
                progress_callback=progress_callback,
                file_path=file_path,
                mtool=mtool,
                progress_records=result_records,
                glossary=pipeline.glossary,
                mark_dirty=pipeline._writer.mark_dirty,
                emit_progress=pipeline._emit_progress,
                progress_status=pipeline._progress_status,
                apply_confirmed_terms_to_outputs=lambda _file_path, terms: deferred_confirmed_terms.update({
                    (str(term.get("source", "")), str(term.get("target", ""))): term
                    for term in terms
                }),
                batch_id=result.batch_id,
                model_identifier=str(job.model or pipeline.model),
                retry_count=max(0, int(result.attempts) - 1),
            )

            if len(result_records) >= 1000:
                checkpoint.save_progress_many(file_path, result_records)
                result_records.clear()
            if glossary_changed:
                pipeline.glossary.save()

        sensitive_repair_options = dict(batch_options)
        for repair_round in (1, 2):
            if not pending_sensitive_repair_candidates:
                break
            if (
                repair_round == 2
                and not batch_cfg.get("api_sensitive_repair_single_retry", True)
            ):
                break

            repair_jobs, repair_job_map = _build_sensitive_repair_jobs(
                pending_sensitive_repair_candidates,
                repair_round=repair_round,
                batch_options=sensitive_repair_options,
                batch_cfg=batch_cfg,
            )
            pending_sensitive_repair_candidates = []
            for result in pipeline._run_concurrent_batches(
                repair_jobs,
                worker_count,
                pipeline._translate_api_batch_job,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff,
                check_stop=pipeline._check_control_flags,
            ):
                pipeline._check_control_flags()
                job = repair_job_map[result.batch_id]
                translated_payloads = _finish_api_batch_result(
                    pipeline,
                    job,
                    result,
                    file_path,
                    sensitive_repair_options,
                    batch_cfg,
                )
                accepted_candidates = [
                    dict(
                        candidate,
                        sensitive_repair_retry_count=_repair_retry_count(
                            candidate,
                            result.attempts,
                        ),
                    )
                    for candidate in job.candidates
                ]

                allow_next_round = (
                    repair_round == 1
                    and batch_cfg.get("api_sensitive_repair_single_retry", True)
                )
                if allow_next_round:
                    pending_ids = {
                        candidate["idx"]
                        for candidate in job.candidates
                        if candidate_needs_sensitive_repair(
                            candidate,
                            translated_payloads[candidate["idx"]][1],
                            translated_payloads[candidate["idx"]][2],
                            batch_cfg,
                            repair_round=2,
                        )
                    }
                    if pending_ids:
                        accepted_candidates = [
                            candidate
                            for candidate in accepted_candidates
                            if candidate["idx"] not in pending_ids
                        ]
                        for candidate in job.candidates:
                            if candidate["idx"] not in pending_ids:
                                continue
                            translated, _status, issues = translated_payloads[
                                candidate["idx"]
                            ]
                            pending_sensitive_repair_candidates.append(
                                _sensitive_repair_candidate(
                                    candidate,
                                    translated,
                                    issues,
                                    model=_sensitive_single_retry_model(
                                        batch_cfg,
                                        str(job.model or pipeline.model),
                                    ),
                                    repair_round=2,
                                    prior_retry_count=_repair_retry_count(
                                        candidate,
                                        result.attempts,
                                    ),
                                )
                            )
                        translated_payloads = {
                            idx_key: payload
                            for idx_key, payload in translated_payloads.items()
                            if idx_key not in pending_ids
                        }

                if (
                    repair_round == 2
                    and batch_cfg.get("api_sensitive_parent_repair_enabled", True)
                ):
                    composition_plan = getattr(
                        pipeline,
                        "_mtool_composition_plan",
                        None,
                    )
                    max_parent_chars = max(
                        1,
                        int(
                            batch_cfg.get(
                                "api_sensitive_parent_repair_max_chars",
                                2400,
                            )
                        ),
                    )
                    accepted_by_idx = {
                        int(candidate["idx"]): candidate
                        for candidate in accepted_candidates
                    }
                    parent_pending_ids: set[int] = set()
                    for candidate in job.candidates:
                        idx_key = int(candidate["idx"])
                        translated, status, issues = translated_payloads[idx_key]
                        if not candidate_needs_sensitive_repair(
                            candidate,
                            status,
                            issues,
                            batch_cfg,
                            repair_round=2,
                        ):
                            continue
                        parent_entry = (
                            composition_plan.repair_parent_for_child(idx_key)
                            if composition_plan is not None
                            else None
                        )
                        if (
                            parent_entry is None
                            or len(parent_entry.source) > max_parent_chars
                            or idx_key not in accepted_by_idx
                        ):
                            continue
                        model = str(
                            candidate.get("sensitive_repair_model")
                            or job.model
                            or batch_cfg.get("api_sensitive_model")
                            or pipeline.model
                        )
                        pending_sensitive_parent_repairs.setdefault(
                            (int(parent_entry.parent_index), model),
                            [],
                        ).append({
                            "candidate": accepted_by_idx[idx_key],
                            "fallback_payload": (
                                translated,
                                status,
                                list(issues),
                            ),
                            "parent_entry": parent_entry,
                        })
                        parent_pending_ids.add(idx_key)

                    if parent_pending_ids:
                        accepted_candidates = [
                            candidate
                            for candidate in accepted_candidates
                            if int(candidate["idx"]) not in parent_pending_ids
                        ]
                        translated_payloads = {
                            idx_key: payload
                            for idx_key, payload in translated_payloads.items()
                            if int(idx_key) not in parent_pending_ids
                        }

                if not accepted_candidates:
                    continue
                processed_targets, glossary_changed = apply_batch_translation_results(
                    candidates=accepted_candidates,
                    translated_payloads=translated_payloads,
                    translated_items=translated_items,
                    processed_targets=processed_targets,
                    total_targets=total_targets,
                    progress_callback=progress_callback,
                    file_path=file_path,
                    mtool=mtool,
                    progress_records=result_records,
                    glossary=pipeline.glossary,
                    mark_dirty=pipeline._writer.mark_dirty,
                    emit_progress=pipeline._emit_progress,
                    progress_status=pipeline._progress_status,
                    apply_confirmed_terms_to_outputs=lambda _file_path, terms: deferred_confirmed_terms.update({
                        (str(term.get("source", "")), str(term.get("target", ""))): term
                        for term in terms
                    }),
                    batch_id=result.batch_id,
                    model_identifier=str(job.model or pipeline.model),
                )
                if len(result_records) >= 1000:
                    checkpoint.save_progress_many(file_path, result_records)
                    result_records.clear()
                if glossary_changed:
                    pipeline.glossary.save()

        if pending_sensitive_parent_repairs:
            parent_jobs, parent_job_map = _build_sensitive_parent_repair_jobs(
                pipeline,
                pending_sensitive_parent_repairs,
                batch_options=sensitive_repair_options,
            )
            requests_by_parent_model = pending_sensitive_parent_repairs
            for result in pipeline._run_concurrent_batches(
                parent_jobs,
                worker_count,
                pipeline._translate_api_batch_job,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff,
                check_stop=pipeline._check_control_flags,
            ):
                pipeline._check_control_flags()
                job = parent_job_map[result.batch_id]
                parent_candidate = job.candidates[0]
                parent_entry = parent_candidate["_composition_entry"]
                request_key = (
                    int(parent_entry.parent_index),
                    str(job.model or pipeline.model),
                )
                requests = requests_by_parent_model[request_key]
                parent_payloads = _finish_api_batch_result(
                    pipeline,
                    job,
                    result,
                    file_path,
                    sensitive_repair_options,
                    batch_cfg,
                )
                parent_translation, parent_status, parent_issues = parent_payloads[
                    int(parent_entry.parent_index)
                ]
                if parent_status == "review_required":
                    parent_issue_types = sorted({
                        str(issue.get("type", ""))
                        for issue in parent_issues
                        if isinstance(issue, dict) and str(issue.get("type", ""))
                    })
                    parent_failure_message = (
                        "Full-parent repair failed parent validation"
                        + (
                            ": " + ", ".join(parent_issue_types)
                            if parent_issue_types
                            else "."
                        )
                    )
                else:
                    parent_failure_message = (
                        "Full-parent repair did not return an exactly "
                        "line-aligned usable translation."
                    )
                extracted = (
                    pipeline._mtool_composition_plan.extract_child_translations(
                        parent_entry,
                        parent_translation,
                    )
                    if parent_status != "review_required"
                    else {}
                )

                child_candidates: list[dict[str, Any]] = []
                child_payloads: dict[
                    int,
                    tuple[str, str, list[dict[str, Any]]],
                ] = {}
                for request in requests:
                    original_candidate = request["candidate"]
                    child_index = int(original_candidate["idx"])
                    final_candidate = dict(
                        original_candidate,
                        sensitive_repair_round=3,
                        sensitive_parent_repair=True,
                        sensitive_parent_index=int(parent_entry.parent_index),
                        sensitive_repair_retry_count=(
                            max(
                                0,
                                int(
                                    original_candidate.get(
                                        "sensitive_repair_retry_count",
                                        2,
                                    )
                                ),
                            )
                            + max(1, int(result.attempts))
                        ),
                    )
                    extracted_translation = extracted.get(child_index)
                    if extracted_translation is None:
                        child_payload = _failed_parent_repair_payload(
                            request["fallback_payload"],
                            message=parent_failure_message,
                        )
                    else:
                        child_payload = _parent_repair_child_payload(
                            pipeline,
                            final_candidate,
                            extracted_translation,
                        )
                        if child_payload[1] == "review_required":
                            child_payload = _failed_parent_repair_payload(
                                request["fallback_payload"],
                                message=(
                                    "Full-parent repair still failed child-line "
                                    "quality validation."
                                ),
                            )
                    child_candidates.append(final_candidate)
                    child_payloads[child_index] = child_payload

                processed_targets, glossary_changed = apply_batch_translation_results(
                    candidates=child_candidates,
                    translated_payloads=child_payloads,
                    translated_items=translated_items,
                    processed_targets=processed_targets,
                    total_targets=total_targets,
                    progress_callback=progress_callback,
                    file_path=file_path,
                    mtool=mtool,
                    progress_records=result_records,
                    glossary=pipeline.glossary,
                    mark_dirty=pipeline._writer.mark_dirty,
                    emit_progress=pipeline._emit_progress,
                    progress_status=pipeline._progress_status,
                    apply_confirmed_terms_to_outputs=lambda _file_path, terms: deferred_confirmed_terms.update({
                        (str(term.get("source", "")), str(term.get("target", ""))): term
                        for term in terms
                    }),
                    batch_id=result.batch_id,
                    model_identifier=str(job.model or pipeline.model),
                )
                if len(result_records) >= 1000:
                    checkpoint.save_progress_many(file_path, result_records)
                    result_records.clear()
                if glossary_changed:
                    pipeline.glossary.save()

        quality_model = str(batch_cfg.get("api_quality_model") or "")
        if pending_quality_candidates and quality_model:
            quality_options = dict(batch_options)
            if batch_cfg.get("quality_num_predict"):
                quality_options["num_predict"] = int(batch_cfg["quality_num_predict"])
            quality_jobs: list[BatchJob] = []
            quality_job_map: dict[str, BatchJob] = {}
            for candidates in pack_api_candidate_batches(
                pending_quality_candidates,
                batch_size=batch_size,
                max_batch_chars=max_batch_chars,
                batch_cfg=batch_cfg,
            ):
                batch_id = f"api_quality_retry_{len(quality_jobs):06d}"
                job = BatchJob(
                    batch_id=batch_id,
                    candidates=candidates,
                    protocol="json",
                    model=quality_model,
                    options=quality_options,
                )
                quality_jobs.append(job)
                quality_job_map[batch_id] = job

            for result in pipeline._run_concurrent_batches(
                quality_jobs,
                worker_count,
                pipeline._translate_api_batch_job,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff,
                check_stop=pipeline._check_control_flags,
            ):
                pipeline._check_control_flags()
                job = quality_job_map[result.batch_id]
                translated_payloads = _finish_api_batch_result(
                    pipeline,
                    job,
                    result,
                    file_path,
                    quality_options,
                    batch_cfg,
                )
                processed_targets, glossary_changed = apply_batch_translation_results(
                    candidates=job.candidates,
                    translated_payloads=translated_payloads,
                    translated_items=translated_items,
                    processed_targets=processed_targets,
                    total_targets=total_targets,
                    progress_callback=progress_callback,
                    file_path=file_path,
                    mtool=mtool,
                    progress_records=result_records,
                    glossary=pipeline.glossary,
                    mark_dirty=pipeline._writer.mark_dirty,
                    emit_progress=pipeline._emit_progress,
                    progress_status=pipeline._progress_status,
                    apply_confirmed_terms_to_outputs=lambda _file_path, terms: deferred_confirmed_terms.update({
                        (str(term.get("source", "")), str(term.get("target", ""))): term
                        for term in terms
                    }),
                    batch_id=result.batch_id,
                    model_identifier=str(job.model or pipeline.model),
                    retry_count=max(0, int(result.attempts) - 1),
                )
                if len(result_records) >= 1000:
                    checkpoint.save_progress_many(file_path, result_records)
                    result_records.clear()
                if glossary_changed:
                    pipeline.glossary.save()
    finally:
        checkpoint.save_progress_many(file_path, collection_records)
        collection_records.clear()
        checkpoint.save_progress_many(file_path, result_records)
        result_records.clear()
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


def _event_candidate_suffix(candidates: list[dict[str, Any]]) -> str:
    indexes = ",".join(
        str(int(candidate["idx"]))
        for candidate in sorted(candidates, key=lambda item: int(item["idx"]))
    )
    return hashlib.sha256(indexes.encode("utf-8")).hexdigest()[:12]


def _event_primary_jobs(
    pipeline: Any,
    candidates: list[dict[str, Any]],
    *,
    prefix: str,
    priority: int,
    batch_size: int,
    max_batch_chars: int,
    batch_options: dict[str, Any],
    configured_protocol: str,
    batch_protocol: str,
    batch_cfg: dict[str, Any],
) -> list[BatchJob]:
    jobs: list[BatchJob] = []
    for packed in pack_api_candidate_batches(
        candidates,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        batch_cfg=batch_cfg,
    ):
        protocol = pipeline._resolve_parallel_candidate_protocol(
            configured_protocol,
            batch_protocol,
            packed,
            batch_cfg,
        )
        model = pipeline._select_api_job_model(packed, batch_cfg)
        options = pipeline._select_api_job_options(
            packed,
            batch_options,
            batch_cfg,
        )
        jobs.append(BatchJob(
            batch_id=f"{prefix}_{_event_candidate_suffix(packed)}",
            candidates=packed,
            protocol=protocol,
            model=model,
            options=options,
            priority=priority,
        ))
    return jobs


def _event_quality_jobs(
    candidates: list[dict[str, Any]],
    *,
    quality_model: str,
    quality_options: dict[str, Any],
    batch_size: int,
    max_batch_chars: int,
    batch_cfg: dict[str, Any],
) -> list[BatchJob]:
    jobs: list[BatchJob] = []
    for packed in pack_api_candidate_batches(
        candidates,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        batch_cfg=batch_cfg,
    ):
        prepared = reindex_candidates([
            dict(
                candidate,
                quality_repair_depth=0,
                quality_repair_fresh=False,
            )
            for candidate in packed
        ])
        jobs.append(BatchJob(
            batch_id=(
                "api_event_quality_r1_"
                + _event_candidate_suffix(prepared)
            ),
            candidates=prepared,
            protocol="json",
            model=quality_model,
            options=dict(quality_options),
            priority=0,
        ))
    return jobs


def _recursive_quality_issue_types(batch_cfg: dict[str, Any]) -> set[str]:
    configured = batch_cfg.get("api_quality_recursive_issue_types", ())
    if isinstance(configured, str):
        return {
            item.strip()
            for item in configured.split(",")
            if item.strip()
        }
    return {
        str(item).strip()
        for item in configured or ()
        if str(item).strip()
    }


def _event_quality_followups(
    job: BatchJob,
    payloads: dict[int, tuple[str, str, list[dict[str, Any]]]],
    *,
    quality_model: str,
    quality_options: dict[str, Any],
    batch_cfg: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    dict[int, tuple[str, str, list[dict[str, Any]]]],
    list[BatchJob],
]:
    """Split failures, then allow one fresh and one structural-isolation retry."""
    if not batch_cfg.get("api_quality_recursive_repair_enabled", True):
        return list(job.candidates), payloads, []

    recursive_types = _recursive_quality_issue_types(batch_cfg)
    pending: list[dict[str, Any]] = []
    for candidate in job.candidates:
        _translated, status, issues = payloads[int(candidate["idx"])]
        issue_types = {
            str(issue.get("type", ""))
            for issue in issues
            if isinstance(issue, dict) and str(issue.get("type", ""))
        }
        if (
            candidate_needs_quality_model_retry(
                candidate,
                status,
                issues,
                batch_cfg,
            )
            and (
                status == "review_required"
                or bool(issue_types & recursive_types)
            )
        ):
            pending.append(candidate)
    if not pending:
        return list(job.candidates), payloads, []

    pending_ids = {int(candidate["idx"]) for candidate in pending}
    accepted = [
        candidate
        for candidate in job.candidates
        if int(candidate["idx"]) not in pending_ids
    ]
    accepted_payloads = {
        idx: payload
        for idx, payload in payloads.items()
        if int(idx) not in pending_ids
    }
    depth = max(
        int(candidate.get("quality_repair_depth", 0) or 0)
        for candidate in pending
    )
    fresh = all(
        bool(candidate.get("quality_repair_fresh", False))
        for candidate in pending
    )
    context_isolated = all(
        bool(candidate.get("quality_repair_context_isolated", False))
        for candidate in pending
    )
    pending_issue_types = {
        str(issue.get("type", ""))
        for candidate in pending
        for issue in payloads[int(candidate["idx"])][2]
        if isinstance(issue, dict) and str(issue.get("type", ""))
    }
    max_depth = max(
        0,
        int(batch_cfg.get("api_quality_recursive_max_depth", 6)),
    )
    should_split = len(job.candidates) > 1 and depth < max_depth
    should_fresh_single = (
        len(job.candidates) == 1
        and len(pending) == 1
        and not fresh
        and batch_cfg.get("api_quality_recursive_fresh_single", True)
    )
    should_isolated_single = (
        len(job.candidates) == 1
        and len(pending) == 1
        and fresh
        and not context_isolated
        and bool(pending_issue_types & _STRUCTURAL_QUALITY_ISSUE_TYPES)
        and bool(pending[0].get("contexts"))
        and batch_cfg.get("api_quality_recursive_fresh_single", True)
    )
    if not should_split and not should_fresh_single and not should_isolated_single:
        return list(job.candidates), payloads, []

    next_candidates: list[dict[str, Any]] = []
    for candidate in pending:
        translated, _status, issues = payloads[int(candidate["idx"])]
        next_candidate = dict(
            candidate,
            quality_retry={
                "previous": "",
                "issues": [
                    str(issue.get("type", ""))
                    for issue in issues
                    if isinstance(issue, dict)
                    and str(issue.get("type", ""))
                ],
            },
            quality_repair_depth=depth + (1 if should_split else 0),
            quality_repair_fresh=bool(should_fresh_single),
            quality_repair_previous_rejected=translated,
        )
        issue_types = {
            str(issue.get("type", ""))
            for issue in issues
            if isinstance(issue, dict) and str(issue.get("type", ""))
        }
        isolate_from_context = bool(
            issue_types & _STRUCTURAL_QUALITY_ISSUE_TYPES
        )
        isolate_from_context = isolate_from_context and (
            should_fresh_single or should_isolated_single
        )
        next_candidate["quality_repair_fresh"] = bool(
            fresh or should_fresh_single or should_isolated_single
        )
        next_candidate["quality_repair_context_isolated"] = bool(
            candidate.get("quality_repair_context_isolated", False)
            or isolate_from_context
        )
        if isolate_from_context:
            # The final retry must be isolated from scene/neighbor text.
            # Models sometimes translate read-only context as a continuation,
            # duplicating adjacent clauses or expanding a source fragment.
            next_candidate.pop("contexts", None)
        next_candidates.append(next_candidate)

    groups: list[list[dict[str, Any]]]
    if should_split and len(next_candidates) > 1:
        midpoint = max(1, len(next_candidates) // 2)
        groups = [
            next_candidates[:midpoint],
            next_candidates[midpoint:],
        ]
    else:
        groups = [next_candidates]

    followups: list[BatchJob] = []
    for group in groups:
        if not group:
            continue
        prepared = reindex_candidates(group)
        depth_label = max(
            int(candidate.get("quality_repair_depth", 0) or 0)
            for candidate in prepared
        )
        fresh_suffix = (
            "_isolated"
            if should_isolated_single
            else ("_fresh" if should_fresh_single else "")
        )
        followups.append(BatchJob(
            batch_id=(
                f"api_event_quality_r{depth_label + 1}{fresh_suffix}_"
                + _event_candidate_suffix(prepared)
            ),
            candidates=prepared,
            protocol="json",
            model=quality_model,
            options=dict(quality_options),
            priority=0,
        ))
    return accepted, accepted_payloads, followups


def _event_sensitive_jobs(
    candidates: list[dict[str, Any]],
    *,
    repair_round: int,
    batch_options: dict[str, Any],
    batch_cfg: dict[str, Any],
) -> list[BatchJob]:
    jobs, _job_map = _build_sensitive_repair_jobs(
        candidates,
        repair_round=repair_round,
        batch_options=batch_options,
        batch_cfg=batch_cfg,
    )
    return [
        BatchJob(
            batch_id=(
                f"api_event_sensitive_r{repair_round}_"
                + _event_candidate_suffix(job.candidates)
            ),
            candidates=job.candidates,
            protocol=job.protocol,
            model=job.model,
            options=job.options,
            priority=0,
        )
        for job in jobs
    ]


def _interleave_event_jobs(
    first: list[BatchJob],
    second: list[BatchJob],
) -> list[BatchJob]:
    interleaved: list[BatchJob] = []
    maximum = max(len(first), len(second))
    for index in range(maximum):
        if index < len(first):
            interleaved.append(first[index])
        if index < len(second):
            interleaved.append(second[index])
    return interleaved


def _translate_json_batched_event_workflow(
    pipeline: Any,
    file_path: str,
    translated_items: list[tuple[Any, Any]],
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    target_path: str,
    total_targets: int,
    progress_callback: ProgressCallback | None,
    batch_size: int,
    max_batch_chars: int,
    batch_options: dict[str, Any],
    configured_protocol: str,
    batch_protocol: str,
    batch_cfg: dict[str, Any],
) -> list[tuple[Any, Any]]:
    """Translate with a bounded dynamic queue and immediate follow-up jobs."""
    processed_targets = 0
    collection_records: list[dict[str, Any]] = []
    result_records: list[dict[str, Any]] = []
    deferred_confirmed_terms: dict[tuple[str, str], dict[str, Any]] = {}
    parent_fallback_buffer: list[dict[str, Any]] = []
    pending_parent_repairs: dict[
        tuple[int, str],
        list[dict[str, Any]],
    ] = {}
    active_parent_repairs: set[tuple[int, str]] = set()
    parent_repair_outcomes: dict[
        tuple[int, str],
        dict[str, Any],
    ] = {}

    def apply_results(
        candidates: list[dict[str, Any]],
        payloads: dict[int, tuple[str, str, list[dict[str, Any]]]],
        *,
        batch_id: str,
        model: str,
        retry_count: int | None = None,
    ) -> None:
        nonlocal processed_targets
        if not candidates:
            return
        processed_targets, glossary_changed = apply_batch_translation_results(
            candidates=candidates,
            translated_payloads=payloads,
            translated_items=translated_items,
            processed_targets=processed_targets,
            total_targets=total_targets,
            progress_callback=progress_callback,
            file_path=file_path,
            mtool=mtool,
            progress_records=result_records,
            glossary=pipeline.glossary,
            mark_dirty=pipeline._writer.mark_dirty,
            emit_progress=pipeline._emit_progress,
            progress_status=pipeline._progress_status,
            apply_confirmed_terms_to_outputs=lambda _file_path, terms: deferred_confirmed_terms.update({
                (str(term.get("source", "")), str(term.get("target", ""))): term
                for term in terms
            }),
            batch_id=batch_id,
            model_identifier=model,
            retry_count=retry_count,
        )
        if len(result_records) >= 1000:
            checkpoint.save_progress_many(file_path, result_records)
            result_records.clear()
        if glossary_changed:
            pipeline.glossary.save()

    def process_parent_repair_requests(
        key: tuple[int, str],
        requests: list[dict[str, Any]],
        outcome: dict[str, Any],
    ) -> list[BatchJob]:
        parent_entry = outcome["parent_entry"]
        extracted = outcome["extracted"]
        child_candidates: list[dict[str, Any]] = []
        child_payloads: dict[
            int,
            tuple[str, str, list[dict[str, Any]]],
        ] = {}
        terminal_jobs: list[BatchJob] = []
        for request in requests:
            original_candidate = request["candidate"]
            child_index = int(original_candidate["idx"])
            final_candidate = dict(
                original_candidate,
                sensitive_repair_round=3,
                sensitive_parent_repair=True,
                sensitive_parent_index=int(parent_entry.parent_index),
                sensitive_repair_retry_count=(
                    max(
                        0,
                        int(
                            original_candidate.get(
                                "sensitive_repair_retry_count",
                                2,
                            )
                        ),
                    )
                    + max(1, int(outcome["attempts"]))
                ),
            )
            extracted_translation = extracted.get(child_index)
            if extracted_translation is None:
                child_payload = _failed_parent_repair_payload(
                    request["fallback_payload"],
                    message=outcome["failure_message"],
                )
            else:
                child_payload = _parent_repair_child_payload(
                    pipeline,
                    final_candidate,
                    extracted_translation,
                )
                if child_payload[1] == "review_required":
                    child_payload = _failed_parent_repair_payload(
                        request["fallback_payload"],
                        message=(
                            "Full-parent repair still failed child-line "
                            "quality validation."
                        ),
                    )
            issue_types = {
                str(issue.get("type", ""))
                for issue in child_payload[2]
                if isinstance(issue, dict) and str(issue.get("type", ""))
            }
            if (
                child_payload[1] == "review_required"
                and issue_types & _STRUCTURAL_QUALITY_ISSUE_TYPES
                and quality_model
            ):
                terminal_candidate = dict(
                    final_candidate,
                    quality_retry={
                        "previous": "",
                        "issues": sorted(issue_types),
                    },
                    sensitive_repair_round=4,
                    sensitive_terminal_retry=True,
                    quality_repair_fresh=True,
                    quality_repair_context_isolated=True,
                )
                terminal_candidate.pop("contexts", None)
                prepared = reindex_candidates([terminal_candidate])
                terminal_jobs.append(BatchJob(
                    batch_id=(
                        "api_event_sensitive_terminal_"
                        + _event_candidate_suffix(prepared)
                    ),
                    candidates=prepared,
                    protocol="json",
                    model=quality_model,
                    options=dict(quality_options),
                    priority=0,
                ))
                continue
            child_candidates.append(final_candidate)
            child_payloads[child_index] = child_payload
        apply_results(
            child_candidates,
            child_payloads,
            batch_id=outcome["batch_id"],
            model=key[1],
        )
        return terminal_jobs

    def schedule_parent_repair(
        request: dict[str, Any],
        *,
        model: str,
        sensitive_options: dict[str, Any],
    ) -> list[BatchJob]:
        parent_entry = request["parent_entry"]
        key = (int(parent_entry.parent_index), model)
        cached = parent_repair_outcomes.get(key)
        if cached is not None:
            return process_parent_repair_requests(key, [request], cached)
        pending_parent_repairs.setdefault(key, []).append(request)
        if key in active_parent_repairs:
            return []
        active_parent_repairs.add(key)
        jobs, _job_map = _build_sensitive_parent_repair_jobs(
            pipeline,
            {key: pending_parent_repairs[key]},
            batch_options=sensitive_options,
        )
        original = jobs[0]
        model_hash = hashlib.sha256(model.encode("utf-8")).hexdigest()[:8]
        return [BatchJob(
            batch_id=(
                f"api_event_sensitive_parent_{key[0]}_{model_hash}"
            ),
            candidates=original.candidates,
            protocol=original.protocol,
            model=original.model,
            options=original.options,
            priority=0,
        )]

    try:
        model_candidates: list[dict[str, Any]] = []
        index = 0
        while index < len(translated_items):
            pipeline._check_control_flags()
            candidates, next_index, processed_targets = (
                pipeline._collect_json_batch_window(
                    translated_items,
                    index,
                    mtool,
                    completed,
                    batch_size,
                    max_batch_chars,
                    file_path,
                    total_targets,
                    processed_targets,
                    progress_callback,
                    progress_records=collection_records,
                )
            )
            if len(collection_records) >= 1000:
                checkpoint.save_progress_many(file_path, collection_records)
                collection_records.clear()
            for candidate in candidates:
                pipeline._emit_progress(
                    progress_callback,
                    file_path,
                    candidate["idx"],
                    0,
                    "queued",
                    processed_targets,
                    total_targets,
                    original_text=candidate["source"],
                )
            model_candidates.extend(candidates)
            index = next_index

        checkpoint.save_progress_many(file_path, collection_records)
        collection_records.clear()
        worker_count = max(1, int(batch_cfg.get("api_concurrency", 1)))
        max_retries = max(0, int(batch_cfg.get("api_max_retries", 2)))
        retry_backoff = [
            float(item)
            for item in batch_cfg.get(
                "api_retry_backoff_seconds",
                [2, 5, 15],
            )
        ]
        sensitive_options = dict(batch_options)
        quality_options = dict(batch_options)
        if batch_cfg.get("quality_num_predict"):
            quality_options["num_predict"] = int(
                batch_cfg["quality_num_predict"]
            )
        quality_model = str(batch_cfg.get("api_quality_model") or "")

        parent_jobs, _parent_job_map, standalone_candidates = (
            _build_parent_first_jobs(
                pipeline,
                model_candidates,
                translated_items,
                batch_size=batch_size,
                max_batch_chars=max_batch_chars,
                batch_options=batch_options,
                batch_cfg=batch_cfg,
            )
        )
        parent_jobs = [
            BatchJob(
                batch_id=job.batch_id,
                candidates=job.candidates,
                protocol=job.protocol,
                model=job.model,
                options=job.options,
                priority=10,
            )
            for job in parent_jobs
        ]
        remaining_parent_jobs = len(parent_jobs)
        primary_jobs = _event_primary_jobs(
            pipeline,
            standalone_candidates,
            prefix="api_event_primary",
            priority=10,
            batch_size=batch_size,
            max_batch_chars=max_batch_chars,
            batch_options=batch_options,
            configured_protocol=configured_protocol,
            batch_protocol=batch_protocol,
            batch_cfg=batch_cfg,
        )

        def on_result(
            job: BatchJob,
            result: Any,
        ) -> list[BatchJob]:
            nonlocal remaining_parent_jobs
            pipeline._check_control_flags()
            followups: list[BatchJob] = []
            model = str(job.model or pipeline.model)

            if job.batch_id.startswith("api_parent_first_"):
                accepted, payloads, fallback = _finish_parent_first_result(
                    pipeline,
                    job,
                    result,
                )
                apply_results(
                    accepted,
                    payloads,
                    batch_id=result.batch_id,
                    model=model,
                    retry_count=max(0, int(result.attempts) - 1),
                )
                parent_fallback_buffer.extend(fallback)
                remaining_parent_jobs -= 1
                if (
                    len(parent_fallback_buffer) >= batch_size
                    or remaining_parent_jobs == 0
                ):
                    buffered = list(parent_fallback_buffer)
                    parent_fallback_buffer.clear()
                    followups.extend(_event_primary_jobs(
                        pipeline,
                        buffered,
                        prefix="api_event_parent_fallback",
                        priority=0,
                        batch_size=batch_size,
                        max_batch_chars=max_batch_chars,
                        batch_options=batch_options,
                        configured_protocol=configured_protocol,
                        batch_protocol=batch_protocol,
                        batch_cfg=batch_cfg,
                    ))
                return followups

            if job.batch_id.startswith("api_event_quality_"):
                payloads = _finish_api_batch_result(
                    pipeline,
                    job,
                    result,
                    file_path,
                    quality_options,
                    batch_cfg,
                )
                accepted, accepted_payloads, quality_followups = (
                    _event_quality_followups(
                        job,
                        payloads,
                        quality_model=quality_model,
                        quality_options=quality_options,
                        batch_cfg=batch_cfg,
                    )
                )
                apply_results(
                    accepted,
                    accepted_payloads,
                    batch_id=result.batch_id,
                    model=model,
                    retry_count=max(0, int(result.attempts) - 1),
                )
                return quality_followups

            if job.batch_id.startswith("api_event_sensitive_parent_"):
                parent_candidate = job.candidates[0]
                parent_entry = parent_candidate["_composition_entry"]
                key = (int(parent_entry.parent_index), model)
                parent_payloads = _finish_api_batch_result(
                    pipeline,
                    job,
                    result,
                    file_path,
                    sensitive_options,
                    batch_cfg,
                )
                parent_translation, parent_status, parent_issues = (
                    parent_payloads[int(parent_entry.parent_index)]
                )
                if parent_status == "review_required":
                    issue_types = sorted({
                        str(issue.get("type", ""))
                        for issue in parent_issues
                        if isinstance(issue, dict)
                        and str(issue.get("type", ""))
                    })
                    failure_message = (
                        "Full-parent repair failed parent validation"
                        + (
                            ": " + ", ".join(issue_types)
                            if issue_types
                            else "."
                        )
                    )
                else:
                    failure_message = (
                        "Full-parent repair did not return an exactly "
                        "line-aligned usable translation."
                    )
                extracted = (
                    pipeline._mtool_composition_plan.extract_child_translations(
                        parent_entry,
                        parent_translation,
                    )
                    if parent_status != "review_required"
                    else {}
                )
                outcome = {
                    "parent_entry": parent_entry,
                    "extracted": extracted,
                    "failure_message": failure_message,
                    "attempts": result.attempts,
                    "batch_id": result.batch_id,
                }
                parent_repair_outcomes[key] = outcome
                active_parent_repairs.discard(key)
                requests = pending_parent_repairs.pop(key, [])
                return process_parent_repair_requests(
                    key,
                    requests,
                    outcome,
                )

            if job.batch_id.startswith("api_event_sensitive_terminal_"):
                payloads = _finish_api_batch_result(
                    pipeline,
                    job,
                    result,
                    file_path,
                    quality_options,
                    batch_cfg,
                )
                accepted = [
                    dict(
                        candidate,
                        sensitive_repair_retry_count=(
                            max(
                                0,
                                int(
                                    candidate.get(
                                        "sensitive_repair_retry_count",
                                        3,
                                    )
                                ),
                            )
                            + max(1, int(result.attempts))
                        ),
                    )
                    for candidate in job.candidates
                ]
                apply_results(
                    accepted,
                    payloads,
                    batch_id=result.batch_id,
                    model=model,
                )
                return []

            if job.batch_id.startswith("api_event_sensitive_r"):
                repair_round = (
                    2
                    if job.batch_id.startswith("api_event_sensitive_r2_")
                    else 1
                )
                payloads = _finish_api_batch_result(
                    pipeline,
                    job,
                    result,
                    file_path,
                    sensitive_options,
                    batch_cfg,
                )
                accepted = [
                    dict(
                        candidate,
                        sensitive_repair_retry_count=_repair_retry_count(
                            candidate,
                            result.attempts,
                        ),
                    )
                    for candidate in job.candidates
                ]
                if (
                    repair_round == 1
                    and batch_cfg.get(
                        "api_sensitive_repair_single_retry",
                        True,
                    )
                ):
                    pending_ids = {
                        int(candidate["idx"])
                        for candidate in job.candidates
                        if candidate_needs_sensitive_repair(
                            candidate,
                            payloads[int(candidate["idx"])][1],
                            payloads[int(candidate["idx"])][2],
                            batch_cfg,
                            repair_round=2,
                        )
                    }
                    if pending_ids:
                        accepted = [
                            candidate
                            for candidate in accepted
                            if int(candidate["idx"]) not in pending_ids
                        ]
                        next_candidates: list[dict[str, Any]] = []
                        for candidate in job.candidates:
                            if int(candidate["idx"]) not in pending_ids:
                                continue
                            translated, _status, issues = payloads[
                                int(candidate["idx"])
                            ]
                            next_candidates.append(
                                _sensitive_repair_candidate(
                                    candidate,
                                    translated,
                                    issues,
                                    model=_sensitive_single_retry_model(
                                        batch_cfg,
                                        model,
                                    ),
                                    repair_round=2,
                                    prior_retry_count=_repair_retry_count(
                                        candidate,
                                        result.attempts,
                                    ),
                                )
                            )
                        payloads = {
                            idx_key: payload
                            for idx_key, payload in payloads.items()
                            if int(idx_key) not in pending_ids
                        }
                        followups.extend(_event_sensitive_jobs(
                            next_candidates,
                            repair_round=2,
                            batch_options=sensitive_options,
                            batch_cfg=batch_cfg,
                        ))

                if (
                    repair_round == 2
                    and batch_cfg.get(
                        "api_sensitive_parent_repair_enabled",
                        True,
                    )
                ):
                    composition_plan = getattr(
                        pipeline,
                        "_mtool_composition_plan",
                        None,
                    )
                    max_parent_chars = max(
                        1,
                        int(
                            batch_cfg.get(
                                "api_sensitive_parent_repair_max_chars",
                                2400,
                            )
                        ),
                    )
                    accepted_by_index = {
                        int(candidate["idx"]): candidate
                        for candidate in accepted
                    }
                    withheld: set[int] = set()
                    for candidate in job.candidates:
                        child_index = int(candidate["idx"])
                        translated, status, issues = payloads[child_index]
                        if not candidate_needs_sensitive_repair(
                            candidate,
                            status,
                            issues,
                            batch_cfg,
                            repair_round=2,
                        ):
                            continue
                        parent_entry = (
                            composition_plan.repair_parent_for_child(
                                child_index
                            )
                            if composition_plan is not None
                            else None
                        )
                        if (
                            parent_entry is None
                            or len(parent_entry.source) > max_parent_chars
                            or child_index not in accepted_by_index
                        ):
                            continue
                        request = {
                            "candidate": accepted_by_index[child_index],
                            "fallback_payload": (
                                translated,
                                status,
                                list(issues),
                            ),
                            "parent_entry": parent_entry,
                        }
                        parent_model = str(
                            candidate.get("sensitive_repair_model")
                            or model
                            or batch_cfg.get("api_sensitive_model")
                            or pipeline.model
                        )
                        followups.extend(schedule_parent_repair(
                            request,
                            model=parent_model,
                            sensitive_options=sensitive_options,
                        ))
                        withheld.add(child_index)
                    if withheld:
                        accepted = [
                            candidate
                            for candidate in accepted
                            if int(candidate["idx"]) not in withheld
                        ]
                        payloads = {
                            idx_key: payload
                            for idx_key, payload in payloads.items()
                            if int(idx_key) not in withheld
                        }
                apply_results(
                    accepted,
                    payloads,
                    batch_id=result.batch_id,
                    model=model,
                )
                return followups

            payloads = _finish_api_batch_result(
                pipeline,
                job,
                result,
                file_path,
                batch_options,
                batch_cfg,
            )
            accepted = list(job.candidates)
            fast_model = str(batch_cfg.get("api_fast_model") or "")
            configured_quality_model = str(
                batch_cfg.get("api_quality_model") or ""
            )
            quality_candidates: list[dict[str, Any]] = []
            if (
                batch_cfg.get("api_model_routing_enabled", False)
                and fast_model
                and configured_quality_model
                and fast_model != configured_quality_model
                and job.model in {fast_model, configured_quality_model}
            ):
                pending_ids = {
                    int(candidate["idx"])
                    for candidate in job.candidates
                    if candidate_needs_quality_model_retry(
                        candidate,
                        payloads[int(candidate["idx"])][1],
                        payloads[int(candidate["idx"])][2],
                        batch_cfg,
                    )
                }
                if pending_ids:
                    for candidate in job.candidates:
                        child_index = int(candidate["idx"])
                        if child_index not in pending_ids:
                            continue
                        translated, _status, issues = payloads[child_index]
                        quality_candidates.append(dict(
                            candidate,
                            quality_retry={
                                "previous": translated,
                                "issues": [
                                    str(issue.get("type", ""))
                                    for issue in issues
                                    if str(issue.get("type", ""))
                                ],
                            },
                        ))
                    accepted = [
                        candidate
                        for candidate in accepted
                        if int(candidate["idx"]) not in pending_ids
                    ]
                    payloads = {
                        idx_key: payload
                        for idx_key, payload in payloads.items()
                        if int(idx_key) not in pending_ids
                    }

            sensitive_ids = {
                int(candidate["idx"])
                for candidate in accepted
                if candidate_needs_sensitive_repair(
                    candidate,
                    payloads[int(candidate["idx"])][1],
                    payloads[int(candidate["idx"])][2],
                    batch_cfg,
                    repair_round=1,
                )
            }
            sensitive_candidates: list[dict[str, Any]] = []
            if sensitive_ids:
                for candidate in accepted:
                    child_index = int(candidate["idx"])
                    if child_index not in sensitive_ids:
                        continue
                    translated, _status, issues = payloads[child_index]
                    sensitive_candidates.append(
                        _sensitive_repair_candidate(
                            candidate,
                            translated,
                            issues,
                            model=model,
                            repair_round=1,
                            prior_retry_count=max(
                                0,
                                int(result.attempts) - 1,
                            ),
                        )
                    )
                accepted = [
                    candidate
                    for candidate in accepted
                    if int(candidate["idx"]) not in sensitive_ids
                ]
                payloads = {
                    idx_key: payload
                    for idx_key, payload in payloads.items()
                    if int(idx_key) not in sensitive_ids
                }
            apply_results(
                accepted,
                payloads,
                batch_id=result.batch_id,
                model=model,
                retry_count=max(0, int(result.attempts) - 1),
            )
            if quality_candidates and quality_model:
                followups.extend(_event_quality_jobs(
                    quality_candidates,
                    quality_model=quality_model,
                    quality_options=quality_options,
                    batch_size=batch_size,
                    max_batch_chars=max_batch_chars,
                    batch_cfg=batch_cfg,
                ))
            if sensitive_candidates:
                followups.extend(_event_sensitive_jobs(
                    sensitive_candidates,
                    repair_round=1,
                    batch_options=sensitive_options,
                    batch_cfg=batch_cfg,
                ))
            return followups

        initial_jobs = _interleave_event_jobs(
            parent_jobs,
            primary_jobs,
        )
        admission_policy = _build_model_admission_policy(batch_cfg)
        pipeline._run_dynamic_batches(
            initial_jobs,
            worker_count,
            pipeline._translate_api_batch_job,
            on_result,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff,
            check_stop=pipeline._check_control_flags,
            admission_policy=admission_policy,
        )
        pipeline._api_admission_snapshot = (
            admission_policy.snapshot()
            if admission_policy is not None
            else {"enabled": False}
        )
        token_usage.set_runtime_metadata(
            "adaptive_concurrency",
            pipeline._api_admission_snapshot,
        )
    finally:
        checkpoint.save_progress_many(file_path, collection_records)
        collection_records.clear()
        checkpoint.save_progress_many(file_path, result_records)
        result_records.clear()
        if deferred_confirmed_terms:
            pipeline._apply_confirmed_terms_to_outputs(
                file_path,
                list(deferred_confirmed_terms.values()),
            )
            pipeline.glossary.save()
        processed_targets = finalize_mtool_compositions(
            pipeline,
            file_path=file_path,
            translated_items=translated_items,
            processed_targets=processed_targets,
            total_targets=total_targets,
            progress_callback=progress_callback,
        )
        checkpoint.set_glossary_version(
            file_path,
            pipeline.glossary.version(),
            update_entries=True,
        )
        pipeline._update_token_usage(file_path)
        if pipeline._writer:
            pipeline._writer.stop()

    write_json_items(translated_items, target_path)
    return translated_items


__all__ = ["translate_json_batched_parallel_workflow"]
