"""Runtime token, symbol, and placeholder protection."""

from translation.protection.runtime import (
    protect_runtime_tokens,
    restore_runtime_tokens,
    runtime_token_kind,
    strip_foreign_runtime_placeholders,
    validate_runtime_tokens,
)
from translation.protection.symbols import (
    SYMBOL_PROTECTION_VERSION,
    protect_symbols,
    restore_symbols,
)
from translation.protection.version import PROTECTED_RESTORATION_VERSION


def restore_protected_translation(*args, **kwargs):
    from translation.protection.restore import restore_protected_translation as _restore_protected_translation

    return _restore_protected_translation(*args, **kwargs)


__all__ = [
    "protect_runtime_tokens",
    "PROTECTED_RESTORATION_VERSION",
    "SYMBOL_PROTECTION_VERSION",
    "restore_protected_translation",
    "restore_runtime_tokens",
    "runtime_token_kind",
    "strip_foreign_runtime_placeholders",
    "validate_runtime_tokens",
    "protect_symbols",
    "restore_symbols",
]
