from __future__ import annotations


DIALOGUE_OPENERS = ("「", "『", "（")
DIALOGUE_CLOSERS = ("」", "』")
_CONTEXT_BOUNDARY_PREFIXES = (
    "【",
    "[",
    "<",
    "＜",
    "◆",
    "■",
    "●",
    "//",
    "/*",
    "{",
)


def looks_like_dialogue_boundary(text: str) -> bool:
    """Return whether text has a clear Japanese speech or thought boundary."""
    stripped = str(text or "").strip()
    return bool(
        stripped
        and (
            stripped.startswith(DIALOGUE_OPENERS)
            or stripped.endswith(DIALOGUE_CLOSERS)
        )
    )


def looks_like_context_boundary(text: str) -> bool:
    """Reject obvious UI, markup, and code records as scene-neighbor context."""
    stripped = str(text or "").lstrip()
    return not stripped or stripped.startswith(_CONTEXT_BOUNDARY_PREFIXES)


__all__ = [
    "DIALOGUE_CLOSERS",
    "DIALOGUE_OPENERS",
    "looks_like_context_boundary",
    "looks_like_dialogue_boundary",
]
