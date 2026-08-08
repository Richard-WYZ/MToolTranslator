from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping, Protocol

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.schemas import ExportRequest
from app.services.files import export_mtool_json, preview_mtool_json, save_mtool_upload, translation_output_state


class TaskLike(Protocol):
    status: str
    has_unexported_result: bool


def create_router(
    *,
    upload_dir: str | Path,
    tasks: Mapping[str, TaskLike],
    get_task_for_file: Callable[[str], TaskLike | None],
    ai_review_tasks: Mapping[str, TaskLike] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/import")
    async def import_file(file: UploadFile = File(...)):
        """Upload one MTool-style JSON file."""
        if any(task.status in ("running", "paused", "stopping") for task in tasks.values()) or any(
            task.status in {"preparing", "reviewing", "verifying", "applying", "finalizing", "stopping"}
            for task in (ai_review_tasks or {}).values()
        ):
            raise HTTPException(status_code=409, detail="Cannot import or switch files while translation is running")
        filename = file.filename or "unknown"
        return save_mtool_upload(upload_dir, filename, await file.read())

    @router.get("/api/preview")
    def preview_file(path: str = Query(...), limit: int = Query(10)):
        """Read first N MTool JSON entries."""
        return preview_mtool_json(path, limit)

    @router.post("/api/export")
    async def export_file(req: ExportRequest):
        """Export translated MTool JSON."""
        session_dir = Path(upload_dir) / req.session_id
        source_path = req.file_path

        task = get_task_for_file(source_path)
        if task and task.status in ("running", "paused", "stopping", "finalizing"):
            raise HTTPException(status_code=409, detail="Translation output is not finalized; stop or wait before exporting")
        if ai_review_tasks:
            from app.services.ai_review_tasks import active_ai_review_for_file
            if active_ai_review_for_file(ai_review_tasks, source_path):
                raise HTTPException(status_code=409, detail="AI review is still modifying the translated output")

        result = export_mtool_json(
            session_dir,
            source_path,
            output_dir=req.output_dir,
            output_path=req.output_path,
        )

        task = get_task_for_file(req.file_path)
        if task:
            task.has_unexported_result = False

        return result

    @router.get("/api/export/status")
    def export_status(file_path: str = Query(...)):
        task = get_task_for_file(file_path)
        translation_active = bool(task and task.status in ("running", "paused", "stopping", "finalizing"))
        review_active = False
        if ai_review_tasks:
            from app.services.ai_review_tasks import active_ai_review_for_file

            review_active = active_ai_review_for_file(ai_review_tasks, file_path) is not None
        output = translation_output_state(file_path)
        return {
            **output,
            "ready": bool(output["ready"] and not translation_active and not review_active),
            "translation_active": translation_active,
            "review_active": review_active,
            "task_status": task.status if task else "",
        }

    @router.get("/api/download")
    def download_file(path: str = Query(...)):
        """Download a file by its path."""
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=f"File does not exist: {path}")
        return FileResponse(path, filename=Path(path).name, media_type="application/octet-stream")

    return router


__all__ = ["create_router"]
