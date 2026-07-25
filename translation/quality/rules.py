from __future__ import annotations

import re

from translation.protection.runtime import (
    AMBIGUOUS_KEY_NAMES,
    CODE_EXPRESSION_RE,
    CODE_IDENTIFIER_RE,
    KEY_NAMES,
    NUMERIC_TOKEN_RE,
    VARIABLE_RE,
    normalize_fixed_key,
)


FIXED_TRANSLATIONS: dict[str, str] = {
    "continue": "继续",
    "new game": "新游戏",
    "load": "读取",
    "load game": "读取游戏",
    "save": "保存",
    "save game": "保存游戏",
    "settings": "设置",
    "options": "选项",
    "option": "选项",
    "config": "设置",
    "configuration": "设置",
    "exit": "退出",
    "quit": "退出",
    "back": "返回",
    "cancel": "取消",
    "confirm": "确认",
    "ok": "确定",
    "retry": "重试",
    "skip": "跳过",
    "auto": "自动",
    "log": "日志",
    "gallery": "鉴赏",
    "inventory": "物品栏",
    "mission": "任务",
    "quest": "任务",
    "battle": "战斗",
    "start": "开始",
    "title": "标题",
    "menu": "菜单",
    "item": "道具",
    "skill": "技能",
    "weapon": "武器",
    "armor": "防具",
    "status": "状态",
    "level": "等级",
    "hp": "体力",
    "mp": "魔力",
    "コンティニュー": "继续",
    "セーブ": "保存",
    "ロード": "读取",
    "オプション": "选项",
    "オプションズ": "选项",
    "コンフィグ": "设置",
    "ギャラリー": "鉴赏",
    "インベントリ": "物品栏",
    "ミッション": "任务",
    "クエスト": "任务",
    "バトル": "战斗",
    "読んでみる": "读读看",
    "読む": "阅读",
    "門番": "门卫",
    "兵士": "士兵",
    "観客": "观众",
    "天候等": "天气等",
    "オーク": "兽人",
    "メスガキ": "小恶女",
    "スパッツ": "运动短裤",
    "ショーツ": "内裤",
    "ニプルファック": "乳头抽插",
    "手マン": "指交",
    "ショタレイプ": "正太强奸",
    "挨拶": "问候",
}

ENGLISH_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")
SUSPICIOUS_ARTIFACT_RE = re.compile(r"[{}]|[\"'][,;:.!?，。！？、]|[,;:.!?，。！？、][\"']|[\"'][)\]）】]|[(\[（【][\"']")
NUMERIC_ONLY_RE = re.compile(r"^\s*[+-]?[0-9０-９]+(?:[,.，．][0-9０-９]+)*(?:[%％])?\s*$")
CODE_LIKE_RE = re.compile(r"^\s*[A-Za-z]{1,12}[_-]?[0-9０-９]{1,8}\s*$")
TARGET_SCRIPT_RE = re.compile("[\u3041-\u309f\u30a1-\u30fa\u30fd-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
URL_FRAGMENT_RE = re.compile(
    r"(https?://|www\.|[A-Za-z0-9.-]+\.(?:com|jp|net|org|io|work|html|php|fc2|ne\.jp|co\.jp))",
    re.IGNORECASE,
)
TECHNICAL_MARKER_RE = re.compile(r"[\\/$_.#:=;{}[\]<>]")
NON_JP_PUNCT_ONLY_RE = re.compile(r"^[^A-Za-z0-9\u3040-\u30ff\u3400-\u9fff]+$")
UPPER_CODE_RE = re.compile(r"^[A-Z0-9_ .+\-()/（）【】\[\]{}:;]+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:[._-][A-Za-z0-9_$]+)*$")
CAMEL_OR_ALNUM_RE = re.compile(r"[a-z][A-Z]|[A-Za-z]+[0-9]|[0-9]+[A-Za-z]")
NUMERIC_LIST_RE = re.compile(r"^[0-9０-９]+(?:\s*,\s*[0-9０-９]+)+\s*[\]\)]?$")
RESOURCE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_$+./:-]+$")
COPYRIGHT_RE = re.compile(r"^(?:\(?[cC]\)|©|copyright\b)", re.IGNORECASE)
ENCODED_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{50,}$")
STYLE_COMMAND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\s*,\s*[0-9０-９]+)+\s*$")
SOURCE_VERSION_TOKEN_RE = re.compile(r"(?<![A-Za-z])ver(?:sion)?\.?(?![A-Za-z])", re.IGNORECASE)
VERSION_MARKER_RE = re.compile(r"(?<![A-Za-z])(?:ver(?:sion)?\.?|version)\s*[0-9][0-9A-Za-z._-]*", re.IGNORECASE)
RESOURCE_FILE_RE = re.compile(
    r"^\s*[^\s\r\n<>]+\.(?:png|jpe?g|gif|webp|bmp|ogg|wav|mp3|m4a|json|js|css|"
    r"glsl|vert|frag|rvdata2?|rpgmvp|rpgmvo|rpgmvm)\s*$",
    re.IGNORECASE,
)
ANGLE_CONFIG_FRAGMENT_RE = re.compile(r"^\s*<[^<>\r\n]{1,200}>\s*$")
NUMERIC_ASSIGNMENT_RE = re.compile(
    r"^\s*[^=\r\n]{1,80}\s*=\s*[-+]?[0-9\uff10-\uff19]+(?:\.[0-9\uff10-\uff19]+)?\s*$"
)
KANA_RE = re.compile("[\\u3041-\\u309f\\u30a1-\\u30fa\\u30fd-\\u30ff]")
LEAKED_TERM_PLACEHOLDER_RE = re.compile(
    r"__(?:PERSON|TERM|KEEP|SYM)_\d+__",
    re.IGNORECASE,
)
HONORIFIC_SUFFIX_RE = re.compile(
    r"[\u30a1-\u30fa\u30fc\u3400-\u4dbf\u4e00-\u9fff]{1,12}"
    r"(?:\u3055\u3093|\u3061\u3083\u3093|\u304f\u3093|\u69d8|\u3055\u307e|\u541b)"
    r"(?=$|[\u306f\u304c\u3092\u306b\u3068\u3082\u306e\u3078\u3001\u3002\uff1a:\u300c\u300d\s])"
)
CHINESE_HONORIFIC_MARKER_RE = re.compile(
    "\u5927\u4eba|\u9601\u4e0b|\u6bbf\u4e0b|\u965b\u4e0b|\u5148\u751f|\u5973\u58eb|\u5c0f\u59d0|\u540c\u5b66|"
    "\u524d\u8f88|\u8001\u5e08|\u4e3b\u4eba|\u5c11\u7237|\u5c0f\u59d0|\u54e5|\u59d0|\u5f1f|\u59b9|\u53d4|\u59e8|"
    "\u7237\u7237|\u5976\u5976|\u5927\u5bb6|\u672c\u5927\u7237|\u56fd\u738b|\u5973\u738b|"
    "\u5c0f[\u4e00-\u9fff]|[\u4e00-\u9fff]\u541b|[\u4e00-\u9fff]\u9171"
)


def exact_fixed_translation(text: str) -> str:
    if not text:
        return ""
    leading = re.match(r"^\s*", text).group(0)
    trailing = re.search(r"\s*$", text).group(0)
    core = text.strip()
    punct_prefix = re.match(r"^[\[\(（【「『]*", core).group(0)
    punct_suffix = re.search(r"[\]\)）】」』?!！？。]*$", core).group(0)
    inner = core[len(punct_prefix): len(core) - len(punct_suffix) if punct_suffix else len(core)]
    translated = FIXED_TRANSLATIONS.get(normalize_fixed_key(inner))
    if not translated:
        return ""
    return f"{leading}{punct_prefix}{translated}{punct_suffix}{trailing}"


def exact_japanese_menu_translation(text: str) -> str:
    if not text:
        return ""
    stripped = text.strip()
    direct = FIXED_TRANSLATIONS.get(stripped)
    if direct:
        return text.replace(stripped, direct, 1)
    use_suffix = "を使う"
    if stripped.endswith(use_suffix) and len(stripped) > len(use_suffix):
        item = stripped[: -len(use_suffix)]
        translated_item = FIXED_TRANSLATIONS.get(item)
        if translated_item:
            return text.replace(stripped, "使用" + translated_item, 1)
    read_suffix = "を読む"
    if stripped.endswith(read_suffix) and len(stripped) > len(read_suffix):
        item = stripped[: -len(read_suffix)]
        translated_item = FIXED_TRANSLATIONS.get(item)
        if translated_item:
            return text.replace(stripped, "阅读" + translated_item, 1)
    return ""


def exact_nonlinguistic_translation(text: str) -> str:
    if not text:
        return ""
    if NUMERIC_ONLY_RE.fullmatch(text) or CODE_LIKE_RE.fullmatch(text):
        return text
    if (
        RESOURCE_FILE_RE.fullmatch(text)
        or ANGLE_CONFIG_FRAGMENT_RE.fullmatch(text)
        or NUMERIC_ASSIGNMENT_RE.fullmatch(text)
    ):
        return text
    if _looks_like_non_japanese_resource(text):
        return text
    return ""


def apply_fixed_translations(text: str) -> str:
    if not text:
        return text
    result = text
    for src, tgt in sorted(FIXED_TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.fullmatch(r"[a-z0-9 ]+", src):
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(src)}(?![A-Za-z0-9_])", re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(src))
        result = pattern.sub(tgt, result)
    return result


def apply_source_conditioned_fixes(source: str, translated: str) -> str:
    if not source or not translated:
        return translated
    result = translated
    if "メスガキ" in source:
        result = result.replace("女童", "小恶女")
        result = result.replace("幼女", "小恶女")
    if "オーク" in source:
        result = result.replace("橡树", "兽人")
    if "ショーツ" in source:
        result = result.replace("短裤", "内裤")
    if "スパッツ" in source:
        result = result.replace("紧身裤", "运动短裤")
    if "手マン" in source:
        result = result.replace("手按摩", "指交")
        result = result.replace("手操", "指交")
    if "ショタレイプ" in source:
        result = result.replace("幼女性侵", "正太强奸")
        result = result.replace("小恶女性侵", "正太强奸")
    return result


def english_residue(text: str, original: str = "") -> list[str]:
    if not text:
        return []
    if exact_nonlinguistic_translation(text):
        return []
    residue: list[str] = []
    spans_to_ignore = [m.span() for m in VARIABLE_RE.finditer(text)]
    allowed_source_tokens = _preservable_source_tokens(original)

    def ignored_span(start: int, end: int) -> bool:
        return any(start >= s and end <= e for s, e in spans_to_ignore)

    for match in ENGLISH_WORD_RE.finditer(text):
        start, end = match.span()
        word = match.group(0)
        if ignored_span(start, end):
            continue
        if word.startswith("__") and word.endswith("__"):
            continue
        if word in allowed_source_tokens:
            continue
        if len(word) == 1:
            continue
        if (
            re.fullmatch(r"[wW]{2,}", word)
            and re.search(r"(?:[wW]|\uff57){2,}", original)
        ):
            continue
        if _is_key_context(text, start, end, word):
            continue
        residue.append(word)
    return residue


def suspicious_artifacts(text: str) -> list[str]:
    if not text:
        return []
    artifacts: list[str] = []
    for match in SUSPICIOUS_ARTIFACT_RE.finditer(text):
        artifacts.append(match.group(0))
    return artifacts


def translation_issues(original: str, translated: str, short_label: bool = False) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    original = original or ""
    translated = translated or ""
    if not translated.strip():
        issues.append({"type": "empty_translation", "message": "Translated text is empty."})
        return issues

    if translated == original and TARGET_SCRIPT_RE.search(original):
        issues.append({
            "type": "identical_japanese_source",
            "message": "Model output is identical to eligible Japanese source text.",
        })

    if (
        TARGET_SCRIPT_RE.search(original)
        and not re.search(r"[A-Za-z0-9\u3400-\u9fff]", translated)
    ):
        issues.append({
            "type": "model_refusal",
            "message": "Translation contains no lexical content for a linguistic source.",
        })

    if KANA_RE.search(translated):
        issues.append({
            "type": "untranslated_japanese",
            "message": "Japanese kana remain in the translated text.",
        })

    leaked_term_placeholders = LEAKED_TERM_PLACEHOLDER_RE.findall(translated)
    if leaked_term_placeholders:
        issues.append({
            "type": "term_placeholder_leak",
            "message": "Internal terminology placeholders remain in the translated text: "
            + ", ".join(sorted(set(leaked_term_placeholders))[:5]),
        })

    if HONORIFIC_SUFFIX_RE.search(original) and not CHINESE_HONORIFIC_MARKER_RE.search(translated):
        issues.append({
            "type": "honorific_rendering_review",
            "message": "A Japanese honorific appears to have lost its respect, intimacy, or hierarchy in Chinese.",
        })

    source_numbers = [match.group(0) for match in NUMERIC_TOKEN_RE.finditer(original)]
    target_numbers = [match.group(0) for match in NUMERIC_TOKEN_RE.finditer(translated)]
    if source_numbers != target_numbers:
        issues.append({
            "type": "numeric_preservation",
            "message": "Numeric values or parameters differ from the source sequence.",
        })

    source_line_breaks = re.findall(r"\r\n|\r|\n", original)
    target_line_breaks = re.findall(r"\r\n|\r|\n", translated)
    if source_line_breaks != target_line_breaks:
        issues.append({
            "type": "line_break_preservation",
            "message": "Line break count or encoding differs from the source.",
        })

    residue = english_residue(translated, original=original)
    if residue:
        issues.append({
            "type": "english_residue",
            "message": "English words remain in the translated text: " + ", ".join(sorted(set(residue))[:8]),
        })

    artifacts = suspicious_artifacts(translated)
    if artifacts:
        issues.append({
            "type": "suspicious_artifact",
            "message": "Suspicious punctuation or model artifacts remain in the translated text: " + ", ".join(artifacts[:8]),
        })

    if original and len(translated) > max(80, len(original) * 4):
        issues.append({
            "type": "length_expansion",
            "message": "Translated text is much longer than the source text.",
        })

    if short_label and len(translated) > max(24, len(original) * 3):
        issues.append({
            "type": "short_label_expansion",
            "message": "Short label was translated into an unusually long phrase.",
        })

    if VERSION_MARKER_RE.search(original) and not VERSION_MARKER_RE.search(translated):
        issues.append({
            "type": "version_marker_lost",
            "message": "Source version marker is missing from the translated text.",
        })

    marker_pairs = (
        (("「",), ("」",), "corner_bracket"),
        (("(", "（"), (")", "）"), "paren"),
    )
    for left_options, right_options, name in marker_pairs:
        original_has_marker = any(left in original for left in left_options) and any(right in original for right in right_options)
        translated_has_marker = any(left in translated for left in left_options) and any(right in translated for right in right_options)
        if original_has_marker and not translated_has_marker:
            issues.append({
                "type": "marker_lost",
                "message": f"Source {name} marker is missing from the translated text.",
            })
            break

    return issues


def _looks_like_non_japanese_resource(text: str) -> bool:
    stripped = text.strip()
    if not stripped or TARGET_SCRIPT_RE.search(stripped):
        return False
    if URL_FRAGMENT_RE.search(stripped):
        return True
    if "http" in stripped.lower():
        return True
    if NON_JP_PUNCT_ONLY_RE.fullmatch(stripped) or NUMERIC_LIST_RE.fullmatch(stripped):
        return True
    if STYLE_COMMAND_RE.fullmatch(stripped):
        return True
    if _looks_like_encoded_blob(stripped):
        return True
    if UPPER_CODE_RE.fullmatch(stripped):
        return True
    if TECHNICAL_MARKER_RE.search(stripped):
        return True
    if _looks_like_resource_command(stripped):
        return True
    if IDENTIFIER_RE.fullmatch(stripped):
        return len(stripped) <= 6 or "_" in stripped or "-" in stripped or CAMEL_OR_ALNUM_RE.search(stripped) is not None
    return False


def _looks_like_encoded_blob(stripped: str) -> bool:
    if len(stripped) < 50 or not ENCODED_BLOB_RE.fullmatch(stripped):
        return False
    has_alpha = re.search(r"[A-Za-z]", stripped) is not None
    has_digit = re.search(r"[0-9]", stripped) is not None
    return has_alpha and has_digit


def _looks_like_resource_command(stripped: str) -> bool:
    if COPYRIGHT_RE.search(stripped):
        return True
    tokens = stripped.split()
    if not (2 <= len(tokens) <= 12):
        return False
    if not all(RESOURCE_TOKEN_RE.fullmatch(token.strip("()[]{},")) for token in tokens):
        return False

    def codeish(token: str) -> bool:
        cleaned = token.strip("()[]{},")
        if not cleaned:
            return False
        return (
            "_" in cleaned
            or "-" in cleaned
            or cleaned.isupper()
            or CAMEL_OR_ALNUM_RE.search(cleaned) is not None
        )

    first_codeish = codeish(tokens[0])
    codeish_count = sum(1 for token in tokens if codeish(token) or re.fullmatch(r"[0-9０-９]+", token))
    return first_codeish or codeish_count >= 2


def _is_key_context(text: str, start: int, end: int, word: str) -> bool:
    normalized = normalize_fixed_key(word)
    if normalized not in KEY_NAMES and normalized not in AMBIGUOUS_KEY_NAMES:
        return False
    window = text[max(0, start - 8): min(len(text), end + 8)]
    if re.search(r"[\[\(<][A-Za-z0-9+ _-]{1,20}[\]\)>]", window):
        return True
    return bool(re.search(r"(按|键|按键|按钮|button|key|press)", window, re.IGNORECASE))


def _preservable_source_tokens(original: str) -> set[str]:
    if not original:
        return set()
    tokens: set[str] = set()
    for match in SOURCE_VERSION_TOKEN_RE.finditer(original):
        tokens.update(ENGLISH_WORD_RE.findall(match.group(0)))
    for match in CODE_EXPRESSION_RE.finditer(original):
        tokens.update(ENGLISH_WORD_RE.findall(match.group(0)))
    for match in CODE_IDENTIFIER_RE.finditer(original):
        tokens.add(match.group(0))
    for token in ENGLISH_WORD_RE.findall(original):
        if (
            any(character.isdigit() for character in token)
            or "_" in token
            or "-" in token
            or token.isupper()
            or normalize_fixed_key(token) not in FIXED_TRANSLATIONS
        ):
            tokens.add(token)
    return tokens


__all__ = [
    "FIXED_TRANSLATIONS",
    "apply_fixed_translations",
    "apply_source_conditioned_fixes",
    "english_residue",
    "exact_fixed_translation",
    "exact_japanese_menu_translation",
    "exact_nonlinguistic_translation",
    "suspicious_artifacts",
    "translation_issues",
]
