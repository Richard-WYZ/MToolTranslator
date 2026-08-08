from __future__ import annotations

from typing import Any

from translation.protection.runtime import (
    reconcile_line_break_placeholders,
    restore_runtime_tokens,
    strip_foreign_runtime_placeholders,
    validate_runtime_tokens,
)
from translation.protection.symbols import restore_symbols
from translation.quality.rules import apply_fixed_translations


def restore_protected_translation(
    *,
    glossary: Any,
    original_text: str,
    prepared_text: str,
    protected_text: str,
    translated: str,
    symbol_tokens: list,
    term_tokens: list[tuple[str, str, str]],
    runtime_tokens: list,
    term_hits: list[dict[str, str]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    """Restore protected placeholders and report missing terminology."""
    restored, symbol_issues = restore_symbols(prepared_text, protected_text, translated, symbol_tokens)
    restored = reconcile_line_break_placeholders(restored, runtime_tokens, protected_text)
    symbol_issues.extend(validate_runtime_tokens(restored, runtime_tokens, protected_text))
    restored = restore_runtime_tokens(restored, runtime_tokens)
    restored = strip_foreign_runtime_placeholders(restored, original_text)
    # Glossary placeholders are protected again as runtime tokens. Restore
    # __KEEP_* to __PERSON_*/__TERM_* before resolving the glossary target.
    restored = glossary.restore_terms(restored, term_tokens)
    restored = apply_fixed_translations(restored)
    restored = glossary.apply_post_translation(original_text, restored)
    if term_tokens:
        missing_terms = glossary.missing_restored_terms(original_text, restored, term_tokens)
    else:
        missing_terms = glossary.missing_hits(original_text, restored, term_hits)
    return restored, symbol_issues, missing_terms


__all__ = ["restore_protected_translation"]
