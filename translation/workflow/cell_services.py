from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

from translation.workflow.cell import CellTranslationServices


if TYPE_CHECKING:
    from translation.workflow.pipeline import TranslationPipeline


@dataclass(frozen=True)
class CellDependencies:
    """Explicit policy dependencies; no string lookup into module globals."""

    deterministic_translation: Callable[..., str]
    prepare_model_candidate: Callable[..., dict[str, Any]]
    is_unusable_model_output: Callable[..., bool]
    assess_model_output: Callable[..., Any]
    english_residue: Callable[..., list[str]]
    retry_english_residue_translation: Callable[..., tuple]
    protect_runtime_tokens: Callable[..., tuple]
    protect_symbols: Callable[..., tuple]
    retry_missing_terms_translation: Callable[..., tuple]
    translate: Callable[..., str]
    apply_fixed_translations: Callable[[str], str]
    apply_source_conditioned_fixes: Callable[[str, str], str]
    translation_issues: Callable[..., list[dict[str, str]]]
    output_constraints: Callable[[], tuple[int | None, int | None]]
    apply_output_constraints: Callable[..., str]
    has_japanese: Callable[[str], bool]


def build_cell_translation_services(pipeline: TranslationPipeline, dependencies: CellDependencies) -> CellTranslationServices:
    return CellTranslationServices(
        model=pipeline.model,
        system_prompt=pipeline.system_prompt,
        glossary=pipeline.glossary,
        short_label_options=pipeline._short_label_options,
        deterministic_translation=dependencies.deterministic_translation,
        prepare_model_candidate=dependencies.prepare_model_candidate,
        looks_like_short_label=pipeline._looks_like_short_label,
        compose_label_prompt=pipeline._compose_label_prompt,
        compose_system_prompt=pipeline._compose_system_prompt,
        call_translate=pipeline._call_translate,
        is_refusal=dependencies.is_unusable_model_output,
        assess_model_output=dependencies.assess_model_output,
        retry_short_label=pipeline._retry_short_label,
        fallback_translate=pipeline._fallback_translate,
        status_for_output=pipeline._status_for_output,
        restore_protected_translation=pipeline._restore_protected_translation,
        english_residue=dependencies.english_residue,
        retry_english_residue_translation=dependencies.retry_english_residue_translation,
        protect_runtime_tokens=dependencies.protect_runtime_tokens,
        protect_symbols=dependencies.protect_symbols,
        retry_missing_terms_translation=dependencies.retry_missing_terms_translation,
        translate=dependencies.translate,
        apply_fixed_translations=dependencies.apply_fixed_translations,
        apply_source_conditioned_fixes=dependencies.apply_source_conditioned_fixes,
        translation_issues=dependencies.translation_issues,
        new_issues=pipeline._new_issues,
        pollution_issues=pipeline._pollution_issues,
        output_constraints=dependencies.output_constraints,
        apply_output_constraints=dependencies.apply_output_constraints,
        has_japanese=dependencies.has_japanese,
    )


__all__ = ["CellDependencies", "build_cell_translation_services"]
