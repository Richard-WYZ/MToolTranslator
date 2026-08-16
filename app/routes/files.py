from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.schemas import DesktopImportRequest, ExportFinalizeRequest, ExportRequest
from app.services.desktop_sources import desktop_source_registry, validate_desktop_source
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
    finalize_completed_session: Callable[[str], dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter()
    pending_finalizations: dict[str, tuple[str, str, bool]] = {}
    pending_finalization_lock = threading.Lock()

    def completed_review(file_path: str) -> bool:
        from translation.review import build_review_summary

        summary = build_review_summary(file_path)
        return bool(
            summary.total > 0
            and summary.pending == 0
            and summary.review_required == 0
            and summary.translated_needs_review == 0
        )

    def finalize_safely(file_path: str) -> dict[str, Any]:
        if not finalize_completed_session:
            return {"cleaned": False, "error": "Automatic cleanup is not configured"}
        try:
            cleanup_result = finalize_completed_session(file_path)
            return {"cleaned": True, "cleanup_result": cleanup_result}
        except Exception as exc:
            return {"cleaned": False, "error": str(exc)}

    def ensure_import_available() -> None:
        if any(task.status in ("running", "paused", "stopping") for task in tasks.values()) or any(
            task.status in {"preparing", "reviewing", "verifying", "applying", "finalizing", "stopping"}
            for task in (ai_review_tasks or {}).values()
        ):
            raise HTTPException(status_code=409, detail="Cannot import or switch files while translation is running")

    def desktop_original_path(source_token: str, filename: str, content: bytes) -> str | None:
        if not source_token:
            return None
        record = desktop_source_registry.consume(source_token)
        if not record:
            raise HTTPException(status_code=400, detail="Desktop source token is invalid or expired")
        try:
            return validate_desktop_source(record, filename, content)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/import")
    async def import_file(file: UploadFile = File(...), source_token: str = Form("")):
        """Upload one MTool-style JSON file."""
        ensure_import_available()
        filename = file.filename or "unknown"
        content = await file.read()
        original_path = desktop_original_path(source_token, filename, content)
        return save_mtool_upload(upload_dir, filename, content, original_path=original_path)

    @router.post("/api/import-local")
    def import_local_file(req: DesktopImportRequest):
        """Import a file selected by the trusted desktop bridge."""
        ensure_import_available()
        record = desktop_source_registry.consume(req.source_token)
        if not record:
            raise HTTPException(status_code=400, detail="Desktop source token is invalid or expired")
        try:
            content = Path(record.path).read_bytes()
            original_path = validate_desktop_source(record, record.filename, content)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return save_mtool_upload(
            upload_dir,
            record.filename,
            content,
            original_path=original_path,
        )

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
            overwrite_original=req.overwrite_original,
        )

        task = get_task_for_file(req.file_path)
        if task:
            task.has_unexported_result = False

        eligible = completed_review(source_path)
        finalization: dict[str, Any] = {
            "eligible": eligible,
            "pending_download": False,
            "cleaned": False,
            "confirmation_required": False,
        }
        if eligible and finalize_completed_session:
            browser_download = not (req.overwrite_original or req.output_path or req.output_dir)
            cleanup_token = uuid.uuid4().hex
            with pending_finalization_lock:
                pending_finalizations[cleanup_token] = (
                    os.path.abspath(str(result["export_path"])),
                    source_path,
                    browser_download,
                )
            finalization.update({
                "confirmation_required": True,
                "pending_download": browser_download,
                "cleanup_token": cleanup_token,
            })

        result["finalization"] = finalization

        return result

    @router.post("/api/export/finalize")
    def finalize_export(req: ExportFinalizeRequest):
        with pending_finalization_lock:
            pending = pending_finalizations.get(req.cleanup_token)
            if not pending:
                raise HTTPException(status_code=409, detail="Export cleanup token is invalid or expired")
            if not req.cleanup:
                pending_finalizations.pop(req.cleanup_token, None)
                return {"ok": True, "cleaned": False, "kept": True, "pending_download": False}
            if pending[2]:
                return {"ok": True, "cleaned": False, "kept": False, "pending_download": True}
            pending_finalizations.pop(req.cleanup_token, None)
        return {"ok": True, "kept": False, "pending_download": False, **finalize_safely(pending[1])}

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
    def download_file(
        path: str = Query(...),
        filename: str | None = Query(None),
        cleanup_token: str | None = Query(None),
    ):
        """Download a file by its path."""
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=f"File does not exist: {path}")
        download_name = Path(filename).name if filename else Path(path).name
        background = None
        if cleanup_token:
            with pending_finalization_lock:
                pending = pending_finalizations.get(cleanup_token)
                if not pending or not pending[2] or pending[0] != os.path.abspath(path):
                    raise HTTPException(status_code=409, detail="Download cleanup token is invalid or expired")
                pending_finalizations.pop(cleanup_token, None)
            background = BackgroundTask(finalize_safely, pending[1])
        return FileResponse(
            path,
            filename=download_name,
            media_type="application/octet-stream",
            background=background,
        )

    return router


__all__ = ["create_router"]
