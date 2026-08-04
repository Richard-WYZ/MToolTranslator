from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.schemas import DynamicGlossaryRequest, GlossaryTermRequest, PromoteGlossaryRequest
from app.services.glossary import (
    apply_term_edit_to_outputs,
    pause_and_flush_for_edit,
    resume_after_edit,
    sync_task_glossary,
)
from app.services.translation_tasks import TranslationTask
from translation import checkpoint
from translation.terminology import Glossary


def create_router(
    *,
    base_dir: Path,
    tasks: MutableMapping[str, TranslationTask],
    ai_review_tasks: Mapping[str, Any] | None = None,
) -> APIRouter:
    router = APIRouter()
    shared_glossary = None

    def get_shared_glossary():
        nonlocal shared_glossary
        if shared_glossary is None:
            shared_glossary = Glossary(file_path=str(base_dir / "glossary.json"))
        return shared_glossary

    def ensure_ai_review_idle(file_path: str) -> None:
        if not ai_review_tasks:
            return
        from app.services.ai_review_tasks import active_ai_review_for_file
        if active_ai_review_for_file(ai_review_tasks, file_path):
            raise HTTPException(status_code=409, detail="Cannot edit terminology while AI review is active")

    @router.get("/api/glossary")
    def get_glossary():
        g = get_shared_glossary()
        return {"terms": g.terms}

    @router.post("/api/glossary")
    def add_glossary_term(req: GlossaryTermRequest):
        raise HTTPException(status_code=410, detail="External glossary add is disabled; terms are accumulated during translation")

    @router.delete("/api/glossary/{term}")
    def delete_glossary_term(term: str):
        raise HTTPException(status_code=410, detail="Use dynamic glossary editing for the active translation file")

    @router.post("/api/glossary/import")
    def import_glossary(terms: dict[str, str]):
        raise HTTPException(status_code=410, detail="External glossary import is disabled")

    @router.put("/api/glossary/{japanese}")
    def update_glossary_term(japanese: str, req: GlossaryTermRequest):
        raise HTTPException(status_code=410, detail="Use dynamic glossary editing for the active translation file")

    @router.get("/api/glossary/export")
    def export_glossary():
        g = get_shared_glossary()
        content = json.dumps(g.terms, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=glossary.json"},
        )

    @router.get("/api/glossary/dynamic")
    def get_dynamic_glossary(file_path: str = Query(...)):
        g = Glossary(file_path=checkpoint.get_glossary_path(file_path))
        if g.prune_invalid_terms():
            g.save()
            sync_task_glossary(tasks, file_path, g)
        return g.as_payload()

    @router.put("/api/glossary/dynamic/{term}")
    def update_dynamic_glossary(term: str, req: DynamicGlossaryRequest):
        ensure_ai_review_idle(req.file_path)
        task, was_running = pause_and_flush_for_edit(tasks, req.file_path)
        try:
            g = Glossary(file_path=checkpoint.get_glossary_path(req.file_path))
            old_target = g.terms.get(term, "")
            aliases = list((g.candidates.get(term, {}) or {}).get("targets", {}).keys())
            if term != req.source:
                g.remove(term)
            g.add(req.source, req.target)
            g.save()
            sync_task_glossary(tasks, req.file_path, g)
            changed = apply_term_edit_to_outputs(
                req.file_path,
                term,
                old_target,
                req.source,
                req.target,
                tasks=tasks,
                aliases=aliases,
            )
            return {"ok": True, "terms": g.terms, "candidates": g.candidates, "updated_cells": changed}
        finally:
            resume_after_edit(task, was_running)

    @router.delete("/api/glossary/dynamic/{term}")
    def delete_dynamic_glossary(term: str, file_path: str = Query(...)):
        ensure_ai_review_idle(file_path)
        task, was_running = pause_and_flush_for_edit(tasks, file_path)
        try:
            g = Glossary(file_path=checkpoint.get_glossary_path(file_path))
            g.remove(term)
            g.save()
            sync_task_glossary(tasks, file_path, g)
            return {"ok": True, "terms": g.terms, "candidates": g.candidates}
        finally:
            resume_after_edit(task, was_running)

    @router.post("/api/glossary/promote")
    def promote_dynamic_glossary(req: PromoteGlossaryRequest):
        ensure_ai_review_idle(req.file_path)
        task, was_running = pause_and_flush_for_edit(tasks, req.file_path)
        try:
            g = Glossary(file_path=checkpoint.get_glossary_path(req.file_path))
            aliases = list((g.candidates.get(req.source, {}) or {}).get("targets", {}).keys())
            if not g.promote(req.source, req.target):
                raise HTTPException(status_code=404, detail="Candidate term not found")
            g.save()
            sync_task_glossary(tasks, req.file_path, g)
            changed = apply_term_edit_to_outputs(
                req.file_path,
                req.source,
                "",
                req.source,
                req.target,
                tasks=tasks,
                aliases=aliases,
            )
            return {"ok": True, "terms": g.terms, "candidates": g.candidates, "updated_cells": changed}
        finally:
            resume_after_edit(task, was_running)

    return router


__all__ = ["create_router"]
