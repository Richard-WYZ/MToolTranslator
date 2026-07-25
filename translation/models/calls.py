from __future__ import annotations

from typing import Any, Callable


TranslateFunc = Callable[..., str]


def call_translate_with_options(
    *,
    model: str,
    text: str,
    system_prompt: str,
    options: dict[str, Any] | None,
    translate_func: TranslateFunc,
) -> str:
    """Call a translate function while tolerating transports without options."""
    if options:
        try:
            return translate_func(model, text, system_prompt=system_prompt, terminology=None, options=options)
        except TypeError:
            return translate_func(model, text, system_prompt=system_prompt, terminology=None)
    return translate_func(model, text, system_prompt=system_prompt, terminology=None)


def retry_short_label_translation(
    *,
    model: str,
    protected_text: str,
    retry_prompt: str,
    options: dict[str, Any],
    translate_func: TranslateFunc,
    is_refusal_func: Callable[..., bool],
) -> str:
    """Retry a short-label translation with strict prompt/options."""
    try:
        result = call_translate_with_options(
            model=model,
            text=protected_text,
            system_prompt=retry_prompt,
            options=options,
            translate_func=translate_func,
        )
        if result and not is_refusal_func(result, original=protected_text):
            return result
    except Exception:
        return ""
    return ""


__all__ = ["TranslateFunc", "call_translate_with_options", "retry_short_label_translation"]
