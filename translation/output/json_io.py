from __future__ import annotations

import json
from typing import Any


def serialize_json_items(data: list[tuple[Any, Any]]) -> str:
    """Serialize ordered translation item pairs to JSON text."""
    if not data:
        payload: Any = {}
    elif isinstance(data[0][0], int):
        payload = [item for _, item in data]
    else:
        payload = dict(data)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_json_items(data: list[tuple[Any, Any]], file_path: str) -> None:
    """Write ordered translation item pairs to a JSON file."""
    with open(file_path, "w", encoding="utf-8") as stream:
        stream.write(serialize_json_items(data))


__all__ = ["serialize_json_items", "write_json_items"]
