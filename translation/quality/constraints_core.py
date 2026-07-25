from __future__ import annotations

from typing import Any


def validate(text: str, max_chars: int = 30, max_lines: int = 4) -> bool:
    """Return whether text satisfies max chars per line and max line count."""
    if not text:
        return True
    lines = text.split("\n")
    if any(len(line) > max_chars for line in lines):
        return False
    return len(lines) <= max_lines


def get_violations(
    text: str,
    max_chars: int = 30,
    max_lines: int = 4,
) -> list[dict[str, Any]]:
    """Return detailed line-length and line-count violations."""
    violations: list[dict[str, Any]] = []
    if not text:
        return violations

    lines = text.split("\n")
    for i, line in enumerate(lines, start=1):
        line_len = len(line)
        if line_len > max_chars:
            violations.append({
                "line": i,
                "length": line_len,
                "limit": max_chars,
                "type": "line_too_long",
                "text": line[:50] + ("..." if len(line) > 50 else ""),
            })

    if len(lines) > max_lines:
        violations.append({
            "line": 0,
            "total_lines": len(lines),
            "limit": max_lines,
            "type": "too_many_lines",
        })
    return violations


def auto_wrap(text: str, max_chars: int = 30, max_lines: int = 4) -> str:
    """Wrap text to satisfy the configured game UI text constraints."""
    if not text:
        return text
    if validate(text, max_chars, max_lines):
        return text

    processed_lines: list[str] = []
    for line in text.split("\n"):
        if len(line) <= max_chars:
            processed_lines.append(line)
        else:
            processed_lines.extend(_break_long_line(line, max_chars))

    if len(processed_lines) > max_lines:
        processed_lines = _truncate_lines(processed_lines, max_chars, max_lines)
    return "\n".join(processed_lines)


_WRAP_PRIORITY = ["\u3002", "\uff0c", " ", "\t", "\uff1b", "\uff01", "\uff1f", "\u3001", "\uff09", "\u3011", "\u300d", "\u300f"]


def _find_break_position(line: str, max_chars: int) -> int:
    segment = line[:max_chars] if len(line) > max_chars else line
    for ch in _WRAP_PRIORITY:
        pos = segment.rfind(ch)
        if pos != -1:
            return pos + 1
    return -1


def _break_long_line(line: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    remaining = line.rstrip()

    while len(remaining) > max_chars:
        break_pos = _find_break_position(remaining, max_chars)
        if break_pos == -1 or break_pos == 0:
            break_pos = max_chars
        parts.append(remaining[:break_pos].strip())
        remaining = remaining[break_pos:].strip()
        if not remaining:
            break

    if remaining:
        parts.append(remaining)
    return parts


def _truncate_lines(lines: list[str], max_chars: int, max_lines: int) -> list[str]:
    result = list(lines)

    if len(result) >= 2:
        merged = (result[-2] + result[-1]).strip()
        if len(merged) <= max_chars:
            result = result[:-2] + [merged]

    if len(result) > max_lines:
        merged = "".join(result[max_lines - 1:]).strip()
        result = result[:max_lines - 1]
        if len(merged) <= max_chars - 2:
            result.append(merged + "\u2026\u2026")
        else:
            result.append(merged[:max_chars - 2] + "\u2026\u2026")

    if len(result) > max_lines:
        result = result[:max_lines]
        last = result[-1]
        if len(last) > max_chars - 2:
            last = last[:max_chars - 2]
        result[-1] = last + "\u2026\u2026"
    return result


__all__ = ["auto_wrap", "get_violations", "validate"]
