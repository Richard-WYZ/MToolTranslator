from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, MutableMapping

from fastapi import APIRouter, HTTPException, Query

from app.schemas import CleanupRequest, HistoryProjectNameRequest, RecoveryResumeRequest
from app.services.files import (
    require_mtool_json_file,
    session_project_info,
    session_metadata_path,
    translated_path,
    translation_output_state,
    update_session_project_name,
)
from app.services.translation_task_service import start_translation_task, task_for_file
from app.services.translation_tasks import BatchTranslationManager, TranslationTask
from app.services.task_coordinator import TaskCoordinator, default_task_coordinator
from common.files import is_path_inside
from translation import checkpoint
from translation.config import default_model


_HISTORY_CLEANUP_LOCK = threading.RLock()
_ACTIVE_TRANSLATION_STATUSES = {"starting", "running", "paused", "stopping", "finalizing"}
_ACTIVE_AI_REVIEW_STATUSES = {"preparing", "reviewing", "verifying", "applying", "finalizing", "stopping"}


def _path_key(file_path: str) -> str:
    return os.path.normcase(os.path.abspath(str(file_path)))


def _tasks_for_file(tasks: MutableMapping[str, TranslationTask], file_path: str) -> list[TranslationTask]:
    target = _path_key(file_path)
    return [task for task in tasks.values() if _path_key(task.file_path) == target]


def _wait_for_task_stop(task: Any, timeout: float = 5.0) -> bool:
    waiter = getattr(task, "wait_for_stop", None)
    if callable(waiter):
        return bool(waiter(timeout=timeout))
    thread = getattr(task, "_thread", None)
    if thread is not None:
        thread.join(timeout=max(0.0, timeout))
        if thread.is_alive():
            return False
    runtime = getattr(task, "runtime", None)
    writer_stopped = getattr(runtime, "writer_stopped", None)
    return bool(writer_stopped()) if callable(writer_stopped) else True


def _cleanup_task_for_request(
    req: CleanupRequest,
    tasks: MutableMapping[str, TranslationTask],
) -> TranslationTask | None:
    matching = _tasks_for_file(tasks, req.file_path)
    selected = tasks.get(req.task_id) if req.task_id else None
    if selected is not None and _path_key(selected.file_path) != _path_key(req.file_path):
        raise HTTPException(status_code=409, detail="Task does not belong to the requested file")
    if selected is not None and selected not in matching:
        matching.append(selected)
    return selected or (matching[-1] if matching else None)


def cleanup_translation_state(
    req: CleanupRequest,
    *,
    tasks: MutableMapping[str, TranslationTask],
    ai_review_tasks: MutableMapping[str, Any] | None = None,
    coordinator: TaskCoordinator | None = None,
    _reservation_held: bool = False,
):
    coordinator = coordinator or default_task_coordinator()
    if _reservation_held:
        return _cleanup_translation_state_unreserved(
            req,
            tasks=tasks,
            ai_review_tasks=ai_review_tasks,
            coordinator=coordinator,
        )
    if req.fast:
        try:
            reservation = coordinator.cleanup_reservation(req.file_path)
            reservation.__enter__()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            if ai_review_tasks and coordinator.active_review(ai_review_tasks, req.file_path):
                raise HTTPException(status_code=409, detail="Stop AI review before cleaning translation state")
            task = _cleanup_task_for_request(req, tasks)
            if task and task.status in ("starting", "running", "paused", "stopping", "finalizing"):
                task.cancel()
                task.has_unexported_result = False
            return {"ok": True, "scheduled": False, "deleted": [], "skipped": [], "cancelled": bool(task)}
        finally:
            reservation.__exit__(None, None, None)

    try:
        reservation = coordinator.cleanup_reservation(req.file_path)
        reservation.__enter__()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        return _cleanup_translation_state_unreserved(
            req,
            tasks=tasks,
            ai_review_tasks=ai_review_tasks,
            coordinator=coordinator,
        )
    finally:
        reservation.__exit__(None, None, None)


def _cleanup_translation_state_unreserved(
    req: CleanupRequest,
    *,
    tasks: MutableMapping[str, TranslationTask],
    ai_review_tasks: MutableMapping[str, Any] | None,
    coordinator: TaskCoordinator,
):
    task = _cleanup_task_for_request(req, tasks)
    if ai_review_tasks and coordinator.active_review(ai_review_tasks, req.file_path):
        raise HTTPException(status_code=409, detail="Stop AI review before cleaning translation state")
    deleted: list[str] = []
    skipped: list[str] = []

    matching_tasks = _tasks_for_file(tasks, req.file_path)
    if task is not None and task not in matching_tasks:
        matching_tasks.append(task)
    for candidate in matching_tasks:
        if candidate.status in ("starting", "running", "paused", "stopping", "finalizing"):
            candidate.cancel()
        if not _wait_for_task_stop(candidate, timeout=5):
            raise HTTPException(
                status_code=409,
                detail="Translation task is still stopping; temporary files were preserved",
            )
    output_path = translated_path(req.file_path)
    if os.path.exists(output_path):
        os.remove(output_path)
        deleted.append(output_path)
    else:
        skipped.append(output_path)
    from translation.review import review_report_path

    report_path = review_report_path(req.file_path, output_path)
    if os.path.exists(report_path):
        os.remove(report_path)
        deleted.append(report_path)
    else:
        skipped.append(report_path)
    deleted.extend(checkpoint.clear_checkpoint(req.file_path, include_glossary=True))
    from translation.review.ai import ai_review_store_path

    ai_review_path = ai_review_store_path(req.file_path)
    if os.path.exists(ai_review_path):
        os.remove(ai_review_path)
        deleted.append(ai_review_path)
    metadata_path = str(session_metadata_path(req.file_path))
    if os.path.exists(metadata_path):
        os.remove(metadata_path)
        deleted.append(metadata_path)
    if task:
        task.has_unexported_result = False
        task.status = "cancelled"
    return {"ok": True, "deleted": deleted, "skipped": skipped}


def _delete_history_session_unlocked(
    file_path: str,
    *,
    tasks: MutableMapping[str, TranslationTask],
    ai_review_tasks: MutableMapping[str, Any] | None = None,
    upload_dir: str | Path | None = None,
    purge_working_source: bool = False,
    coordinator: TaskCoordinator | None = None,
) -> dict[str, Any]:
    coordinator = coordinator or default_task_coordinator()
    try:
        reservation = coordinator.cleanup_reservation(file_path)
        reservation.__enter__()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        return _delete_history_session_reserved(
            file_path,
            tasks=tasks,
            ai_review_tasks=ai_review_tasks,
            upload_dir=upload_dir,
            purge_working_source=purge_working_source,
            coordinator=coordinator,
        )
    finally:
        reservation.__exit__(None, None, None)


def _delete_history_session_reserved(
    file_path: str,
    *,
    tasks: MutableMapping[str, TranslationTask],
    ai_review_tasks: MutableMapping[str, Any] | None = None,
    upload_dir: str | Path | None = None,
    purge_working_source: bool = False,
    coordinator: TaskCoordinator,
) -> dict[str, Any]:
    absolute_path = _path_key(file_path)
    matching_tasks = [
        (task_id, task)
        for task_id, task in tasks.items()
        if _path_key(task.file_path) == absolute_path
    ]
    if any(task.status in _ACTIVE_TRANSLATION_STATUSES for _task_id, task in matching_tasks):
        raise HTTPException(status_code=409, detail="Stop the translation task before deleting its history")

    matching_ai_tasks = [
        (task_id, task)
        for task_id, task in (ai_review_tasks or {}).items()
        if _path_key(str(getattr(task, "file_path", ""))) == absolute_path
    ]
    if any(
        task.status in _ACTIVE_AI_REVIEW_STATUSES
        for _task_id, task in matching_ai_tasks
    ):
        raise HTTPException(status_code=409, detail="Stop AI review before deleting its history")

    result = cleanup_translation_state(
        CleanupRequest(file_path=file_path),
        tasks=tasks,
        ai_review_tasks=ai_review_tasks,
        coordinator=coordinator,
        _reservation_held=True,
    )
    for task_id, _task in matching_tasks:
        tasks.pop(task_id, None)
    for task_id, _task in matching_ai_tasks:
        (ai_review_tasks or {}).pop(task_id, None)

    from app.services.review import invalidate_review_cache

    invalidate_review_cache(file_path)

    removed_working_source = False
    removed_session_dir = False
    if purge_working_source and upload_dir and is_path_inside(absolute_path, upload_dir):
        source = Path(absolute_path)
        upload_root = Path(upload_dir).resolve()
        if source.resolve() != upload_root and source.is_file():
            source.unlink()
            result["deleted"].append(str(source))
            removed_working_source = True
        parent = source.parent.resolve()
        if parent != upload_root and is_path_inside(parent, upload_root):
            try:
                parent.rmdir()
                removed_session_dir = True
            except OSError:
                pass
    return {
        **result,
        "removed_tasks": len(matching_tasks),
        "removed_ai_review_tasks": len(matching_ai_tasks),
        "removed_working_source": removed_working_source,
        "removed_session_dir": removed_session_dir,
    }


def delete_history_session(
    file_path: str,
    *,
    tasks: MutableMapping[str, TranslationTask],
    ai_review_tasks: MutableMapping[str, Any] | None = None,
    upload_dir: str | Path | None = None,
    purge_working_source: bool = False,
) -> dict[str, Any]:
    with _HISTORY_CLEANUP_LOCK:
        return _delete_history_session_unlocked(
            file_path,
            tasks=tasks,
            ai_review_tasks=ai_review_tasks,
            upload_dir=upload_dir,
            purge_working_source=purge_working_source,
        )


def create_router(
    *,
    tasks: MutableMapping[str, TranslationTask],
    batches: MutableMapping[str, BatchTranslationManager],
    ai_review_tasks: MutableMapping[str, Any] | None = None,
    upload_dir: str | Path | None = None,
) -> APIRouter:
    router = APIRouter()

    def cancel_all_translation_activity() -> int:
        cancelled = 0
        for batch in list(batches.values()):
            if batch.status in ("starting", "running", "paused", "stopping", "finalizing"):
                batch.cancel()
                cancelled += 1
        for task in list(tasks.values()):
            if task.status in ("starting", "running", "paused", "stopping", "finalizing"):
                task.cancel()
                cancelled += 1
        for task in list((ai_review_tasks or {}).values()):
            if task.status in {"preparing", "reviewing", "verifying", "applying", "finalizing", "stopping"}:
                task.cancel()
                cancelled += 1
        return cancelled

    def schedule_process_exit(delay: float = 0.2) -> None:
        def exit_later():
            time.sleep(delay)
            os._exit(0)

        threading.Thread(target=exit_later, daemon=False).start()

    def enrich_history_session(session: dict[str, Any]) -> dict[str, Any]:
        file_path = str(session.get("file_path") or "")
        return {**session, **session_project_info(file_path)} if file_path else session

    @router.post("/api/desktop/shutdown")
    def shutdown_desktop():
        """Stop in-process services and exit the desktop app without deleting temp files."""
        cancelled = cancel_all_translation_activity()
        schedule_process_exit()
        return {"ok": True, "cancelled": cancelled}

    @router.get("/api/recovery/sessions")
    def get_recovery_sessions():
        return {"sessions": [enrich_history_session(item) for item in checkpoint.list_recovery_sessions()]}

    @router.get("/api/history/sessions")
    def get_history_sessions():
        sessions = checkpoint.list_translation_sessions(include_completed=True)
        known_paths = {
            os.path.normcase(os.path.abspath(str(item.get("file_path") or "")))
            for item in sessions
            if item.get("file_path")
        }
        for task in tasks.values():
            normalized_path = os.path.normcase(os.path.abspath(task.file_path))
            if normalized_path in known_paths:
                continue
            sessions.append({
                "file_path": task.file_path,
                "file_exists": os.path.isfile(task.file_path),
                "file_name": getattr(task, "file_name", "") or os.path.basename(task.file_path),
                "model": getattr(task, "model", ""),
                "completed": int(getattr(task, "progress", {}).get("current", 0) or 0),
                "total": int(getattr(task, "progress", {}).get("total", 0) or 0),
                "updated_at": getattr(task, "finished_at", None) or getattr(task, "started_at", None),
                "status": task.status,
                "review_queue_size": 0,
            })
        sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"sessions": [enrich_history_session(item) for item in sessions]}

    @router.put("/api/history/session/name")
    def rename_history_project(req: HistoryProjectNameRequest):
        if upload_dir and not is_path_inside(req.file_path, upload_dir):
            raise HTTPException(status_code=400, detail="Only internal translation sessions can be renamed")
        with _HISTORY_CLEANUP_LOCK:
            return {"ok": True, **update_session_project_name(req.file_path, req.project_name)}

    @router.get("/api/desktop/exit-state")
    def get_desktop_exit_state():
        states: list[dict[str, Any]] = []
        for task in tasks.values():
            if task.status in _ACTIVE_TRANSLATION_STATUSES:
                states.append({
                    "task_id": task.task_id,
                    "file_path": task.file_path,
                    "status": task.status,
                    "kind": "translation",
                })
        for batch_id, batch in batches.items():
            if batch.status in _ACTIVE_TRANSLATION_STATUSES:
                states.append({"task_id": batch_id, "status": batch.status, "kind": "batch"})
        for task in (ai_review_tasks or {}).values():
            if task.status in _ACTIVE_AI_REVIEW_STATUSES:
                states.append({
                    "task_id": task.task_id,
                    "file_path": task.file_path,
                    "status": task.status,
                    "kind": "ai_review",
                })
        return {"requires_confirmation": bool(states), "states": states}

    @router.delete("/api/history/session")
    def delete_history(file_path: str = Query(...)):
        with _HISTORY_CLEANUP_LOCK:
            return delete_history_session(
                file_path,
                tasks=tasks,
                ai_review_tasks=ai_review_tasks,
                upload_dir=upload_dir,
                purge_working_source=True,
            )

    @router.post("/api/history/clear")
    def clear_history():
        candidates: dict[str, str] = {}
        for session in checkpoint.list_translation_sessions(include_completed=True):
            file_path = str(session.get("file_path", ""))
            if file_path:
                candidates[os.path.normcase(os.path.abspath(file_path))] = file_path
        for task in tasks.values():
            if task.file_path:
                candidates[os.path.normcase(os.path.abspath(task.file_path))] = task.file_path
        for task in (ai_review_tasks or {}).values():
            file_path = str(getattr(task, "file_path", ""))
            if file_path:
                candidates[os.path.normcase(os.path.abspath(file_path))] = file_path

        deleted_paths: list[str] = []
        skipped: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        with _HISTORY_CLEANUP_LOCK:
            for file_path in candidates.values():
                try:
                    delete_history_session(
                        file_path,
                        tasks=tasks,
                        ai_review_tasks=ai_review_tasks,
                        upload_dir=upload_dir,
                        purge_working_source=True,
                    )
                    deleted_paths.append(file_path)
                except HTTPException as exc:
                    if exc.status_code == 409:
                        skipped.append({"file_path": file_path, "reason": str(exc.detail)})
                    else:
                        failed.append({"file_path": file_path, "reason": str(exc.detail)})
                except Exception as exc:
                    failed.append({"file_path": file_path, "reason": str(exc)})
        return {
            "ok": not failed,
            "deleted": len(deleted_paths),
            "skipped": len(skipped),
            "failed": len(failed),
            "deleted_paths": deleted_paths,
            "skipped_items": skipped,
            "failed_items": failed,
        }

    @router.post("/api/recovery/resume")
    def resume_recovery_session(req: RecoveryResumeRequest):
        if not os.path.exists(req.file_path):
            raise HTTPException(status_code=409, detail="Original file is missing; select the source file before resuming")

        cp = checkpoint.load_checkpoint(req.file_path)
        if cp.get("version") != 2:
            raise HTTPException(status_code=404, detail="No v2 checkpoint found")
        require_mtool_json_file(req.file_path)
        if ai_review_tasks:
            from app.services.ai_review_tasks import active_ai_review_for_file
            if active_ai_review_for_file(ai_review_tasks, req.file_path):
                raise HTTPException(status_code=409, detail="Cannot resume translation while AI review is active")
        model = req.model or cp.get("model") or default_model()
        profile_name = req.execution_profile
        profile_options = dict(req.profile_options or {})
        if not profile_name:
            saved_batch = (cp.get("model_configuration", {}) or {}).get("batch_translation")
            if isinstance(saved_batch, dict) and saved_batch:
                profile_name = "checkpoint"
                profile_options = {"batch_translation": saved_batch}
            else:
                profile_name = "single_model" if str(model).startswith("api:") else "local"
        return start_translation_task(
            tasks,
            file_path=req.file_path,
            model=model,
            provider=None,
            prompt_style=req.prompt_style or cp.get("prompt_style") or "professional",
            translate_columns=[1],
            execution_profile=profile_name,
            profile_options=profile_options,
            ai_review_tasks=ai_review_tasks,
        )

    @router.get("/api/translation/dirty-state")
    def get_translation_dirty_state(file_path: str | None = Query(None)):
        states = []
        for task in tasks.values():
            if file_path and os.path.abspath(task.file_path) != os.path.abspath(file_path):
                continue
            output_path = translated_path(task.file_path)
            output_state = translation_output_state(task.file_path)
            dirty = (
                task.status in ("running", "paused", "stopping", "finalizing")
                or task.has_unexported_result
                or output_state["dirty"]
            )
            if dirty:
                states.append({
                    "task_id": task.task_id,
                    "file_path": task.file_path,
                    "status": task.status,
                    "translated_path": output_path,
                    "has_unexported_result": task.has_unexported_result or os.path.exists(output_path),
                })
        for task in (ai_review_tasks or {}).values():
            if file_path and os.path.abspath(task.file_path) != os.path.abspath(file_path):
                continue
            if task.status in {"preparing", "reviewing", "verifying", "applying", "finalizing", "stopping"}:
                states.append({
                    "task_id": task.task_id,
                    "file_path": task.file_path,
                    "status": task.status,
                    "kind": "ai_review",
                    "has_unexported_result": True,
                })
        return {"dirty": bool(states), "states": states}

    @router.post("/api/translation/cleanup")
    def cleanup_translation(req: CleanupRequest):
        if ai_review_tasks:
            from app.services.ai_review_tasks import active_ai_review_for_file
            active = active_ai_review_for_file(ai_review_tasks, req.file_path)
            if active:
                raise HTTPException(status_code=409, detail="Stop AI review before cleaning translation state")
        return cleanup_translation_state(req, tasks=tasks, ai_review_tasks=ai_review_tasks)

    return router


__all__ = ["cleanup_translation_state", "create_router", "delete_history_session"]
