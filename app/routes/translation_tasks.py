from __future__ import annotations

from typing import Any, Mapping, MutableMapping

from fastapi import APIRouter, HTTPException

from app.schemas import TranslateStartRequest
from app.services.translation_task_service import (
    cancel_translation_task,
    list_translation_tasks,
    pause_translation_task,
    resume_translation_task,
    start_translation_task,
    translation_task_progress,
)
from app.services.translation_tasks import TranslationTask


def create_router(*, tasks: MutableMapping[str, TranslationTask], ai_review_tasks: Mapping[str, Any] | None = None) -> APIRouter:
    router = APIRouter()

    @router.post("/api/translate/start")
    def start_translation(req: TranslateStartRequest):
        """Start a new translation task in a background thread."""
        if ai_review_tasks:
            from app.services.ai_review_tasks import active_ai_review_for_file
            if active_ai_review_for_file(ai_review_tasks, req.file_path):
                raise HTTPException(status_code=409, detail="Cannot start translation while AI review is active")
        return start_translation_task(
            tasks,
            file_path=req.file_path,
            model=req.model,
            provider=req.provider,
            prompt_style=req.prompt_style,
            translate_columns=[1],
            execution_profile=req.execution_profile,
            profile_options=req.profile_options,
        )

    @router.post("/api/translate/{task_id}/pause")
    def pause_translation(task_id: str):
        return pause_translation_task(tasks, task_id)

    @router.post("/api/translate/{task_id}/resume")
    def resume_translation(task_id: str):
        return resume_translation_task(tasks, task_id)

    @router.post("/api/translate/{task_id}/cancel")
    def cancel_translation(task_id: str):
        return cancel_translation_task(tasks, task_id)

    @router.get("/api/translate/{task_id}/progress")
    def get_translation_progress(task_id: str):
        return translation_task_progress(tasks, task_id)

    @router.get("/api/translate/tasks")
    def list_tasks():
        return list_translation_tasks(tasks)

    return router


__all__ = ["create_router"]
