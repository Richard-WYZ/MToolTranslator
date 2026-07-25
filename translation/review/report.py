from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import translation.checkpoint as checkpoint
from translation.output import default_output_path
from translation.review.summary import build_review_summary


REVIEW_STATUSES = {"translated_needs_review", "review_required"}
DERIVED_COMPOSITION_ISSUES = {
    "composed_dependency_review_required",
    "composed_dependency_needs_review",
}


def review_report_path(file_path: str, output_path: str | None = None) -> str:
    translated_path = Path(output_path or default_output_path(file_path))
    return str(translated_path.with_suffix(".review.json"))


def build_review_report(file_path: str, output_path: str | None = None) -> dict[str, Any]:
    data = checkpoint.load_checkpoint(file_path)
    review_entries: list[dict[str, Any]] = []
    derived_review_entries = 0
    for entry_key, entry in (data.get("entries", {}) or {}).items():
        if not isinstance(entry, dict):
            continue
        status = checkpoint.normalize_status(
            str(entry.get("status", "")),
            entry.get("issues", []) if isinstance(entry.get("issues"), list) else [],
            translated=str(entry.get("translated", "")),
            original=str(entry.get("original", "")),
        )
        if status not in REVIEW_STATUSES:
            continue
        issues = entry.get("issues", []) or []
        issue_types = {
            str(issue.get("type", ""))
            for issue in issues
            if isinstance(issue, dict)
        }
        if (
            str(entry.get("entry_classification", "")) == "composed_multiline"
            and issue_types
            and issue_types.issubset(DERIVED_COMPOSITION_ISSUES)
        ):
            derived_review_entries += 1
            continue
        review_reasons = entry.get("review_reasons", []) or [
            str(issue.get("type", "translation_issue"))
            for issue in issues
            if isinstance(issue, dict)
        ]
        if not review_reasons:
            review_reasons = [status]
        review_entries.append({
            "entry_id": str(entry_key),
            "row": entry.get("row"),
            "col": entry.get("col"),
            "source_key": entry.get("source_key", entry.get("json_key", entry.get("original", ""))),
            "source_hash": entry.get("source_hash", ""),
            "source": entry.get("original", ""),
            "translation": entry.get("output_translation", entry.get("translated", "")),
            "status": status,
            "entry_classification": entry.get("entry_classification", ""),
            "batch_id": entry.get("batch_id", ""),
            "model_identifier": entry.get("model_identifier", data.get("model", "")),
            "prompt_version": entry.get("prompt_version", data.get("prompt_version", "default")),
            "glossary_version": entry.get("glossary_version", data.get("glossary_version", "0")),
            "retry_count": int(entry.get("retry_count", 0) or 0),
            "issues": issues,
            "review_reasons": review_reasons,
            "updated_at": entry.get("updated_at", ""),
        })
    summary = build_review_summary(file_path).as_dict()
    summary["raw_review_queue_size"] = summary["review_queue_size"]
    summary["derived_review_entries"] = derived_review_entries
    summary["review_queue_size"] = len(review_entries)
    return {
        "source_file": str(Path(file_path).resolve()),
        "translated_file": str(Path(output_path or default_output_path(file_path)).resolve()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "items": review_entries,
    }


def write_review_report(file_path: str, output_path: str | None = None) -> str:
    target = Path(review_report_path(file_path, output_path))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_review_report(file_path, output_path), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(target)


__all__ = ["REVIEW_STATUSES", "build_review_report", "review_report_path", "write_review_report"]
