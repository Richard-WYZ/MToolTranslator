"""Quality checks and issue generation."""

from translation.quality.constraints_core import auto_wrap, get_violations, validate
from translation.quality.rules import (
    FIXED_TRANSLATIONS,
    apply_fixed_translations,
    apply_source_conditioned_fixes,
    english_residue,
    exact_fixed_translation,
    exact_japanese_menu_translation,
    exact_nonlinguistic_translation,
    suspicious_artifacts,
    translation_issues,
)
from translation.protection.runtime import ProtectedToken, protect_runtime_tokens, restore_runtime_tokens
from translation.quality.constraints import apply_output_constraints
from translation.quality.issues import new_issues
from translation.quality.prompts import quality_prompt_rules
from translation.quality.refusal import has_japanese, is_refusal
from translation.quality.retry import retry_english_residue_translation, retry_missing_terms_translation
from translation.quality.status import progress_status, status_for_output

__all__ = [
    "get_violations",
    "has_japanese",
    "is_refusal",
    "new_issues",
    "FIXED_TRANSLATIONS",
    "ProtectedToken",
    "apply_fixed_translations",
    "apply_output_constraints",
    "apply_source_conditioned_fixes",
    "auto_wrap",
    "english_residue",
    "exact_fixed_translation",
    "exact_japanese_menu_translation",
    "exact_nonlinguistic_translation",
    "progress_status",
    "protect_runtime_tokens",
    "quality_prompt_rules",
    "retry_english_residue_translation",
    "retry_missing_terms_translation",
    "restore_runtime_tokens",
    "status_for_output",
    "suspicious_artifacts",
    "translation_issues",
    "validate",
]
