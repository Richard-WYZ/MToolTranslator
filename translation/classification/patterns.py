from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from translation.classification.rules import looks_like_short_label


JP_BLOCK = r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
KANJI_BLOCK = r"\u3400-\u4dbf\u4e00-\u9fff"

ASCII_LETTER_SUFFIX_RE = re.compile(rf"^(?P<base>[{JP_BLOCK}]{{1,30}})(?P<suffix>[A-Z])$")
FULLWIDTH_LETTER_SUFFIX_RE = re.compile(rf"^(?P<base>[{JP_BLOCK}]{{1,30}})(?P<suffix>[\uff21-\uff3a])$")
NUMBER_SUFFIX_RE = re.compile(rf"^(?P<base>[{JP_BLOCK}]{{1,30}})(?P<suffix>[0-9\uff10-\uff19]{{1,3}})$")
COUNT_SUFFIX_RE = re.compile(
    rf"^(?P<base>[{JP_BLOCK}]{{1,30}})"
    r"(?P<suffix>[0-9\uff10-\uff19]{1,3}"
    r"(?:\u65e5\u76ee|\u56de\u76ee|\u56de|\u4eba\u76ee|\u500b\u76ee|\u679a\u76ee|\u756a\u76ee))$"
)
SENTENCE_MARKER_RE = re.compile(r"[\u3002\uff01\uff1f!?]")
PARTICLE_RE = re.compile(
    r"(\u306f|\u304c|\u3092|\u306b|\u3078|\u3068|\u3067|\u306e|\u3082|\u3084|"
    r"\u304b|\u304b\u3089|\u307e\u3067|\u3088\u308a)"
)
KANJI_RE = re.compile(f"[{KANJI_BLOCK}]")
JP_RE = re.compile(f"[{JP_BLOCK}]")


@dataclass(frozen=True)
class LabelVariant:
    source: str
    base: str
    suffix: str
    kind: str


def parse_label_variant(text: str) -> LabelVariant | None:
    """Return a conservative, composable short-label variant shape."""
    stripped = (text or "").strip()
    if not _eligible_variant_source(stripped):
        return None
    for kind, pattern in (
        ("count_suffix", COUNT_SUFFIX_RE),
        ("letter_suffix", ASCII_LETTER_SUFFIX_RE),
        ("letter_suffix", FULLWIDTH_LETTER_SUFFIX_RE),
        ("number_suffix", NUMBER_SUFFIX_RE),
    ):
        match = pattern.fullmatch(stripped)
        if not match:
            continue
        base = match.group("base")
        if _eligible_variant_base(base):
            return LabelVariant(source=stripped, base=base, suffix=match.group("suffix"), kind=kind)
    return None


def label_variant_groups(texts: Iterable[str], min_group_size: int = 2) -> dict[str, list[LabelVariant]]:
    groups: dict[str, list[LabelVariant]] = {}
    for text in texts:
        variant = parse_label_variant(text)
        if not variant:
            continue
        groups.setdefault(variant.base, []).append(variant)
    return {base: items for base, items in groups.items() if len(items) >= min_group_size}


def _eligible_variant_source(text: str) -> bool:
    if not text or not JP_RE.search(text) or not looks_like_short_label(text):
        return False
    if "\n" in text or SENTENCE_MARKER_RE.search(text):
        return False
    return True


def _eligible_variant_base(base: str) -> bool:
    if not (1 <= len(base) <= 30):
        return False
    if PARTICLE_RE.search(base):
        return False
    return bool(KANJI_RE.search(base))


__all__ = ["LabelVariant", "label_variant_groups", "parse_label_variant"]
