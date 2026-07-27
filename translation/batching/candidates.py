from __future__ import annotations

from typing import Any

from translation.classification import looks_like_short_label, normalize_model_source
from translation.protection import protect_runtime_tokens, protect_symbols, runtime_token_kind


def prepare_model_candidate(
    *,
    batch_i: int,
    idx: int,
    source: str,
    glossary: Any | None = None,
    short_label: bool | None = None,
) -> dict[str, Any]:
    """Build a protected model-bound translation candidate."""
    term_prepared, term_tokens = glossary.protect_terms(source) if glossary is not None else (source, [])
    term_prepared = normalize_model_source(term_prepared)
    prepared, runtime_tokens = protect_runtime_tokens(term_prepared)
    protected, symbol_tokens = protect_symbols(prepared)
    term_hits = glossary.find_hits(source) if glossary is not None else []
    is_short_label = looks_like_short_label(source) if short_label is None else short_label
    return {
        "i": batch_i,
        "idx": idx,
        "source": source,
        "text": protected,
        "prepared": prepared,
        "protected": protected,
        "terms": term_hits,
        "term_hits": term_hits,
        "term_tokens": term_tokens,
        "short_label": is_short_label,
        "entry_classification": "short_label" if is_short_label else "model_text",
        "runtime_tokens": runtime_tokens,
        "symbol_tokens": symbol_tokens,
    }


def reindex_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return shallow candidate copies with dense model-facing batch indexes."""
    return [dict(candidate, i=index) for index, candidate in enumerate(candidates)]


def candidate_template_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    """Return a safe reuse key for candidates that differ only in protected values."""
    term_hits = tuple(sorted(
        (
            str(hit.get("source", "")),
            str(hit.get("target", "")),
            str(hit.get("type", "")),
        )
        for hit in candidate.get("term_hits", candidate.get("terms", [])) or []
        if isinstance(hit, dict)
    ))
    token_kinds = tuple(runtime_token_kind(str(token.value)) for token in candidate.get("runtime_tokens", []) or [])
    contexts = tuple(
        (
            str(context.get("text", "")),
            int(context.get("line", 0) or 0),
            int(context.get("offset", 0) or 0),
            str(context.get("context_kind", "")),
        )
        for context in candidate.get("contexts", []) or []
        if isinstance(context, dict)
    )
    scene_lines = tuple(
        (
            int(line.get("i", -1)),
            str(line.get("text", "")),
            bool(line.get("target", False)),
        )
        for line in candidate.get("scene_lines", []) or []
        if isinstance(line, dict)
    )
    return (
        str(candidate.get("protected", candidate.get("text", ""))),
        bool(candidate.get("short_label", False)),
        str(candidate.get("entry_classification", "")),
        token_kinds,
        term_hits,
        contexts,
        scene_lines,
    )

__all__ = ["candidate_template_key", "prepare_model_candidate", "reindex_candidates"]
