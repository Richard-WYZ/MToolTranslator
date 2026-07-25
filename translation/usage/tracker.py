from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any


_lock = threading.Lock()
_totals: dict[str, Any] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "calls": 0,
    "request_calls": 0,
    "first_request_started": None,
    "last_response_received": None,
    "by_provider": {},
    "runtime": {},
}
_request_latencies: dict[tuple[str, str], list[float]] = {}


def reset() -> None:
    with _lock:
        _totals["prompt_tokens"] = 0
        _totals["completion_tokens"] = 0
        _totals["total_tokens"] = 0
        _totals["calls"] = 0
        _totals["request_calls"] = 0
        _totals["first_request_started"] = None
        _totals["last_response_received"] = None
        _totals["by_provider"] = {}
        _totals["runtime"] = {}
        _request_latencies.clear()


def snapshot() -> dict[str, Any]:
    with _lock:
        result = deepcopy(_totals)
        latency_samples = deepcopy(_request_latencies)
        started = result.pop("first_request_started", None)
        finished = result.pop("last_response_received", None)
        result["translation_phase_seconds"] = (
            max(0.0, float(finished) - float(started))
            if started is not None and finished is not None
            else 0.0
        )
        result["request_latency_seconds"] = _latency_summary(latency_samples)
        return result


def record_request_start(provider: str = "", model: str = "") -> float:
    now = time.perf_counter()
    with _lock:
        if _totals["first_request_started"] is None:
            _totals["first_request_started"] = now
        _totals["request_calls"] += 1
    return now


def record_response_received(
    provider: str = "",
    model: str = "",
    started_at: float | None = None,
) -> None:
    now = time.perf_counter()
    with _lock:
        _totals["last_response_received"] = now
        if started_at is not None:
            key = (provider or "unknown", model or "unknown")
            _request_latencies.setdefault(key, []).append(max(0.0, now - float(started_at)))


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


def record(provider: str, model: str, usage: dict[str, Any] | None) -> None:
    if not usage:
        return
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    if prompt_tokens == 0 and completion_tokens == 0 and total_tokens == 0:
        return

    provider = provider or "unknown"
    model = model or "unknown"
    with _lock:
        _totals["prompt_tokens"] += prompt_tokens
        _totals["completion_tokens"] += completion_tokens
        _totals["total_tokens"] += total_tokens
        _totals["calls"] += 1
        provider_totals = _totals["by_provider"].setdefault(provider, {
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


def set_runtime_metadata(key: str, value: Any) -> None:
    with _lock:
        _totals["runtime"][str(key)] = deepcopy(value)


__all__ = [
    "diff",
    "record",
    "record_request_start",
    "record_response_received",
    "reset",
    "set_runtime_metadata",
    "snapshot",
]
