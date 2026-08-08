from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import HTTPException
from app.services.files import json_original_text, require_mtool_json_file, translated_path
from translation.config import output_constraints


_ACTIONABLE_STATUSES = {"translated_needs_review", "review_required"}
_DERIVED_COMPOSITION_ISSUES = {
    "composed_dependency_review_required",
    "composed_dependency_needs_review",
}
_REVIEW_CACHE: dict[str, dict[str, Any]] = {}
_REVIEW_CACHE_LOCK = threading.RLock()


def checkpoint_path_review(file_path: str) -> str:
    return f"{os.path.abspath(file_path)}.checkpoint.json"


def load_checkpoint_data(file_path: str) -> dict:
    import json

    checkpoint_path = checkpoint_path_review(file_path)
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def save_checkpoint_data(file_path: str, data: dict) -> None:
    import json

    checkpoint_path = checkpoint_path_review(file_path)
    checkpoint_dir = os.path.dirname(checkpoint_path)
    if checkpoint_dir:
        os.makedirs(checkpoint_dir, exist_ok=True)
    with open(checkpoint_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def find_glossary_hits(original: str, glossary_terms: dict) -> list:
    if not original or not glossary_terms:
        return []
    hits = []
    for source, target in glossary_terms.items():
        if source in original:
            hits.append({"source": source, "target": target})
    return hits


def checkpoint_violations(cp_entry: dict, status: str) -> list[dict]:
    violations = []
    if isinstance(cp_entry, dict):
        for issue in cp_entry.get("issues", []):
            issue_type = issue.get("type", "translation_issue")
            violations.append({
                "type": issue_type,
                "message": issue.get("message", issue_type),
            })
    if status == "failed_refusal" and not any(item.get("type") == "model_refusal" for item in violations):
        violations.append({"type": "model_refusal", "message": "Model refused translation; manual review is required."})
    if status == "failed_untranslated" and not any(item.get("type") == "untranslated_japanese" for item in violations):
        violations.append({"type": "untranslated_japanese", "message": "Model returned Japanese source text; manual review is required."})
    return violations


def is_reviewed_status(status: str) -> bool:
    return status in (
        "done",
        "failed_refusal",
        "failed_untranslated",
        "translated",
        "translated_needs_review",
        "review_required",
        "preserved",
    )


def normalized_review_status(status: str) -> str:
    from translation import checkpoint

    raw_status = str(status or "pending")
    return checkpoint.LEGACY_STATUS_MAP.get(raw_status, raw_status)


def review_violations(translated_text: str, cp_entry: dict, status: str, max_chars: int, max_lines: int) -> list[dict]:
    status = normalized_review_status(status)
    if status in ("pending", "preserved") or not is_reviewed_status(status):
        return []
    from translation.quality import get_violations

    violations = get_violations(translated_text, max_chars=max_chars, max_lines=max_lines)
    violations.extend(checkpoint_violations(cp_entry, status))
    unique: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for violation in violations:
        key = (str(violation.get("type", "")), str(violation.get("message", "")))
        if key not in seen:
            unique.append(violation)
            seen.add(key)
    return unique


def review_filter_matches(columns: list[dict], filter_name: str) -> bool:
    filter_name = filter_name or "all"
    if filter_name == "all":
        return bool(columns)

    def issue_types(col: dict) -> set[str]:
        return {str(item.get("type", "")) for item in col.get("violations", []) if isinstance(item, dict)}

    if filter_name == "issues":
        return any(_is_actionable_review_column(col) for col in columns)
    if filter_name == "required":
        return any(_is_actionable_review_column(col) and col.get("status") == "review_required" for col in columns)
    if filter_name == "advisory":
        return any(_is_actionable_review_column(col) and col.get("status") == "translated_needs_review" for col in columns)
    if filter_name == "preserved":
        return any(col.get("status") == "preserved" for col in columns)
    if filter_name == "violated":
        return any(
            col.get("violations")
            for col in columns
        )
    if filter_name == "pending":
        return any(col.get("status") == "pending" for col in columns)
    if filter_name == "refusal":
        return any(
            col.get("is_refusal")
            or "model_refusal" in issue_types(col)
            for col in columns
        )
    if filter_name == "term":
        return any("term_preservation" in issue_types(col) for col in columns)
    if filter_name == "english":
        return any("english_residue" in issue_types(col) for col in columns)
    if filter_name == "symbol":
        return any("symbol_preservation" in issue_types(col) for col in columns)
    if filter_name == "length":
        return any(issue_types(col) & {"line_too_long", "too_many_lines"} for col in columns)
    if filter_name.startswith("ai_"):
        expected = {
            "ai_fixed": "fixed",
            "ai_confirmed": "confirmed",
            "ai_unresolved": "unresolved",
            "ai_conflict": "conflict",
        }.get(filter_name)
        if expected:
            return any((col.get("ai_review") or {}).get("status") == expected for col in columns)
        if filter_name == "ai_pending":
            return any(
                _is_actionable_review_column(col)
                and not (col.get("ai_review") or {}).get("status")
                for col in columns
            )
    return bool(columns)


def _is_actionable_review_column(column: dict) -> bool:
    return (
        column.get("status") in _ACTIONABLE_STATUSES
        and not bool(column.get("derived_review"))
    )


def _is_derived_composition_review(cp_entry: dict) -> bool:
    if str(cp_entry.get("entry_classification", "")) != "composed_multiline":
        return False
    issue_types = {
        str(issue.get("type", ""))
        for issue in cp_entry.get("issues", []) or []
        if isinstance(issue, dict)
    }
    return bool(issue_types) and issue_types.issubset(_DERIVED_COMPOSITION_ISSUES)


def _file_stamp(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _review_signature(file_path: str) -> tuple[Any, ...]:
    from translation import checkpoint
    from translation.review.ai import ai_review_store_path

    return (
        _file_stamp(file_path),
        _file_stamp(translated_path(file_path)),
        _file_stamp(checkpoint.get_checkpoint_path(file_path)),
        _file_stamp(checkpoint.get_glossary_path(file_path)),
        _file_stamp(ai_review_store_path(file_path)),
    )


def invalidate_review_cache(file_path: str) -> None:
    with _REVIEW_CACHE_LOCK:
        _REVIEW_CACHE.pop(os.path.abspath(file_path), None)


def _translation_for_row(cp_entry: dict, output_text: str) -> str:
    status = normalized_review_status(cp_entry.get("status", "pending")) if isinstance(cp_entry, dict) else "pending"
    checkpoint_text = cp_entry.get("translated") if isinstance(cp_entry, dict) else None
    if status in {"translated", "preserved", "translated_needs_review", "review_required"} and isinstance(checkpoint_text, str):
        return checkpoint_text
    return output_text


def _build_review_context(file_path: str, signature: tuple[Any, ...]) -> dict:
    from translation import checkpoint
    from translation.input import is_mtool_items, load_json_items
    from translation.terminology import Glossary
    from translation.review.ai import latest_ai_review_by_row

    require_mtool_json_file(file_path)
    original_items = load_json_items(file_path)
    if not is_mtool_items(original_items):
        raise HTTPException(status_code=400, detail="Only flat MTool JSON mappings are supported")
    output_path = translated_path(file_path)
    translated_items = load_json_items(output_path) if os.path.isfile(output_path) else []
    cp_data = checkpoint.load_checkpoint(file_path)
    cp_entries = cp_data.get("entries", {})
    if not isinstance(cp_entries, dict):
        cp_entries = {}
    max_chars, max_lines = output_constraints()
    glossary = Glossary(file_path=checkpoint.get_glossary_path(file_path))
    ai_review_by_row = latest_ai_review_by_row(file_path)

    source_texts: list[str] = []
    translated_texts: list[str] = []
    columns_by_row: list[list[dict]] = []
    filter_names = (
        "all", "issues", "required", "advisory", "preserved", "violated", "pending",
        "refusal", "term", "english", "symbol", "length",
        "ai_fixed", "ai_confirmed", "ai_unresolved", "ai_conflict", "ai_pending",
    )
    rows_by_filter: dict[str, list[int]] = {name: [] for name in filter_names}
    stats = {
        "total": 0,
        "reviewed": 0,
        "needs_review": 0,
        "required_review": 0,
        "advisory_review": 0,
        "system_preserved": 0,
        "confirmed_translation": 0,
        "violations_count": 0,
        "diagnostics_count": 0,
        "total_rows": len(original_items),
        "ai_fixed": 0,
        "ai_confirmed": 0,
        "ai_unresolved": 0,
        "ai_conflict": 0,
        "ai_pending": 0,
    }

    for row, (key, value) in enumerate(original_items):
        cp_entry = cp_entries.get(f"{row}_0", {})
        if not isinstance(cp_entry, dict):
            cp_entry = {}
        original_text = str(json_original_text(key, value, cp_entry, mtool=True))
        output_text = ""
        if row < len(translated_items) and isinstance(translated_items[row][1], str):
            output_text = translated_items[row][1]
        translated_text = _translation_for_row(cp_entry, output_text)
        ai_metadata = dict(ai_review_by_row.get(row, {}) or {})
        if ai_metadata and str(ai_metadata.get("translation", "")) != translated_text:
            ai_metadata = {}
        source_texts.append(original_text)
        translated_texts.append(translated_text)

        columns: list[dict] = []
        if original_text.strip():
            status = normalized_review_status(cp_entry.get("status", "pending"))
            violations = review_violations(translated_text, cp_entry, status, max_chars, max_lines)
            issue_types = {str(item.get("type", "")) for item in violations}
            refusal = "model_refusal" in issue_types
            derived_review = _is_derived_composition_review(cp_entry)
            columns.append({
                "col": 0,
                "key": str(key),
                "original": original_text,
                "translated": translated_text,
                "status": status,
                "violations": violations,
                "is_refusal": refusal,
                "derived_review": derived_review,
                "entry_classification": cp_entry.get("entry_classification", ""),
                "model_identifier": cp_entry.get("model_identifier", ""),
                "batch_id": cp_entry.get("batch_id", ""),
                "retry_count": int(cp_entry.get("retry_count", 0) or 0),
                "review_reasons": cp_entry.get("review_reasons", []),
                "updated_at": cp_entry.get("updated_at", ""),
                "ai_review": ai_metadata,
            })
            stats["total"] += 1
            if status in _ACTIONABLE_STATUSES and not derived_review:
                stats["needs_review"] += 1
                if status == "review_required":
                    stats["required_review"] += 1
                else:
                    stats["advisory_review"] += 1
            elif status in ("translated", "preserved"):
                stats["reviewed"] += 1
                if status == "preserved":
                    stats["system_preserved"] += 1
                else:
                    stats["confirmed_translation"] += 1
            if violations:
                stats["violations_count"] += 1
            if violations and (status not in _ACTIONABLE_STATUSES or derived_review):
                stats["diagnostics_count"] += 1
            ai_status = str(ai_metadata.get("status", ""))
            if ai_status in {"fixed", "confirmed", "unresolved", "conflict"}:
                stats[f"ai_{ai_status}"] += 1
            elif status in _ACTIONABLE_STATUSES and not derived_review:
                stats["ai_pending"] += 1
        columns_by_row.append(columns)
        for filter_name in filter_names:
            if review_filter_matches(columns, filter_name):
                rows_by_filter[filter_name].append(row)

    return {
        "signature": signature,
        "file_type": "json",
        "original_items": original_items,
        "mtool": True,
        "total_rows": len(original_items),
        "header": ["Key", "Value"],
        "cp_entries": cp_entries,
        "max_chars": max_chars,
        "max_lines": max_lines,
        "glossary_terms": glossary.terms,
        "source_texts": source_texts,
        "translated_texts": translated_texts,
        "columns_by_row": columns_by_row,
        "rows_by_filter": rows_by_filter,
        "stats": stats,
    }


def load_review_context(file_path: str) -> dict:
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"File does not exist: {file_path}")
    cache_key = os.path.abspath(file_path)
    signature = _review_signature(file_path)
    with _REVIEW_CACHE_LOCK:
        cached = _REVIEW_CACHE.get(cache_key)
        if cached and cached.get("signature") == signature:
            return cached
        context = _build_review_context(file_path, signature)
        _REVIEW_CACHE[cache_key] = context
        return context


def build_review_row(ctx: dict, row: int) -> dict:
    if row < 0 or row >= ctx["total_rows"]:
        raise HTTPException(status_code=400, detail=f"Row out of range: {row}, total rows: {ctx['total_rows']}")

    columns = [dict(column) for column in ctx["columns_by_row"][row]]
    for column in columns:
        column["glossary_hits"] = find_glossary_hits(column["original"], ctx["glossary_terms"])
    neighbors = []
    for neighbor_row in range(max(0, row - 2), min(ctx["total_rows"], row + 3)):
        if neighbor_row == row:
            continue
        neighbors.append({
            "row": neighbor_row,
            "position": "before" if neighbor_row < row else "after",
            "original": ctx["source_texts"][neighbor_row],
            "translated": ctx["translated_texts"][neighbor_row],
        })
    return {
        "row": row,
        "total_rows": ctx["total_rows"],
        "file_type": "json",
        "columns": columns,
        "header": ctx["header"],
        "neighbors": neighbors,
    }


def matching_review_rows(ctx: dict, filter_name: str) -> list[int]:
    normalized_filter = filter_name if filter_name in ctx["rows_by_filter"] else "all"
    return ctx["rows_by_filter"][normalized_filter]
