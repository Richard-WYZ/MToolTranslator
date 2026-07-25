from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException
from app.services.files import json_original_text, require_mtool_json_file, translated_path
from translation.config import output_constraints


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


def review_violations(translated_text: str, cp_entry: dict, status: str, max_chars: int, max_lines: int) -> list[dict]:
    if not is_reviewed_status(status):
        return []
    from translation.quality import get_violations

    violations = get_violations(translated_text, max_chars=max_chars, max_lines=max_lines)
    violations.extend(checkpoint_violations(cp_entry, status))
    return violations


def review_filter_matches(columns: list[dict], filter_name: str) -> bool:
    filter_name = filter_name or "all"
    if filter_name == "all":
        return bool(columns)

    def issue_types(col: dict) -> set[str]:
        return {str(item.get("type", "")) for item in col.get("violations", []) if isinstance(item, dict)}

    if filter_name in ("issues", "violated"):
        return any(
            col.get("violations")
            or col.get("is_refusal")
            or col.get("status") in ("failed_refusal", "review_required", "translated_needs_review")
            for col in columns
        )
    if filter_name == "pending":
        return any(col.get("status") == "pending" for col in columns)
    if filter_name == "refusal":
        return any(
            col.get("is_refusal")
            or col.get("status") in ("failed_refusal", "review_required")
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
    return bool(columns)


def load_review_context(file_path: str) -> dict:
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"File does not exist: {file_path}")

    from translation import checkpoint as translation_checkpoint
    from translation.input import load_json_items
    from translation.terminology import Glossary

    output_path = translated_path(file_path)
    cp_data = translation_checkpoint.load_checkpoint(file_path)
    max_chars, max_lines = output_constraints()
    glossary = Glossary(file_path=translation_checkpoint.get_glossary_path(file_path))

    require_mtool_json_file(file_path)
    original_items = load_json_items(file_path)
    translated_items = load_json_items(output_path) if os.path.isfile(output_path) else []
    return {
        "file_type": "json",
        "original_items": original_items,
        "translated_items": translated_items,
        "mtool": True,
        "total_rows": len(original_items),
        "header": ["Key", "Value"],
        "cp_entries": cp_data.get("entries", {}),
        "max_chars": max_chars,
        "max_lines": max_lines,
        "glossary_terms": glossary.terms,
    }


def build_review_row(ctx: dict, row: int) -> dict:
    from translation.quality import is_refusal

    if row < 0 or row >= ctx["total_rows"]:
        raise HTTPException(status_code=400, detail=f"Row out of range: {row}, total rows: {ctx['total_rows']}")

    cp_entries = ctx["cp_entries"]
    columns: list[dict] = []

    if ctx["file_type"] == "json":
        key, value = ctx["original_items"][row]
        translated_text = ""
        translated_items = ctx["translated_items"]
        if row < len(translated_items) and isinstance(translated_items[row][1], str):
            translated_text = translated_items[row][1]
        cp_entry = cp_entries.get(f"{row}_0", {})
        original_text = json_original_text(key, value, cp_entry, mtool=ctx["mtool"])
        if str(original_text).strip():
            status = cp_entry.get("status", "pending") if isinstance(cp_entry, dict) else "pending"
            refusal = status != "failed_untranslated" and is_reviewed_status(status) and is_refusal(translated_text) if translated_text else False
            if refusal:
                status = "failed_refusal"
            columns.append({
                "col": 0,
                "key": str(key),
                "original": original_text,
                "translated": translated_text,
                "status": status,
                "violations": review_violations(translated_text, cp_entry, status, ctx["max_chars"], ctx["max_lines"]),
                "glossary_hits": find_glossary_hits(original_text, ctx["glossary_terms"]),
                "is_refusal": refusal,
                "entry_classification": cp_entry.get("entry_classification", "") if isinstance(cp_entry, dict) else "",
                "model_identifier": cp_entry.get("model_identifier", "") if isinstance(cp_entry, dict) else "",
                "batch_id": cp_entry.get("batch_id", "") if isinstance(cp_entry, dict) else "",
                "retry_count": int(cp_entry.get("retry_count", 0) or 0) if isinstance(cp_entry, dict) else 0,
                "review_reasons": cp_entry.get("review_reasons", []) if isinstance(cp_entry, dict) else [],
                "updated_at": cp_entry.get("updated_at", "") if isinstance(cp_entry, dict) else "",
            })
        neighbors = []
        for neighbor_row in range(max(0, row - 2), min(ctx["total_rows"], row + 3)):
            if neighbor_row == row:
                continue
            neighbor_key, neighbor_value = ctx["original_items"][neighbor_row]
            neighbor_cp = cp_entries.get(f"{neighbor_row}_0", {})
            neighbor_source = json_original_text(
                neighbor_key,
                neighbor_value,
                neighbor_cp,
                mtool=ctx["mtool"],
            )
            neighbor_translation = ""
            if neighbor_row < len(translated_items) and isinstance(translated_items[neighbor_row][1], str):
                neighbor_translation = translated_items[neighbor_row][1]
            neighbors.append({
                "row": neighbor_row,
                "position": "before" if neighbor_row < row else "after",
                "original": neighbor_source,
                "translated": neighbor_translation,
            })
        return {
            "row": row,
            "total_rows": ctx["total_rows"],
            "file_type": "json",
            "columns": columns,
            "header": ctx["header"],
            "neighbors": neighbors,
        }

    raise HTTPException(status_code=400, detail="Only MTool JSON review is supported")


def matching_review_rows(ctx: dict, filter_name: str) -> list[int]:
    rows: list[int] = []
    for row in range(ctx["total_rows"]):
        item = build_review_row(ctx, row)
        if review_filter_matches(item.get("columns", []), filter_name):
            rows.append(row)
    return rows
