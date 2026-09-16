"""Compatibility export for the quality fallback policy."""
from collections.abc import Callable, Iterable
from typing import Any

from translation.quality.retry import fallback_translate

TranslateFunc = Callable[..., str]
RetryFallbackFunc = Callable[..., dict[str, Any]]
ChunkTranslateFunc = Callable[..., str]
PromptBuilder = Callable[[str], str]
RefusalChecker = Callable[..., bool]

__all__ = [
    "ChunkTranslateFunc",
    "PromptBuilder",
    "RefusalChecker",
    "RetryFallbackFunc",
    "TranslateFunc",
    "fallback_translate",
]
