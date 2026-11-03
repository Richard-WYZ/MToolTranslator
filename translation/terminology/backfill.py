from __future__ import annotations

from typing import Any, Callable

import translation.checkpoint as checkpoint
from translation.pollution import translation_pollution_issues
from translation.quality import new_issues, status_for_output
from translation.terminology.aliases import apply_term_aliases
from translation.terminology.glossary import Glossary


OutputCellUpdater = Callable[[int, int, str], bool]


def backfill_confirmed_terms_to_outputs(
    file_path: str,
    confirmed_terms: list[dict[str, Any]],
    *,
    update_output_cell: OutputCellUpdater | None = None,
    glossary_mappings: list[dict[str, str]] | None = None,
    glossary_version: str | None = None,
) -> int:
    """Repair whole-cell aliases and queue source-matched sentences for translation."""
    if not confirmed_terms:
        return 0

    cp = checkpoint.load_checkpoint(file_path)
    changed = 0
    mappings = glossary_mappings or []
    for entry in cp.get("entries", {}).values():
        original = entry.get("original", "")
        translated = entry.get("translated", "")
        if entry.get("status") == "preserved" or entry.get("entry_classification") == "composed_multiline":
            continue
        affected = [
            term for term in confirmed_terms
            if Glossary.contains_term(str(original), str(term.get("source", "")))
        ]
        if not affected:
            continue
        if (
            glossary_version
            and entry.get("glossary_version") == glossary_version
            and not entry.get("glossary_pending_sources")
        ):
            continue
        updated = apply_term_aliases(original, translated, affected)
        issues = list(entry.get("issues", []) or [])
        if updated != translated:
            issues = [issue for issue in issues if issue.get("type") != "term_preservation"]
            pollution_issues = translation_pollution_issues(original, updated, glossary_mappings=mappings)
            issues.extend(new_issues(issues, pollution_issues))
            entry.pop("glossary_pending_sources", None)
        else:
            issues.extend(new_issues(issues, [{
                "type": "glossary_update_pending",
                "message": "Confirmed terminology changed; retranslate this source before treating it as current.",
            }]))
            entry["glossary_pending_sources"] = [str(term["source"]) for term in affected]
        entry["translated"] = updated
        entry["output_translation"] = updated
        entry["issues"] = issues
        entry["validation_issues"] = issues
        entry["status"] = status_for_output(original, updated, issues)
        row = int(entry.get("row", 0))
        col = int(entry.get("col", 0))
        if updated != translated and update_output_cell is not None:
            update_output_cell(row, col, updated)
        changed += 1

    if changed:
        checkpoint.save_checkpoint(file_path, cp)
    return changed


__all__ = ["OutputCellUpdater", "backfill_confirmed_terms_to_outputs"]
