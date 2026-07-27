from __future__ import annotations

import re
from typing import Any

from translation.quality.rules import (
    apply_fixed_translations,
    english_residue,
    exact_fixed_translation,
    exact_japanese_menu_translation,
    exact_nonlinguistic_translation,
)
from translation.quality.refusal import has_japanese


SHORT_LABEL_MAX_CHARS = 40
CLASSIFICATION_VERSION = "classification-v4-source-code-preservation"
SOURCE_JAPANESE_RE = re.compile("[\\u3041-\\u309f\\u30a1-\\u30fa\\u30fd-\\u30ff\\u3400-\\u4dbf\\u4e00-\\u9fff]")
PURE_CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff\u3005\u3006\u3024]+$")
LONG_FORM_MARKERS = ("\u3002", "\u300c", "\u300d", "\u300e", "\u300f")
JAPANESE_NUMERIC_ORDINAL_RE = re.compile(
    r"(?:\u7b2c)?(?P<number>[0-9\uff10-\uff19]+)"
    r"(?P<counter>\u3064\u76ee|\u500b\u76ee|\u4eba\u76ee|\u56de\u76ee|\u756a\u76ee)"
    r"(?:\u306e)?"
)
CHINESE_ORDINAL_COUNTERS = {
    "\u3064\u76ee": "\u4e2a",
    "\u500b\u76ee": "\u4e2a",
    "\u4eba\u76ee": "\u4e2a\u4eba",
    "\u56de\u76ee": "\u6b21",
    "\u756a\u76ee": "\u4e2a",
}


def has_source_japanese(text: str) -> bool:
    return bool(text and SOURCE_JAPANESE_RE.search(text))


def looks_like_kanji_proper_name(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 12:
        return False
    return bool(PURE_CJK_RE.fullmatch(stripped))


def looks_like_short_label(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped or "\n" in stripped:
        return False
    if len(stripped) > SHORT_LABEL_MAX_CHARS:
        return False
    if any(mark in stripped for mark in LONG_FORM_MARKERS):
        return False
    return True


def normalize_model_source(text: str) -> str:
    """Pretranslate unambiguous Japanese ordinal syntax before model token protection."""
    return JAPANESE_NUMERIC_ORDINAL_RE.sub(
        lambda match: (
            "\u7b2c"
            + match.group("number")
            + CHINESE_ORDINAL_COUNTERS[match.group("counter")]
        ),
        text,
    )


def deterministic_translation(text: str, glossary: Any | None = None) -> str:
    """Return a safe rule-based translation, or empty string when a model is needed."""
    if text and not has_source_japanese(text):
        return text

    fixed = exact_fixed_translation(text)
    if fixed:
        return fixed

    fixed_menu = exact_japanese_menu_translation(text)
    if fixed_menu:
        return fixed_menu

    nonlinguistic = exact_nonlinguistic_translation(text)
    if nonlinguistic:
        return nonlinguistic

    if (
        looks_like_kanji_proper_name(text)
        and glossary is not None
        and getattr(glossary, "is_identified_kanji_name", lambda _text: False)(text)
    ):
        return text

    if glossary is not None:
        deterministic = apply_fixed_translations(glossary.apply_post_translation(text, text))
        if deterministic != text and not has_japanese(deterministic) and not english_residue(deterministic):
            return deterministic

    return ""


__all__ = [
    "CLASSIFICATION_VERSION",
    "deterministic_translation",
    "has_source_japanese",
    "looks_like_short_label",
    "normalize_model_source",
]
