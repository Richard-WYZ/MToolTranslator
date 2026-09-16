"""Compatibility exports for the legacy model retry module.

Retry and chunking policy lives in :mod:`translation.quality.retry`; these
wrappers retain the old monkeypatch-friendly default translator behavior.
"""
from __future__ import annotations

from typing import Callable

from translation.quality.retry import (
    RetryBudget,
    chunk_translate as _chunk_translate,
    log_retry_stats,
    retry_with_fallback as _retry_with_fallback,
)
from translation.models.router import translate


def chunk_translate(*args, translator: Callable[..., str] | None = None, **kwargs):
    return _chunk_translate(*args, translator=translator or translate, **kwargs)


def retry_with_fallback(*args, translator: Callable[..., str] | None = None, **kwargs):
    return _retry_with_fallback(*args, translator=translator or translate, **kwargs)


__all__ = ["RetryBudget", "chunk_translate", "log_retry_stats", "retry_with_fallback"]
