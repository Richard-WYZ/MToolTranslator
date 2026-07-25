from __future__ import annotations

from typing import Any, Callable

from translation.config import output_constraints
from translation.quality import (
    apply_fixed_translations,
    apply_output_constraints,
    apply_source_conditioned_fixes,
    has_japanese,
    is_refusal,
    new_issues,
    translation_issues,
)
from translation.repair import strip_source_echo


def finish_batch_translation(
    candidate: dict[str, Any],
    translated: str,
    *,
    glossary: Any,
    restore_func: Callable[..., tuple[str, list[dict[str, Any]], list[dict[str, Any]]]],
    pollution_issues_func: Callable[[str, str], list[dict[str, str]]],
    status_for_output_func: Callable[[str, str, list[dict[str, Any]] | None], str],
) -> tuple[str, str, list[dict[str, Any]]]:
    """Finish a raw batch model output into accepted text, status, and issues."""
    source_text = candidate["source"]
    translated = strip_source_echo(source_text, translated)
    issues: list[dict[str, Any]] = []
    if not translated or is_refusal(translated, original=candidate["protected"]):
        fallback_text = apply_fixed_translations(glossary.apply_post_translation(source_text, source_text))
        if translated and has_japanese(translated):
            issues.append({
                "type": "untranslated_japanese",
                "message": "Batch model returned Japanese text without translating; source text was kept for review.",
            })
            return fallback_text, "review_required", issues
        issues.append({
            "type": "model_refusal",
            "message": "Batch model refused or failed; source text was kept for review.",
        })
        return fallback_text, "review_required", issues

    restored, symbol_issues, missing_terms = restore_func(
        source_text,
        candidate["prepared"],
        candidate["protected"],
        translated,
        candidate["symbol_tokens"],
        candidate.get("term_tokens", []),
        candidate["runtime_tokens"],
        candidate["term_hits"],
    )
    issues.extend(symbol_issues)
    if missing_terms:
        issues.append({
            "type": "term_preservation",
            "message": "Terminology was not preserved; please review: "
            + ", ".join(f"{item['source']}=>{item['target']}" for item in missing_terms),
        })
    restored = glossary.apply_post_translation(source_text, restored)
    restored = apply_fixed_translations(restored)
    restored = apply_source_conditioned_fixes(source_text, restored)
    issues.extend(new_issues(issues, translation_issues(source_text, restored, short_label=candidate["short_label"])))
    issues.extend(new_issues(issues, pollution_issues_func(source_text, restored)))

    max_chars, max_lines = output_constraints()
    if not candidate.get("preserve_source_layout", False):
        restored = apply_output_constraints(restored, max_chars=max_chars, max_lines=max_lines)
    return restored, status_for_output_func(source_text, restored, issues), issues


__all__ = ["finish_batch_translation"]
