from __future__ import annotations

from typing import Any


def apply_term_aliases(original: str, translated: str, confirmed_terms: list[dict[str, Any]]) -> str:
    """Repair only whole-cell aliases; sentences need source-aware retranslation."""
    updated = translated
    for item in confirmed_terms:
        src = item.get("source", "")
        tgt = item.get("target", "")
        if not src or not tgt or original.strip() != src:
            continue
        if translated.strip() in {src, *(str(alias) for alias in item.get("aliases", []) if alias)}:
            updated = tgt
    return updated


__all__ = ["apply_term_aliases"]
