"""Translation domain package.

New code should depend on this package instead of directly coupling UI or app
layers to low-level translator modules.
"""

from importlib import import_module

__all__ = [
    "TranslationRequest",
    "TranslationResult",
    "TranslationRuntime",
    "TranslationWorkflowContext",
    "translate",
]


def __getattr__(name: str):
    if name == "TranslationRuntime":
        from translation.runtime import TranslationRuntime

        return TranslationRuntime
    if name in {"TranslationRequest", "TranslationResult", "TranslationWorkflowContext", "translate"}:
        translate_module = import_module("translation.translate")
        return getattr(translate_module, name)
    raise AttributeError(f"module 'translation' has no attribute {name!r}")
