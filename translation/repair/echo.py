from __future__ import annotations


def strip_source_echo(source_text: str, translated: str) -> str:
    """Remove leading source-text echoes from model output."""
    if not source_text or not translated:
        return translated
    stripped = translated.strip()
    source = source_text.strip()
    for separator in ("->", "=>", "\uff1a", ":"):
        prefix = source + " " + separator
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
        prefix = source + separator
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return translated


__all__ = ["strip_source_echo"]
