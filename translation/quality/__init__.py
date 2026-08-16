"""Quality checks and issue generation."""

from translation.quality.constraints_core import auto_wrap, get_violations, validate
from translation.quality.rules import (
    FIXED_TRANSLATIONS,
    QUALITY_RULES_VERSION,
    apply_fixed_translations,
    apply_source_conditioned_fixes,
    english_residue,
    exact_fixed_translation,
    exact_grammatical_fragment_translation,
    exact_japanese_menu_translation,
    exact_nonlinguistic_translation,
    is_valid_identical_han_translation,
    normalize_small_tsu_residue,
    suspicious_artifacts,
    translation_issues,
)
from translation.protection.runtime import ProtectedToken, protect_runtime_tokens, restore_runtime_tokens
from translation.quality.constraints import apply_output_constraints
from translation.quality.issues import new_issues
from translation.quality.prompts import quality_prompt_rules
from translation.quality.refusal import (
    ModelOutputAssessment,
    assess_model_output,
    has_japanese,
    is_refusal,
    is_unusable_model_output,
)
from translation.quality.retry import retry_english_residue_translation, retry_missing_terms_translation
from translation.quality.status import progress_status, status_for_output

__all__ = [
    "get_violations",
    "assess_model_output",
    "has_japanese",
    "is_refusal",
    "is_unusable_model_output",
    "new_issues",
    "ModelOutputAssessment",
    "FIXED_TRANSLATIONS",
    "QUALITY_RULES_VERSION",
    "ProtectedToken",
    "apply_fixed_translations",
    "apply_output_constraints",
    "apply_source_conditioned_fixes",
    "auto_wrap",
    "english_residue",
    "exact_fixed_translation",
    "exact_grammatical_fragment_translation",
    "exact_japanese_menu_translation",
    "exact_nonlinguistic_translation",
    "is_valid_identical_han_translation",
    "normalize_small_tsu_residue",
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
