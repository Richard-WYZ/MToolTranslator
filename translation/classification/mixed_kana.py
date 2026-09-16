from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass


MIXED_KANA_NORMALIZATION_VERSION = "mixed-kana-v1-exact-suffix"

# A direct script switch is only a candidate. Japanese normally places
# hiragana particles and inflections next to katakana words, so matches still
# require a phonetic, mora-level equality check below.
HIRAGANA_KATAKANA_BOUNDARY_RE = re.compile(
    r"(?P<hiragana>[\u3041-\u3096\u309d-\u309f\u30fc]+)"
    r"(?P<katakana>[\u30a1-\u30fa\u30fc\u30fd-\u30ff\uff66-\uff9f]+)"
)

_SMALL_HIRAGANA = frozenset("ぁぃぅぇぉゃゅょゎゕゖ")
_COMBINING_VOICING_MARKS = frozenset(("\u3099", "\u309a"))


@dataclass(frozen=True)
class MixedKanaSpan:
    start: int
    end: int
    kept: str
    removed: str
    reading: str
    kind: str = "cross_script_duplicate"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MixedKanaNormalization:
    text: str
    spans: tuple[MixedKanaSpan, ...] = ()
    version: str = MIXED_KANA_NORMALIZATION_VERSION

    @property
    def changed(self) -> bool:
        return bool(self.spans)


def kana_comparison_key(text: str) -> str:
    """Return a width- and script-folded key without mutating source text."""
    normalized = unicodedata.normalize("NFKC", text or "")
    folded: list[str] = []
    for character in normalized:
        codepoint = ord(character)
        if 0x30A1 <= codepoint <= 0x30F6 or 0x30FD <= codepoint <= 0x30FE:
            folded.append(chr(codepoint - 0x60))
        else:
            folded.append(character)
    return unicodedata.normalize("NFC", "".join(folded))


def kana_moras(text: str) -> tuple[str, ...]:
    """Split a comparison key into conservative Japanese mora units."""
    key = unicodedata.normalize("NFD", kana_comparison_key(text))
    moras: list[str] = []
    for character in key:
        if character in _COMBINING_VOICING_MARKS:
            if moras:
                moras[-1] += character
            else:
                moras.append(character)
        elif character in _SMALL_HIRAGANA or character == "ー":
            if moras:
                moras[-1] += character
            else:
                moras.append(character)
        else:
            moras.append(character)
    return tuple(unicodedata.normalize("NFC", mora) for mora in moras)


def normalize_mixed_kana_for_model(text: str) -> MixedKanaNormalization:
    """Collapse only high-confidence hiragana/katakana duplicate boundaries.

    The katakana run must represent at least two mora and must exactly equal a
    suffix of the immediately preceding hiragana run after width/script
    folding. One-mora overlaps are left untouched because ordinary Japanese
    boundaries such as ``私はハーフ`` otherwise become false positives.
    """
    source = text or ""
    if not source:
        return MixedKanaNormalization(source)

    parts: list[str] = []
    spans: list[MixedKanaSpan] = []
    cursor = 0
    for match in HIRAGANA_KATAKANA_BOUNDARY_RE.finditer(source):
        hiragana = match.group("hiragana")
        katakana = match.group("katakana")
        hiragana_moras = kana_moras(hiragana)
        katakana_moras = kana_moras(katakana)
        if (
            len(katakana_moras) < 2
            or len(hiragana_moras) < len(katakana_moras)
            or hiragana_moras[-len(katakana_moras):] != katakana_moras
        ):
            continue

        katakana_start, katakana_end = match.span("katakana")
        parts.append(source[cursor:katakana_start])
        spans.append(
            MixedKanaSpan(
                start=katakana_start,
                end=katakana_end,
                kept="".join(hiragana_moras[-len(katakana_moras):]),
                removed=katakana,
                reading="".join(katakana_moras),
            )
        )
        cursor = katakana_end

    if not spans:
        return MixedKanaNormalization(source)
    parts.append(source[cursor:])
    return MixedKanaNormalization("".join(parts), tuple(spans))


__all__ = [
    "MIXED_KANA_NORMALIZATION_VERSION",
    "MixedKanaNormalization",
    "MixedKanaSpan",
    "kana_comparison_key",
    "kana_moras",
    "normalize_mixed_kana_for_model",
]
