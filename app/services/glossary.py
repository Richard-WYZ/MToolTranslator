from __future__ import annotations

import os
import time
from typing import Any, MutableMapping

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


def glossary_occurrences(
    file_path: str, source: str, *, offset: int = 0, limit: int = 20,
    target: str | None = None,
) -> dict[str, Any]:
    """Read current source-matched examples and preview a term edit without applying it."""
    from fastapi import HTTPException
    from app.services.review import load_review_context
    from translation import checkpoint
    from translation.terminology import Glossary
    from translation.terminology.aliases import apply_term_aliases

    if not source:
        raise HTTPException(status_code=400, detail="Source term is required")
    ctx = load_review_context(file_path)
    glossary = Glossary(file_path=checkpoint.get_glossary_path(file_path))
    info = glossary.candidates.get(source, {}) or {}
    old_target = str(glossary.terms.get(source, ""))
    proposed = str(target if target is not None else old_target)
    caches = ctx.setdefault("term_occurrences", {})
    matches = caches.get(source)
    if matches is None:
        pattern = Glossary._term_pattern(source)
        matches = [
            (row, count) for row, original in enumerate(ctx["source_texts"])
            if (count := sum(1 for _ in pattern.finditer(original)))
        ]
        if len(caches) >= 128:
            caches.pop(next(iter(caches)))
        caches[source] = matches
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    if matches and offset >= len(matches):
        offset = ((len(matches) - 1) // limit) * limit
    aliases = list((info.get("targets") or {}).keys()) if isinstance(info.get("targets"), dict) else []
    term = {"source": source, "target": proposed, "aliases": [old_target, *aliases]}

    def effect(row: int) -> str:
        entry = ctx["cp_entries"].get(f"{row}_0", {}) or {}
        columns = ctx["columns_by_row"][row]
        status = columns[0].get("status", "pending") if columns else "pending"
        if status == "preserved":
            return "preserved"
        if entry.get("entry_classification") == "composed_multiline":
            return "derived"
        if status == "pending" or not entry:
            return "pending"
        return "revalidate"

    items = []
    for row, count in matches[offset:offset + limit]:
        original = ctx["source_texts"][row]
        translated = ctx["translated_texts"][row]
        kind = effect(row)
        preview = apply_term_aliases(original, translated, [term]) if kind == "revalidate" else translated
        if kind == "revalidate" and preview != translated:
            kind = "replace"
        columns = ctx["columns_by_row"][row]
        items.append({
            "row": row, "original": original, "translated": translated,
            "status": columns[0].get("status", "pending") if columns else "pending",
            "occurrence_count": count, "preview_translation": preview, "effect": kind,
        })
    return {
        "source": source, "current_target": old_target, "target": proposed,
        "entry_count": len(matches), "occurrence_count": sum(count for _, count in matches),
        "affected_count": sum(effect(row) == "revalidate" for row, _ in matches),
        "offset": offset, "limit": limit, "items": items,
        "target_valid": bool(proposed) and glossary._passes_term_review(source, proposed, str(info.get("type") or "proper_noun")),
        "preview_only": True,
    }


def apply_term_edit_to_outputs(
    file_path: str,
    old_src: str,
    old_tgt: str,
    new_src: str,
    new_tgt: str,
    *,
    tasks: MutableMapping[str, TranslationTask],
    aliases: list[str] | None = None,
    defer_repair: bool = False,
) -> int:
    if not new_src or not new_tgt:
        return 0
    from translation import checkpoint
    from translation.terminology import Glossary
    from translation.terminology.backfill import backfill_confirmed_terms_to_outputs

    require_mtool_json_file(file_path)
    glossary = Glossary(file_path=checkpoint.get_glossary_path(file_path))
    if glossary.terms.get(new_src) != new_tgt:
        raise ValueError("The confirmed glossary target changed before backfill")
    trans_path = translated_path(file_path)
    from translation.input import load_json_items
    from translation.output import write_json_items
    items = load_json_items(trans_path) if os.path.isfile(trans_path) else None
    changed_rows: set[int] = set()

    def update_cell(row: int, col: int, value: str) -> bool:
        if items is None or col != 0 or row < 0 or row >= len(items):
            return False
        items[row] = (items[row][0], value)
        changed_rows.add(row)
        sync_task_output_cell(tasks, file_path, row, col, value)
        return True

    term = {
        "source": new_src,
        "target": new_tgt,
        "aliases": list(dict.fromkeys([old_tgt, *(aliases or [])])),
    }
    affected = backfill_confirmed_terms_to_outputs(
        file_path, [term], update_output_cell=update_cell,
        glossary_mappings=[
            {"source": source, "target": target}
            for source, target, _owner, _type in glossary.iter_mappings()
        ],
        glossary_version=glossary.version(),
    )
    if affected:
        checkpoint.set_glossary_version(file_path, glossary.version())
    if items is not None and not defer_repair and affected:
        from translation.workflow.glossary_repair import repair_pending_glossary_entries
        from translation.workflow.pipeline import TranslationPipeline

        task = get_task_for_file(tasks, file_path)
        cp = checkpoint.load_checkpoint(file_path)
        pipeline = TranslationPipeline(
            model=(task.model if task else cp.get("model")) or None,
            glossary=glossary,
            prompt_style=(task.prompt_style if task else cp.get("prompt_style")) or "professional",
            batch_config_override=task.batch_config_override if task else None,
        )
        repair_pending_glossary_entries(
            pipeline, file_path, items, update_output_cell=update_cell,
        )
        _recompose_affected_parents(file_path, items, changed_rows)
    if items is not None and changed_rows:
        write_json_items(items, trans_path)
    from app.services.review import invalidate_review_cache
    invalidate_review_cache(file_path)
    return affected


def _recompose_affected_parents(
    file_path: str,
    items: list[tuple[object, object]],
    changed_rows: set[int],
) -> None:
    if not changed_rows:
        return
    from translation.analysis import apply_mtool_compositions, build_mtool_composition_plan
    from translation.checkpoint import load_progress, save_progress_many

    plan = build_mtool_composition_plan(items)
    plan.entries = {
        row: entry for row, entry in plan.entries.items()
        if changed_rows.intersection(entry.dependency_indexes)
    }
    if not plan.entries:
        return
    records: list[dict] = []
    apply_mtool_compositions(
        plan, translated_items=items, checkpoint_entries=load_progress(file_path),
        file_path=file_path, progress_records=records, processed_targets=0,
        total_targets=0, progress_callback=None,
        save_record=lambda _path, _records, **record: records.append(record),
        mark_dirty=lambda: None, emit_progress=lambda *_args, **_kwargs: None,
        progress_status=lambda status: status,
    )
    save_progress_many(file_path, records)


__all__ = [
    "apply_term_edit_to_outputs",
    "get_task_for_file",
    "glossary_occurrences",
    "pause_and_flush_for_edit",
    "resume_after_edit",
    "sync_task_glossary",
    "sync_task_output_cell",
]
