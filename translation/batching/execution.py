from __future__ import annotations

from typing import Any, Callable

from translation.batching.payloads import (
    BatchTranslationError,
    translate_batch,
    translate_line_batch,
    translate_parent_batch,
)
from translation.batching.candidates import candidate_template_key, reindex_candidates


RawBatchTranslator = Callable[[str, list[dict[str, Any]], Callable[..., str], dict[str, Any]], dict[int, str]]


def translate_candidate_batch_raw(
    model: str,
    candidates: list[dict[str, Any]],
    *,
    translator: Callable[..., str],
    options: dict[str, Any],
    protocol: str = "json",
) -> dict[int, str]:
    """Translate a prepared candidate batch using the selected response protocol."""
    if protocol == "line":
        batch_translator: RawBatchTranslator = translate_line_batch
    elif protocol == "parent_json":
        batch_translator = translate_parent_batch
    else:
        batch_translator = translate_batch
    representatives: list[dict[str, Any]] = []
    representative_by_key: dict[tuple[Any, ...], int] = {}
    original_to_representative: dict[int, int] = {}
    for candidate in candidates:
        key = candidate_template_key(candidate)
        representative_i = representative_by_key.get(key)
        if representative_i is None:
            representative_i = len(representatives)
            representative_by_key[key] = representative_i
            representatives.append(dict(candidate, i=representative_i))
        original_to_representative[int(candidate["i"])] = representative_i

    try:
        representative_results = batch_translator(
            model,
            representatives,
            translator=translator,
            options=options,
        )
    except BatchTranslationError as exc:
        expanded_partial = {
            original_i: exc.partial_results[representative_i]
            for original_i, representative_i in original_to_representative.items()
            if representative_i in exc.partial_results
        }
        expanded_retry = {
            original_i
            for original_i, representative_i in original_to_representative.items()
            if representative_i in exc.retry_indexes
        }
        raise BatchTranslationError(
            str(exc),
            partial_results=expanded_partial,
            retry_indexes=expanded_retry,
        ) from exc
    return {
        original_i: representative_results[representative_i]
        for original_i, representative_i in original_to_representative.items()
    }


def translate_candidates_with_split(
    candidates: list[dict[str, Any]],
    *,
    batch_options: dict[str, Any],
    batch_protocol: str,
    model: str | None,
    translate_raw: Callable[[list[dict[str, Any]], dict[str, Any], str, str | None], dict[int, str]],
    finish_candidate: Callable[[dict[str, Any], str], tuple[str, str, list[dict[str, Any]]]],
    fallback_candidate: Callable[[dict[str, Any], Exception], dict[int, tuple[str, str, list[dict[str, Any]]]]],
) -> dict[int, tuple[str, str, list[dict[str, Any]]]]:
    """Translate candidates, recursively splitting failed batches before single fallback."""
    candidates = reindex_candidates(candidates)
    try:
        batch_result = translate_raw(candidates, batch_options, batch_protocol, model)
        return {
            candidate["idx"]: finish_candidate(candidate, batch_result[candidate["i"]])
            for candidate in candidates
        }
    except BatchTranslationError as exc:
        return _split_or_fallback(
            candidates,
            batch_options=batch_options,
            batch_protocol=batch_protocol,
            model=model,
            translate_raw=translate_raw,
            finish_candidate=finish_candidate,
            fallback_candidate=fallback_candidate,
            exc=exc,
        )
    except Exception as exc:
        return _split_or_fallback(
            candidates,
            batch_options=batch_options,
            batch_protocol=batch_protocol,
            model=model,
            translate_raw=translate_raw,
            finish_candidate=finish_candidate,
            fallback_candidate=fallback_candidate,
            exc=exc,
        )


def _split_or_fallback(
    candidates: list[dict[str, Any]],
    *,
    batch_options: dict[str, Any],
    batch_protocol: str,
    model: str | None,
    translate_raw: Callable[[list[dict[str, Any]], dict[str, Any], str, str | None], dict[int, str]],
    finish_candidate: Callable[[dict[str, Any], str], tuple[str, str, list[dict[str, Any]]]],
    fallback_candidate: Callable[[dict[str, Any], Exception], dict[int, tuple[str, str, list[dict[str, Any]]]]],
    exc: Exception,
) -> dict[int, tuple[str, str, list[dict[str, Any]]]]:
    if len(candidates) <= 1:
        return fallback_candidate(candidates[0], exc)

    mid = len(candidates) // 2
    translated: dict[int, tuple[str, str, list[dict[str, Any]]]] = {}
    translated.update(
        translate_candidates_with_split(
            candidates[:mid],
            batch_options=batch_options,
            batch_protocol=batch_protocol,
            model=model,
            translate_raw=translate_raw,
            finish_candidate=finish_candidate,
            fallback_candidate=fallback_candidate,
        )
    )
    translated.update(
        translate_candidates_with_split(
            candidates[mid:],
            batch_options=batch_options,
            batch_protocol=batch_protocol,
            model=model,
            translate_raw=translate_raw,
            finish_candidate=finish_candidate,
            fallback_candidate=fallback_candidate,
        )
    )
    return translated


__all__ = ["translate_candidate_batch_raw", "translate_candidates_with_split"]
