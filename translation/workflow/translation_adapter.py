from __future__ import annotations

from typing import Any, Callable

from translation.config import fallback_chunk_strategy, fallback_models, system_prompts
from translation.prompts import compose_label_prompt, compose_translation_prompt
from translation.quality import quality_prompt_rules


def restore_protected_for_pipeline(
    pipeline: Any,
    original_text: str,
    prepared_text: str,
    protected_text: str,
    translated: str,
    symbol_tokens: list,
    term_tokens: list[tuple[str, str, str]],
    runtime_tokens: list,
    term_hits: list[dict[str, str]],
    *,
    restore_protected_translation_func: Callable[..., tuple[str, list[dict[str, Any]], list[dict[str, str]]]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    return restore_protected_translation_func(
        glossary=pipeline.glossary,
        original_text=original_text,
        prepared_text=prepared_text,
        protected_text=protected_text,
        translated=translated,
        symbol_tokens=symbol_tokens,
        term_tokens=term_tokens,
        runtime_tokens=runtime_tokens,
        term_hits=term_hits,
    )


def glossary_mappings_for_quality(pipeline: Any) -> list[dict[str, str]]:
    mappings: list[dict[str, str]] = []
    for src, tgt, owner, typ in pipeline.glossary.iter_mappings():
        mappings.append({
            "source": owner or src,
            "target": tgt,
            "type": typ,
        })
    return mappings


def pollution_issues_for_pipeline(
    pipeline: Any,
    source: str,
    translated: str,
    *,
    translation_pollution_issues_func: Callable[..., list[dict[str, str]]],
) -> list[dict[str, str]]:
    return translation_pollution_issues_func(
        source,
        translated,
        glossary_mappings=glossary_mappings_for_quality(pipeline),
    )


def fallback_translate_for_pipeline(
    pipeline: Any,
    protected_text: str,
    file_path: str,
    row_idx: int,
    col_idx: int,
    term_hits: list[dict[str, str]],
    *,
    fallback_translate_func: Callable[..., str],
    translate_func: Callable[..., str],
    retry_with_fallback_func: Callable[..., dict[str, Any]],
    chunk_translate_func: Callable[..., str],
    is_refusal_func: Callable[..., bool],
    primary_failed: bool = False,
) -> str:
    return fallback_translate_func(
        model=pipeline.model,
        protected_text=protected_text,
        system_prompt=pipeline.system_prompt,
        prompt_style=pipeline.prompt_style,
        system_prompts=system_prompts(),
        fallback_models=fallback_models(),
        chunk_strategy=fallback_chunk_strategy(),
        file_path=file_path,
        row_idx=row_idx,
        col_idx=col_idx,
        compose_prompt=lambda base: pipeline._compose_system_prompt(base, term_hits=term_hits),
        translate_func=translate_func,
        retry_with_fallback_func=retry_with_fallback_func,
        chunk_translate_func=chunk_translate_func,
        is_refusal_func=is_refusal_func,
        primary_failed=primary_failed,
    )


def compose_label_prompt_for_pipeline(
    pipeline: Any,
    term_hits: list[dict[str, str]] | None = None,
    strict: bool = False,
) -> str:
    prompts = system_prompts()
    base = prompts.get("uncensored") or pipeline.system_prompt
    return compose_label_prompt(
        base,
        pipeline.glossary.prompt_for_hits(term_hits or []),
        strict=strict,
        quality_rules=quality_prompt_rules(),
    )


def retry_short_label_for_pipeline(
    pipeline: Any,
    protected_text: str,
    term_hits: list[dict[str, str]],
    *,
    retry_short_label_translation_func: Callable[..., str],
    translate_func: Callable[..., str],
    is_refusal_func: Callable[..., bool],
) -> str:
    return retry_short_label_translation_func(
        model=pipeline.model,
        protected_text=protected_text,
        retry_prompt=pipeline._compose_label_prompt(term_hits=term_hits, strict=True),
        options=pipeline._short_label_options,
        translate_func=translate_func,
        is_refusal_func=is_refusal_func,
    )


def call_translate_for_pipeline(
    pipeline: Any,
    text: str,
    system_prompt: str,
    options: dict[str, Any] | None = None,
    *,
    call_translate_with_options_func: Callable[..., str],
    translate_func: Callable[..., str],
) -> str:
    return call_translate_with_options_func(
        model=pipeline.model,
        text=text,
        system_prompt=system_prompt,
        options=options,
        translate_func=translate_func,
    )


def compose_system_prompt_for_pipeline(
    pipeline: Any,
    base_prompt: str,
    term_hits: list[dict[str, str]] | None = None,
    strict: bool = False,
) -> str:
    return compose_translation_prompt(
        base_prompt,
        pipeline.glossary.prompt_for_hits(term_hits or []),
        strict=strict,
        quality_rules=quality_prompt_rules(),
    )


__all__ = [
    "call_translate_for_pipeline",
    "compose_label_prompt_for_pipeline",
    "compose_system_prompt_for_pipeline",
    "fallback_translate_for_pipeline",
    "glossary_mappings_for_quality",
    "pollution_issues_for_pipeline",
    "restore_protected_for_pipeline",
    "retry_short_label_for_pipeline",
]
