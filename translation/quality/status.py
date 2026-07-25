from __future__ import annotations

from typing import Any


HARD_REVIEW_ISSUE_TYPES = {
    "empty_translation",
    "untranslated_japanese",
    "model_refusal",
    "runtime_token_preservation",
    "term_placeholder_leak",
    "contextual_term_pollution",
    "unsupported_glossary_name",
    "unsupported_proper_name",
}


def status_for_output(source: str, translated: str, issues: list[dict[str, Any]] | None = None) -> str:
    """Return the final translation state for a source/output pair."""
    if not source or not source.strip():
        return "preserved"
    issue_types = {
        str(issue.get("type", ""))
        for issue in issues or []
        if isinstance(issue, dict)
    }
    if issue_types.intersection(HARD_REVIEW_ISSUE_TYPES):
        return "review_required"
    if translated == source:
        return "translated_needs_review" if issues else "preserved"
    return "translated_needs_review" if issues else "translated"


def progress_status(status: str) -> str:
    """Map final statuses to progress event statuses."""
    if status in ("translated", "translated_needs_review"):
        return "translated"
    return status


__all__ = [
    "HARD_REVIEW_ISSUE_TYPES",
    "progress_status",
    "status_for_output",
]
