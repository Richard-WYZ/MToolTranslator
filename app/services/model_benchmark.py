from __future__ import annotations

import threading
import time
import uuid
from typing import Any, MutableMapping

from fastapi import HTTPException

from app.services.settings import ACTIVE_TASK_STATES, public_settings, set_default_model_setting
from translation.benchmark.runner import BenchmarkCancelled, run_benchmark
from translation.benchmark.suite import cases_for_mode
from translation.benchmark.store import apply_strategy, load_benchmark, save_benchmark
from translation.config import disabled_models


BENCHMARK_ACTIVE_STATES = {"starting", "running", "stopping"}


class ModelBenchmarkTask:
    def __init__(self, provider: str, models: list[str], mode: str):
        self.task_id = uuid.uuid4().hex
        self.provider = provider
        self.models = list(models)
        self.mode = mode
        self.status = "idle"
        self.completed = 0
        cases, repetitions = cases_for_mode(mode)
        self.total = len(cases) * repetitions * len(models)
        self.current_model = ""
        self.current_case = ""
        self.error = ""
        self.result: dict[str, Any] | None = None
        self.started_at = 0.0
        self.finished_at = 0.0
        self.updated_at = 0.0
        self._cancel_event = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            self.status = "starting"
            self.started_at = self.updated_at = time.time()
            self._thread = threading.Thread(target=self._run, daemon=True, name=f"model-benchmark-{self.task_id}")
            self._thread.start()

    def cancel(self) -> None:
        with self._lock:
            if self.status in BENCHMARK_ACTIVE_STATES:
                self.status = "stopping"
                self.updated_at = time.time()
                self._cancel_event.set()

    def progress(self) -> dict[str, Any]:
        with self._lock:
            elapsed = max(0.0, (self.finished_at or time.time()) - self.started_at) if self.started_at else 0.0
            return {
                "task_id": self.task_id,
                "status": self.status,
                "provider": self.provider,
                "models": list(self.models),
                "mode": self.mode,
                "completed": self.completed,
                "total": self.total,
                "percentage": round(self.completed / self.total * 100.0, 1) if self.total else 0.0,
                "current_model": self.current_model,
                "current_case": self.current_case,
                "elapsed_seconds": round(elapsed, 1),
                "error": self.error,
                "result": self.result,
            }

    def _update(self, progress: dict[str, Any]) -> None:
        with self._lock:
            self.status = "running"
            self.completed = int(progress.get("completed") or 0)
            self.total = int(progress.get("total") or 0)
            self.current_model = str(progress.get("model") or "")
            self.current_case = str(progress.get("case_id") or "")
            self.updated_at = time.time()

    def _run(self) -> None:
        try:
            with self._lock:
                self.status = "running"
            result = run_benchmark(
                self.provider,
                self.models,
                self.mode,
                cancel_event=self._cancel_event,
                progress_callback=self._update,
            )
            if not save_benchmark(result):
                raise OSError("Unable to persist benchmark result")
            with self._lock:
                self.result = result
                self.status = "completed"
                self.completed = self.total
        except BenchmarkCancelled:
            with self._lock:
                self.status = "cancelled"
        except Exception as exc:
            with self._lock:
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            with self._lock:
                self.finished_at = self.updated_at = time.time()


_LOCK = threading.RLock()
_TASK: ModelBenchmarkTask | None = None


def benchmark_status() -> dict[str, Any]:
    with _LOCK:
        task = _TASK
    if task:
        payload = task.progress()
        if payload["status"] in BENCHMARK_ACTIVE_STATES:
            return payload
    stored = load_benchmark()
    return {
        "status": task.status if task else "idle",
        "error": task.error if task else "",
        "result": stored.get("result"),
        "applied": stored.get("applied"),
    }


def benchmark_active() -> bool:
    with _LOCK:
        return bool(_TASK and _TASK.status in BENCHMARK_ACTIVE_STATES)


def start_benchmark(
    provider: str,
    models: list[str],
    mode: str,
    tasks: MutableMapping[str, Any],
) -> dict[str, Any]:
    global _TASK
    if any(getattr(task, "status", "") in ACTIVE_TASK_STATES for task in tasks.values()):
        raise HTTPException(status_code=409, detail="Stop active translation or review tasks before benchmarking")
    selected = list(dict.fromkeys(_canonical_model(provider, model) for model in models if str(model).strip()))
    if not selected:
        raise HTTPException(status_code=400, detail="Select at least one enabled model")
    _validate_enabled_models(provider, selected)
    with _LOCK:
        if _TASK and _TASK.status in BENCHMARK_ACTIVE_STATES:
            raise HTTPException(status_code=409, detail="A model benchmark is already active")
        _TASK = ModelBenchmarkTask(provider, selected, mode)
        _TASK.start()
        return _TASK.progress()


def cancel_benchmark() -> dict[str, Any]:
    with _LOCK:
        task = _TASK
    if not task or task.status not in BENCHMARK_ACTIVE_STATES:
        raise HTTPException(status_code=409, detail="No active model benchmark")
    task.cancel()
    return task.progress()


def apply_benchmark_strategy(strategy: str, tasks: MutableMapping[str, Any]) -> dict[str, Any]:
    with _LOCK:
        if _TASK and _TASK.status in BENCHMARK_ACTIVE_STATES:
            raise HTTPException(status_code=409, detail="Wait for the benchmark to finish")
    if any(getattr(task, "status", "") in ACTIVE_TASK_STATES for task in tasks.values()):
        raise HTTPException(status_code=409, detail="Stop active translation or review tasks before applying a benchmark")
    stored = load_benchmark().get("result")
    recommendations = stored.get("recommendations") if isinstance(stored, dict) else {}
    profile = (recommendations or {}).get("profiles", {}).get(strategy)
    if not isinstance(stored, dict) or stored.get("stale") or not isinstance(profile, dict):
        raise HTTPException(status_code=409, detail="Benchmark result is missing, stale, or has no such strategy")
    if (recommendations or {}).get("auto_applicable") is False:
        raise HTTPException(status_code=409, detail="No model passed the reliability gate")
    settings = set_default_model_setting(str(profile["primary_model"]), tasks)
    try:
        apply_strategy(strategy)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "strategy": strategy, "profile": profile, "settings": settings}


def _canonical_model(provider: str, model: str) -> str:
    rendered = str(model).strip()
    if ":" in rendered and not rendered.startswith(f"{provider}:"):
        raise HTTPException(status_code=400, detail=f"Model does not belong to {provider}: {rendered}")
    return rendered if rendered.startswith(f"{provider}:") else f"{provider}:{rendered}"


def _validate_enabled_models(provider: str, models: list[str]) -> None:
    settings = public_settings()
    disabled = set(disabled_models(provider))
    if provider == "api":
        configured = set(str(model) for model in (settings.get("api") or {}).get("models") or [])
        invalid = [model for model in models if model.split(":", 1)[-1] not in configured]
    else:
        invalid = []
    invalid.extend(model for model in models if model.split(":", 1)[-1] in disabled)
    if invalid:
        raise HTTPException(status_code=400, detail=f"Models are not enabled in settings: {', '.join(sorted(set(invalid)))}")


__all__ = [
    "ModelBenchmarkTask",
    "apply_benchmark_strategy",
    "benchmark_active",
    "benchmark_status",
    "cancel_benchmark",
    "start_benchmark",
]
