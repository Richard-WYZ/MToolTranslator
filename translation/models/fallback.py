from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


TranslateFunc = Callable[..., str]
RetryFallbackFunc = Callable[..., dict[str, Any]]
ChunkTranslateFunc = Callable[..., str]
PromptBuilder = Callable[[str], str]
RefusalChecker = Callable[..., bool]


def fallback_translate(
    protected_text: str,
    *,
    model: str,
    system_prompt: str,
    prompt_style: str,
    system_prompts: dict[str, str],
    fallback_models: Iterable[str],
    chunk_strategy: dict[str, Any],
    file_path: str,
    row_idx: int,
    col_idx: int,
    compose_prompt: PromptBuilder,
    translate_func: TranslateFunc,
    retry_with_fallback_func: RetryFallbackFunc,
    chunk_translate_func: ChunkTranslateFunc,
    is_refusal_func: RefusalChecker,
    primary_failed: bool = False,
) -> str:
    """Run provider-neutral fallback translation attempts for one protected text."""
    styles = [prompt_style, "uncensored", "academic", "professional"]
    tried: set[str] = set()
    for style in ([] if primary_failed else styles):
        if style in tried:
            continue
        tried.add(style)
        base = system_prompts.get(style)
        if not base:
            continue
        try:
            result = translate_func(model, protected_text, system_prompt=compose_prompt(base), terminology=None)
        except Exception as exc:
            if getattr(exc, "retryable", True) is False:
                break
            continue
        if result and not is_refusal_func(result, original=protected_text):
            return result

    fallback_system_prompt = compose_prompt(system_prompt)
    for fallback_model in fallback_models:
        if fallback_model == model:
            continue
        try:
            result = translate_func(
                fallback_model,
                protected_text,
                system_prompt=fallback_system_prompt,
                terminology=None,
            )
        except Exception:
            continue
        if result and not is_refusal_func(result, original=protected_text):
            return result

    if primary_failed:
        return ""

    fallback = retry_with_fallback_func(
        protected_text,
        model=model,
        system_prompt=fallback_system_prompt,
        terminology=None,
        file_path=file_path,
        row=row_idx,
        col=col_idx,
    )
    if fallback.get("status") == "SUCCESS":
        return fallback.get("translation", "")

    return chunk_translate_func(
        model,
        protected_text,
        fallback_system_prompt,
        max_chars=chunk_strategy.get("max_chars", 50),
        overlap=chunk_strategy.get("overlap", 10),
    )


__all__ = [
    "ChunkTranslateFunc",
    "PromptBuilder",
    "RefusalChecker",
    "RetryFallbackFunc",
    "TranslateFunc",
    "fallback_translate",
]
