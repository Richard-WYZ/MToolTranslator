from __future__ import annotations

from typing import Any


def new_issues(existing: list[dict[str, Any]], new_items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return issues whose type/message pair is not already present."""
    seen = {(str(item.get("type", "")), str(item.get("message", ""))) for item in existing}
    return [
        item
        for item in new_items
        if (item.get("type", ""), item.get("message", "")) not in seen
    ]


__all__ = ["new_issues"]
