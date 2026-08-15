from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping

from app.services.files import translated_path
from app.services.model_status import public_model_statuses
from app.services.models import available_models
from app.services.review import load_review_context, matching_review_rows
from app.services.runtime_profiles import QUALITY_FAST_MODEL, QUALITY_PRIMARY_MODEL, canonical_model_id
from translation.classification import has_explicit_adult_content
from translation.review.ai import (
    AI_REVIEW_ACTIVE_STATUSES,
    AIReviewCancelled,
    AIReviewModels,
    begin_ai_review_session,
    build_deterministic_reclassification_records,
    estimate_review_usage,
    get_ai_review_session,
    load_ai_review_store,
    rollback_ai_review_session,
    run_ai_review,
    update_ai_review_session_status,
)
from translation.usage import diff as usage_diff
from translation.usage import snapshot as usage_snapshot


TranslateFunc = Callable[[str, str, str, dict[str, Any] | None], str]


class AIReviewTask:
    def __init__(
        self,
        *,
        task_id: str,
        file_path: str,
        items: list[dict[str, Any]],
        models: AIReviewModels,
        auto_apply: bool = True,
        translator: TranslateFunc | None = None,
        reclassification_records: list[dict[str, Any]] | None = None,
    ):
        self.task_id = task_id
        self.file_path = os.path.abspath(file_path)
        self.items = list(items)
        self.models = models
        self.auto_apply = bool(auto_apply)
        self.translator = translator
        self.reclassification_records = [dict(record) for record in reclassification_records or []]
        self.status = "idle"
        self.phase = "preparing"
        self.current = 0
        reclassified_rows = {int(record["row"]) for record in self.reclassification_records}
        self.total = len(self.reclassification_records) + sum(
            int(item.get("row", -1)) not in reclassified_rows for item in items
        )
        self.percentage = 0.0
        self.error = ""
        self.counts = {
            "fixed": 0,
            "confirmed": 0,
            "reclassified": 0,
            "unresolved": 0,
            "conflict": 0,
            "applied": 0,
        }
        self.token_usage: dict[str, Any] = {}
        self.started_at = 0.0
        self.finished_at = 0.0
        self.updated_at = 0.0
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._persisted_phase = ""

    def start(self) -> None:
        with self._lock:
            if self.status in AI_REVIEW_ACTIVE_STATUSES:
                return
            self.status = "preparing"
            self.phase = "preparing"
            self.started_at = time.time()
            self.updated_at = self.started_at
            begin_ai_review_session(
                task_id=self.task_id,
                file_path=self.file_path,
                models=self.models,
                items=self.items,
                auto_apply=self.auto_apply,
            )
            self._persisted_phase = "preparing"
            self._thread = threading.Thread(target=self._run, daemon=True, name=f"ai-review-{self.task_id}")
            self._thread.start()

    def cancel(self) -> None:
        with self._lock:
            if self.status in AI_REVIEW_ACTIVE_STATUSES:
                self._cancel_event.set()
                self.status = "stopping"
                self.phase = "stopping"
                self.updated_at = time.time()

    def progress(self) -> dict[str, Any]:
        with self._lock:
            now = self.finished_at or time.time()
            elapsed = max(0.0, now - self.started_at) if self.started_at else 0.0
            rate = self.current / elapsed if elapsed > 0 else 0.0
            remaining = max(0, self.total - self.current)
            return {
                "task_id": self.task_id,
                "file_path": self.file_path,
                "status": self.status,
                "phase": self.phase,
                "current": self.current,
                "total": self.total,
                "percentage": self.percentage,
                "elapsed_seconds": round(elapsed, 1),
                "eta_seconds": round(remaining / rate, 1) if rate > 0 and self.status in AI_REVIEW_ACTIVE_STATUSES else None,
                "models": self.models.as_dict(),
                "counts": dict(self.counts),
                "token_usage": dict(self.token_usage),
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "updated_at": self.updated_at,
                "can_rollback": self.status == "completed" and int(self.counts.get("applied", 0)) > 0,
            }

    def _update(self, payload: dict[str, Any]) -> None:
        persist_status = ""
        with self._lock:
            self.status = str(payload.get("status") or self.status)
            self.phase = str(payload.get("phase") or self.phase)
            self.current = max(0, int(payload.get("current", self.current) or 0))
            self.total = max(0, int(payload.get("total", self.total) or 0))
            self.percentage = min(99.9, (self.current / self.total * 100.0) if self.total else 0.0)
            self.updated_at = time.time()
            if self.phase != self._persisted_phase:
                self._persisted_phase = self.phase
                persist_status = self.phase
        if persist_status:
            update_ai_review_session_status(self.file_path, self.task_id, persist_status)

    def _run(self) -> None:
        usage_before = usage_snapshot()
        try:
            result = run_ai_review(
                task_id=self.task_id,
                file_path=self.file_path,
                items=self.items,
                models=self.models,
                output_path=translated_path(self.file_path),
                progress=self._update,
                cancelled=self._cancel_event.is_set,
                translator=self.translator,
                auto_apply=self.auto_apply,
                reclassification_records=self.reclassification_records,
            )
            from app.services.review import invalidate_review_cache

            invalidate_review_cache(self.file_path)
            with self._lock:
                self.counts = dict(result.get("counts", self.counts))
                self.status = "completed"
                self.phase = "completed"
                self.total = int(result.get("total", self.total) or 0)
                self.current = self.total
                self.percentage = 100.0
                self.finished_at = time.time()
                self.updated_at = self.finished_at
        except AIReviewCancelled:
            with self._lock:
                self.status = "cancelled"
                self.phase = "cancelled"
                self.finished_at = time.time()
                self.updated_at = self.finished_at
            update_ai_review_session_status(self.file_path, self.task_id, "cancelled")
        except Exception as exc:
            with self._lock:
                self.status = "error"
                self.phase = "error"
                self.error = str(exc)
                self.finished_at = time.time()
                self.updated_at = self.finished_at
            update_ai_review_session_status(self.file_path, self.task_id, "error", error=str(exc))
        finally:
            with self._lock:
                self.token_usage = usage_diff(usage_before)
            update_ai_review_session_status(
                self.file_path,
                self.task_id,
                self.status,
                error=self.error,
                token_usage=self.token_usage,
            )


def build_ai_review_items(
    file_path: str,
    *,
    scope: str,
    rows: list[int] | None = None,
    filter_name: str = "issues",
) -> list[dict[str, Any]]:
    ctx = load_review_context(file_path)
    requested_rows: list[int]
    if scope == "required":
        requested_rows = list(matching_review_rows(ctx, "required"))
    elif scope == "all":
        requested_rows = list(matching_review_rows(ctx, "issues"))
    elif scope == "filter":
        requested_rows = list(matching_review_rows(ctx, filter_name))
    elif scope == "selected":
        requested_rows = list(dict.fromkeys(int(row) for row in rows or []))
    else:
        raise ValueError(f"Unsupported AI review scope: {scope}")

    items: list[dict[str, Any]] = []
    for row in requested_rows:
        if row < 0 or row >= ctx["total_rows"]:
            raise ValueError(f"AI review row out of range: {row}")
        columns = ctx["columns_by_row"][row]
        if not columns:
            continue
        column = columns[0]
        if column.get("status") not in {"review_required", "translated_needs_review"}:
            continue
        if column.get("derived_review"):
            continue
        source = str(column.get("original", ""))
        issues = list(column.get("violations", []) or [])
        if not issues and column.get("review_reasons"):
            issues = [
                {"type": str(reason), "message": str(reason)}
                for reason in column.get("review_reasons", [])
            ]
        neighbors = []
        for neighbor_row in range(max(0, row - 2), min(ctx["total_rows"], row + 3)):
            if neighbor_row == row:
                continue
            neighbors.append({
                "row": neighbor_row,
                "position": "before" if neighbor_row < row else "after",
                "original": ctx["source_texts"][neighbor_row],
                "translated": ctx["translated_texts"][neighbor_row],
            })
        items.append({
            "row": row,
            "source": source,
            "current": str(column.get("translated", "")),
            "status": str(column.get("status", "translated_needs_review")),
            "issues": issues,
            "issue_types": sorted({str(issue.get("type", "")) for issue in issues if isinstance(issue, dict)}),
            "neighbors": neighbors,
            "entry_classification": str(column.get("entry_classification", "")),
            "model_identifier": str(column.get("model_identifier", "")),
            "sensitive": has_explicit_adult_content(source) or any(
                has_explicit_adult_content(str(neighbor.get("original", "")))
                for neighbor in neighbors
            ),
        })
    return items


def build_reclassification_records(file_path: str) -> list[dict[str, Any]]:
    ctx = load_review_context(file_path)
    return build_deterministic_reclassification_records(
        list(ctx["source_texts"]),
        list(ctx["translated_texts"]),
        dict(ctx["cp_entries"]),
    )


def ai_review_preflight(
    *,
    file_path: str,
    scope: str,
    rows: list[int] | None,
    filter_name: str,
    review_model: str | None,
    verifier_model: str | None,
    sensitive_model: str | None,
) -> dict[str, Any]:
    output_path = translated_path(file_path)
    if not Path(output_path).is_file():
        raise FileNotFoundError("Translated output does not exist; finish translation first")
    items = build_ai_review_items(file_path, scope=scope, rows=rows, filter_name=filter_name)
    reclassification_records = build_reclassification_records(file_path)
    reclassified_rows = {int(record["row"]) for record in reclassification_records}
    model_items = [item for item in items if int(item.get("row", -1)) not in reclassified_rows]
    needs_sensitive = any(item.get("sensitive") for item in model_items)
    models = (
        resolve_ai_review_models(
            review_model=review_model,
            verifier_model=verifier_model,
            sensitive_model=sensitive_model,
            needs_sensitive=needs_sensitive,
        )
        if model_items
        else AIReviewModels(
            "deterministic:classification",
            "deterministic:classification",
            "deterministic:classification",
            "deterministic:classification",
        )
    )
    estimate = estimate_review_usage(model_items)
    return {
        "ok": bool(model_items or reclassification_records),
        "scope": scope,
        "counts": {
            "total": len(model_items) + len(reclassification_records),
            "requested": len(items),
            "model_entries": len(model_items),
            "system_corrections": len(reclassification_records),
            "required": sum(item.get("status") == "review_required" for item in items),
            "advisory": sum(item.get("status") == "translated_needs_review" for item in items),
            "sensitive": sum(bool(item.get("sensitive")) for item in model_items),
        },
        "models": models.as_dict(),
        **estimate,
    }


def resolve_ai_review_models(
    *,
    review_model: str | None,
    verifier_model: str | None,
    sensitive_model: str | None,
    needs_sensitive: bool,
) -> AIReviewModels:
    enabled = {
        canonical_model_id(str(item.get("name") or ""))
        for item in available_models()
        if item.get("enabled", True) and str(item.get("name") or "")
    }
    statuses = public_model_statuses()
    basic = [model for model in enabled if _tested_available(statuses, model, "basic")]
    adult = [model for model in basic if _tested_available(statuses, model, "adult")]
    if not basic:
        raise ValueError("No enabled model has a current successful basic test; test a model in Settings first")

    review = _resolve_requested_model(review_model, basic, preferred=QUALITY_PRIMARY_MODEL, label="review")
    verifier = _resolve_requested_model(
        verifier_model,
        basic,
        preferred=QUALITY_FAST_MODEL if review == QUALITY_PRIMARY_MODEL else QUALITY_PRIMARY_MODEL,
        label="verifier",
        avoid=review,
    )
    if needs_sensitive and not adult:
        raise ValueError("No enabled model has a current successful sensitive-content test")
    sensitive = _resolve_requested_model(
        sensitive_model,
        adult or [review],
        preferred=QUALITY_FAST_MODEL,
        label="sensitive review",
    )
    sensitive_verifier = _choose_model(
        adult or [sensitive],
        preferred=verifier if verifier in adult else QUALITY_FAST_MODEL,
        avoid=sensitive,
    )
    return AIReviewModels(review, verifier, sensitive, sensitive_verifier)


def start_ai_review_task(
    registry: MutableMapping[str, AIReviewTask],
    *,
    file_path: str,
    scope: str,
    rows: list[int] | None,
    filter_name: str,
    review_model: str | None,
    verifier_model: str | None,
    sensitive_model: str | None,
    auto_apply: bool,
    translator: TranslateFunc | None = None,
) -> AIReviewTask:
    existing = active_ai_review_for_file(registry, file_path)
    if existing:
        raise RuntimeError(f"An AI review task is already active for this file: {existing.task_id}")
    items = build_ai_review_items(file_path, scope=scope, rows=rows, filter_name=filter_name)
    reclassification_records = build_reclassification_records(file_path)
    if not items and not reclassification_records:
        raise ValueError("No entries match the selected AI review scope")
    reclassified_rows = {int(record["row"]) for record in reclassification_records}
    model_items = [item for item in items if int(item.get("row", -1)) not in reclassified_rows]
    models = (
        resolve_ai_review_models(
            review_model=review_model,
            verifier_model=verifier_model,
            sensitive_model=sensitive_model,
            needs_sensitive=any(item.get("sensitive") for item in model_items),
        )
        if model_items
        else AIReviewModels(
            "deterministic:classification",
            "deterministic:classification",
            "deterministic:classification",
            "deterministic:classification",
        )
    )
    task = AIReviewTask(
        task_id=uuid.uuid4().hex[:12],
        file_path=file_path,
        items=items,
        models=models,
        auto_apply=auto_apply,
        translator=translator,
        reclassification_records=reclassification_records,
    )
    registry[task.task_id] = task
    task.start()
    return task


def active_ai_review_for_file(
    registry: Mapping[str, AIReviewTask],
    file_path: str,
) -> AIReviewTask | None:
    absolute = os.path.abspath(file_path)
    matches = [
        task for task in registry.values()
        if task.file_path == absolute and task.status in AI_REVIEW_ACTIVE_STATUSES | {"stopping"}
    ]
    return max(matches, key=lambda task: task.started_at, default=None)


def latest_ai_review_for_file(
    registry: Mapping[str, AIReviewTask],
    file_path: str,
) -> AIReviewTask | None:
    absolute = os.path.abspath(file_path)
    matches = [task for task in registry.values() if task.file_path == absolute]
    return max(matches, key=lambda task: task.started_at, default=None)


def persisted_ai_review_progress(file_path: str) -> dict[str, Any] | None:
    store = load_ai_review_store(file_path)
    sessions = [session for session in store.get("sessions", []) if isinstance(session, dict)]
    if not sessions:
        return None
    session = sessions[-1]
    request = session.get("request", {}) or {}
    items = request.get("items", []) or []
    records = session.get("records", []) or []
    counts = {
        "fixed": 0,
        "confirmed": 0,
        "reclassified": 0,
        "unresolved": 0,
        "conflict": 0,
        "applied": int(session.get("applied", 0) or 0),
    }
    for record in records:
        if not isinstance(record, dict):
            continue
        result = str(record.get("result", "unresolved"))
        if result in counts and result != "applied":
            counts[result] += 1
    stored_status = str(session.get("status", ""))
    interrupted = stored_status in AI_REVIEW_ACTIVE_STATUSES | {"stopping"}
    status = "interrupted" if interrupted else "completed" if stored_status == "candidate_ready" else stored_status or "idle"
    total = max(len(items), len(records))
    current = total if status == "completed" else 0
    return {
        "task_id": str(session.get("task_id", "")),
        "file_path": os.path.abspath(file_path),
        "status": status,
        "phase": status,
        "current": current,
        "total": total,
        "percentage": 100.0 if status == "completed" else 0.0,
        "elapsed_seconds": 0.0,
        "eta_seconds": None,
        "models": dict(session.get("models", {}) or {}),
        "counts": counts,
        "token_usage": dict(session.get("token_usage", {}) or {}),
        "error": str(session.get("error", "")),
        "started_at": session.get("started_at", ""),
        "finished_at": session.get("finished_at", ""),
        "updated_at": session.get("updated_at", ""),
        "can_resume": interrupted and bool(items),
        "can_rollback": counts["applied"] > 0 and not session.get("rolled_back_at"),
    }


def resume_ai_review_task(
    registry: MutableMapping[str, AIReviewTask],
    *,
    file_path: str,
    task_id: str,
    translator: TranslateFunc | None = None,
) -> AIReviewTask:
    if active_ai_review_for_file(registry, file_path):
        raise RuntimeError("An AI review task is already active for this file")
    session = get_ai_review_session(file_path, task_id)
    if not session:
        raise ValueError(f"AI review session not found: {task_id}")
    request = session.get("request", {}) or {}
    items = request.get("items", []) or []
    model_data = session.get("models", {}) or {}
    if not items or not model_data:
        raise ValueError("AI review session does not contain a resumable request snapshot")
    task = AIReviewTask(
        task_id=task_id,
        file_path=file_path,
        items=list(items),
        models=AIReviewModels(
            str(model_data.get("review", "")),
            str(model_data.get("verifier", "")),
            str(model_data.get("sensitive", "")),
            str(model_data.get("sensitive_verifier", "")),
        ),
        auto_apply=bool(request.get("auto_apply", True)),
        translator=translator,
        reclassification_records=build_reclassification_records(file_path),
    )
    registry[task_id] = task
    task.start()
    return task


def rollback_ai_review_task(task: AIReviewTask) -> dict[str, Any]:
    if task.status != "completed":
        raise RuntimeError("Only a completed AI review task can be rolled back")
    result = rollback_ai_review_session(task.file_path, task.task_id, translated_path(task.file_path))
    from app.services.review import invalidate_review_cache

    invalidate_review_cache(task.file_path)
    if result.get("restored"):
        task.counts["applied"] = max(0, int(task.counts.get("applied", 0)) - int(result["restored"]))
    return result


def translation_task_is_active(tasks: Mapping[str, Any], file_path: str) -> bool:
    absolute = os.path.abspath(file_path)
    return any(
        os.path.abspath(str(getattr(task, "file_path", ""))) == absolute
        and str(getattr(task, "status", "")) in {"running", "paused", "stopping", "finalizing"}
        for task in tasks.values()
    )


def _tested_available(statuses: dict[str, Any], model: str, kind: str) -> bool:
    record = (statuses.get(model) or {}).get(kind) or {}
    return record.get("status") == "available" and not record.get("stale", False)


def _resolve_requested_model(
    requested: str | None,
    eligible: list[str],
    *,
    preferred: str,
    label: str,
    avoid: str = "",
) -> str:
    value = str(requested or "").strip()
    if value and value != "auto":
        canonical = canonical_model_id(value)
        if canonical not in eligible:
            raise ValueError(f"Selected {label} model is not enabled and currently tested: {canonical}")
        return canonical
    return _choose_model(eligible, preferred=preferred, avoid=avoid)


def _choose_model(eligible: list[str], *, preferred: str, avoid: str = "") -> str:
    if preferred in eligible and preferred != avoid:
        return preferred
    different = sorted(model for model in eligible if model != avoid)
    if different:
        return different[0]
    return sorted(eligible)[0]


__all__ = [
    "AIReviewTask",
    "active_ai_review_for_file",
    "ai_review_preflight",
    "build_ai_review_items",
    "build_reclassification_records",
    "latest_ai_review_for_file",
    "persisted_ai_review_progress",
    "resume_ai_review_task",
    "resolve_ai_review_models",
    "rollback_ai_review_task",
    "start_ai_review_task",
    "translation_task_is_active",
]
