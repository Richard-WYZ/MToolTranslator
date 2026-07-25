from __future__ import annotations

import time
import heapq
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ThreadPoolExecutor,
    as_completed,
    wait,
)
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from translation.batching.payloads import BatchTranslationError


@dataclass(frozen=True)
class BatchJob:
    batch_id: str
    candidates: list[dict[str, Any]]
    protocol: str
    model: str | None = None
    options: dict[str, Any] | None = None
    priority: int = 10


@dataclass(frozen=True)
class BatchResult:
    batch_id: str
    translations: dict[int, str]
    error: Exception | None
    attempts: int
    elapsed_seconds: float


BatchCallable = Callable[[BatchJob], dict[int, str]]
StopCallable = Callable[[], None]
ResultCallback = Callable[[BatchJob, BatchResult], Iterable[BatchJob] | None]


@dataclass
class _ModelAdmissionState:
    window: int
    maximum: int
    maximum_inflight_chars: int
    active: int = 0
    active_chars: int = 0
    peak_active: int = 0
    peak_active_chars: int = 0
    peak_window: int = 0
    clean_successes: int = 0
    completions: int = 0
    retry_results: int = 0
    errors: int = 0
    increases: int = 0
    decreases: int = 0

    def __post_init__(self) -> None:
        self.peak_window = self.window


@dataclass
class ModelAdmissionPolicy:
    """Independent additive-increase/multiplicative-decrease windows per model."""

    initial_by_model: dict[str, int] = field(default_factory=dict)
    maximum_by_model: dict[str, int] = field(default_factory=dict)
    maximum_inflight_chars_by_model: dict[str, int] = field(default_factory=dict)
    default_initial: int = 1
    default_maximum: int = 1
    default_maximum_inflight_chars: int = 40000
    increase_every: int = 8
    decrease_factor: float = 0.5
    minimum: int = 1
    _states: dict[str, _ModelAdmissionState] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.minimum = max(1, int(self.minimum))
        self.default_initial = max(self.minimum, int(self.default_initial))
        self.default_maximum = max(
            self.default_initial,
            int(self.default_maximum),
        )
        self.default_maximum_inflight_chars = max(
            1,
            int(self.default_maximum_inflight_chars),
        )
        self.increase_every = max(1, int(self.increase_every))
        self.decrease_factor = min(0.99, max(0.01, float(self.decrease_factor)))
        self.initial_by_model = {
            str(model): max(self.minimum, int(value))
            for model, value in self.initial_by_model.items()
            if str(model)
        }
        self.maximum_by_model = {
            str(model): max(self.minimum, int(value))
            for model, value in self.maximum_by_model.items()
            if str(model)
        }
        self.maximum_inflight_chars_by_model = {
            str(model): max(1, int(value))
            for model, value in self.maximum_inflight_chars_by_model.items()
            if str(model)
        }
        for model, initial in self.initial_by_model.items():
            self.maximum_by_model[model] = max(
                initial,
                self.maximum_by_model.get(model, initial),
            )

    @staticmethod
    def _model_key(job: BatchJob) -> str:
        return str(job.model or "__default__")

    def _state(self, job: BatchJob) -> _ModelAdmissionState:
        key = self._model_key(job)
        state = self._states.get(key)
        if state is None:
            initial = self.initial_by_model.get(key, self.default_initial)
            maximum = max(
                initial,
                self.maximum_by_model.get(key, self.default_maximum),
            )
            maximum_inflight_chars = self.maximum_inflight_chars_by_model.get(
                key,
                self.default_maximum_inflight_chars,
            )
            state = _ModelAdmissionState(
                initial,
                maximum,
                maximum_inflight_chars,
            )
            self._states[key] = state
        return state

    def can_submit(self, job: BatchJob) -> bool:
        state = self._state(job)
        if state.active >= state.window:
            return False
        projected_chars = state.active_chars + _job_character_cost(job)
        return (
            state.active == 0
            or projected_chars <= state.maximum_inflight_chars
        )

    def submitted(self, job: BatchJob) -> None:
        state = self._state(job)
        state.active += 1
        state.active_chars += _job_character_cost(job)
        state.peak_active = max(state.peak_active, state.active)
        state.peak_active_chars = max(
            state.peak_active_chars,
            state.active_chars,
        )

    def completed(self, job: BatchJob, result: BatchResult) -> None:
        state = self._state(job)
        state.active = max(0, state.active - 1)
        state.active_chars = max(
            0,
            state.active_chars - _job_character_cost(job),
        )
        state.completions += 1
        if result.attempts > 1:
            state.retry_results += 1
        if result.error is not None:
            state.errors += 1

        if _result_signals_transport_congestion(result):
            reduced = max(
                self.minimum,
                int(state.window * self.decrease_factor),
            )
            if reduced < state.window:
                state.window = reduced
                state.decreases += 1
            state.clean_successes = 0
            return

        if result.error is not None:
            # Structured/content validation failures are neutral admission
            # signals: they may need splitting or repair, but do not describe
            # provider transport capacity.
            return
        if result.attempts != 1:
            state.clean_successes = 0
            return

        state.clean_successes += 1
        if (
            state.clean_successes >= self.increase_every
            and state.window < state.maximum
        ):
            state.window += 1
            state.peak_window = max(state.peak_window, state.window)
            state.increases += 1
            state.clean_successes = 0

    def executor_capacity(self) -> int:
        configured = set(self.initial_by_model) | set(self.maximum_by_model)
        if not configured:
            return self.default_maximum
        return sum(
            max(
                self.initial_by_model.get(model, self.default_initial),
                self.maximum_by_model.get(model, self.default_maximum),
            )
            for model in configured
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "increase_every": self.increase_every,
            "decrease_factor": self.decrease_factor,
            "models": {
                key: {
                    "window": state.window,
                    "maximum": state.maximum,
                    "maximum_inflight_chars": state.maximum_inflight_chars,
                    "active": state.active,
                    "active_chars": state.active_chars,
                    "peak_active": state.peak_active,
                    "peak_active_chars": state.peak_active_chars,
                    "peak_window": state.peak_window,
                    "completions": state.completions,
                    "retry_results": state.retry_results,
                    "errors": state.errors,
                    "increases": state.increases,
                    "decreases": state.decreases,
                }
                for key, state in sorted(self._states.items())
            },
        }


def _job_character_cost(job: BatchJob) -> int:
    candidate_chars = sum(
        len(str(
            candidate.get("protected")
            or candidate.get("text")
            or candidate.get("source")
            or ""
        ))
        for candidate in job.candidates
    )
    context_chars = sum(
        len(str(context.get("text", "")))
        for candidate in job.candidates
        for context in candidate.get("contexts", []) or []
        if isinstance(context, dict)
    )
    return max(1, candidate_chars + context_chars)


def run_concurrent_batches(
    jobs: Iterable[BatchJob],
    worker_count: int,
    translate_job: BatchCallable,
    *,
    max_retries: int = 2,
    retry_backoff_seconds: list[float] | None = None,
    check_stop: StopCallable | None = None,
) -> Iterable[BatchResult]:
    """Run independent model batch requests concurrently."""
    queued = list(jobs)
    if not queued:
        return []
    workers = max(1, min(int(worker_count), len(queued)))
    retries = max(0, int(max_retries))
    backoff = retry_backoff_seconds or [2.0, 5.0, 15.0]

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="api-batch") as executor:
        future_map: dict[Future[BatchResult], str] = {}
        for job in queued:
            if check_stop:
                check_stop()
            future = executor.submit(_run_with_retries, job, translate_job, retries, backoff, check_stop)
            future_map[future] = job.batch_id

        for future in as_completed(future_map):
            if check_stop:
                check_stop()
            yield future.result()


def run_dynamic_batches(
    initial_jobs: Iterable[BatchJob],
    worker_count: int,
    translate_job: BatchCallable,
    on_result: ResultCallback,
    *,
    max_retries: int = 2,
    retry_backoff_seconds: list[float] | None = None,
    check_stop: StopCallable | None = None,
    admission_policy: ModelAdmissionPolicy | None = None,
) -> list[BatchResult]:
    """Run a bounded queue whose coordinator may enqueue follow-up jobs."""
    retries = max(0, int(max_retries))
    backoff = retry_backoff_seconds or [2.0, 5.0, 15.0]
    workers = max(1, int(worker_count))
    if admission_policy is not None:
        workers = max(workers, admission_policy.executor_capacity())
    ready: list[tuple[int, int, BatchJob]] = []
    sequence = 0
    known_ids: set[str] = set()

    def enqueue(job: BatchJob) -> None:
        nonlocal sequence
        if job.batch_id in known_ids:
            raise ValueError(f"duplicate dynamic batch id: {job.batch_id}")
        known_ids.add(job.batch_id)
        heapq.heappush(ready, (int(job.priority), sequence, job))
        sequence += 1

    for initial_job in initial_jobs:
        enqueue(initial_job)
    if not ready:
        return []

    completed: list[BatchResult] = []

    def pop_admissible() -> BatchJob | None:
        if not ready:
            return None
        held: list[tuple[int, int, BatchJob]] = []
        selected: BatchJob | None = None
        while ready:
            item = heapq.heappop(ready)
            job = item[2]
            if admission_policy is None or admission_policy.can_submit(job):
                selected = job
                break
            held.append(item)
        for item in held:
            heapq.heappush(ready, item)
        return selected

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="api-batch",
    ) as executor:
        future_map: dict[Future[BatchResult], BatchJob] = {}
        while ready or future_map:
            while ready and len(future_map) < workers:
                if check_stop:
                    check_stop()
                job = pop_admissible()
                if job is None:
                    break
                if admission_policy is not None:
                    admission_policy.submitted(job)
                future = executor.submit(
                    _run_with_retries,
                    job,
                    translate_job,
                    retries,
                    backoff,
                    check_stop,
                )
                future_map[future] = job
            if not future_map:
                continue
            done, _pending = wait(
                tuple(future_map),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                if check_stop:
                    check_stop()
                job = future_map.pop(future)
                result = future.result()
                if admission_policy is not None:
                    admission_policy.completed(job, result)
                completed.append(result)
                followups = on_result(job, result) or ()
                for followup in followups:
                    enqueue(followup)
    return completed


def _result_signals_transport_congestion(result: BatchResult) -> bool:
    if result.attempts > 1:
        return True
    error = result.error
    if error is None or isinstance(error, BatchTranslationError):
        return False
    if getattr(error, "retry_after_seconds", None) is not None:
        return True
    status_code = getattr(error, "status_code", None)
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        status = 0
    return status >= 500 or status in {408, 409, 425, 429}


def _run_with_retries(
    job: BatchJob,
    translate_job: BatchCallable,
    max_retries: int,
    retry_backoff_seconds: list[float],
    check_stop: StopCallable | None,
) -> BatchResult:
    start = time.perf_counter()
    attempts = 0
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            if check_stop:
                check_stop()
            translations = translate_job(job)
            elapsed = time.perf_counter() - start
            return BatchResult(job.batch_id, translations, None, attempts, elapsed)
        except Exception as exc:
            last_error = exc
            if isinstance(exc, BatchTranslationError) or getattr(exc, "retryable", True) is False:
                break
            if attempt >= max_retries:
                break
            delay = retry_backoff_seconds[min(attempt, len(retry_backoff_seconds) - 1)]
            retry_after = getattr(exc, "retry_after_seconds", None)
            if retry_after is not None:
                delay = max(float(delay), max(0.0, float(retry_after)))
            if delay > 0:
                time.sleep(delay)
    elapsed = time.perf_counter() - start
    return BatchResult(job.batch_id, {}, last_error, attempts, elapsed)


__all__ = [
    "BatchJob",
    "BatchResult",
    "ModelAdmissionPolicy",
    "run_concurrent_batches",
    "run_dynamic_batches",
]
