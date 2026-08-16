from __future__ import annotations

import os
from typing import Any, MutableMapping

from fastapi import APIRouter, HTTPException, Query

from app.schemas import AIReviewActionRequest, AIReviewRequest
from app.services.ai_review_tasks import (
    AIReviewTask,
    active_ai_review_for_file,
    ai_review_preflight,
    latest_ai_review_for_file,
    persisted_ai_review_progress,
    resume_ai_review_task,
    rollback_ai_review_task,
    start_ai_review_task,
    translation_task_is_active,
)
from app.services.files import require_mtool_json_file


def create_router(
    *,
    translation_tasks: MutableMapping[str, Any],
    ai_review_tasks: MutableMapping[str, AIReviewTask],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/review/ai/preflight")
    def preflight_ai_review(req: AIReviewRequest):
        _require_ready_file(req.file_path)
        if translation_task_is_active(translation_tasks, req.file_path):
            raise HTTPException(status_code=409, detail="Finish or stop the active translation task before AI review")
        try:
            return ai_review_preflight(
                file_path=req.file_path,
                scope=req.scope,
                rows=req.rows,
                filter_name=req.filter,
                review_model=req.review_model,
                verifier_model=req.verifier_model,
                sensitive_model=req.sensitive_model,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/review/ai/start")
    def start_ai_review(req: AIReviewRequest):
        _require_ready_file(req.file_path)
        if translation_task_is_active(translation_tasks, req.file_path):
            raise HTTPException(status_code=409, detail="Finish or stop the active translation task before AI review")
        if active_ai_review_for_file(ai_review_tasks, req.file_path):
            raise HTTPException(status_code=409, detail="An AI review task is already active for this file")
        try:
            task = start_ai_review_task(
                ai_review_tasks,
                file_path=req.file_path,
                scope=req.scope,
                rows=req.rows,
                filter_name=req.filter,
                review_model=req.review_model,
                verifier_model=req.verifier_model,
                sensitive_model=req.sensitive_model,
                auto_apply=req.auto_apply,
                auto_retry=req.auto_retry,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409 if isinstance(exc, RuntimeError) else 400, detail=str(exc)) from exc
        return task.progress()

    @router.get("/api/review/ai/current")
    def current_ai_review(file_path: str = Query(...)):
        task = latest_ai_review_for_file(ai_review_tasks, file_path)
        return {"task": task.progress() if task else persisted_ai_review_progress(file_path)}

    @router.get("/api/review/ai/{task_id}/progress")
    def ai_review_progress(task_id: str):
        task = ai_review_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="AI review task not found")
        return task.progress()

    @router.post("/api/review/ai/{task_id}/cancel")
    def cancel_ai_review(task_id: str):
        task = ai_review_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="AI review task not found")
        task.cancel()
        return task.progress()

    @router.post("/api/review/ai/{task_id}/resume")
    def resume_ai_review(task_id: str, req: AIReviewActionRequest):
        _require_ready_file(req.file_path)
        if translation_task_is_active(translation_tasks, req.file_path):
            raise HTTPException(status_code=409, detail="Cannot resume AI review while translation is active")
        try:
            task = resume_ai_review_task(ai_review_tasks, file_path=req.file_path, task_id=task_id)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return task.progress()

    @router.post("/api/review/ai/{task_id}/rollback")
    def rollback_ai_review(task_id: str, req: AIReviewActionRequest):
        task = ai_review_tasks.get(task_id)
        file_path = task.file_path if task else req.file_path
        _require_ready_file(file_path)
        if translation_task_is_active(translation_tasks, file_path):
            raise HTTPException(status_code=409, detail="Cannot roll back while translation is active")
        if active_ai_review_for_file(ai_review_tasks, file_path):
            raise HTTPException(status_code=409, detail="Cannot roll back while AI review is active")
        try:
            if task:
                return rollback_ai_review_task(task)
            from app.services.files import translated_path
            from app.services.review import invalidate_review_cache
            from translation.review.ai import rollback_ai_review_session

            result = rollback_ai_review_session(file_path, task_id, translated_path(file_path))
            invalidate_review_cache(file_path)
            return result
        except (RuntimeError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router


def _require_ready_file(file_path: str) -> None:
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"File does not exist: {file_path}")
    require_mtool_json_file(file_path)


__all__ = ["create_router"]
