from __future__ import annotations

import inspect
import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from translation.config import (
    default_model,
    default_system_prompt,
    fallback_chunk_strategy,
    fallback_models as configured_fallback_models,
    fallback_prompt_names,
    system_prompts,
)
from translation.quality.refusal import is_unusable_model_output


TranslateFunc = Callable[..., str]
RestoreFunc = Callable[[str], tuple[str, list[dict[str, Any]], list[dict[str, str]]]]
RefusalChecker = Callable[..., bool]
EnglishResidueFunc = Callable[..., list[str]]

# Bump when chunk boundaries or fallback budget semantics change; checkpoint
# fingerprints use this to avoid reusing output produced by an older policy.
RETRY_POLICY_VERSION = "quality-retry-v2"


def _supports_keyword(func: Callable[..., Any], keyword: str) -> bool:
    """Return whether a callback can receive ``keyword``.

    Do not use a trial call and catch ``TypeError`` here: a callback may raise
    that error while doing real work, and retrying without the shared budget
    would multiply requests.  Callbacks without inspectable signatures are
    treated conservatively and receive the legacy argument set.
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    if keyword in signature.parameters:
        parameter = signature.parameters[keyword]
        return parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())


def _call_retry_callback(
    callback: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    supported = {key: value for key, value in kwargs.items() if _supports_keyword(callback, key)}
    return callback(*args, **supported)


def _wait_for_retry_after(error: BaseException) -> None:
    """Honor a transport-provided Retry-After without owning transport policy."""
    if getattr(error, "retryable", True) is False:
        return
    delay = getattr(error, "retry_after_seconds", None)
    if delay is None:
        return
    try:
        seconds = max(0.0, float(delay))
    except (TypeError, ValueError):
        return
    if seconds:
        time.sleep(seconds)


@dataclass
class RetryBudget:
    """A small, auditable budget shared by one fallback operation."""

    limit: int = 3
    attempts: int = 0

    def take(self) -> bool:
        if self.attempts >= max(0, int(self.limit)):
            return False
        self.attempts += 1
        return True

    @property
    def exhausted(self) -> bool:
        return self.attempts >= max(0, int(self.limit))


# Runtime/control placeholders are indivisible. This deliberately covers the
# common forms without making the quality policy depend on one game's syntax.
_PROTECTED_TOKEN_RE = re.compile(
    r"(?:__[A-Za-z][A-Za-z0-9_]*__|"
    r"\$[A-Za-z_][A-Za-z0-9_]*|"
    r"%[A-Za-z_][A-Za-z0-9_]*%|%\d+|"
    r"\{\{?[^{}\n]+\}?\}|"
    r"<[^<>\n]+>|"
    r"\\[A-Za-z]+(?:\[[^\]\n]*\])?)"
)
_SENTENCE_BOUNDARIES = re.compile(r"([。！？.!?；\n]+)")


def _safe_split(text: str, max_chars: int) -> list[str]:
    """Split into non-overlapping chunks while keeping protected tokens whole."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]

    spans = [(m.start(), m.end()) for m in _PROTECTED_TOKEN_RE.finditer(text)]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        # If the boundary is inside a protected token, move it before that
        # token. A token longer than max_chars is retained as one atomic chunk.
        for token_start, token_end in spans:
            if token_start < end < token_end and token_start >= start:
                if token_start > start:
                    end = token_start
                else:
                    end = token_end
                break
        if end <= start:
            end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        start = end
    return chunks


def _chunk_source(text: str, max_chars: int) -> list[str]:
    # Mask protected spans while looking for sentence boundaries.  A tag such
    # as ``<line break="。">`` must remain atomic; splitting the raw text first
    # would make the subsequent restoration impossible.
    protected: list[str] = []

    def mask(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"__PROTECTED_CHUNK_{len(protected) - 1}__"

    masked = _PROTECTED_TOKEN_RE.sub(mask, text)
    parts = _SENTENCE_BOUNDARIES.split(masked)
    sentences: list[str] = []
    i = 0
    while i < len(parts):
        sentence = parts[i]
        if i + 1 < len(parts) and _SENTENCE_BOUNDARIES.fullmatch(parts[i + 1]):
            sentence += parts[i + 1]
            i += 2
        else:
            i += 1
        if sentence:
            sentences.append(sentence)

    if protected:
        marker_re = re.compile(r"__PROTECTED_CHUNK_(\d+)__")

        def restore_marker(match: re.Match[str]) -> str:
            return protected[int(match.group(1))]

        for index, sentence in enumerate(sentences):
            sentences[index] = marker_re.sub(restore_marker, sentence)

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_safe_split(sentence, max_chars))
        elif current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    return chunks or _safe_split(text, max_chars)


def chunk_translate(
    model: str,
    text: str,
    system_prompt: str,
    max_chars: int = 50,
    overlap: int = 10,
    terminology=None,
    translator: Callable[..., str] | None = None,
    budget: RetryBudget | None = None,
) -> str:
    """Translate chunks exactly once each; ``overlap`` is compatibility-only.

    Older callers pass overlap to provide context. It is intentionally ignored
    for output assembly because overlapping source windows cannot be safely
    concatenated after translation.
    """
    del overlap
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if translator is None:
        from translation.models.router import translate as translator
    if not text:
        return ""
    if len(text) <= max_chars:
        if budget is not None and not budget.take():
            return text
        return translator(model, text, system_prompt=system_prompt, terminology=terminology)

    translated_parts: list[str] = []
    for chunk in _chunk_source(text, max_chars):
        if not chunk.strip():
            # Formatting-only chunks still carry meaningful source layout.
            # Keep them without spending a model call.
            translated_parts.append(chunk)
            continue
        if budget is not None and not budget.take():
            translated_parts.append(chunk)
            continue
        try:
            result = translator(model, chunk, system_prompt=system_prompt, terminology=terminology)
            if is_unusable_model_output(result, original=chunk):
                # Do not recursively spend an unbounded second budget. Preserve
                # this source chunk once and let the caller mark it for review.
                translated_parts.append(chunk)
            else:
                translated_parts.append(result)
        except Exception as exc:
            _wait_for_retry_after(exc)
            translated_parts.append(chunk)
    return "".join(translated_parts)


def log_retry_stats(file_path: str, stats: dict) -> None:
    log_dir = os.path.join(".omo", "logs")
    os.makedirs(log_dir, exist_ok=True)
    try:
        rendered = dict(stats)
        rendered.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        with open(os.path.join(log_dir, "refusal_retries.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(rendered, ensure_ascii=False) + "\n")
    except OSError:
        return


def retry_with_fallback(
    text: str,
    model: str | None = None,
    system_prompt: str | None = None,
    terminology=None,
    translator: Callable[..., str] | None = None,
    fallback_models: Iterable[str] | None = None,
    attempt: int = 0,
    file_path: str = "",
    row: int = 0,
    col: int = 0,
    max_attempts: int | None = None,
    budget: RetryBudget | None = None,
) -> dict:
    """Run one bounded fallback sequence with one shared attempt budget."""
    if translator is None:
        from translation.models.router import translate as translator
    primary = model or default_model()
    prompts_cfg = system_prompts()
    professional_prompt = system_prompt or default_system_prompt("professional")
    prompts: list[str] = [professional_prompt]
    for name in fallback_prompt_names():
        value = prompts_cfg.get(name)
        if value and value not in prompts:
            prompts.append(value)
    configured_models = list(fallback_models if fallback_models is not None else configured_fallback_models())
    if max_attempts is None:
        max_attempts = max(3, len(prompts) + len(configured_models))
    budget = budget or RetryBudget(max_attempts)
    models = [primary]
    for candidate in configured_models:
        if candidate not in models:
            models.append(candidate)
    strategies: list[str] = []
    primary_failed = False

    for prompt in prompts:
        if not budget.take():
            break
        try:
            result = translator(primary, text, system_prompt=prompt, terminology=terminology)
            strategies.append("prompt_switch")
            if result and not is_unusable_model_output(result, original=text):
                return {"status": "SUCCESS", "translation": result, "attempts": budget.attempts}
        except Exception as exc:
            strategies.append("prompt_switch_error")
            if getattr(exc, "retryable", True) is False:
                # A permanent primary failure cannot be fixed by changing
                # prompt wording; continue only with distinct fallback models.
                primary_failed = True
                break
            _wait_for_retry_after(exc)
    for candidate in models[1:]:
        if not budget.take():
            break
        try:
            result = translator(candidate, text, system_prompt=professional_prompt, terminology=terminology)
            strategies.append(f"model_switch:{candidate}")
            if result and not is_unusable_model_output(result, original=text):
                return {"status": "SUCCESS", "translation": result, "attempts": budget.attempts}
        except Exception as exc:
            strategies.append(f"model_switch_error:{candidate}")
            if getattr(exc, "retryable", True) is False:
                continue
            _wait_for_retry_after(exc)

    if not primary_failed and not budget.exhausted:
        strategy = fallback_chunk_strategy()
        result = chunk_translate(
            primary,
            text,
            professional_prompt,
            max_chars=int(strategy.get("max_chars", 50)),
            overlap=int(strategy.get("overlap", 0)),
            terminology=terminology,
            translator=translator,
            budget=budget,
        )
        strategies.append("chunk_translate")
        if result and not is_unusable_model_output(result, original=text):
            return {"status": "SUCCESS", "translation": result, "attempts": budget.attempts}

    if file_path:
        log_retry_stats(file_path, {"file": file_path, "row": row, "col": col,
                                    "attempts": budget.attempts, "strategies": strategies,
                                    "final_status": "NEEDS_REVIEW"})
    return {"status": "NEEDS_REVIEW", "original": text, "reason": "max_retries_exceeded",
            "attempts": budget.attempts}


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
    compose_prompt: Callable[[str], str],
    translate_func: TranslateFunc,
    retry_with_fallback_func: Callable[..., dict[str, Any]] | None = None,
    chunk_translate_func: Callable[..., str] | None = None,
    is_refusal_func: RefusalChecker = lambda value, original=None: False,
    primary_failed: bool = False,
    max_attempts: int = 3,
    budget: RetryBudget | None = None,
) -> str:
    """Provider-neutral fallback policy with one shared bounded budget."""
    budget = budget or RetryBudget(max_attempts)
    configured_fallbacks: list[str] = []
    seen_fallbacks: set[str] = set()
    for candidate in fallback_models:
        if candidate != model and candidate not in seen_fallbacks:
            seen_fallbacks.add(candidate)
            configured_fallbacks.append(candidate)
    styles = [prompt_style, "uncensored", "academic", "professional"]
    tried: set[str] = set()
    if not primary_failed:
        # Keep enough budget for configured model fallbacks.  With the default
        # three-attempt budget, spending all three prompt variants would make
        # the actual fallback chain unreachable.
        style_attempts = max(0, budget.limit - len(configured_fallbacks))
        styles_used = 0
        for style in styles:
            if style in tried:
                continue
            tried.add(style)
            base = system_prompts.get(style)
            if not base:
                continue
            if styles_used >= style_attempts:
                break
            if not budget.take():
                break
            styles_used += 1
            try:
                result = translate_func(model, protected_text, system_prompt=compose_prompt(base), terminology=None)
                if result and not is_refusal_func(result, original=protected_text):
                    return result
            except Exception as exc:
                if getattr(exc, "retryable", True) is False:
                    primary_failed = True
                    break
                _wait_for_retry_after(exc)

    fallback_system_prompt = compose_prompt(system_prompt)
    for fallback_model in configured_fallbacks:
        if not budget.take():
            if budget.exhausted:
                break
            continue
        try:
            result = translate_func(fallback_model, protected_text,
                                    system_prompt=fallback_system_prompt, terminology=None)
            if result and not is_refusal_func(result, original=protected_text):
                return result
        except Exception as exc:
            _wait_for_retry_after(exc)
            continue

    # Retain injectable hooks for callers/tests, while passing the same budget
    # whenever supported so nested policies cannot multiply retries.
    if not primary_failed and not budget.exhausted and retry_with_fallback_func:
        fallback = _call_retry_callback(
            retry_with_fallback_func,
            (protected_text,),
            {
                "model": model,
                "system_prompt": fallback_system_prompt,
                "terminology": None,
                "file_path": file_path,
                "row": row_idx,
                "col": col_idx,
                "budget": budget,
                "max_attempts": budget.limit,
            },
        )
        if fallback.get("status") == "SUCCESS":
            return fallback.get("translation", "")

    if not primary_failed and not budget.exhausted and chunk_translate_func:
        return _call_retry_callback(
            chunk_translate_func,
            (model, protected_text, fallback_system_prompt),
            {
                "max_chars": chunk_strategy.get("max_chars", 50),
                "overlap": chunk_strategy.get("overlap", 0),
                "budget": budget,
            },
        )
    if not primary_failed and not budget.exhausted:
        return chunk_translate(model, protected_text, fallback_system_prompt,
                               max_chars=int(chunk_strategy.get("max_chars", 50)),
                               overlap=int(chunk_strategy.get("overlap", 0)),
                               translator=translate_func, budget=budget)
    return ""


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


def call_translate_with_options(
    *,
    model: str,
    text: str,
    system_prompt: str,
    options: dict[str, Any] | None,
    translate_func: TranslateFunc,
) -> str:
    """Call a translator with optional generation settings when supported."""
    if options and _supports_keyword(translate_func, "options"):
        try:
            return translate_func(
                model, text, system_prompt=system_prompt, terminology=None, options=options
            )
        except TypeError as exc:
            message = str(exc).lower()
            if "does not support options" not in message and "unexpected keyword argument 'options'" not in message:
                raise
    return translate_func(model, text, system_prompt=system_prompt, terminology=None)


def retry_short_label_translation(
    *,
    model: str,
    protected_text: str,
    retry_prompt: str,
    options: dict[str, Any],
    translate_func: TranslateFunc,
    is_refusal_func: RefusalChecker,
) -> str:
    """Retry a short label with strict quality instructions and options."""
    try:
        result = call_translate_with_options(
            model=model,
            text=protected_text,
            system_prompt=retry_prompt,
            options=options,
            translate_func=translate_func,
        )
    except Exception:
        return ""
    if result and not is_refusal_func(result, original=protected_text):
        return result
    return ""


__all__ = [
    "EnglishResidueFunc",
    "RefusalChecker",
    "RestoreFunc",
    "TranslateFunc",
    "RetryBudget",
    "RETRY_POLICY_VERSION",
    "call_translate_with_options",
    "chunk_translate",
    "fallback_translate",
    "log_retry_stats",
    "retry_with_fallback",
    "retry_english_residue_translation",
    "retry_missing_terms_translation",
    "retry_short_label_translation",
]
