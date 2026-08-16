from __future__ import annotations

from collections import Counter
import re
from dataclasses import dataclass


KEY_NAMES = {
    "a", "b", "x", "y", "q", "e", "w", "s", "d", "z", "c", "v",
    "l", "r", "lb", "rb", "lt", "rt", "l1", "r1", "l2", "r2",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "shift", "ctrl", "control", "alt", "esc", "escape", "space", "enter", "tab",
    "backspace", "delete", "insert", "home", "end", "pageup", "pagedown",
    "up", "down", "left", "right", "wasd", "lmb", "rmb", "mmb",
}

AMBIGUOUS_KEY_NAMES = {"start", "select"}

VARIABLE_RE = re.compile(
    r"(%[A-Za-z0-9_]+%|\{[^{}\n]{1,40}\}|\\\\[A-Za-z]+\[[^\]]+\]|https?://\S+)"
)
ANGLE_FRAGMENT_RE = re.compile(r"<[^<>\n]{1,80}>")
RESOURCE_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:"
    r"(?:[A-Za-z]:[\\/]|\.{1,2}[\\/])?"
    r"(?:[A-Za-z0-9_$@+.-][A-Za-z0-9_$@+.\-\u3040-\u30ff\u3400-\u9fff]*[\\/])+"
    r"[A-Za-z0-9_$@+\-\u3040-\u30ff\u3400-\u9fff]+"
    r"|[A-Za-z0-9_$@+.-]+"
    r")"
    r"\.(?:png|jpg|jpeg|webp|gif|ogg|mp3|wav|dat|json|csv|js|css|txt)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
RESOURCE_EXTENSION_RE = re.compile(
    r"(?<![A-Za-z0-9_])\.(?:png|jpg|jpeg|webp|gif|ogg|mp3|wav|dat|json|csv|js|css|txt)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
CODE_EXPRESSION_RE = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*\(\)?)+")
CODE_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:[A-Z]{2,}[A-Za-z0-9_]*|[A-Za-z]+[A-Z][A-Za-z0-9_]*|"
    r"[A-Za-z][A-Za-z_]*[0-9][A-Za-z0-9_]*|[A-Za-z]+_[A-Za-z0-9_]+)"
    r"(?![A-Za-z0-9_])"
)
BRACKETED_KEY_RE = re.compile(r"(?P<bracket>[\[\(<])(?P<key>[A-Za-z0-9+ _-]{1,20})(?P<close>[\]\)>])")
LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
NUMERIC_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"[+\-＋－]?"
    r"(?:[0-9]+(?:[.,][0-9]+)*|[０-９]+(?:[．，][０-９]+)*)"
    r"(?:[%％])?"
    r"(?![A-Za-z0-9_])"
)
RUNTIME_PLACEHOLDER_RE = re.compile(r"__KEEP_\d+__")


@dataclass(frozen=True)
class ProtectedToken:
    token: str
    value: str


def runtime_token_kind(value: str) -> str:
    if value in ("\n", "\r", "\r\n"):
        return "line_break"
    if any(character.isdigit() for character in value):
        return "numeric"
    if value.startswith(("http://", "https://")):
        return "url"
    return "runtime"


def normalize_fixed_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def is_runtime_angle_fragment(value: str) -> bool:
    """Return whether an angle fragment is syntax rather than translatable UI text."""
    if not ANGLE_FRAGMENT_RE.fullmatch(value or ""):
        return False
    inner = value[1:-1].strip()
    if not inner:
        return True
    if not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", inner):
        return True
    return bool(
        inner.startswith(("/", "!", "?"))
        or re.search(r"(?:^|\s)[A-Za-z_:][A-Za-z0-9_.:-]*\s*=", inner)
        or re.search(r"[=:]", inner)
    )


def protect_runtime_tokens(text: str) -> tuple[str, list[ProtectedToken]]:
    if not text:
        return text, []
    tokens: list[ProtectedToken] = []
    source_markers = set(RUNTIME_PLACEHOLDER_RE.findall(text))
    occupied_markers = set(source_markers)

    def add_token(value: str) -> str:
        index = len(tokens)
        token = f"__KEEP_{index}__"
        while token in occupied_markers:
            index += 1
            token = f"__KEEP_{index}__"
        occupied_markers.add(token)
        tokens.append(ProtectedToken(token, value))
        return token

    protected = VARIABLE_RE.sub(lambda m: add_token(m.group(0)), text)
    protected = ANGLE_FRAGMENT_RE.sub(
        lambda match: add_token(match.group(0))
        if is_runtime_angle_fragment(match.group(0))
        else match.group(0),
        protected,
    )
    protected = RESOURCE_REFERENCE_RE.sub(lambda m: add_token(m.group(0)), protected)
    protected = RESOURCE_EXTENSION_RE.sub(lambda m: add_token(m.group(0)), protected)
    protected = CODE_EXPRESSION_RE.sub(lambda m: add_token(m.group(0)), protected)
    protected = CODE_IDENTIFIER_RE.sub(lambda m: add_token(m.group(0)), protected)
    protected = LINE_BREAK_RE.sub(lambda m: add_token(m.group(0)), protected)
    protected = NUMERIC_TOKEN_RE.sub(lambda m: add_token(m.group(0)), protected)

    def bracket_repl(match: re.Match[str]) -> str:
        key = normalize_fixed_key(match.group("key").replace("_", " "))
        if key in KEY_NAMES or key in AMBIGUOUS_KEY_NAMES:
            return add_token(match.group(0))
        return match.group(0)

    protected = BRACKETED_KEY_RE.sub(bracket_repl, protected)
    return _renumber_tokens_in_source_order(
        protected,
        tokens,
        reserved_markers=source_markers,
    )


def _renumber_tokens_in_source_order(
    protected: str,
    tokens: list[ProtectedToken],
    *,
    reserved_markers: set[str] | None = None,
) -> tuple[str, list[ProtectedToken]]:
    """Make marker numbers monotonic in text order so models do not reorder mixed token types."""
    if len(tokens) < 2:
        return protected, tokens
    ordered = sorted(tokens, key=lambda item: protected.find(item.token))
    sentinels: list[str] = []
    for index, token in enumerate(ordered):
        sentinel = f"\ufff0{index}\ufff1"
        sentinels.append(sentinel)
        protected = protected.replace(token.token, sentinel)
    renumbered: list[ProtectedToken] = []
    reserved = set(reserved_markers or ())
    next_index = 0
    for token, sentinel in zip(ordered, sentinels):
        marker = f"__KEEP_{next_index}__"
        while marker in reserved:
            next_index += 1
            marker = f"__KEEP_{next_index}__"
        reserved.add(marker)
        next_index += 1
        protected = protected.replace(sentinel, marker)
        renumbered.append(ProtectedToken(marker, token.value))
    return protected, renumbered


def restore_runtime_tokens(text: str, tokens: list[ProtectedToken]) -> str:
    restored = text
    for token in tokens:
        restored = restored.replace(token.token, token.value)
    return restored


def reconcile_line_break_placeholders(
    text: str,
    tokens: list[ProtectedToken],
    expected_text: str | None = None,
) -> str:
    """Recover line-break placeholders when a model rendered real newlines instead.

    Some providers normalize ``__KEEP_*`` line-break markers into literal ``\n``
    characters even when the number and placement of logical breaks are intact.
    When every missing source line-break run has a one-to-one literal counterpart,
    replace it with the authoritative source markers before normal runtime-token
    validation and restoration. This also recovers providers that collapse a
    mixed ``\n\r\n`` run to one logical newline. Ambiguous partial/mismatched
    cases remain untouched and continue to fail validation.
    """
    line_tokens = [token for token in tokens if runtime_token_kind(token.value) == "line_break"]
    if not line_tokens or any(text.count(token.token) for token in line_tokens):
        return text
    ordered = (
        sorted(line_tokens, key=lambda item: expected_text.find(item.token))
        if expected_text is not None
        else line_tokens
    )
    groups: list[list[ProtectedToken]] = []
    for token in ordered:
        if (
            groups
            and expected_text is not None
            and expected_text.find(token.token)
            == expected_text.find(groups[-1][-1].token) + len(groups[-1][-1].token)
        ):
            groups[-1].append(token)
        else:
            groups.append([token])
    literal_breaks = list(re.finditer(r"(?:\r\n|\r|\n)+", text))
    if len(literal_breaks) != len(groups):
        return text
    parts: list[str] = []
    start = 0
    for match, group in zip(literal_breaks, groups):
        parts.append(text[start:match.start()])
        parts.append("".join(token.token for token in group))
        start = match.end()
    parts.append(text[start:])
    return "".join(parts)


def strip_foreign_runtime_placeholders(text: str, source_text: str) -> str:
    """Remove model-copied runtime markers while preserving literal source text."""
    allowed = Counter(RUNTIME_PLACEHOLDER_RE.findall(source_text))
    retained: Counter[str] = Counter()

    def replace(match: re.Match[str]) -> str:
        marker = match.group(0)
        if retained[marker] < allowed[marker]:
            retained[marker] += 1
            return marker
        return ""

    return RUNTIME_PLACEHOLDER_RE.sub(replace, text)


def validate_runtime_tokens(
    text: str,
    tokens: list[ProtectedToken],
    expected_text: str | None = None,
) -> list[dict[str, str]]:
    """Verify exact multiplicity and preserve order where token order is semantic."""
    if not tokens:
        return []
    counts = [text.count(token.token) for token in tokens]
    order_sensitive = [
        token
        for token in tokens
        if runtime_token_kind(token.value) != "numeric"
    ]
    expected_order = [token.token for token in order_sensitive]
    if expected_text is not None:
        expected_order = [
            token.token
            for token in sorted(order_sensitive, key=lambda item: expected_text.find(item.token))
        ]
    actual_order = [
        token.token
        for token in sorted(order_sensitive, key=lambda item: text.find(item.token))
    ]
    if all(count == 1 for count in counts) and actual_order == expected_order:
        return []
    missing = [token.token for token, count in zip(tokens, counts) if count == 0]
    duplicated = [token.token for token, count in zip(tokens, counts) if count > 1]
    details = []
    if missing:
        details.append("missing=" + ",".join(missing))
    if duplicated:
        details.append("duplicated=" + ",".join(duplicated))
    if not missing and not duplicated and actual_order != expected_order:
        details.append("order_changed")
    return [{
        "type": "runtime_token_preservation",
        "message": "Protected runtime tokens were not restored exactly once or in required order: " + "; ".join(details),
    }]


__all__ = [
    "ProtectedToken",
    "RUNTIME_PLACEHOLDER_RE",
    "is_runtime_angle_fragment",
    "protect_runtime_tokens",
    "reconcile_line_break_placeholders",
    "restore_runtime_tokens",
    "runtime_token_kind",
    "strip_foreign_runtime_placeholders",
    "validate_runtime_tokens",
]
