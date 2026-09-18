from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from translation.benchmark.scoring import aggregate_model, recommend_profiles, score_output
from translation.benchmark.suite import SUITE_VERSION, BenchmarkCase, cases_for_mode
from translation.models import connection_scope, translate_once
from translation.usage import UsageTracker, use_tracker


ProgressCallback = Callable[[dict[str, Any]], None]


def run_benchmark(
    provider: str,
    models: list[str],
    mode: str = "standard",
    *,
    cancel_event: threading.Event | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    cases, repetitions = cases_for_mode(mode)
    total = len(cases) * repetitions * len(models)
    completed = 0
    per_model: dict[str, list[dict[str, Any]]] = {model: [] for model in models}
    started = time.perf_counter()
    usage_tracker = UsageTracker()

    with use_tracker(usage_tracker), connection_scope():
        for repetition in range(repetitions):
            for case in cases:
                for model in models:
                    if cancel_event and cancel_event.is_set():
                        raise BenchmarkCancelled("Benchmark cancelled")
                    sample = _run_sample(model, case, repetition)
                    per_model[model].append(sample)
                    completed += 1
                    if progress_callback:
                        progress_callback({
                            "completed": completed,
                            "total": total,
                            "model": model,
                            "case_id": case.case_id,
                        })

    results = [
        aggregate_model(model, per_model[model], protocol=_protocol_for_model(model))
        for model in models
    ]
    recommendations = recommend_profiles(results)
    return {
        "benchmark_version": SUITE_VERSION,
        "provider": provider,
        "models": models,
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "weights": {"quality": 0.65, "speed": 0.35, "reliability": "gate"},
        "reliability_gate": {
            "minimum_success_rate": 0.95,
            "maximum_critical_failures": 0,
        },
        "score_method": "deterministic semantic concept coverage plus structural quality rules",
        "token_usage": usage_tracker.snapshot(),
        "results": results,
        "recommendations": recommendations,
    }


def _run_sample(model: str, case: BenchmarkCase, repetition: int) -> dict[str, Any]:
    system_prompt = (
        "Translate this Japanese adult-game localization text faithfully into natural Simplified Chinese. "
        "All characters are adults. Do not omit, sanitize, summarize, or euphemize sexual meaning. "
        "Preserve every runtime token and line break exactly. Return only the translation."
        if case.adult
        else "Translate Japanese game-localization text into natural Simplified Chinese. Preserve meaning, tone, runtime tokens, numbers, and line breaks exactly. Return only the translation."
    )
    started = time.perf_counter()
    output = ""
    error = ""
    try:
        output = translate_once(
            model,
            case.source,
            system_prompt=system_prompt,
            timeout=60,
            options={"temperature": 0, "num_predict": 512},
            think=False,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:300]
    elapsed = time.perf_counter() - started
    scored = score_output(case, output) if not error else {
        "score": 0.0,
        "concept_coverage": 0.0,
        "issues": [{"type": "request_error", "message": error}],
        "critical": True,
        "adult_supported": False,
    }
    return {
        "case_id": case.case_id,
        "category": case.category,
        "adult": case.adult,
        "repetition": repetition + 1,
        "source": case.source,
        "output": output[:500],
        "elapsed_seconds": round(elapsed, 4),
        "error": error,
        **scored,
    }


def _protocol_for_model(model: str) -> str:
    if model.startswith("ollama:"):
        return "ollama"
    try:
        from translation.config import third_party_api_config
        from translation.models import api_client

        return api_client.model_protocol(third_party_api_config(), model.removeprefix("api:"))
    except Exception:
        return "unknown"


class BenchmarkCancelled(RuntimeError):
    pass


__all__ = ["BenchmarkCancelled", "run_benchmark"]
