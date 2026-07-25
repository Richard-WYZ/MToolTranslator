from __future__ import annotations

from typing import Any


def apply_term_aliases(original: str, translated: str, confirmed_terms: list[dict[str, Any]]) -> str:
    """Backfill confirmed term aliases in an existing translation."""
    updated = translated
    for item in confirmed_terms:
        src = item.get("source", "")
        tgt = item.get("target", "")
        if not src or not tgt or src not in original:
            continue
        aliases = sorted(
            {
                alias
                for alias in item.get("aliases", [])
                if alias and alias != tgt and len(alias) > 1 and alias not in tgt and tgt not in alias
            },
            key=len,
            reverse=True,
        )
        for alias in aliases:
            updated = updated.replace(alias, tgt)
        updated = updated.replace(src, tgt)
    return updated


__all__ = ["apply_term_aliases"]
