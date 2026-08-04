from __future__ import annotations

import os
from typing import Any, Mapping, MutableMapping

from fastapi import APIRouter, HTTPException, Query
from app.schemas import ReviewBatchSaveRequest, ReviewSaveRequest
from translation.config import output_constraints
from app.services.files import is_mtool_items, json_original_text, require_mtool_json_file, translated_path
from app.services.glossary import pause_and_flush_for_edit, resume_after_edit, sync_task_output_cell
from app.services.review import (
    build_review_row,
    invalidate_review_cache,
    load_review_context,
    matching_review_rows,
)
from app.services.translation_tasks import TranslationTask


def create_router(
    *,
    tasks: MutableMapping[str, TranslationTask],
    ai_review_tasks: Mapping[str, Any] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/review/list")
    def get_review_list(
        file_path: str = Query(...),
        offset: int = Query(0),
        limit: int = Query(20),
        filter: str = Query("all"),
    ):
        ctx = load_review_context(file_path)
        if ctx["total_rows"] == 0:
            raise HTTPException(status_code=404, detail="File has no data")
        limit = max(1, min(int(limit), 100))
        matched_rows = matching_review_rows(ctx, filter)
        offset = max(0, min(int(offset), max(0, len(matched_rows) - 1))) if matched_rows else 0
        page_rows = matched_rows[offset: offset + limit]
        return {
            "file_type": ctx["file_type"],
            "header": ctx["header"],
            "total_rows": ctx["total_rows"],
            "filter": filter,
            "offset": offset,
            "limit": limit,
            "matched_total": len(matched_rows),
            "items": [build_review_row(ctx, row) for row in page_rows],
        }

    @router.get("/api/review/jump")
    def jump_review_row(
        file_path: str = Query(...),
        row: int = Query(...),
        limit: int = Query(20),
        filter: str = Query("all"),
    ):
        ctx = load_review_context(file_path)
        if row < 0 or row >= ctx["total_rows"]:
            raise HTTPException(status_code=400, detail=f"Row out of range: {row}, total rows: {ctx['total_rows']}")
        limit = max(1, min(int(limit), 100))
        matched_rows = matching_review_rows(ctx, filter)
        if row not in matched_rows:
            return {
                "found": False,
                "row": row,
                "filter": filter,
                "matched_total": len(matched_rows),
                "message": "Row is not present under the current filter.",
            }
        index = matched_rows.index(row)
        return {
            "found": True,
            "row": row,
            "filter": filter,
            "matched_total": len(matched_rows),
            "offset": (index // limit) * limit,
            "index": index,
        }

    @router.get("/api/review")
    def get_review_data(file_path: str = Query(...), row: int = Query(0)):
        ctx = load_review_context(file_path)
        if ctx["total_rows"] == 0:
            raise HTTPException(status_code=404, detail="File has no data")
        return build_review_row(ctx, row)

    @router.post("/api/review/save")
    async def save_review_edit(req: ReviewSaveRequest):
        if not os.path.isfile(req.file_path):
            raise HTTPException(status_code=404, detail=f"File does not exist: {req.file_path}")
        require_mtool_json_file(req.file_path)
        _ensure_ai_review_idle(ai_review_tasks, req.file_path)

        task, was_running = pause_and_flush_for_edit(tasks, req.file_path)
        try:
            from translation import checkpoint as translation_checkpoint
            from translation.input import load_json_items
            from translation.output import write_json_items
            from translation.quality import get_violations

            output_path = translated_path(req.file_path)
            if not os.path.isfile(output_path):
                raise HTTPException(status_code=404, detail="Translated file does not exist; finish translation first")

            translated_items = load_json_items(output_path)
            original_items = load_json_items(req.file_path)
            mtool = is_mtool_items(original_items)
            if req.row < 0 or req.row >= len(translated_items):
                raise HTTPException(status_code=400, detail=f"Row out of range: {req.row}")

            action = str(req.action or "accept").lower()
            if action not in ("accept", "draft", "preserve"):
                raise HTTPException(status_code=400, detail=f"Unsupported review action: {action}")
            key, _ = translated_items[req.row]
            saved_text = original_items[req.row][0] if action == "preserve" else req.text
            translated_items[req.row] = (key, saved_text)
            write_json_items(translated_items, output_path)

            original_text = ""
            if req.row < len(original_items):
                cp_entry = translation_checkpoint.get_entry(req.file_path, req.row, 0)
                original_text = json_original_text(original_items[req.row][0], original_items[req.row][1], cp_entry, mtool=mtool)
            translation_checkpoint.save_progress(
                req.file_path,
                req.row,
                0,
                original_text,
                saved_text,
                status={
                    "accept": "translated",
                    "draft": "translated_needs_review",
                    "preserve": "preserved",
                }[action],
                issues=[],
            )
            sync_task_output_cell(tasks, req.file_path, req.row, 0, saved_text)
            invalidate_review_cache(req.file_path)

            max_chars, max_lines = output_constraints()
            violations = get_violations(saved_text, max_chars=max_chars, max_lines=max_lines)
            return {"ok": True, "violations": violations, "status": action, "text": saved_text}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to save JSON review edit: {exc}")
        finally:
            resume_after_edit(task, was_running)

    @router.post("/api/review/batch-save")
    async def batch_save_review(req: ReviewBatchSaveRequest):
        if not os.path.isfile(req.file_path):
            raise HTTPException(status_code=404, detail=f"File does not exist: {req.file_path}")
        require_mtool_json_file(req.file_path)
        _ensure_ai_review_idle(ai_review_tasks, req.file_path)

        task, was_running = pause_and_flush_for_edit(tasks, req.file_path)
        try:
            from translation import checkpoint as translation_checkpoint
            from translation.input import load_json_items
            from translation.output import write_json_items

            output_path = translated_path(req.file_path)
            if not os.path.isfile(output_path):
                raise HTTPException(status_code=404, detail="Translated file does not exist")

            translated_items = load_json_items(output_path)
            original_items = load_json_items(req.file_path)
            mtool = is_mtool_items(original_items)
            saved_count = 0
            for edit in req.edits:
                edit_row = edit.get("row")
                edit_text = edit.get("text", "")
                action = str(edit.get("action") or "accept").lower()
                if action not in ("accept", "draft", "preserve"):
                    continue
                if edit_row is None or edit_row < 0 or edit_row >= len(translated_items):
                    continue
                key, _ = translated_items[edit_row]
                saved_text = original_items[edit_row][0] if action == "preserve" else edit_text
                translated_items[edit_row] = (key, saved_text)
                original_text = ""
                if edit_row < len(original_items):
                    cp_entry = translation_checkpoint.get_entry(req.file_path, edit_row, 0)
                    original_text = json_original_text(original_items[edit_row][0], original_items[edit_row][1], cp_entry, mtool=mtool)
                translation_checkpoint.save_progress(
                    req.file_path,
                    edit_row,
                    0,
                    original_text,
                    saved_text,
                    status={
                        "accept": "translated",
                        "draft": "translated_needs_review",
                        "preserve": "preserved",
                    }[action],
                    issues=[],
                )
                sync_task_output_cell(tasks, req.file_path, edit_row, 0, saved_text)
                saved_count += 1
            write_json_items(translated_items, output_path)
            invalidate_review_cache(req.file_path)
            return {"ok": True, "saved_count": saved_count}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to batch-save JSON review edits: {exc}")
        finally:
            resume_after_edit(task, was_running)

    @router.get("/api/review/stats")
    def get_review_stats(file_path: str = Query(...)):
        return dict(load_review_context(file_path)["stats"])

    return router


def _ensure_ai_review_idle(ai_review_tasks: Mapping[str, Any] | None, file_path: str) -> None:
    if not ai_review_tasks:
        return
    from app.services.ai_review_tasks import active_ai_review_for_file

    if active_ai_review_for_file(ai_review_tasks, file_path):
        raise HTTPException(status_code=409, detail="Cannot edit review text while AI review is active")


__all__ = ["create_router"]
