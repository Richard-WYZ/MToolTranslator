from __future__ import annotations

import re


REFUSAL_MARKERS = (
    "i can't assist",
    "i cannot assist",
    "i can’t assist",
    "i cannot comply",
    "i'm sorry",
    "i am sorry",
    "as an ai",
    "cannot translate",
    "can't translate",
    "unable to translate",
    "抱歉",
    "对不起",
    "不能协助",
    "无法协助",
    "不能翻译",
    "无法翻译",
    "不适当",
    "不适当内容",
    "违反政策",
    "作为ai",
    "ai助手",
    "申し訳",
    "できません",
    "適切では",
    "ご容赦",
    "拒否",
    "不適切",
    "无法完成",
    "不适合",
    "违反",
    "不能提供",
    "refuse",
    "violation",
    "inappropriate",
    "apologize",
    "i cannot",
    "i can't",
    "i can not",
    "i'm unable",
    "i am unable",
    "not able",
)

CODE_EXPRESSION_RE = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*\(\)?)+")
CODE_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:[A-Z]{2,}[A-Za-z0-9_]*|[A-Za-z]+[A-Z][A-Za-z0-9_]*|"
    r"[A-Za-z][A-Za-z_]*[0-9][A-Za-z0-9_]*|[A-Za-z]+_[A-Za-z0-9_]+)"
    r"(?![A-Za-z0-9_])"
)
ENGLISH_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")
JAPANESE_KANA_RE = re.compile(r"[\u3041-\u309f\u30a1-\u30fa\u30fd-\u30ff]")
CHINESE_APOLOGY_RE = re.compile(r"(\u62b1\u6b49|\u5bf9\u4e0d\u8d77|\u4e0d\u597d\u610f\u601d)")
CHINESE_REFUSAL_CONTEXT_RE = re.compile(
    r"(\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u53ef\u4ee5|\u4e0d\u4fbf|\u62d2\u7edd).{0,16}"
    r"(\u7ffb\u8bd1|\u534f\u52a9|\u63d0\u4f9b|\u5904\u7406|\u5b8c\u6210|\u6ee1\u8db3|\u56de\u7b54|\u5185\u5bb9|\u8bf7\u6c42)"
    r"|(\u4f5c\u4e3a).{0,8}(AI|ai)"
    r"|(\u653f\u7b56|\u8fdd\u89c4|\u4e0d\u9002\u5f53)"
)


def has_japanese(text: str) -> bool:
    return bool(JAPANESE_KANA_RE.search(text or ""))


def is_refusal(text: str, original: str = "") -> bool:
    """Return True when a model response looks like a safety refusal or bad translation."""
    if not text or not text.strip():
        return True

    stripped = text.strip()
    if _is_only_punctuation(stripped):
        return True

    lowered = stripped.lower()
    if any(marker in lowered for marker in REFUSAL_MARKERS) and not _looks_like_dialogue_apology(stripped):
        return True

    if has_japanese(stripped):
        return True

    if original and has_japanese(original) and _english_ratio_ignoring_source_tokens(stripped, original) > 0.5:
        return True

    return False


def _is_only_punctuation(text: str) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    return not bool(re.search(r"[\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf]", stripped))


def _english_ratio(text: str) -> float:
    cleaned = re.sub(r"\s", "", text)
    if not cleaned:
        return 0.0
    english_chars = len(re.findall(r"[a-zA-Z]", cleaned))
    return english_chars / len(cleaned)


def _english_ratio_ignoring_source_tokens(text: str, original: str) -> float:
    cleaned = text
    for token in sorted(_preservable_source_tokens(original), key=len, reverse=True):
        cleaned = cleaned.replace(token, "")
    return _english_ratio(cleaned)


def _preservable_source_tokens(original: str) -> set[str]:
    if not original:
        return set()
    tokens: set[str] = set()
    for match in CODE_EXPRESSION_RE.finditer(original):
        tokens.update(ENGLISH_WORD_RE.findall(match.group(0)))
    for match in CODE_IDENTIFIER_RE.finditer(original):
        tokens.add(match.group(0))
    return tokens


def _looks_like_dialogue_apology(text: str) -> bool:
    if not CHINESE_APOLOGY_RE.search(text):
        return False
    return not CHINESE_REFUSAL_CONTEXT_RE.search(text)


__all__ = ["has_japanese", "is_refusal"]
