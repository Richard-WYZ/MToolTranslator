from __future__ import annotations

from typing import Any, Callable


TranslateFunc = Callable[..., str]
RestoreFunc = Callable[[str], tuple[str, list[dict[str, Any]], list[dict[str, str]]]]
RefusalChecker = Callable[..., bool]
EnglishResidueFunc = Callable[..., list[str]]


def retry_english_residue_translation(
    *,
    original_text: str,
    protected_text: str,
    current_restored: str,
    current_missing_terms: list[dict[str, str]],
    residue: list[str],
    retry_prompt: str,
    model: str,
    translate_func: TranslateFunc,
    restore_func: RestoreFunc,
    is_refusal_func: RefusalChecker,
    english_residue_func: EnglishResidueFunc,
) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    """Retry output with English residue and accept only a measurable improvement."""
    issues: list[dict[str, Any]] = []
    try:
        retried = translate_func(model, protected_text, system_prompt=retry_prompt, terminology=None)
        if retried and not is_refusal_func(retried, original=protected_text):
            retry_restored, retry_symbol_issues, retry_missing = restore_func(retried)
            if len(english_residue_func(retry_restored, original=original_text)) < len(residue):
                return retry_restored, retry_missing, retry_symbol_issues
    except Exception as exc:
        issues.append({"type": "english_retry_error", "message": str(exc)})
    return current_restored, current_missing_terms, issues


def retry_missing_terms_translation(
    *,
    protected_text: str,
    retry_prompt: str,
    model: str,
    current_restored: str,
    current_missing_terms: list[dict[str, str]],
    translate_func: TranslateFunc,
    restore_func: RestoreFunc,
    is_refusal_func: RefusalChecker,
) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
    """Retry output with explicit term protection and accept non-regression."""
    issues: list[dict[str, Any]] = []
    try:
        retried = translate_func(model, protected_text, system_prompt=retry_prompt, terminology=None)
        if retried and not is_refusal_func(retried, original=protected_text):
            retry_restored, retry_symbol_issues, retry_missing = restore_func(retried)
            if len(retry_missing) <= len(current_missing_terms):
                return retry_restored, retry_missing, retry_symbol_issues
    except Exception as exc:
        issues.append({"type": "term_retry_error", "message": str(exc)})
    return current_restored, current_missing_terms, issues


__all__ = [
    "EnglishResidueFunc",
    "RefusalChecker",
    "RestoreFunc",
    "TranslateFunc",
    "retry_english_residue_translation",
    "retry_missing_terms_translation",
]
