from __future__ import annotations

from typing import Any, Callable

import translation.checkpoint as checkpoint
from translation.analysis import apply_mtool_compositions
from translation.batching import default_batch_options, prepare_model_candidate
from translation.quality import new_issues, status_for_output


def finalize_mtool_compositions(
    pipeline: Any,
    *,
    file_path: str,
    translated_items: list[tuple[Any, Any]],
    processed_targets: int,
    total_targets: int,
    progress_callback: Callable[[dict[str, Any]], None] | None,
) -> int:
    """Apply deterministic multiline compositions after child outputs are final."""
    plan = getattr(pipeline, "_mtool_composition_plan", None)
    if plan is None or not plan.entries:
        return processed_targets

    _repair_copied_context_lines(pipeline, plan, file_path, translated_items)

    records: list[dict[str, Any]] = []
    processed_targets = apply_mtool_compositions(
        plan,
        translated_items=translated_items,
        checkpoint_entries=checkpoint.load_progress(file_path),
        file_path=file_path,
        progress_records=records,
        processed_targets=processed_targets,
        total_targets=total_targets,
        progress_callback=progress_callback,
        save_record=pipeline._save_or_buffer_progress,
        mark_dirty=pipeline._writer.mark_dirty,
        emit_progress=pipeline._emit_progress,
        progress_status=pipeline._progress_status,
    )
    checkpoint.save_progress_many(file_path, records)
    return processed_targets


def _repair_copied_context_lines(
    pipeline: Any,
    plan: Any,
    file_path: str,
    translated_items: list[tuple[Any, Any]],
) -> None:
    """Retry leaves that copied an earlier line from read-only scene context."""
    contaminated: dict[int, str] = {}
    for parent in plan.entries.values():
        previous_child: int | None = None
        for piece in parent.pieces:
            child = piece.child_index
            if child is None:
                continue
            if previous_child is not None and child != previous_child:
                previous_source = str(translated_items[previous_child][0])
                source = str(translated_items[child][0])
                previous = str(translated_items[previous_child][1]).strip()
                current = str(translated_items[child][1]).strip()
                if (
                    previous_source != source
                    and len(previous.rstrip("。！？!?，,")) >= 6
                    and current.startswith(previous)
                    and len(current) > len(previous)
                ):
                    contaminated[child] = previous
            previous_child = child
    if not contaminated:
        return

    cfg = pipeline._batch_translation_config()
    model = str(cfg.get("api_quality_model") or pipeline.model)
    options = default_batch_options(cfg)
    records: list[dict[str, Any]] = []
    entries = checkpoint.load_progress(file_path)
    for row, previous in sorted(contaminated.items()):
        pipeline._check_control_flags()
        source = str(translated_items[row][0])
        candidate = prepare_model_candidate(
            batch_i=0, idx=row, source=source, glossary=pipeline.glossary,
        )
        candidate["preserve_source_layout"] = True
        candidate["quality_retry"] = {"previous": "", "issues": ["context_contamination"]}
        try:
            translated, status, issues = pipeline._translate_json_candidates(
                [candidate], file_path, options, "json", model=model,
            )[row]
        except Exception:
            translated, status, issues = "", "review_required", []
        accepted = bool(
            status != "review_required"
            and translated.strip()
            and not translated.strip().startswith(previous)
        )
        if accepted:
            translated_items[row] = (source, translated)
            pipeline.update_output_cell(row, 0, translated)
        else:
            translated = str(translated_items[row][1])
            existing = entries.get((row, 0), {})
            issues = list(existing.get("issues", []) or [])
            issues.extend(new_issues(issues, [{
                "type": "context_contamination",
                "message": "A translated line copied the previous read-only scene line.",
            }]))
            status = status_for_output(source, translated, issues)
        records.append({
            "row": row, "col": 0, "original": source, "translated": translated,
            "status": status, "issues": issues, "json_key": source,
            "entry_classification": candidate["entry_classification"],
            "batch_id": "composition-context-repair",
            "model_identifier": model if accepted else str(entries.get((row, 0), {}).get("model_identifier", model)),
            "retry_count": 1,
        })
    checkpoint.save_progress_many(file_path, records)


__all__ = ["finalize_mtool_compositions"]
