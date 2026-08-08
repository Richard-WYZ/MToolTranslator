from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from translation.workflow.cell import CellTranslationServices


def build_cell_translation_services(pipeline: Any, symbols: Mapping[str, Any]) -> CellTranslationServices:
    return CellTranslationServices(
        model=pipeline.model,
        system_prompt=pipeline.system_prompt,
        glossary=pipeline.glossary,
        short_label_options=pipeline._short_label_options,
        deterministic_translation=symbols["deterministic_translation"],
        prepare_model_candidate=symbols["prepare_model_candidate"],
        looks_like_short_label=pipeline._looks_like_short_label,
        compose_label_prompt=pipeline._compose_label_prompt,
        compose_system_prompt=pipeline._compose_system_prompt,
        call_translate=pipeline._call_translate,
        is_refusal=symbols["is_unusable_model_output"],
        assess_model_output=symbols["assess_model_output"],
        retry_short_label=pipeline._retry_short_label,
        fallback_translate=pipeline._fallback_translate,
        status_for_output=pipeline._status_for_output,
        restore_protected_translation=pipeline._restore_protected_translation,
        english_residue=symbols["english_residue"],
        retry_english_residue_translation=symbols["retry_english_residue_translation"],
        protect_runtime_tokens=symbols["protect_runtime_tokens"],
        protect_symbols=symbols["protect_symbols"],
        retry_missing_terms_translation=symbols["retry_missing_terms_translation"],
        translate=symbols["translate"],
        apply_fixed_translations=symbols["apply_fixed_translations"],
        apply_source_conditioned_fixes=symbols["apply_source_conditioned_fixes"],
        translation_issues=symbols["translation_issues"],
        new_issues=pipeline._new_issues,
        pollution_issues=pipeline._pollution_issues,
        output_constraints=symbols["output_constraints"],
        apply_output_constraints=symbols["apply_output_constraints"],
        has_japanese=symbols["has_japanese"],
    )


__all__ = ["build_cell_translation_services"]
