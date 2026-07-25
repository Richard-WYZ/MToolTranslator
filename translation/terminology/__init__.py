"""Terminology and glossary workflow components."""

from translation.terminology.aliases import apply_term_aliases
from translation.terminology.backfill import OutputCellUpdater, backfill_confirmed_terms_to_outputs
from translation.terminology.dictionary import (
    DictionaryToken,
    DictionaryTranslation,
    SudachiProvider,
    YomitanDictionary,
    is_dictionary_term,
    is_kanji_only_term,
    is_usable_chinese_gloss,
    summarize_sudachi_candidates,
    summarize_yomitan_matches,
)
from translation.terminology.glossary import Glossary

__all__ = [
    "DictionaryToken",
    "DictionaryTranslation",
    "Glossary",
    "OutputCellUpdater",
    "SudachiProvider",
    "YomitanDictionary",
    "apply_term_aliases",
    "backfill_confirmed_terms_to_outputs",
    "is_dictionary_term",
    "is_kanji_only_term",
    "is_usable_chinese_gloss",
    "summarize_sudachi_candidates",
    "summarize_yomitan_matches",
]
