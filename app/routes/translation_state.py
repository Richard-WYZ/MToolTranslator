from __future__ import annotations

import os
import threading
import time
from typing import MutableMapping

from fastapi import APIRouter, HTTPException, Query

from app.schemas import CleanupRequest, RecoveryResumeRequest
from app.services.files import require_mtool_json_file, translated_path
from app.services.translation_task_service import start_translation_task, task_for_file
from app.services.translation_tasks import BatchTranslationManager, TranslationTask
from translation import checkpoint
from translation.config import default_model


def cleanup_translation_state(
    req: CleanupRequest,
    *,
    tasks: MutableMapping[str, TranslationTask],
):
    if req.fast:
        task = tasks.get(req.task_id) if req.task_id else task_for_file(tasks, req.file_path)
        if task and task.status in ("running", "paused", "stopping"):
            task.cancel()
            task.has_unexported_result = False
        return {"ok": True, "scheduled": False, "deleted": [], "skipped": [], "cancelled": bool(task)}

    task = tasks.get(req.task_id) if req.task_id else task_for_file(tasks, req.file_path)
    deleted: list[str] = []
    skipped: list[str] = []

    if task and task.status in ("running", "paused", "stopping"):
        task.cancel()
        if task._thread:
            task._thread.join(timeout=5)
    output_path = translated_path(req.file_path)
    if os.path.exists(output_path):
        os.remove(output_path)
        deleted.append(output_path)
    else:
        skipped.append(output_path)
    deleted.extend(checkpoint.clear_checkpoint(req.file_path, include_glossary=True))
    if task:
        task.has_unexported_result = False
        task.status = "cancelled"
    return {"ok": True, "deleted": deleted, "skipped": skipped}


def create_router(
    *,
    tasks: MutableMapping[str, TranslationTask],
    batches: MutableMapping[str, BatchTranslationManager],
) -> APIRouter:
    router = APIRouter()

    def cancel_all_translation_activity() -> int:
        cancelled = 0
        for batch in list(batches.values()):
            if batch.status in ("running", "paused", "stopping"):
                batch.cancel()
                cancelled += 1
        for task in list(tasks.values()):
            if task.status in ("running", "paused", "stopping"):
                task.cancel()
                cancelled += 1
        return cancelled

    def schedule_process_exit(delay: float = 0.2) -> None:
        def exit_later():
            time.sleep(delay)
            os._exit(0)

        threading.Thread(target=exit_later, daemon=False).start()

    @router.post("/api/desktop/shutdown")
    def shutdown_desktop():
        """Stop in-process services and exit the desktop app without deleting temp files."""
        cancelled = cancel_all_translation_activity()
        schedule_process_exit()
        return {"ok": True, "cancelled": cancelled}

    @router.get("/api/recovery/sessions")
    def get_recovery_sessions():
        return {"sessions": checkpoint.list_recovery_sessions()}

    @router.get("/api/history/sessions")
    def get_history_sessions():
        return {"sessions": checkpoint.list_translation_sessions(include_completed=True)}

    @router.post("/api/recovery/resume")
    def resume_recovery_session(req: RecoveryResumeRequest):
        if not os.path.exists(req.file_path):
            raise HTTPException(status_code=409, detail="Original file is missing; select the source file before resuming")

        cp = checkpoint.load_checkpoint(req.file_path)
        if cp.get("version") != 2:
            raise HTTPException(status_code=404, detail="No v2 checkpoint found")
        require_mtool_json_file(req.file_path)
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
        )

    @router.get("/api/translation/dirty-state")
    def get_translation_dirty_state(file_path: str | None = Query(None)):
        states = []
        for task in tasks.values():
            if file_path and os.path.abspath(task.file_path) != os.path.abspath(file_path):
                continue
            output_path = translated_path(task.file_path)
            dirty = task.status in ("running", "paused", "stopping") or task.has_unexported_result or os.path.exists(output_path)
            if dirty:
                states.append({
                    "task_id": task.task_id,
                    "file_path": task.file_path,
                    "status": task.status,
                    "translated_path": output_path,
                    "has_unexported_result": task.has_unexported_result or os.path.exists(output_path),
                })
        return {"dirty": bool(states), "states": states}

    @router.post("/api/translation/cleanup")
    def cleanup_translation(req: CleanupRequest):
        return cleanup_translation_state(req, tasks=tasks)

    return router


__all__ = ["cleanup_translation_state", "create_router"]
