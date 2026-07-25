from __future__ import annotations

import unicodedata
import re
from typing import Any, Iterable


SENSITIVITY_CLASSIFIER_VERSION = "explicit-adult-vocalization-v3"

# Deliberately limited to explicit sexual anatomy, acts, fluids, and devices.
# Broad words such as slave, market, girl, body, naked, punishment, and training
# are excluded because they are not reliable adult-content signals by themselves.
_EXPLICIT_ADULT_TERMS = (
    "アナル",
    "イラマチオ",
    "ヴァギナ",
    "オナニー",
    "オナホ",
    "オーガズム",
    "おちんちん",
    "おっぱい",
    "クリトリス",
    "クンニ",
    "ザーメン",
    "セックス",
    "ディルド",
    "バイブ",
    "パイズリ",
    "フェラチオ",
    "ペニス",
    "ローター",
    "まんこ",
    "マンコ",
    "ちんこ",
    "チンコ",
    "ちんぽ",
    "チンポ",
    "ぶっかけ",
    "中出し",
    "乳首",
    "全裸",
    "処女膜",
    "処女",
    "勃起",
    "口内射精",
    "外出し",
    "女性器",
    "売春",
    "射精",
    "性交",
    "強姦",
    "愛液",
    "手コキ",
    "挿入",
    "凌辱",
    "潮吹き",
    "男根",
    "痴女",
    "痴漢",
    "種付け",
    "童貞",
    "精液",
    "絶頂",
    "肛門",
    "肉便器",
    "膣内",
    "膣",
    "自慰",
    "性奴隷",
    "足コキ",
    "輪姦",
    "孕ませ",
    "淫乱",
    "淫語",
    "発情",
    "貧乳",
    "陰茎",
    "陰部",
    "雌穴",
    "顔射",
    "巨乳",
    "レイプ",
)
_EXPLICIT_ADULT_PATTERNS = (
    re.compile(r"フェラ(?!ーリ)"),
    re.compile(r"(?:ケツ|尻)の穴.{0,16}(?:犯|挿|突)"),
)
_ADULT_VOCALIZATION_PATTERNS = (
    re.compile(r"イ[゛ﾞ]?(?:ク|グ|ケ)[゛ﾞ]?"),
    re.compile(r"イカせ"),
    re.compile(r"イッて(?:る|しま)"),
    re.compile(r"感度.{0,24}(?:我慢|限界|上げ|あげ)"),
    re.compile(r"気持ち良"),
)
_ADULT_FETISH_CONTEXT_RE = re.compile(r"(?:奴隷|調教|服従|屈服)")
_REPEATED_KANA_RE = re.compile(r"([ぁ-んァ-ヿ])\1{3,}")
_HEART_MARKERS = ("♡", "♥")


def has_explicit_adult_content(
    text: str,
    *,
    context_texts: Iterable[str] = (),
) -> bool:
    """Detect high-confidence adult content without broad contextual guessing."""
    combined = "\n".join([str(text or ""), *(str(item or "") for item in context_texts)])
    normalized = unicodedata.normalize("NFKC", combined).casefold()
    return (
        any(term.casefold() in normalized for term in _EXPLICIT_ADULT_TERMS)
        or any(pattern.search(normalized) for pattern in _EXPLICIT_ADULT_PATTERNS)
        or _looks_like_adult_vocalization(normalized)
    )


def _looks_like_adult_vocalization(normalized: str) -> bool:
    """Detect compound erotic-vocalization signals without treating hearts alone as adult."""
    heart_count = sum(normalized.count(marker) for marker in _HEART_MARKERS)
    if not heart_count:
        return False
    if _ADULT_FETISH_CONTEXT_RE.search(normalized):
        return True
    if any(pattern.search(normalized) for pattern in _ADULT_VOCALIZATION_PATTERNS):
        return True
    return (
        heart_count >= 3
        and (
            _REPEATED_KANA_RE.search(normalized) is not None
            or (
                normalized.count("゛")
                + normalized.count("ﾞ")
                + normalized.count("\u3099")
                >= 2
            )
        )
    )


def candidate_has_explicit_adult_content(candidate: dict[str, Any]) -> bool:
    """Apply the adult-content detector to source plus read-only context."""
    contexts = (
        str(context.get("text", ""))
        for context in candidate.get("contexts", []) or []
        if isinstance(context, dict)
    )
    return has_explicit_adult_content(
        str(candidate.get("source", "")),
        context_texts=contexts,
    )


__all__ = [
    "SENSITIVITY_CLASSIFIER_VERSION",
    "candidate_has_explicit_adult_content",
    "has_explicit_adult_content",
]
