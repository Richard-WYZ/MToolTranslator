from __future__ import annotations
import json
from typing import Any, Callable, TYPE_CHECKING
from translation.batching import (
    BatchJob,
    BatchTranslationError,
    ModelAdmissionPolicy,
    pack_api_candidate_batches,
    prepare_model_candidate,
    reindex_candidates,
)
from translation.protection import protect_symbols
from translation.quality import apply_source_conditioned_fixes, new_issues, translation_issues

ProgressCallback = Callable[[dict[str, Any]], None]


if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


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
    pipeline: TranslationPipeline,
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
    pipeline: TranslationPipeline,
    candidate: dict[str, Any],
    translated: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Validate an extracted parent line as an ordinary child translation."""
    source = str(candidate["source"])
    translated = pipeline.glossary.apply_post_translation(source, str(translated))
    translated = apply_source_conditioned_fixes(source, translated)
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
    pipeline: TranslationPipeline,
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
    pipeline: TranslationPipeline,
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
    pipeline: TranslationPipeline,
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
    pipeline: TranslationPipeline,
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
    pipeline: TranslationPipeline,
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
    pipeline: TranslationPipeline,
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
        retry_protocol = "json" if job.protocol == "line" else job.protocol
        translated_payloads.update(pipeline._translate_json_candidates(
            retry_candidates,
            file_path,
            job.options or default_options,
            retry_protocol,
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
