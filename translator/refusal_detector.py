from __future__ import annotations

from translation.models import retry as _model_retry
from translation.models.router import translate
from translation.quality.refusal import has_japanese, is_refusal

log_retry_stats = _model_retry.log_retry_stats


def chunk_translate(*args, **kwargs):
    if kwargs.get("translator") is None:
        kwargs["translator"] = translate
    return _model_retry.chunk_translate(*args, **kwargs)


def retry_with_fallback(*args, **kwargs):
    if kwargs.get("translator") is None:
        kwargs["translator"] = translate
    return _model_retry.retry_with_fallback(*args, **kwargs)


__all__ = [
    "chunk_translate",
    "has_japanese",
    "is_refusal",
    "log_retry_stats",
    "retry_with_fallback",
    "translate",
]