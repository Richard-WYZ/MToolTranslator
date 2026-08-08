from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CellTranslationServices:
    model: str
    system_prompt: str
    glossary: Any
    short_label_options: dict[str, Any]
    deterministic_translation: Callable[..., str]
    prepare_model_candidate: Callable[..., dict[str, Any]]
    looks_like_short_label: Callable[[str], bool]
    compose_label_prompt: Callable[..., str]
    compose_system_prompt: Callable[..., str]
    call_translate: Callable[..., str]
    is_refusal: Callable[..., bool]
    assess_model_output: Callable[..., Any]
    retry_short_label: Callable[[str, list[dict[str, str]]], str]
    fallback_translate: Callable[[str, str, int, int, list[dict[str, str]]], str]
    status_for_output: Callable[..., str]
    restore_protected_translation: Callable[..., tuple[str, list[dict[str, Any]], list[dict[str, str]]]]
    english_residue: Callable[..., list[str]]
    retry_english_residue_translation: Callable[..., tuple[str, list[dict[str, str]], list[dict[str, Any]]]]
    protect_runtime_tokens: Callable[..., tuple[str, list[Any]]]
    protect_symbols: Callable[..., tuple[str, list[Any]]]
    retry_missing_terms_translation: Callable[..., tuple[str, list[dict[str, str]], list[dict[str, Any]]]]
    translate: Callable[..., str]
    apply_fixed_translations: Callable[[str], str]
    apply_source_conditioned_fixes: Callable[[str, str], str]
    translation_issues: Callable[..., list[dict[str, str]]]
    new_issues: Callable[[list[dict[str, Any]], list[dict[str, str]]], list[dict[str, str]]]
    pollution_issues: Callable[[str, str], list[dict[str, str]]]
    output_constraints: Callable[[], tuple[int | None, int | None]]
    apply_output_constraints: Callable[..., str]
    has_japanese: Callable[[str], bool]


def translate_cell_with_meta(
    *,
    text: str,
    row_idx: int,
    col_idx: int,
    file_path: str,
    services: CellTranslationServices,
    context: list[str] | None = None,
    preserve_source_layout: bool = False,
) -> tuple[str, str, list[dict[str, Any]]]:
    if not text or not text.strip():
        return "", "preserved", []

    deterministic = services.deterministic_translation(text)
    if deterministic:
        return deterministic, services.status_for_output(text, deterministic), []

    deterministic = services.deterministic_translation(text, glossary=services.glossary)
    if deterministic:
        return deterministic, services.status_for_output(text, deterministic), []

    issues: list[dict[str, Any]] = []
    candidate = services.prepare_model_candidate(
        batch_i=0,
        idx=row_idx,
        source=text,
        glossary=services.glossary,
        short_label=services.looks_like_short_label(text),
    )
    term_hits = candidate["term_hits"]
    prepared_text = candidate["prepared"]
    protected_text = candidate["protected"]
    runtime_tokens = candidate["runtime_tokens"]
    symbol_tokens = candidate["symbol_tokens"]
    term_tokens = candidate.get("term_tokens", [])
    short_label = candidate["short_label"]
    system_prompt = (
        services.compose_label_prompt(term_hits=term_hits)
        if short_label
        else services.compose_system_prompt(services.system_prompt, term_hits=term_hits, strict=False)
    )

    try:
        translated = services.call_translate(
            protected_text,
            system_prompt,
            options=services.short_label_options if short_label else None,
        )
        if services.is_refusal(translated, original=protected_text):
            if short_label:
                retried = services.retry_short_label(protected_text, term_hits)
                if retried:
                    translated = retried
            else:
                translated = services.fallback_translate(protected_text, file_path, row_idx, col_idx, term_hits)
    except Exception as exc:
        issues.append({"type": "translation_error", "message": str(exc)})
        try:
            translated = services.fallback_translate(
                protected_text,
                file_path,
                row_idx,
                col_idx,
                term_hits,
                primary_failed=True,
            )
        except Exception as fallback_exc:
            issues.append({"type": "fallback_error", "message": str(fallback_exc)})
            translated = ""

    assessment = services.assess_model_output(translated, original=protected_text)
    if assessment.is_hard_failure:
        fallback_text = services.glossary.apply_post_translation(text, text)
        fallback_text = services.apply_fixed_translations(fallback_text)
        fallback_deterministic = services.deterministic_translation(fallback_text, glossary=services.glossary)
        if fallback_deterministic:
            return fallback_deterministic, services.status_for_output(text, fallback_deterministic, issues), issues
        issues.append(assessment.as_issue())
        return fallback_text, "review_required", issues
    if assessment.is_advisory:
        issues.append(assessment.as_issue())

    restored, symbol_issues, missing_terms = services.restore_protected_translation(
        text,
        prepared_text,
        protected_text,
        translated,
        symbol_tokens,
        term_tokens,
        runtime_tokens,
        term_hits,
    )
    issues.extend(symbol_issues)

    residue = services.english_residue(restored, original=text)
    if residue:
        retry_prompt = (
            (services.compose_label_prompt(term_hits=term_hits, strict=True) if short_label else system_prompt)
            + "\n\nQuality retry: translate ordinary English words into Chinese. "
            + "Keep only variables, tags, file names, URLs, control codes, and key/button labels unchanged. "
            + "English words to avoid: "
            + ", ".join(sorted(set(residue))[:8])
            + "."
        )
        restored, missing_terms, retry_issues = services.retry_english_residue_translation(
            original_text=text,
            protected_text=protected_text,
            current_restored=restored,
            current_missing_terms=missing_terms,
            residue=residue,
            retry_prompt=retry_prompt,
            model=services.model,
            translate_func=services.translate,
            restore_func=lambda retried: services.restore_protected_translation(
                text,
                prepared_text,
                protected_text,
                retried,
                symbol_tokens,
                term_tokens,
                runtime_tokens,
                term_hits,
            ),
            is_refusal_func=services.is_refusal,
            english_residue_func=services.english_residue,
        )
        issues.extend(retry_issues)

    if missing_terms:
        term_prepared_text, term_tokens = services.glossary.protect_terms(text)
        runtime_prepared_text, retry_runtime_tokens = services.protect_runtime_tokens(term_prepared_text)
        retry_protected_text, retry_symbol_tokens = services.protect_symbols(runtime_prepared_text)
        retry_prompt = services.compose_system_prompt(services.system_prompt, term_hits=term_hits, strict=True)
        restored, missing_terms, retry_issues = services.retry_missing_terms_translation(
            protected_text=retry_protected_text,
            retry_prompt=retry_prompt,
            model=services.model,
            current_restored=restored,
            current_missing_terms=missing_terms,
            translate_func=services.translate,
            restore_func=lambda retried: services.restore_protected_translation(
                text,
                runtime_prepared_text,
                retry_protected_text,
                retried,
                retry_symbol_tokens,
                term_tokens,
                retry_runtime_tokens,
                term_hits,
            ),
            is_refusal_func=services.is_refusal,
        )
        issues.extend(retry_issues)

    if missing_terms:
        issues.append({
            "type": "term_preservation",
            "message": "Terminology was not preserved; please review: "
            + ", ".join(f"{item['source']}=>{item['target']}" for item in missing_terms),
        })

    restored = services.glossary.apply_post_translation(text, restored)
    restored = services.apply_fixed_translations(restored)
    restored = services.apply_source_conditioned_fixes(text, restored)
    issues.extend(services.new_issues(issues, services.translation_issues(text, restored, short_label=short_label)))
    issues.extend(services.new_issues(issues, services.pollution_issues(text, restored)))

    max_chars, max_lines = services.output_constraints()
    if not preserve_source_layout:
        restored = services.apply_output_constraints(restored, max_chars=max_chars, max_lines=max_lines)
    return restored, services.status_for_output(text, restored, issues), issues


__all__ = ["CellTranslationServices", "translate_cell_with_meta"]
