from __future__ import annotations
from typing import Any, Callable, TYPE_CHECKING
import translation.checkpoint as checkpoint
from translation.batching import BatchJob, candidate_needs_sensitive_repair, pack_api_candidate_batches
from translation.output import write_json_items
from translation.workflow.parallel_decisions import partition_primary_result
from translation.workflow.parallel_progress import apply_parallel_results, finalize_parallel_run
from translation.workflow.parallel_support import (
    _build_parent_first_jobs,
    _build_sensitive_parent_repair_jobs,
    _build_sensitive_repair_jobs,
    _failed_parent_repair_payload,
    _finish_api_batch_result,
    _finish_parent_first_result,
    _parent_repair_child_payload,
    _repair_retry_count,
    _sensitive_repair_candidate,
    _sensitive_single_retry_model,
)

ProgressCallback = Callable[[dict[str, Any]], None]


if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


class PhasedBatchWorkflow:
    """Phased execution sharing acceptance policy and finalization with events."""

    def __init__(
        self,
        pipeline: TranslationPipeline,
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
    ) -> None:

        self.pipeline = pipeline
        self.file_path = file_path
        self.translated_items = translated_items
        self.mtool = mtool
        self.completed = completed
        self.target_path = target_path
        self.total_targets = total_targets
        self.progress_callback = progress_callback
        self.batch_size = batch_size
        self.max_batch_chars = max_batch_chars
        self.batch_options = batch_options
        self.configured_protocol = configured_protocol
        self.batch_protocol = batch_protocol
        self.batch_cfg = batch_cfg
        self.processed_targets = 0
        self.jobs: list[BatchJob] = []
        self.job_map: dict[str, BatchJob] = {}
        self.model_candidates: list[dict[str, Any]] = []
        self.collection_records: list[dict[str, Any]] = []
        self.result_records: list[dict[str, Any]] = []
        self.deferred_confirmed_terms: dict[tuple[str, str], dict[str, Any]] = {}
        self.pending_quality_candidates: list[dict[str, Any]] = []
        self.pending_sensitive_repair_candidates: list[dict[str, Any]] = []
        self.pending_sensitive_parent_repairs: dict[
            tuple[int, str],
            list[dict[str, Any]],
        ] = {}

        self.worker_count = 1
        self.max_retries = 0
        self.retry_backoff: list[float] = []
        self.sensitive_repair_options: dict[str, Any] = {}

    def _collect(self) -> None:
        idx = 0
        while idx < len(self.translated_items):
            self.pipeline._check_control_flags()
            candidates, next_idx, self.processed_targets = self.pipeline._collect_json_batch_window(
                self.translated_items,
                idx,
                self.mtool,
                self.completed,
                self.batch_size,
                self.max_batch_chars,
                self.file_path,
                self.total_targets,
                self.processed_targets,
                self.progress_callback,
                progress_records=self.collection_records,
            )
            if len(self.collection_records) >= 1000:
                checkpoint.save_progress_many(self.file_path, self.collection_records)
                self.collection_records.clear()
            if not candidates:
                idx = next_idx
                continue
            for candidate in candidates:
                self.pipeline._emit_progress(
                    self.progress_callback,
                    self.file_path,
                    candidate["idx"],
                    0,
                    "queued",
                    self.processed_targets,
                    self.total_targets,
                    original_text=candidate["source"],
                )
            self.model_candidates.extend(candidates)
            idx = next_idx

        checkpoint.save_progress_many(self.file_path, self.collection_records)
        self.collection_records.clear()
        self.worker_count = max(1, int(self.batch_cfg.get("api_concurrency", 1)))
        self.max_retries = max(0, int(self.batch_cfg.get("api_max_retries", 2)))
        self.retry_backoff = [
            float(item)
            for item in self.batch_cfg.get(
                "api_retry_backoff_seconds",
                [2, 5, 15],
            )
        ]

    def _translate_parents(self) -> None:
        parent_jobs, parent_job_map, self.model_candidates = _build_parent_first_jobs(
            self.pipeline,
            self.model_candidates,
            self.translated_items,
            batch_size=self.batch_size,
            max_batch_chars=self.max_batch_chars,
            batch_options=self.batch_options,
            batch_cfg=self.batch_cfg,
        )
        parent_fallback_candidates: list[dict[str, Any]] = []
        for result in self.pipeline._run_concurrent_batches(
            parent_jobs,
            self.worker_count,
            self.pipeline._translate_api_batch_job,
            max_retries=self.max_retries,
            retry_backoff_seconds=self.retry_backoff,
            check_stop=self.pipeline._check_control_flags,
        ):
            self.pipeline._check_control_flags()
            job = parent_job_map[result.batch_id]
            accepted, parent_payloads, fallback = _finish_parent_first_result(
                self.pipeline,
                job,
                result,
            )
            parent_fallback_candidates.extend(fallback)
            if not accepted:
                continue
            self.processed_targets = apply_parallel_results(
                pipeline=self.pipeline,
                deferred_confirmed_terms=self.deferred_confirmed_terms,
                candidates=accepted,
                translated_payloads=parent_payloads,
                translated_items=self.translated_items,
                processed_targets=self.processed_targets,
                total_targets=self.total_targets,
                progress_callback=self.progress_callback,
                file_path=self.file_path,
                mtool=self.mtool,
                progress_records=self.result_records,
                batch_id=result.batch_id,
                model_identifier=str(job.model or self.pipeline.model),
                retry_count=max(0, int(result.attempts) - 1),
            )

        self.model_candidates.extend(parent_fallback_candidates)

    def _translate_primary(self) -> None:
        for candidates in pack_api_candidate_batches(
            self.model_candidates,
            batch_size=self.batch_size,
            max_batch_chars=self.max_batch_chars,
            batch_cfg=self.batch_cfg,
        ):
            protocol = self.pipeline._resolve_parallel_candidate_protocol(
                self.configured_protocol,
                self.batch_protocol,
                candidates,
                self.batch_cfg,
            )
            job_model = self.pipeline._select_api_job_model(candidates, self.batch_cfg)
            job_options = self.pipeline._select_api_job_options(candidates, self.batch_options, self.batch_cfg)
            batch_id = f"api_batch_{len(self.jobs):06d}"
            job = BatchJob(batch_id=batch_id, candidates=candidates, protocol=protocol, model=job_model, options=job_options)
            self.jobs.append(job)
            self.job_map[batch_id] = job

        for result in self.pipeline._run_concurrent_batches(
            self.jobs,
            self.worker_count,
            self.pipeline._translate_api_batch_job,
            max_retries=self.max_retries,
            retry_backoff_seconds=self.retry_backoff,
            check_stop=self.pipeline._check_control_flags,
        ):
            self.pipeline._check_control_flags()
            job = self.job_map[result.batch_id]
            translated_payloads = _finish_api_batch_result(
                self.pipeline,
                job,
                result,
                self.file_path,
                self.batch_options,
                self.batch_cfg,
            )

            decision = partition_primary_result(job, translated_payloads, batch_cfg=self.batch_cfg,
                                                model=str(job.model or self.pipeline.model), attempts=result.attempts)
            accepted_candidates, translated_payloads = decision.accepted, decision.payloads
            self.pending_quality_candidates.extend(decision.quality)
            self.pending_sensitive_repair_candidates.extend(decision.sensitive)

            if not accepted_candidates:
                continue

            self.processed_targets = apply_parallel_results(
                pipeline=self.pipeline,
                deferred_confirmed_terms=self.deferred_confirmed_terms,
                candidates=accepted_candidates,
                translated_payloads=translated_payloads,
                translated_items=self.translated_items,
                processed_targets=self.processed_targets,
                total_targets=self.total_targets,
                progress_callback=self.progress_callback,
                file_path=self.file_path,
                mtool=self.mtool,
                progress_records=self.result_records,
                batch_id=result.batch_id,
                model_identifier=str(job.model or self.pipeline.model),
                retry_count=max(0, int(result.attempts) - 1),
            )

    def _repair_sensitive(self) -> None:
        self.sensitive_repair_options = dict(self.batch_options)
        for repair_round in (1, 2):
            if not self.pending_sensitive_repair_candidates:
                break
            if (
                repair_round == 2
                and not self.batch_cfg.get("api_sensitive_repair_single_retry", True)
            ):
                break

            repair_jobs, repair_job_map = _build_sensitive_repair_jobs(
                self.pending_sensitive_repair_candidates,
                repair_round=repair_round,
                batch_options=self.sensitive_repair_options,
                batch_cfg=self.batch_cfg,
            )
            self.pending_sensitive_repair_candidates = []
            for result in self.pipeline._run_concurrent_batches(
                repair_jobs,
                self.worker_count,
                self.pipeline._translate_api_batch_job,
                max_retries=self.max_retries,
                retry_backoff_seconds=self.retry_backoff,
                check_stop=self.pipeline._check_control_flags,
            ):
                self.pipeline._check_control_flags()
                job = repair_job_map[result.batch_id]
                translated_payloads = _finish_api_batch_result(
                    self.pipeline,
                    job,
                    result,
                    self.file_path,
                    self.sensitive_repair_options,
                    self.batch_cfg,
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
                    and self.batch_cfg.get("api_sensitive_repair_single_retry", True)
                )
                if allow_next_round:
                    pending_ids = {
                        candidate["idx"]
                        for candidate in job.candidates
                        if candidate_needs_sensitive_repair(
                            candidate,
                            translated_payloads[candidate["idx"]][1],
                            translated_payloads[candidate["idx"]][2],
                            self.batch_cfg,
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
                            self.pending_sensitive_repair_candidates.append(
                                _sensitive_repair_candidate(
                                    candidate,
                                    translated,
                                    issues,
                                    model=_sensitive_single_retry_model(
                                        self.batch_cfg,
                                        str(job.model or self.pipeline.model),
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
                    and self.batch_cfg.get("api_sensitive_parent_repair_enabled", True)
                ):
                    composition_plan = getattr(
                        self.pipeline,
                        "_mtool_composition_plan",
                        None,
                    )
                    max_parent_chars = max(
                        1,
                        int(
                            self.batch_cfg.get(
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
                            self.batch_cfg,
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
                            or self.batch_cfg.get("api_sensitive_model")
                            or self.pipeline.model
                        )
                        self.pending_sensitive_parent_repairs.setdefault(
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
                self.processed_targets = apply_parallel_results(
                    pipeline=self.pipeline,
                    deferred_confirmed_terms=self.deferred_confirmed_terms,
                    candidates=accepted_candidates,
                    translated_payloads=translated_payloads,
                    translated_items=self.translated_items,
                    processed_targets=self.processed_targets,
                    total_targets=self.total_targets,
                    progress_callback=self.progress_callback,
                    file_path=self.file_path,
                    mtool=self.mtool,
                    progress_records=self.result_records,
                    batch_id=result.batch_id,
                    model_identifier=str(job.model or self.pipeline.model),
                )

    def _repair_parents(self) -> None:
        if self.pending_sensitive_parent_repairs:
            parent_jobs, parent_job_map = _build_sensitive_parent_repair_jobs(
                self.pipeline,
                self.pending_sensitive_parent_repairs,
                batch_options=self.sensitive_repair_options,
            )
            requests_by_parent_model = self.pending_sensitive_parent_repairs
            for result in self.pipeline._run_concurrent_batches(
                parent_jobs,
                self.worker_count,
                self.pipeline._translate_api_batch_job,
                max_retries=self.max_retries,
                retry_backoff_seconds=self.retry_backoff,
                check_stop=self.pipeline._check_control_flags,
            ):
                self.pipeline._check_control_flags()
                job = parent_job_map[result.batch_id]
                parent_candidate = job.candidates[0]
                parent_entry = parent_candidate["_composition_entry"]
                request_key = (
                    int(parent_entry.parent_index),
                    str(job.model or self.pipeline.model),
                )
                requests = requests_by_parent_model[request_key]
                parent_payloads = _finish_api_batch_result(
                    self.pipeline,
                    job,
                    result,
                    self.file_path,
                    self.sensitive_repair_options,
                    self.batch_cfg,
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
                    self.pipeline._mtool_composition_plan.extract_child_translations(
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
                            self.pipeline,
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

                self.processed_targets = apply_parallel_results(
                    pipeline=self.pipeline,
                    deferred_confirmed_terms=self.deferred_confirmed_terms,
                    candidates=child_candidates,
                    translated_payloads=child_payloads,
                    translated_items=self.translated_items,
                    processed_targets=self.processed_targets,
                    total_targets=self.total_targets,
                    progress_callback=self.progress_callback,
                    file_path=self.file_path,
                    mtool=self.mtool,
                    progress_records=self.result_records,
                    batch_id=result.batch_id,
                    model_identifier=str(job.model or self.pipeline.model),
                )

    def _repair_quality(self) -> None:
        quality_model = str(self.batch_cfg.get("api_quality_model") or "")
        if self.pending_quality_candidates and quality_model:
            quality_options = dict(self.batch_options)
            if self.batch_cfg.get("quality_num_predict"):
                quality_options["num_predict"] = int(self.batch_cfg["quality_num_predict"])
            quality_jobs: list[BatchJob] = []
            quality_job_map: dict[str, BatchJob] = {}
            for candidates in pack_api_candidate_batches(
                self.pending_quality_candidates,
                batch_size=self.batch_size,
                max_batch_chars=self.max_batch_chars,
                batch_cfg=self.batch_cfg,
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

            for result in self.pipeline._run_concurrent_batches(
                quality_jobs,
                self.worker_count,
                self.pipeline._translate_api_batch_job,
                max_retries=self.max_retries,
                retry_backoff_seconds=self.retry_backoff,
                check_stop=self.pipeline._check_control_flags,
            ):
                self.pipeline._check_control_flags()
                job = quality_job_map[result.batch_id]
                translated_payloads = _finish_api_batch_result(
                    self.pipeline,
                    job,
                    result,
                    self.file_path,
                    quality_options,
                    self.batch_cfg,
                )
                self.processed_targets = apply_parallel_results(
                    pipeline=self.pipeline,
                    deferred_confirmed_terms=self.deferred_confirmed_terms,
                    candidates=job.candidates,
                    translated_payloads=translated_payloads,
                    translated_items=self.translated_items,
                    processed_targets=self.processed_targets,
                    total_targets=self.total_targets,
                    progress_callback=self.progress_callback,
                    file_path=self.file_path,
                    mtool=self.mtool,
                    progress_records=self.result_records,
                    batch_id=result.batch_id,
                    model_identifier=str(job.model or self.pipeline.model),
                    retry_count=max(0, int(result.attempts) - 1),
                )

    def run(self) -> list[tuple[Any, Any]]:
        try:
            self._collect()
            self._translate_parents()
            self._translate_primary()
            self._repair_sensitive()
            self._repair_parents()
            self._repair_quality()
        finally:
            self.processed_targets = finalize_parallel_run(
                self.pipeline, file_path=self.file_path, translated_items=self.translated_items,
                processed_targets=self.processed_targets, total_targets=self.total_targets,
                progress_callback=self.progress_callback, collection_records=self.collection_records,
                result_records=self.result_records, deferred_confirmed_terms=self.deferred_confirmed_terms,
            )

        write_json_items(self.translated_items, self.target_path)
        return self.translated_items


def translate_phased_batches(
    pipeline: TranslationPipeline,
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

    return PhasedBatchWorkflow(
        pipeline, file_path, translated_items, mtool, completed, target_path,
        total_targets, progress_callback, batch_size, max_batch_chars,
        batch_options, configured_protocol, batch_protocol, batch_cfg,
    ).run()
