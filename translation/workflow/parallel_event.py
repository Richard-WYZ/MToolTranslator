from __future__ import annotations
import hashlib
from typing import Any, Callable, TYPE_CHECKING
import translation.checkpoint as checkpoint
import translation.usage as token_usage
from translation.batching import BatchJob, candidate_needs_sensitive_repair, reindex_candidates
from translation.output import write_json_items
from translation.workflow.parallel_decisions import partition_primary_result
from translation.workflow.parallel_progress import apply_parallel_results, finalize_parallel_run
from translation.workflow.parallel_repair import (
    _event_candidate_suffix,
    _event_primary_jobs,
    _event_quality_followups,
    _event_quality_jobs,
    _event_sensitive_jobs,
    _interleave_event_jobs,
)
from translation.workflow.parallel_support import (
    _build_model_admission_policy,
    _build_parent_first_jobs,
    _build_sensitive_parent_repair_jobs,
    _failed_parent_repair_payload,
    _finish_api_batch_result,
    _finish_parent_first_result,
    _parent_repair_child_payload,
    _repair_retry_count,
    _sensitive_repair_candidate,
    _sensitive_single_retry_model,
)
from translation.workflow.parallel_decisions import _STRUCTURAL_QUALITY_ISSUE_TYPES

ProgressCallback = Callable[[dict[str, Any]], None]


if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


class EventBatchWorkflow:
    """Per-run state, with a dedicated handler for each completed job stage."""

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
        self.collection_records: list[dict[str, Any]] = []
        self.result_records: list[dict[str, Any]] = []
        self.deferred_confirmed_terms: dict[tuple[str, str], dict[str, Any]] = {}
        self.parent_fallback_buffer: list[dict[str, Any]] = []
        self.pending_parent_repairs: dict[
            tuple[int, str],
            list[dict[str, Any]],
        ] = {}
        self.active_parent_repairs: set[tuple[int, str]] = set()
        self.parent_repair_outcomes: dict[
            tuple[int, str],
            dict[str, Any],
        ] = {}
        self.remaining_parent_jobs = 0
        self.quality_model = ""
        self.quality_options: dict[str, Any] = {}
        self.sensitive_options: dict[str, Any] = {}

    def apply_results(
        self,
        candidates: list[dict[str, Any]],
        payloads: dict[int, tuple[str, str, list[dict[str, Any]]]],
        *,
        batch_id: str,
        model: str,
        retry_count: int | None = None,
    ) -> None:
        if not candidates:
            return
        self.processed_targets = apply_parallel_results(
            pipeline=self.pipeline,
            deferred_confirmed_terms=self.deferred_confirmed_terms,
            candidates=candidates,
            translated_payloads=payloads,
            translated_items=self.translated_items,
            processed_targets=self.processed_targets,
            total_targets=self.total_targets,
            progress_callback=self.progress_callback,
            file_path=self.file_path,
            mtool=self.mtool,
            progress_records=self.result_records,
            batch_id=batch_id,
            model_identifier=model,
            retry_count=retry_count,
        )

    def process_parent_repair_requests(
        self,
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
            issue_types = {
                str(issue.get("type", ""))
                for issue in child_payload[2]
                if isinstance(issue, dict) and str(issue.get("type", ""))
            }
            if (
                child_payload[1] == "review_required"
                and issue_types & _STRUCTURAL_QUALITY_ISSUE_TYPES
                and self.quality_model
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
                    model=self.quality_model,
                    options=dict(self.quality_options),
                    priority=0,
                ))
                continue
            child_candidates.append(final_candidate)
            child_payloads[child_index] = child_payload
        self.apply_results(
            child_candidates,
            child_payloads,
            batch_id=outcome["batch_id"],
            model=key[1],
        )
        return terminal_jobs

    def schedule_parent_repair(
        self,
        request: dict[str, Any],
        *,
        model: str,
        sensitive_options: dict[str, Any],
    ) -> list[BatchJob]:
        parent_entry = request["parent_entry"]
        key = (int(parent_entry.parent_index), model)
        cached = self.parent_repair_outcomes.get(key)
        if cached is not None:
            return self.process_parent_repair_requests(key, [request], cached)
        self.pending_parent_repairs.setdefault(key, []).append(request)
        if key in self.active_parent_repairs:
            return []
        self.active_parent_repairs.add(key)
        jobs, _job_map = _build_sensitive_parent_repair_jobs(
            self.pipeline,
            {key: self.pending_parent_repairs[key]},
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

    def _on_parent_first(self, job: BatchJob, result: Any) -> list[BatchJob]:
        followups: list[BatchJob] = []
        model = str(job.model or self.pipeline.model)
        accepted, payloads, fallback = _finish_parent_first_result(
            self.pipeline,
            job,
            result,
        )
        self.apply_results(
            accepted,
            payloads,
            batch_id=result.batch_id,
            model=model,
            retry_count=max(0, int(result.attempts) - 1),
        )
        self.parent_fallback_buffer.extend(fallback)
        self.remaining_parent_jobs -= 1
        if (
            len(self.parent_fallback_buffer) >= self.batch_size
            or self.remaining_parent_jobs == 0
        ):
            buffered = list(self.parent_fallback_buffer)
            self.parent_fallback_buffer.clear()
            followups.extend(_event_primary_jobs(
                self.pipeline,
                buffered,
                prefix="api_event_parent_fallback",
                priority=0,
                batch_size=self.batch_size,
                max_batch_chars=self.max_batch_chars,
                batch_options=self.batch_options,
                configured_protocol=self.configured_protocol,
                batch_protocol=self.batch_protocol,
                batch_cfg=self.batch_cfg,
            ))
        return followups

    def _on_quality(self, job: BatchJob, result: Any) -> list[BatchJob]:
        followups: list[BatchJob] = []
        model = str(job.model or self.pipeline.model)
        payloads = _finish_api_batch_result(
            self.pipeline,
            job,
            result,
            self.file_path,
            self.quality_options,
            self.batch_cfg,
        )
        accepted, accepted_payloads, quality_followups = (
            _event_quality_followups(
                job,
                payloads,
                quality_model=self.quality_model,
                quality_options=self.quality_options,
                batch_cfg=self.batch_cfg,
            )
        )
        self.apply_results(
            accepted,
            accepted_payloads,
            batch_id=result.batch_id,
            model=model,
            retry_count=max(0, int(result.attempts) - 1),
        )
        return quality_followups

    def _on_sensitive_parent(self, job: BatchJob, result: Any) -> list[BatchJob]:
        followups: list[BatchJob] = []
        model = str(job.model or self.pipeline.model)
        parent_candidate = job.candidates[0]
        parent_entry = parent_candidate["_composition_entry"]
        key = (int(parent_entry.parent_index), model)
        parent_payloads = _finish_api_batch_result(
            self.pipeline,
            job,
            result,
            self.file_path,
            self.sensitive_options,
            self.batch_cfg,
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
            self.pipeline._mtool_composition_plan.extract_child_translations(
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
        self.parent_repair_outcomes[key] = outcome
        self.active_parent_repairs.discard(key)
        requests = self.pending_parent_repairs.pop(key, [])
        return self.process_parent_repair_requests(
            key,
            requests,
            outcome,
        )

    def _on_sensitive_terminal(self, job: BatchJob, result: Any) -> list[BatchJob]:
        followups: list[BatchJob] = []
        model = str(job.model or self.pipeline.model)
        payloads = _finish_api_batch_result(
            self.pipeline,
            job,
            result,
            self.file_path,
            self.quality_options,
            self.batch_cfg,
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
        self.apply_results(
            accepted,
            payloads,
            batch_id=result.batch_id,
            model=model,
        )
        return []

    def _on_sensitive_round(self, job: BatchJob, result: Any) -> list[BatchJob]:
        followups: list[BatchJob] = []
        model = str(job.model or self.pipeline.model)
        repair_round = (
            2
            if job.batch_id.startswith("api_event_sensitive_r2_")
            else 1
        )
        payloads = _finish_api_batch_result(
            self.pipeline,
            job,
            result,
            self.file_path,
            self.sensitive_options,
            self.batch_cfg,
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
            and self.batch_cfg.get(
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
                    self.batch_cfg,
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
                                self.batch_cfg,
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
                    batch_options=self.sensitive_options,
                    batch_cfg=self.batch_cfg,
                ))

        if (
            repair_round == 2
            and self.batch_cfg.get(
                "api_sensitive_parent_repair_enabled",
                True,
            )
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
                    self.batch_cfg,
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
                    or self.batch_cfg.get("api_sensitive_model")
                    or self.pipeline.model
                )
                followups.extend(self.schedule_parent_repair(
                    request,
                    model=parent_model,
                    sensitive_options=self.sensitive_options,
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
        self.apply_results(
            accepted,
            payloads,
            batch_id=result.batch_id,
            model=model,
        )
        return followups

    def on_result(self, job: BatchJob, result: Any) -> list[BatchJob]:
        self.pipeline._check_control_flags()
        if job.batch_id.startswith("api_parent_first_"):
            return self._on_parent_first(job, result)
        if job.batch_id.startswith("api_event_quality_"):
            return self._on_quality(job, result)
        if job.batch_id.startswith("api_event_sensitive_parent_"):
            return self._on_sensitive_parent(job, result)
        if job.batch_id.startswith("api_event_sensitive_terminal_"):
            return self._on_sensitive_terminal(job, result)
        if job.batch_id.startswith("api_event_sensitive_r"):
            return self._on_sensitive_round(job, result)
        return self._on_primary(job, result)

    def _on_primary(self, job: BatchJob, result: Any) -> list[BatchJob]:
        followups: list[BatchJob] = []
        model = str(job.model or self.pipeline.model)
        payloads = _finish_api_batch_result(
            self.pipeline,
            job,
            result,
            self.file_path,
            self.batch_options,
            self.batch_cfg,
        )
        decision = partition_primary_result(job, payloads, batch_cfg=self.batch_cfg,
                                            model=model, attempts=result.attempts)
        accepted, payloads = decision.accepted, decision.payloads
        quality_candidates, sensitive_candidates = decision.quality, decision.sensitive
        self.apply_results(
            accepted,
            payloads,
            batch_id=result.batch_id,
            model=model,
            retry_count=max(0, int(result.attempts) - 1),
        )
        if quality_candidates and self.quality_model:
            followups.extend(_event_quality_jobs(
                quality_candidates,
                quality_model=self.quality_model,
                quality_options=self.quality_options,
                batch_size=self.batch_size,
                max_batch_chars=self.max_batch_chars,
                batch_cfg=self.batch_cfg,
            ))
        if sensitive_candidates:
            followups.extend(_event_sensitive_jobs(
                sensitive_candidates,
                repair_round=1,
                batch_options=self.sensitive_options,
                batch_cfg=self.batch_cfg,
            ))
        return followups

    def run(self) -> list[tuple[Any, Any]]:
        try:
            model_candidates: list[dict[str, Any]] = []
            index = 0
            while index < len(self.translated_items):
                self.pipeline._check_control_flags()
                candidates, next_index, self.processed_targets = (
                    self.pipeline._collect_json_batch_window(
                        self.translated_items,
                        index,
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
                )
                if len(self.collection_records) >= 1000:
                    checkpoint.save_progress_many(self.file_path, self.collection_records)
                    self.collection_records.clear()
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
                model_candidates.extend(candidates)
                index = next_index

            checkpoint.save_progress_many(self.file_path, self.collection_records)
            self.collection_records.clear()
            worker_count = max(1, int(self.batch_cfg.get("api_concurrency", 1)))
            max_retries = max(0, int(self.batch_cfg.get("api_max_retries", 2)))
            retry_backoff = [
                float(item)
                for item in self.batch_cfg.get(
                    "api_retry_backoff_seconds",
                    [2, 5, 15],
                )
            ]
            self.sensitive_options = dict(self.batch_options)
            self.quality_options = dict(self.batch_options)
            if self.batch_cfg.get("quality_num_predict"):
                self.quality_options["num_predict"] = int(
                    self.batch_cfg["quality_num_predict"]
                )
            self.quality_model = str(self.batch_cfg.get("api_quality_model") or "")

            parent_jobs, _parent_job_map, standalone_candidates = (
                _build_parent_first_jobs(
                    self.pipeline,
                    model_candidates,
                    self.translated_items,
                    batch_size=self.batch_size,
                    max_batch_chars=self.max_batch_chars,
                    batch_options=self.batch_options,
                    batch_cfg=self.batch_cfg,
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
            self.remaining_parent_jobs = len(parent_jobs)
            primary_jobs = _event_primary_jobs(
                self.pipeline,
                standalone_candidates,
                prefix="api_event_primary",
                priority=10,
                batch_size=self.batch_size,
                max_batch_chars=self.max_batch_chars,
                batch_options=self.batch_options,
                configured_protocol=self.configured_protocol,
                batch_protocol=self.batch_protocol,
                batch_cfg=self.batch_cfg,
            )


            initial_jobs = _interleave_event_jobs(
                parent_jobs,
                primary_jobs,
            )
            admission_policy = _build_model_admission_policy(self.batch_cfg)
            self.pipeline._run_dynamic_batches(
                initial_jobs,
                worker_count,
                self.pipeline._translate_api_batch_job,
                self.on_result,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff,
                check_stop=self.pipeline._check_control_flags,
                admission_policy=admission_policy,
            )
            self.pipeline._api_admission_snapshot = (
                admission_policy.snapshot()
                if admission_policy is not None
                else {"enabled": False}
            )
            token_usage.set_runtime_metadata(
                "adaptive_concurrency",
                self.pipeline._api_admission_snapshot,
            )
        finally:
            self.processed_targets = finalize_parallel_run(
                self.pipeline, file_path=self.file_path, translated_items=self.translated_items,
                processed_targets=self.processed_targets, total_targets=self.total_targets,
                progress_callback=self.progress_callback, collection_records=self.collection_records,
                result_records=self.result_records, deferred_confirmed_terms=self.deferred_confirmed_terms,
            )

        write_json_items(self.translated_items, self.target_path)
        return self.translated_items


def _translate_json_batched_event_workflow(
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
    return EventBatchWorkflow(
        pipeline, file_path, translated_items, mtool, completed, target_path,
        total_targets, progress_callback, batch_size, max_batch_chars,
        batch_options, configured_protocol, batch_protocol, batch_cfg,
    ).run()
