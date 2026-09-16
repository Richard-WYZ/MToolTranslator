from __future__ import annotations
import hashlib
from typing import Any, Callable, TYPE_CHECKING
from translation.batching import (
    BatchJob,
    candidate_needs_quality_model_retry,
    pack_api_candidate_batches,
    reindex_candidates,
)
from translation.workflow.parallel_support import _build_sensitive_repair_jobs
from translation.workflow.parallel_decisions import _STRUCTURAL_QUALITY_ISSUE_TYPES

ProgressCallback = Callable[[dict[str, Any]], None]


if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


def _event_candidate_suffix(candidates: list[dict[str, Any]]) -> str:
    indexes = ",".join(
        str(int(candidate["idx"]))
        for candidate in sorted(candidates, key=lambda item: int(item["idx"]))
    )
    return hashlib.sha256(indexes.encode("utf-8")).hexdigest()[:12]


def _event_primary_jobs(
    pipeline: TranslationPipeline,
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
