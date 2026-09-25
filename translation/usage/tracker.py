from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Iterator


class UsageTracker:
    """Thread-safe counters owned by one run, independent of other tasks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: dict[str, Any] = {}
        self._request_latencies: dict[tuple[str, str], list[float]] = {}
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._totals["prompt_tokens"] = 0
            self._totals["completion_tokens"] = 0
            self._totals["total_tokens"] = 0
            self._totals["calls"] = 0
            self._totals["request_calls"] = 0
            self._totals["first_request_started"] = None
            self._totals["last_response_received"] = None
            self._totals["by_provider"] = {}
            self._totals["runtime"] = {}
            self._request_latencies.clear()


    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = deepcopy(self._totals)
            latency_samples = deepcopy(self._request_latencies)
        started = result.pop("first_request_started", None)
        finished = result.pop("last_response_received", None)
        result["translation_phase_seconds"] = (
            max(0.0, float(finished) - float(started))
            if started is not None and finished is not None
            else 0.0
        )
        result["request_latency_seconds"] = _latency_summary(latency_samples)
        return result


    def record_request_start(self, provider: str = "", model: str = "") -> float:
        now = time.perf_counter()
        with self._lock:
            if self._totals["first_request_started"] is None:
                self._totals["first_request_started"] = now
            self._totals["request_calls"] += 1
        return now


    def record_response_received(
        self,
        provider: str = "",
        model: str = "",
        started_at: float | None = None,
    ) -> None:
        now = time.perf_counter()
        with self._lock:
            self._totals["last_response_received"] = now
            if started_at is not None:
                key = (provider or "unknown", model or "unknown")
                self._request_latencies.setdefault(key, []).append(max(0.0, now - float(started_at)))


    def record(self, provider: str, model: str, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        if prompt_tokens == 0 and completion_tokens == 0 and total_tokens == 0:
            return

        provider = provider or "unknown"
        model = model or "unknown"
        with self._lock:
            self._totals["prompt_tokens"] += prompt_tokens
            self._totals["completion_tokens"] += completion_tokens
            self._totals["total_tokens"] += total_tokens
            self._totals["calls"] += 1
            provider_totals = self._totals["by_provider"].setdefault(provider, {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
                "models": {},
            })
            provider_totals["prompt_tokens"] += prompt_tokens
            provider_totals["completion_tokens"] += completion_tokens
            provider_totals["total_tokens"] += total_tokens
            provider_totals["calls"] += 1
            model_totals = provider_totals["models"].setdefault(model, {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
            })
            model_totals["prompt_tokens"] += prompt_tokens
            model_totals["completion_tokens"] += completion_tokens
            model_totals["total_tokens"] += total_tokens
            model_totals["calls"] += 1


    def set_runtime_metadata(self, key: str, value: Any) -> None:
        with self._lock:
            self._totals["runtime"][str(key)] = deepcopy(value)


def _latency_summary(samples: dict[tuple[str, str], list[float]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for (provider, model), values in samples.items():
        if not values:
            continue
        ordered = sorted(float(value) for value in values)
        provider_summary = summary.setdefault(provider, {"models": {}})
        provider_summary["models"][model] = {
            "count": len(ordered),
            "total": round(sum(ordered), 6),
            "mean": round(sum(ordered) / len(ordered), 6),
            "min": round(ordered[0], 6),
            "p50": round(_percentile(ordered, 0.50), 6),
            "p95": round(_percentile(ordered, 0.95), 6),
            "max": round(ordered[-1], 6),
        }
    return summary


def _percentile(ordered: list[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def diff(before: dict[str, Any], after: dict[str, Any] | None = None) -> dict[str, Any]:
    after = after or snapshot()
    return {
        "prompt_tokens": int(after.get("prompt_tokens", 0)) - int(before.get("prompt_tokens", 0)),
        "completion_tokens": int(after.get("completion_tokens", 0)) - int(before.get("completion_tokens", 0)),
        "total_tokens": int(after.get("total_tokens", 0)) - int(before.get("total_tokens", 0)),
        "calls": int(after.get("calls", 0)) - int(before.get("calls", 0)),
    }


# Direct transport/diagnostic callers retain a default collector. Translation
# and AI review runs always bind their own tracker; executor workers inherit it.
_default_tracker = UsageTracker()
_active_tracker: ContextVar[UsageTracker] = ContextVar("translation_usage", default=_default_tracker)


@contextmanager
def use_tracker(tracker: UsageTracker) -> Iterator[UsageTracker]:
    token = _active_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _active_tracker.reset(token)


def reset() -> None:
    _active_tracker.get().reset()


def snapshot() -> dict[str, Any]:
    return _active_tracker.get().snapshot()


def record_request_start(provider: str = "", model: str = "") -> float:
    return _active_tracker.get().record_request_start(provider, model)


def record_response_received(provider: str = "", model: str = "", started_at: float | None = None) -> None:
    _active_tracker.get().record_response_received(provider, model, started_at)


def record(provider: str, model: str, usage: dict[str, Any] | None) -> None:
    _active_tracker.get().record(provider, model, usage)


def set_runtime_metadata(key: str, value: Any) -> None:
    _active_tracker.get().set_runtime_metadata(key, value)


__all__ = ["UsageTracker", "use_tracker", "diff", "record", "record_request_start", "record_response_received", "reset", "set_runtime_metadata", "snapshot"]
