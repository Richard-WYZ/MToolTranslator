from __future__ import annotations

from typing import Any, Callable


def resolve_scanned_batch_protocol(
    configured_protocol: str,
    *,
    scanned: int,
    short_labels: int,
    total_chars: int,
) -> str:
    """Resolve a file-level batch protocol from model-bound text statistics."""
    if configured_protocol in ("json", "line"):
        return configured_protocol
    if configured_protocol != "auto":
        return "json"
    if not scanned or scanned < 20:
        return "json"

    avg_chars = total_chars / scanned
    short_ratio = short_labels / scanned
    if avg_chars <= 20 and short_ratio >= 0.5:
        return "line"
    return "json"


def resolve_json_batch_protocol_for_items(
    configured_protocol: str,
    *,
    translated_items: list[tuple[Any, Any]],
    mtool: bool,
    completed: dict[tuple[int, int], dict[str, Any]],
    source_text: Callable[[Any, Any, bool], str],
    is_completed_entry: Callable[[Any, str], bool],
    deterministic_translation: Callable[[str], str],
    looks_like_short_label: Callable[[str], bool],
) -> str:
    """Resolve file-level protocol by scanning remaining model-bound JSON items."""
    scanned = 0
    short_labels = 0
    total_chars = 0
    for idx, (key, value) in enumerate(translated_items):
        current_source = source_text(key, value, mtool)
        if is_completed_entry(completed.get((idx, 0)), current_source):
            continue
        if not current_source.strip() or deterministic_translation(current_source):
            continue
        scanned += 1
        total_chars += len(current_source)
        if looks_like_short_label(current_source):
            short_labels += 1

    return resolve_scanned_batch_protocol(
        configured_protocol,
        scanned=scanned,
        short_labels=short_labels,
        total_chars=total_chars,
    )


def resolve_candidate_batch_protocol(
    configured_protocol: str,
    default_protocol: str,
    candidates: list[dict[str, Any]],
) -> str:
    """Resolve the batch response protocol for a candidate group."""
    if any(candidate.get("contexts") for candidate in candidates):
        return "json"
    if configured_protocol in ("json", "line"):
        return configured_protocol
    if configured_protocol != "auto" or not candidates:
        return default_protocol if default_protocol in ("json", "line") else "json"
    if len(candidates) >= 20 and all(bool(candidate.get("short_label")) for candidate in candidates):
        return "line"
    return default_protocol if default_protocol in ("json", "line") else "json"


def api_job_is_short_text(candidates: list[dict[str, Any]], batch_cfg: dict[str, Any]) -> bool:
    """Return whether an API batch job is safe to route as short text."""
    if not candidates:
        return False
    max_chars = max(1, int(batch_cfg.get("short_line_max_chars", 80)))
    return all(
        bool(candidate.get("short_label")) and len(str(candidate.get("source", ""))) <= max_chars
        for candidate in candidates
    )


__all__ = [
    "api_job_is_short_text",
    "resolve_candidate_batch_protocol",
    "resolve_json_batch_protocol_for_items",
    "resolve_scanned_batch_protocol",
]
