from __future__ import annotations

import re
from dataclasses import dataclass


SYMBOL_PROTECTION_VERSION = "protected-symbols-exactly-once-v3-strip-foreign-placeholders"

SYMBOL_RE = re.compile(
    r"[\u2661\u2665\u2764\U0001f495\U0001f496\U0001f497\U0001f498"
    r"\u266a\u266b\u266c\u2605\u2606\u203b\u2640\u2642"
    r"\u300c\u300d\u300e\u300f\u3010\u3011\u3014\u3015]"
)
SYMBOL_PLACEHOLDER_RE = re.compile(r"__SYM_\d+__")


@dataclass(frozen=True)
class SymbolToken:
    token: str
    symbol: str
    index: int


def protect_symbols(text: str) -> tuple[str, list[SymbolToken]]:
    """Replace protected non-text symbols with stable placeholders."""
    if not text:
        return text, []

    tokens: list[SymbolToken] = []

    def repl(match: re.Match[str]) -> str:
        token = f"__SYM_{len(tokens)}__"
        tokens.append(SymbolToken(token=token, symbol=match.group(0), index=match.start()))
        return token

    return SYMBOL_RE.sub(repl, text), tokens


def restore_symbols(
    original_text: str,
    protected_text: str,
    translated_text: str,
    tokens: list[SymbolToken],
) -> tuple[str, list[dict]]:
    """Restore authoritative source symbols, rebuilding them when necessary."""
    if not tokens:
        # Parent/neighbor context can contain protected symbols that do not
        # belong to this source item. A model may copy those internal
        # placeholders into an otherwise valid translation. They are never
        # user text and must not leak into the final artifact.
        return SYMBOL_PLACEHOLDER_RE.sub("", translated_text), []

    warnings: list[dict] = []
    # Every source-side protected symbol was replaced by a placeholder before
    # the model call. Any literal protected symbol returned by the model is
    # therefore copied from context or hallucinated and must not survive in
    # addition to the authoritative source symbols restored below.
    sanitized_text = SYMBOL_RE.sub("", translated_text)
    positions = [sanitized_text.find(t.token) for t in tokens]
    all_present_once = all(
        pos >= 0 and sanitized_text.count(token.token) == 1
        for token, pos in zip(tokens, positions)
    )
    in_order = positions == sorted(positions)

    if all_present_once and in_order:
        restored = sanitized_text
        for token in tokens:
            restored = restored.replace(token.token, token.symbol, 1)
        return SYMBOL_PLACEHOLDER_RE.sub("", restored), warnings

    cleaned = sanitized_text
    cleaned = SYMBOL_PLACEHOLDER_RE.sub("", cleaned)

    source_parts = re.split(r"__SYM_\d+__", protected_text)
    if len(source_parts) <= 1:
        return cleaned + "".join(t.symbol for t in tokens), warnings

    rebuilt = cleaned
    leading = ""
    trailing = ""
    for i, token in enumerate(tokens):
        if i == 0 and not source_parts[0]:
            leading += token.symbol
        elif i == len(tokens) - 1 and not source_parts[-1]:
            trailing += token.symbol
        else:
            trailing += token.symbol
    return leading + rebuilt + trailing, warnings


__all__ = [
    "SYMBOL_PROTECTION_VERSION",
    "SymbolToken",
    "protect_symbols",
    "restore_symbols",
]
