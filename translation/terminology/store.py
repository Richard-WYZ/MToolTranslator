from __future__ import annotations

import json
import os
from typing import Any


GlossaryData = tuple[dict[str, str], dict[str, dict[str, Any]]]


def read_glossary(file_path: str) -> GlossaryData | None:
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r", encoding="utf-8") as stream:
        loaded = json.load(stream)
    if not isinstance(loaded, dict):
        return {}, {}
    if "terms" not in loaded:
        return {str(key): str(value) for key, value in loaded.items()}, {}

    raw_terms = loaded.get("terms", {})
    raw_candidates = loaded.get("candidates", {})
    terms = {str(key): str(value) for key, value in raw_terms.items()} if isinstance(raw_terms, dict) else {}
    candidates = dict(raw_candidates) if isinstance(raw_candidates, dict) else {}
    return terms, candidates


def write_glossary(
    file_path: str,
    terms: dict[str, str],
    candidates: dict[str, dict[str, Any]],
) -> None:
    payload = {
        "version": 2,
        "terms": terms,
        "candidates": candidates,
    }
    os.makedirs(os.path.dirname(os.path.abspath(file_path)) or ".", exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


__all__ = ["GlossaryData", "read_glossary", "write_glossary"]
