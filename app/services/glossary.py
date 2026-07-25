from __future__ import annotations

import os
import time
from typing import MutableMapping

from app.services.files import require_mtool_json_file, translated_path
from app.services.translation_task_service import task_for_file
from app.services.translation_tasks import TranslationTask


def get_task_for_file(tasks: MutableMapping[str, TranslationTask], file_path: str) -> TranslationTask | None:
    return task_for_file(tasks, file_path)


def pause_and_flush_for_edit(
    tasks: MutableMapping[str, TranslationTask],
    file_path: str,
) -> tuple[TranslationTask | None, bool]:
    task = get_task_for_file(tasks, file_path)
    was_running = bool(task and task.status == "running")
    if task and task.status == "running":
        task.pause()
        time.sleep(0.3)
    if task:
        task.flush()
    return task, was_running


def resume_after_edit(task: TranslationTask | None, was_running: bool) -> None:
    if task and was_running and task.status == "paused":
        task.resume()


def sync_task_output_cell(
    tasks: MutableMapping[str, TranslationTask],
    file_path: str,
    row: int,
    col: int,
    text: str,
) -> None:
    task = get_task_for_file(tasks, file_path)
    if task:
        task.update_output_cell(row, col, text)


def sync_task_glossary(
    tasks: MutableMapping[str, TranslationTask],
    file_path: str,
    glossary,
) -> None:
    task = get_task_for_file(tasks, file_path)
    if task:
        task.replace_glossary(glossary)


def apply_term_edit_to_outputs(
    file_path: str,
    old_src: str,
    old_tgt: str,
    new_src: str,
    new_tgt: str,
    *,
    tasks: MutableMapping[str, TranslationTask],
    aliases: list[str] | None = None,
) -> int:
    if not new_tgt:
        return 0
    from translation import checkpoint

    cp = checkpoint.load_checkpoint(file_path)
    changed = 0
    affected: set[tuple[int, int]] = set()
    for key, entry in cp.get("entries", {}).items():
        if old_src and old_src not in entry.get("original", ""):
            continue
        translated = entry.get("translated", "")
        updated = translated
        replacements = set(aliases or [])
        if old_tgt:
            replacements.add(old_tgt)
        for alias in sorted((r for r in replacements if r and r != new_tgt), key=len, reverse=True):
            updated = updated.replace(alias, new_tgt)
        if old_src and old_src in updated:
            updated = updated.replace(old_src, new_tgt)
        if updated != translated:
            entry["translated"] = updated
            entry["issues"] = []
            affected.add((int(entry.get("row", 0)), int(entry.get("col", 0))))
            changed += 1
    if changed:
        checkpoint.save_checkpoint(file_path, cp)

    trans_path = translated_path(file_path)
    if not os.path.isfile(trans_path):
        return changed
    require_mtool_json_file(file_path)
    from translation.input import load_json_items
    from translation.output import write_json_items

    items = load_json_items(trans_path)
    for row, _ in affected:
        if row < len(items) and isinstance(items[row][1], str):
            updated = items[row][1]
            replacements = set(aliases or [])
            if old_tgt:
                replacements.add(old_tgt)
            for alias in sorted((r for r in replacements if r and r != new_tgt), key=len, reverse=True):
                updated = updated.replace(alias, new_tgt)
            if old_src:
                updated = updated.replace(old_src, new_tgt)
            items[row] = (items[row][0], updated)
            sync_task_output_cell(tasks, file_path, row, 0, items[row][1])
    write_json_items(items, trans_path)
    return changed


__all__ = [
    "apply_term_edit_to_outputs",
    "get_task_for_file",
    "pause_and_flush_for_edit",
    "resume_after_edit",
    "sync_task_glossary",
    "sync_task_output_cell",
]
