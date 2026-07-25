from __future__ import annotations

from typing import Any, Callable


def collect_json_batch_window(
    *,
    translated_items: list[tuple[Any, Any]],
    start_idx: int,
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    batch_size: int,
    max_batch_chars: int,
    file_path: str,
    total_targets: int,
    processed_targets: int,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    progress_records: list[dict[str, Any]] | None,
    glossary: Any,
    check_control_flags: Callable[[], None],
    source_text: Callable[[Any, Any, bool], str],
    is_completed_entry: Callable[[Any, str], bool],
    deterministic_translation: Callable[[str], str],
    status_for_output: Callable[[str, str], str],
    progress_status: Callable[[str], str],
    save_or_buffer_progress: Callable[..., None],
    mark_dirty: Callable[[], None],
    emit_progress: Callable[..., None],
    prepare_candidate: Callable[..., dict[str, Any]],
    looks_like_short_label: Callable[[str], bool],
    composition_plan: Any | None = None,
    neighbor_context_plan: Any | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Collect a model-bound JSON batch window while applying non-model entries."""
    candidates: list[dict[str, Any]] = []
    total_chars = 0
    idx = start_idx
    while idx < len(translated_items) and len(candidates) < batch_size:
        check_control_flags()
        key, value = translated_items[idx]
        current_source = source_text(key, value, mtool)
        if composition_plan is not None and composition_plan.is_composed_parent(idx):
            idx += 1
            continue
        if not current_source.strip():
            translated_items[idx] = (key, current_source)
            save_or_buffer_progress(
                file_path,
                progress_records,
                row=idx,
                col=0,
                original=current_source,
                translated=current_source,
                status="preserved",
                issues=[],
                json_key=str(key),
                mtool=mtool,
                entry_classification="empty",
            )
            mark_dirty()
            processed_targets += 1
            emit_progress(progress_callback, file_path, idx, 0, "preserved", processed_targets, total_targets)
            idx += 1
            continue

        cp_entry = completed.get((idx, 0))
        if is_completed_entry(cp_entry, current_source):
            translated = cp_entry.get("translated", current_source)
            deterministic = deterministic_translation(current_source)
            if deterministic and (deterministic != translated or cp_entry.get("issues")):
                translated = deterministic
                save_or_buffer_progress(
                    file_path,
                    progress_records,
                    row=idx,
                    col=0,
                    original=current_source,
                    translated=translated,
                    status=status_for_output(current_source, translated),
                    issues=[],
                    json_key=str(key),
                    mtool=mtool,
                    entry_classification="deterministic",
                )
            translated_items[idx] = (key, translated)
            processed_targets += 1
            mark_dirty()
            emit_progress(
                progress_callback,
                file_path,
                idx,
                0,
                "resumed",
                processed_targets,
                total_targets,
                original_text=current_source,
                translated_text=translated,
            )
            idx += 1
            continue

        deterministic = deterministic_translation(current_source)
        if deterministic:
            translated_items[idx] = (key, deterministic)
            deterministic_status = status_for_output(current_source, deterministic)
            save_or_buffer_progress(
                file_path,
                progress_records,
                row=idx,
                col=0,
                original=current_source,
                translated=deterministic,
                status=deterministic_status,
                issues=[],
                json_key=str(key),
                mtool=mtool,
                entry_classification="deterministic",
            )
            mark_dirty()
            processed_targets += 1
            emit_progress(
                progress_callback,
                file_path,
                idx,
                0,
                progress_status(deterministic_status),
                processed_targets,
                total_targets,
                original_text=current_source,
                translated_text=deterministic,
            )
            idx += 1
            continue

        candidate = prepare_candidate(
            batch_i=len(candidates),
            idx=idx,
            source=current_source,
            glossary=glossary,
            short_label=looks_like_short_label(current_source),
        )
        candidate["preserve_source_layout"] = bool(mtool)
        if composition_plan is not None:
            contexts = composition_plan.contexts_for_child(idx)
            if contexts:
                candidate["contexts"] = contexts
        if not candidate.get("contexts") and neighbor_context_plan is not None:
            contexts = neighbor_context_plan.contexts_for_child(idx)
            if contexts:
                candidate["contexts"] = contexts
        protected_text = candidate["protected"]
        projected_chars = total_chars + len(protected_text)
        if candidates and projected_chars > max_batch_chars:
            break

        candidates.append(candidate)
        total_chars = projected_chars
        idx += 1

    if idx == start_idx and not candidates:
        idx += 1
    return candidates, idx, processed_targets


def collect_json_batch_candidates(
    *,
    translated_items: list[tuple[Any, Any]],
    start_idx: int,
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    batch_size: int,
    max_batch_chars: int,
    glossary: Any,
    source_text: Callable[[Any, Any, bool], str],
    is_completed_entry: Callable[[Any, str], bool],
    deterministic_translation: Callable[[str], str],
    prepare_candidate: Callable[..., dict[str, Any]],
    looks_like_short_label: Callable[[str], bool],
) -> list[dict[str, Any]]:
    """Collect a simple contiguous model-bound candidate batch."""
    candidates: list[dict[str, Any]] = []
    total_chars = 0
    idx = start_idx
    while idx < len(translated_items) and len(candidates) < batch_size:
        key, value = translated_items[idx]
        current_source = source_text(key, value, mtool)
        if not current_source.strip() or is_completed_entry(completed.get((idx, 0)), current_source):
            break
        if deterministic_translation(current_source):
            break
        candidate = prepare_candidate(
            batch_i=len(candidates),
            idx=idx,
            source=current_source,
            glossary=glossary,
            short_label=looks_like_short_label(current_source),
        )
        candidate["preserve_source_layout"] = bool(mtool)
        protected_text = candidate["protected"]
        projected_chars = total_chars + len(protected_text)
        if candidates and projected_chars > max_batch_chars:
            break
        candidates.append(candidate)
        total_chars = projected_chars
        idx += 1
    return candidates


__all__ = ["collect_json_batch_candidates", "collect_json_batch_window"]
