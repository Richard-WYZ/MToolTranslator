from __future__ import annotations

import sys
import threading
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.bootstrap import initialize_runtime_environment

initialize_runtime_environment()

import app.services.glossary as _glossary_service
from common.paths import bundled_base_dir, runtime_base_dir, upload_dir
from app.routes.batch import router as batch_router
from app.routes.core import create_router as create_core_router
from app.routes.files import create_router as create_files_router
from app.routes.glossary import create_router as create_glossary_router
from app.routes.models import router as models_router
from app.routes.review import create_router as create_review_router
from app.routes.ai_review import create_router as create_ai_review_router
from app.routes.settings import create_router as create_settings_router
from app.routes.translation_state import (
    cleanup_translation_state,
    create_router as create_translation_state_router,
    delete_history_session,
)
from app.routes.translation_tasks import create_router as create_translation_tasks_router
from app.schemas import CleanupRequest
from app.services import BatchTranslationManager, TranslationTask
from app.services.ai_review_tasks import AIReviewTask


BASE_DIR = runtime_base_dir()
BUNDLE_DIR = bundled_base_dir()
UPLOAD_DIR = upload_dir()
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="MTool 汉化工具")
app.mount("/static", StaticFiles(directory=str(BUNDLE_DIR / "ui" / "static")), name="static")

app.include_router(create_core_router(bundle_dir=BUNDLE_DIR))
app.include_router(models_router)
app.include_router(batch_router)


_tasks: dict[str, TranslationTask] = {}
_batches: dict[str, BatchTranslationManager] = {}
_ai_review_tasks: dict[str, AIReviewTask] = {}


def _get_task_for_file(file_path: str) -> Optional["TranslationTask"]:
    return _glossary_service.get_task_for_file(_tasks, file_path)


def _pause_and_flush_for_edit(file_path: str) -> tuple[Optional["TranslationTask"], bool]:
    return _glossary_service.pause_and_flush_for_edit(_tasks, file_path)


def _resume_after_edit(task: Optional["TranslationTask"], was_running: bool) -> None:
    _glossary_service.resume_after_edit(task, was_running)


def _sync_task_output_cell(file_path: str, row: int, col: int, text: str) -> None:
    _glossary_service.sync_task_output_cell(_tasks, file_path, row, col, text)


def _sync_task_glossary(file_path: str, glossary) -> None:
    _glossary_service.sync_task_glossary(_tasks, file_path, glossary)


def _apply_term_edit_to_outputs(
    file_path: str,
    old_src: str,
    old_tgt: str,
    new_src: str,
    new_tgt: str,
    aliases: Optional[list[str]] = None,
) -> int:
    return _glossary_service.apply_term_edit_to_outputs(
        file_path,
        old_src,
        old_tgt,
        new_src,
        new_tgt,
        tasks=_tasks,
        aliases=aliases,
    )


def cleanup_translation(req: CleanupRequest):
    from app.services.ai_review_tasks import active_ai_review_for_file

    if active_ai_review_for_file(_ai_review_tasks, req.file_path):
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="Stop AI review before cleaning translation state")
    return cleanup_translation_state(req, tasks=_tasks)


def _finalize_exported_session(file_path: str):
    return delete_history_session(
        file_path,
        tasks=_tasks,
        ai_review_tasks=_ai_review_tasks,
        upload_dir=UPLOAD_DIR,
        purge_working_source=True,
    )


app.include_router(create_glossary_router(base_dir=BASE_DIR, tasks=_tasks, ai_review_tasks=_ai_review_tasks))
app.include_router(create_files_router(
    upload_dir=UPLOAD_DIR,
    tasks=_tasks,
    get_task_for_file=_get_task_for_file,
    ai_review_tasks=_ai_review_tasks,
    finalize_completed_session=_finalize_exported_session,
))
app.include_router(create_translation_tasks_router(tasks=_tasks, ai_review_tasks=_ai_review_tasks))
app.include_router(create_settings_router(tasks=_tasks, ai_review_tasks=_ai_review_tasks))
app.include_router(create_review_router(tasks=_tasks, ai_review_tasks=_ai_review_tasks))
app.include_router(create_ai_review_router(translation_tasks=_tasks, ai_review_tasks=_ai_review_tasks))
app.include_router(create_translation_state_router(
    tasks=_tasks,
    batches=_batches,
    ai_review_tasks=_ai_review_tasks,
    upload_dir=UPLOAD_DIR,
))


if __name__ == "__main__":
    if "--desktop" in sys.argv or getattr(sys, "frozen", False):
        from app.desktop import run_desktop

        run_desktop(app)
    else:
        uvicorn.run(app, host="127.0.0.1", port=8000)
