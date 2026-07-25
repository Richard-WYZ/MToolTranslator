from __future__ import annotations

from translation.quality.constraints_core import auto_wrap, validate


def apply_output_constraints(text: str, *, max_chars: int, max_lines: int) -> str:
    """Return text wrapped to configured output constraints when needed."""
    if validate(text, max_chars=max_chars, max_lines=max_lines):
        return text
    return auto_wrap(text, max_chars=max_chars, max_lines=max_lines)


__all__ = ["apply_output_constraints"]
