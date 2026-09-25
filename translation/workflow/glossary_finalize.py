from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


def finalize_glossary_evidence(
    pipeline: TranslationPipeline,
    file_path: str,
    deferred_confirmed_terms: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Confirm eligible candidates and apply mappings after translation workers stop."""
    glossary = pipeline.glossary
    if glossary.frozen:
        glossary.thaw()

    merged: dict[tuple[str, str], dict[str, Any]] = {
        key: dict(term) for key, term in deferred_confirmed_terms.items()
    }
    for term in glossary.confirm_ready_candidates():
        key = (str(term.get("source", "")), str(term.get("target", "")))
        if key in merged:
            aliases = list(merged[key].get("aliases", []) or [])
            aliases.extend(term.get("aliases", []) or [])
            merged[key]["aliases"] = list(dict.fromkeys(str(alias) for alias in aliases if alias))
        else:
            merged[key] = term

    confirmed = list(merged.values())
    if confirmed:
        pipeline._apply_confirmed_terms_to_outputs(file_path, confirmed)
    glossary.save()
    return confirmed


__all__ = ["finalize_glossary_evidence"]
