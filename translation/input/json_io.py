from __future__ import annotations

import json
from typing import Any


def load_json_items(file_path: str) -> list[tuple[Any, Any]]:
    """Load JSON as ordered translation item pairs."""
    with open(file_path, "r", encoding="utf-8") as stream:
        data = json.load(stream)
    if isinstance(data, dict):
        return list(data.items())
    if isinstance(data, list):
        return list(enumerate(data))
    return [(0, data)]


__all__ = ["load_json_items"]
