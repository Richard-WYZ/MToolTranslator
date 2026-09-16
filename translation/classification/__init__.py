"""Text classification and deterministic translation rules."""

from translation.classification.dialogue import (
    DIALOGUE_CLOSERS,
    DIALOGUE_OPENERS,
    looks_like_context_boundary,
    looks_like_dialogue_boundary,
)
from translation.classification.mixed_kana import (
    MIXED_KANA_NORMALIZATION_VERSION,
    MixedKanaNormalization,
    MixedKanaSpan,
    kana_comparison_key,
    kana_moras,
    normalize_mixed_kana_for_model,
)
from translation.classification.patterns import LabelVariant, label_variant_groups, parse_label_variant
from translation.classification.rules import (
    CLASSIFICATION_VERSION,
    deterministic_translation,
    has_source_japanese,
    looks_like_short_label,
    normalize_model_source,
)
from translation.classification.sensitivity import (
    SENSITIVITY_CLASSIFIER_VERSION,
    candidate_has_explicit_adult_content,
    has_explicit_adult_content,
)

__all__ = [
    "DIALOGUE_CLOSERS",
    "DIALOGUE_OPENERS",
    "LabelVariant",
    "CLASSIFICATION_VERSION",
    "MIXED_KANA_NORMALIZATION_VERSION",
    "MixedKanaNormalization",
    "MixedKanaSpan",
    "SENSITIVITY_CLASSIFIER_VERSION",
    "candidate_has_explicit_adult_content",
    "deterministic_translation",
    "has_explicit_adult_content",
    "has_source_japanese",
    "kana_comparison_key",
    "kana_moras",
    "label_variant_groups",
    "looks_like_context_boundary",
    "looks_like_dialogue_boundary",
    "looks_like_short_label",
    "normalize_mixed_kana_for_model",
    "normalize_model_source",
    "parse_label_variant",
]
