from __future__ import annotations

import os
import uuid
from typing import MutableMapping

from fastapi import HTTPException

from app.services.files import require_mtool_json_file, translated_path
from app.services.runtime_profiles import resolve_execution_profile
from app.services.translation_tasks import TranslationTask


TaskRegistry = MutableMapping[str, TranslationTask]


def task_for_file(tasks: TaskRegistry, file_path: str) -> TranslationTask | None:
    abs_path = os.path.abspath(file_path)
    for task in reversed(list(tasks.values())):
        if os.path.abspath(task.file_path) == abs_path:
            return task
    return None


def start_translation_task(
    tasks: TaskRegistry,
    *,
    file_path: str,
    model: str,
    provider: str | None = None,
    prompt_style: str = "professional",
    translate_columns: list[int] | None = None,
    execution_profile: str = "quality_first",
    profile_options: dict | None = None,
) -> dict:
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    require_mtool_json_file(file_path)
    for task in tasks.values():
        if task.status in ("running", "paused", "stopping"):
            raise HTTPException(status_code=409, detail="A translation task is already active")

    task_id = uuid.uuid4().hex[:12]
    model_name, batch_config, profile_summary = resolve_execution_profile(
        execution_profile,
        model,
        provider,
        profile_options,
    )
    task = TranslationTask(
        task_id=task_id,
        file_path=file_path,
        model=model_name,
        prompt_style=prompt_style,
        translate_columns=translate_columns or [1],
        execution_profile=execution_profile,
        profile_summary=profile_summary,
        batch_config_override=batch_config,
    )
    tasks[task_id] = task
    task.start()
    return {
        "task_id": task_id,
        "status": "running",
        "translated_path": translated_path(file_path),
        "profile": profile_summary,
    }


def require_task(tasks: TaskRegistry, task_id: str) -> TranslationTask:
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def pause_translation_task(tasks: TaskRegistry, task_id: str) -> dict:
    task = require_task(tasks, task_id)
    task.pause()
    return {"task_id": task_id, "status": task.status}


def resume_translation_task(tasks: TaskRegistry, task_id: str) -> dict:
    task = require_task(tasks, task_id)
    task.resume()
    return {"task_id": task_id, "status": task.status}


def cancel_translation_task(tasks: TaskRegistry, task_id: str) -> dict:
    task = require_task(tasks, task_id)
    task.cancel()
    return {"task_id": task_id, "status": task.status}


def translation_task_progress(tasks: TaskRegistry, task_id: str) -> dict:
    return require_task(tasks, task_id).get_progress()


def list_translation_tasks(tasks: TaskRegistry) -> dict:
    return {
        "tasks": [
            {
                "task_id": task_id,
                "file_path": task.file_path,
                "file_name": os.path.basename(task.file_path),
                "status": task.status,
                "percentage": task.progress.get("percentage", 0),
                "model": task.model,
                "execution_profile": task.execution_profile,
                "profile": dict(task.profile_summary),
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "review_summary": dict(task.review_summary),
                "has_unexported_result": task.has_unexported_result,
            }
            for task_id, task in tasks.items()
        ]
    }
