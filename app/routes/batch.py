from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas import BatchTranslateStartRequest


router = APIRouter()


@router.post("/api/translate/batch/start")
def start_batch_translation(req: BatchTranslateStartRequest):
    """Batch translation is currently disabled."""
    raise HTTPException(status_code=410, detail="Batch translation is disabled; translate one file at a time")


@router.get("/api/translate/batch/{batch_id}/progress")
def get_batch_progress(batch_id: str):
    raise HTTPException(status_code=410, detail="Batch translation is disabled")


@router.post("/api/translate/batch/{batch_id}/pause")
def pause_batch_translation(batch_id: str):
    raise HTTPException(status_code=410, detail="Batch translation is disabled")


@router.post("/api/translate/batch/{batch_id}/resume")
def resume_batch_translation(batch_id: str):
    raise HTTPException(status_code=410, detail="Batch translation is disabled")


@router.post("/api/translate/batch/{batch_id}/cancel")
def cancel_batch_translation(batch_id: str):
    raise HTTPException(status_code=410, detail="Batch translation is disabled")


__all__ = ["router"]
