from __future__ import annotations

from typing import Any, Callable


def apply_batch_translation_results(
    *,
    candidates: list[dict[str, Any]],
    translated_payloads: dict[int, tuple[str, str, list[dict[str, Any]]]],
    translated_items: list[tuple[Any, Any]],
    processed_targets: int,
    total_targets: int,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    file_path: str,
    mtool: bool,
    progress_records: list[dict[str, Any]],
    glossary: Any,
    mark_dirty: Callable[[], None],
    emit_progress: Callable[..., None],
    progress_status: Callable[[str], str],
    apply_confirmed_terms_to_outputs: Callable[[str, list[dict[str, Any]]], None],
    batch_id: str | None = None,
    model_identifier: str | None = None,
    retry_count: int | None = None,
) -> tuple[int, bool]:
    """Apply finished batch translations to output state and progress records."""
    glossary_changed = False
    for candidate in candidates:
        translated, status, issues = translated_payloads[candidate["idx"]]
        key, _value = translated_items[candidate["idx"]]
        translated_items[candidate["idx"]] = (key, translated)
        record = {
            "row": candidate["idx"],
            "col": 0,
            "original": candidate["source"],
            "translated": translated,
            "status": status,
            "issues": issues,
            "json_key": str(key),
            "mtool": mtool,
            "entry_classification": candidate.get("entry_classification", "model_text"),
        }
        if batch_id is not None:
            record["batch_id"] = batch_id
        if model_identifier is not None:
            record["model_identifier"] = model_identifier
        if retry_count is not None:
            record["retry_count"] = max(0, int(retry_count))
        if candidate.get("sensitive_repair_retry_count") is not None:
            record["retry_count"] = max(
                0,
                int(candidate["sensitive_repair_retry_count"]),
            )
        if candidate.get("sensitive_adult"):
            record["sensitive_adult"] = True
        if candidate.get("sensitive_repair_round"):
            record["sensitive_repair_round"] = max(
                1,
                int(candidate["sensitive_repair_round"]),
            )
        if candidate.get("sensitive_parent_repair"):
            record["sensitive_parent_repair"] = True
            record["sensitive_parent_index"] = int(
                candidate.get("sensitive_parent_index", -1)
            )
        if candidate.get("parent_first"):
            record["parent_first"] = True
            record["parent_first_index"] = int(
                candidate.get("parent_first_index", -1)
            )
        context_kinds = sorted({
            str(context.get("context_kind", "composition"))
            for context in candidate.get("contexts", []) or []
            if isinstance(context, dict)
        })
        if context_kinds:
            record["context_kinds"] = context_kinds
        progress_records.append(record)
        mark_dirty()

        processed_targets += 1
        emit_progress(
            progress_callback,
            file_path,
            candidate["idx"],
            0,
            progress_status(status),
            processed_targets,
            total_targets,
            original_text=candidate["source"],
            translated_text=translated,
        )

    confirmed_batch_terms: list[dict[str, Any]] = []
    seen_terms: set[tuple[str, str]] = set()
    for candidate in candidates:
        translated, _status, _issues = translated_payloads[candidate["idx"]]
        confirmed_terms = glossary.auto_extract(candidate["source"], translated)
        if confirmed_terms:
            glossary_changed = True
            for term in confirmed_terms:
                identity = (str(term.get("source", "")), str(term.get("target", "")))
                if identity in seen_terms:
                    continue
                seen_terms.add(identity)
                confirmed_batch_terms.append(term)
    if confirmed_batch_terms:
        apply_confirmed_terms_to_outputs(file_path, confirmed_batch_terms)
    return processed_targets, glossary_changed


__all__ = ["apply_batch_translation_results"]
