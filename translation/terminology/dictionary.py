from __future__ import annotations

import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


JP_RE = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
KANA_RE = re.compile("[\u3040-\u30ff]")
KANJI_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff]")
CHINESE_RE = re.compile("[\u4e00-\u9fff]")
NOISE_RE = re.compile(r"^[\s\d\W_]+$", re.UNICODE)
META_GLOSSES = {
    "\u8bcd\u6e90",
    "\u8a5e\u6e90",
    "\u53d1\u97f3",
    "\u767c\u97f3",
    "\u8bfb\u97f3",
    "\u8b80\u97f3",
    "\u53c2\u89c1",
    "\u53c3\u898b",
}


@dataclass(frozen=True)
class DictionaryToken:
    surface: str
    normalized: str
    dictionary_form: str
    reading: str
    part_of_speech: tuple[str, ...]
    provider: str = "sudachi"

    @property
    def is_noun(self) -> bool:
        return bool(self.part_of_speech) and self.part_of_speech[0] == "\u540d\u8a5e"

    @property
    def is_proper_noun(self) -> bool:
        return "\u56fa\u6709\u540d\u8a5e" in self.part_of_speech

    @property
    def is_content_term(self) -> bool:
        return self.is_noun and is_dictionary_term(self.surface)


@dataclass(frozen=True)
class DictionaryTranslation:
    source: str
    target: str
    reading: str = ""
    provider: str = "yomitan"


class SudachiProvider:
    """Optional Sudachi tokenizer wrapper.

    Sudachi is useful for identifying Japanese terms and names, but it does not
    provide Chinese translations. Treat its output as terminology evidence, not
    as final translated text.
    """

    def __init__(self) -> None:
        self.available = False
        self.error = ""
        self._tokenizer: Any | None = None
        self._mode: Any | None = None
        try:
            try:
                from sudachipy import Dictionary, SplitMode

                self._tokenizer = Dictionary().create()
                self._mode = SplitMode.C
            except ImportError:
                from sudachipy import dictionary, tokenizer

                self._tokenizer = dictionary.Dictionary().create()
                self._mode = tokenizer.Tokenizer.SplitMode.C
            self.available = True
        except Exception as exc:  # pragma: no cover - depends on optional data
            self.error = f"{type(exc).__name__}: {exc}"

    def tokenize(self, text: str) -> list[DictionaryToken]:
        if not self.available or not self._tokenizer or not text:
            return []
        result: list[DictionaryToken] = []
        for morpheme in self._tokenizer.tokenize(text, self._mode):
            surface = str(morpheme.surface())
            if not is_dictionary_term(surface):
                continue
            result.append(
                DictionaryToken(
                    surface=surface,
                    normalized=str(morpheme.normalized_form()),
                    dictionary_form=str(morpheme.dictionary_form()),
                    reading=str(morpheme.reading_form()),
                    part_of_speech=tuple(str(item) for item in morpheme.part_of_speech()),
                )
            )
        return result


class YomitanDictionary:
    """Minimal reader for Yomitan term dictionaries.

    It intentionally stores only exact headword matches. This keeps lookup
    deterministic and avoids turning a dictionary into a brittle phrase table.
    """

    def __init__(self, paths: Iterable[str | Path] = ()) -> None:
        self.translations: dict[str, list[DictionaryTranslation]] = defaultdict(list)
        self.loaded_files: list[str] = []
        self.errors: list[str] = []
        for path in paths:
            self.load_zip(path)

    def load_zip(self, path: str | Path) -> None:
        zip_path = Path(path)
        if not zip_path.exists():
            self.errors.append(f"{zip_path}: not found")
            return
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for name in zf.namelist():
                    if not re.fullmatch(r"term_bank_\d+\.json", Path(name).name):
                        continue
                    with zf.open(name) as f:
                        entries = json.load(f)
                    if isinstance(entries, list):
                        self._load_term_bank(entries, zip_path.name)
            self.loaded_files.append(str(zip_path))
        except Exception as exc:
            self.errors.append(f"{zip_path}: {type(exc).__name__}: {exc}")

    def lookup(self, source: str) -> list[DictionaryTranslation]:
        return list(self.translations.get(source, []))

    def _load_term_bank(self, entries: list[Any], provider: str) -> None:
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 6:
                continue
            source = str(entry[0] or "").strip()
            reading = str(entry[1] or "").strip()
            if not is_dictionary_term(source):
                continue
            for target in _extract_glossary_strings(entry[5]):
                target = _clean_glossary(target)
                if is_usable_chinese_gloss(target):
                    self.translations[source].append(
                        DictionaryTranslation(source=source, target=target, reading=reading, provider=provider)
                    )


def is_dictionary_term(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped) < 2 or len(stripped) > 40:
        return False
    if not JP_RE.search(stripped) or NOISE_RE.fullmatch(stripped):
        return False
    return True


def is_kanji_only_term(text: str) -> bool:
    stripped = (text or "").strip()
    return 2 <= len(stripped) <= 12 and KANJI_RE.fullmatch(stripped) is not None


def is_usable_chinese_gloss(text: str) -> bool:
    stripped = (text or "").strip()
    if not (1 <= len(stripped) <= 24):
        return False
    if stripped in META_GLOSSES:
        return False
    if any(stripped.startswith(prefix + "\uff1a") or stripped.startswith(prefix + ":") for prefix in META_GLOSSES):
        return False
    if KANA_RE.search(stripped):
        return False
    return CHINESE_RE.search(stripped) is not None


def summarize_sudachi_candidates(texts: Iterable[str], provider: SudachiProvider) -> dict[str, Any]:
    total = 0
    with_terms = 0
    whole_noun = 0
    whole_proper_noun = 0
    token_counter: Counter[str] = Counter()
    proper_counter: Counter[str] = Counter()
    kanji_only_whole_nouns: Counter[str] = Counter()

    for text in texts:
        total += 1
        tokens = provider.tokenize(text)
        content_tokens = [token for token in tokens if token.is_content_term]
        if content_tokens:
            with_terms += 1
        stripped = text.strip()
        for token in content_tokens:
            token_counter[token.surface] += 1
            if token.is_proper_noun:
                proper_counter[token.surface] += 1
        if len(tokens) == 1 and tokens[0].surface == stripped and tokens[0].is_noun:
            whole_noun += 1
            if is_kanji_only_term(stripped):
                kanji_only_whole_nouns[stripped] += 1
            if tokens[0].is_proper_noun:
                whole_proper_noun += 1

    repeated_terms = {term: count for term, count in token_counter.items() if count >= 2}
    repeated_proper = {term: count for term, count in proper_counter.items() if count >= 2}
    return {
        "available": provider.available,
        "error": provider.error,
        "total_texts": total,
        "texts_with_terms": with_terms,
        "whole_text_nouns": whole_noun,
        "whole_text_proper_nouns": whole_proper_noun,
        "repeated_terms": len(repeated_terms),
        "repeated_proper_nouns": len(repeated_proper),
        "kanji_only_whole_nouns": len(kanji_only_whole_nouns),
        "top_terms": token_counter.most_common(20),
        "top_proper_nouns": proper_counter.most_common(20),
        "top_kanji_only_whole_nouns": kanji_only_whole_nouns.most_common(20),
    }


def summarize_yomitan_matches(texts: Iterable[str], dictionary: YomitanDictionary) -> dict[str, Any]:
    total = 0
    exact = 0
    exact_short = 0
    examples: list[dict[str, str]] = []
    for text in texts:
        total += 1
        hits = dictionary.lookup(text.strip())
        if not hits:
            continue
        exact += 1
        if len(text.strip()) <= 40:
            exact_short += 1
        if len(examples) < 20:
            hit = hits[0]
            examples.append({"source": hit.source, "target": hit.target, "provider": hit.provider})
    return {
        "loaded_files": dictionary.loaded_files,
        "errors": dictionary.errors,
        "entries": sum(len(items) for items in dictionary.translations.values()),
        "headwords": len(dictionary.translations),
        "total_texts": total,
        "exact_matches": exact,
        "exact_short_matches": exact_short,
        "examples": examples,
    }


def _extract_glossary_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_extract_glossary_strings(item))
        return result
    if isinstance(value, dict):
        result = []
        for key in ("text", "content", "glossary", "definitions"):
            if key in value:
                result.extend(_extract_glossary_strings(value[key]))
        return result
    return []


def _clean_glossary(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"^[\[(（【].*?[\])）】]\s*", "", cleaned)
    cleaned = re.sub(r"[;；。].*$", "", cleaned).strip()
    return cleaned
