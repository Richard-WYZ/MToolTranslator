from __future__ import annotations

import re
from collections import Counter
from typing import Any


CANDIDATE_POLICY_VERSION = "term-candidates-v4-strong-name-evidence"
JP_RE = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+")
KANJI_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff]")
KATAKANA_NAME_RE = re.compile("[\u30a1-\u30fa\u30fc]{2,20}")
KATAKANA_COMPOUND_NAME_RE = re.compile(
    "[\u30a1-\u30fa\u30fc]{2,20}[\u30fb\uff65][\u30a1-\u30fa\u30fc]{2,20}"
    "(?:[\u30fb\uff65][\u30a1-\u30fa\u30fc]{2,20})*"
)
KATAKANA_TITLE_NAME_RE = re.compile("[\u30a1-\u30fa\u30fc]{2,20}[\u7537\u5973]")
SUBJECT_KATAKANA_RE = re.compile("([\u30a1-\u30fa\u30fc]{3,20})(?:\u306f|\u304c)")
HIRAGANA_ONLY_RE = re.compile("^[\u3040-\u309f]+$")
SPEAKER_NAME_RE = re.compile(r"^\s*([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]{2,12})\s*[\uff1a:]")
QUOTED_NAME_RE = re.compile(
    r"[\u300c\u300e\u3010\[]\s*([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]{2,12})\s*"
    r"[\u300d\u300f\u3011\]]"
)
JP_PARTICLE_RE = re.compile("(\u306f|\u304c|\u3092|\u306b|\u3078|\u3068|\u3067|\u306e|\u3082|\u3084|\u304b|\u304b\u3089|\u307e\u3067|\u3088\u308a)")
NOISY_RE = re.compile(r"^[\s\d\W_]+$", re.UNICODE)
CN_CHUNK_RE = re.compile("[\u4e00-\u9fff]{1,12}")

HONORIFICS = (
    "\u3055\u3093", "\u3061\u3083\u3093", "\u541b", "\u304f\u3093",
    "\u69d8", "\u3055\u307e", "\u5148\u751f", "\u5148\u8f29",
)
CN_SUFFIXES = (
    "\u6765\u4e86", "\u6765\u5566", "\u8bf4\u9053", "\u8bf4\u7740", "\u8bf4", "\u95ee\u9053",
    "\u95ee", "\u56de\u7b54", "\u7b54\u9053", "\u7b11\u9053", "\u558a\u9053", "\u53eb\u9053",
    "\u770b\u7740", "\u770b\u5411", "\u8d70\u6765", "\u8d70\u4e86", "\u8d77\u6765", "\u8fc7\u53bb",
    "\u8fd9\u91cc", "\u90a3\u91cc", "\u4e86", "\u5462", "\u554a", "\u5440", "\u5417", "\u5427",
)

IGNORED_TERMS = {
    "\u30ad\u30e2",
    "\u30ad\u30e2\u3044",
    "\u30a2\u30f3\u30bf",
    "\u30ca\u30ec\u30fc\u30b7\u30e7\u30f3",
    "\u30e2\u30ce\u30ed\u30fc\u30b0",
    "\u5730\u306e\u6587",
}
BAD_TARGETS = {
    "\u65c1\u767d",
    "\u4ec0\u4e48",
    "\u4f60", "\u6211", "\u4ed6", "\u5979",
    "\u8fd9\u4e2a", "\u90a3\u4e2a", "\u90a3\u91cc", "\u8fd9\u91cc",
}
BAD_TARGET_SUBSTRINGS = (
    "\u6211\u8981",
    "\u4f60\u8981",
    "\u6740\u4e86\u4f60",
    "\u6740\u4e86\u6211",
    "\u770b\u7740",
    "\u8d70\u5411",
    "\u8d70\u8fc7",
    "\u8bf4\u9053",
    "\u95ee\u9053",
)
PARTICLES = {
    "\u306f", "\u304c", "\u3092", "\u306b", "\u3078", "\u3068", "\u3067", "\u306e",
    "\u3082", "\u3084", "\u304b", "\u304b\u3089", "\u307e\u3067", "\u3088\u308a",
    "\u3067\u3059", "\u307e\u3059", "\u3057\u305f", "\u3059\u308b", "\u3053\u308c",
    "\u305d\u308c", "\u3042\u308c", "\u3053\u3053", "\u305d\u3053", "\u3042\u305d\u3053",
    "\u3042\u306a\u305f", "\u5f7c", "\u5f7c\u5973",
}


def has_enough_confirmed_evidence(source: str, info: dict[str, Any]) -> bool:
    term_type = str((info or {}).get("type") or "proper_noun")
    if term_type in ("place", "item", "skill", "organization", "title"):
        return True
    if KATAKANA_TITLE_NAME_RE.fullmatch(source) or KATAKANA_COMPOUND_NAME_RE.fullmatch(source):
        return True
    evidence = {str(item) for item in (info or {}).get("evidence", []) or []}
    if evidence & {"compound_katakana_name", "katakana_title_name"}:
        return True
    if "speaker_position" in evidence and KATAKANA_NAME_RE.fullmatch(source):
        return True
    is_kanji_name = len(KANJI_RE.findall(source)) >= 2 and not KATAKANA_NAME_RE.search(source)
    return bool(
        is_kanji_name
        and (
            {"speaker_position", "standalone_line"} <= evidence
            or {"speaker_position", "quoted_name"} <= evidence
            or {"quoted_name", "standalone_line"} <= evidence
        )
    )


def strip_honorific(term: str) -> str:
    for suffix in HONORIFICS:
        if term.endswith(suffix) and len(term) > len(suffix) + 1:
            return term[: -len(suffix)]
    return term


def is_ignored_term(term: str) -> bool:
    if not term:
        return True
    if term in IGNORED_TERMS or term in PARTICLES:
        return True
    return bool(HIRAGANA_ONLY_RE.match(term) and len(term) <= 4)


def extract_terms(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    stripped = text.strip()
    compound_spans: list[tuple[int, int]] = []
    for match in KATAKANA_COMPOUND_NAME_RE.finditer(text):
        found.append((match.group(0).strip(), "katakana_compound"))
        compound_spans.append(match.span())

    def inside_compound(span: tuple[int, int]) -> bool:
        start, end = span
        return any(start >= compound_start and end <= compound_end for compound_start, compound_end in compound_spans)

    if (
        stripped
        and JP_RE.fullmatch(stripped)
        and 2 <= len(stripped) <= 16
        and not JP_PARTICLE_RE.search(stripped)
        and not HIRAGANA_ONLY_RE.match(stripped)
    ):
        found.append((stripped, "standalone"))
    speaker = SPEAKER_NAME_RE.match(text)
    if speaker and _speaker_prefix_has_dialogue_evidence(text, speaker):
        found.append((speaker.group(1).strip(), "speaker"))
    for match in QUOTED_NAME_RE.findall(text):
        found.append((match.strip(), "quoted"))
    for match in KATAKANA_TITLE_NAME_RE.finditer(text):
        if not inside_compound(match.span()):
            found.append((match.group(0).strip(), "katakana_title"))
    for match in SUBJECT_KATAKANA_RE.finditer(text):
        span = (match.start(1), match.end(1))
        if not inside_compound(span):
            found.append((match.group(1).strip(), "subject_katakana"))
    for match in KATAKANA_NAME_RE.finditer(text):
        if not inside_compound(match.span()):
            found.append((match.group(0).strip(), "katakana"))

    counts: Counter[str] = Counter()
    for match in JP_RE.finditer(text):
        if not inside_compound(match.span()):
            counts[match.group(0)] += 1
    for term in counts:
        term = term.strip()
        if not (2 <= len(term) <= 12) or term in PARTICLES or NOISY_RE.match(term):
            continue
        if JP_PARTICLE_RE.search(term):
            for part in (part for part in JP_PARTICLE_RE.split(term) if part and part not in PARTICLES):
                if 2 <= len(part) <= 12:
                    found.append((part, "chunk"))
            continue
        found.append((term, "chunk"))

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for term, evidence in found:
        clean = strip_honorific(term)
        for item in (term, clean):
            if item and item not in seen and not is_ignored_term(item):
                deduped.append((item, evidence))
                seen.add(item)
    return deduped


def _speaker_prefix_has_dialogue_evidence(
    text: str,
    match: re.Match[str],
) -> bool:
    """Distinguish speaker labels from UI/event category prefixes."""
    tail = text[match.end():].strip()
    if not tail:
        return False
    if any(
        marker in tail
        for marker in ("\u3002", "\uff01", "\uff1f", "!", "?", "\u300c", "\u300d", "\n")
    ):
        return True
    if JP_PARTICLE_RE.search(tail):
        return True
    return bool(
        re.search(
            r"(?:\u3067\u3059|\u307e\u3059|\u3060|\u3088|\u306d|\u305e|\u305c|\u3055|\u304b|"
            r"\u305f|\u3063\u305f|\u3057\u305f|\u304d\u305f|\u308b|\u306a\u3044|\u305f\u3044)$",
            tail,
        )
    )


def looks_like_name(term: str, original: str = "", evidence: str = "") -> bool:
    if not term or is_ignored_term(term) or HIRAGANA_ONLY_RE.match(term):
        return False
    if evidence in ("speaker", "quoted", "subject_katakana"):
        return True
    if evidence == "katakana_compound" and KATAKANA_COMPOUND_NAME_RE.fullmatch(term):
        return True
    if KATAKANA_TITLE_NAME_RE.fullmatch(term):
        return True
    if 3 <= len(term) <= 5 and len(KANJI_RE.findall(term)) >= 2 and not any(ch in term for ch in "\u306e\u306f\u304c\u3092\u306b"):
        return True
    speaker = SPEAKER_NAME_RE.match(original)
    return bool(speaker and speaker.group(1) == term)


def classify_term(term: str, original: str, evidence: str) -> str:
    if looks_like_name(term, original, evidence):
        return "person"
    if any(marker in term for marker in ("\u738b\u56fd", "\u5b66\u5712", "\u5b66\u6821", "\u753a", "\u6751", "\u57ce", "\u90fd", "\u5e02")):
        return "place"
    if any(marker in term for marker in ("\u5263", "\u5200", "\u69cd", "\u6756", "\u9283", "\u85ac", "\u9b54\u6cd5", "\u30bd\u30fc\u30c9")):
        return "item"
    return "proper_noun"


def score_term(term: str, original: str, evidence: str, term_type: str) -> tuple[float, list[str]]:
    del original
    score = 0.0
    reasons: list[str] = []
    evidence_scores = {
        "standalone": (0.35, "standalone_line"),
        "speaker": (0.35, "speaker_position"),
        "quoted": (0.2, "quoted_name"),
        "katakana_compound": (0.35, "compound_katakana_name"),
        "katakana_title": (0.35, "katakana_title_name"),
        "subject_katakana": (0.35, "subject_katakana_name"),
    }
    if evidence in evidence_scores:
        increment, reason = evidence_scores[evidence]
        score += increment
        reasons.append(reason)
    if term_type == "person":
        score += 0.3
        reasons.append("person_like")
    elif term_type in ("place", "item"):
        score += 0.25
        reasons.append(f"{term_type}_like")
    if KATAKANA_NAME_RE.fullmatch(term):
        score += 0.15
        reasons.append("katakana_name")
    if len(term) <= 1:
        score -= 0.5
        reasons.append("too_short")
    if HIRAGANA_ONLY_RE.match(term):
        score -= 0.4
        reasons.append("hiragana_only")
    return max(0.0, min(1.0, score)), reasons


def is_valid_target(target: str, source: str = "") -> bool:
    target = (target or "").strip()
    if not target or target in BAD_TARGETS or any(part in target for part in BAD_TARGET_SUBSTRINGS):
        return False
    if len(target) > 1 and re.search("[\u6211\u4f60\u4ed6\u5979]", target):
        return False
    if len(target) > 10 or re.search("[A-Za-z]{2,}", target):
        return False
    if source and KATAKANA_COMPOUND_NAME_RE.fullmatch(source):
        parts = re.split("[\u30fb\uff65]", source)
        if len(KANJI_RE.findall(target)) < len(parts) + 1:
            return False
    return True


def is_preseed_worthy(term: str, evidence: str, score_evidence: list[str], term_type: str) -> bool:
    strong_evidence = {
        "speaker_position",
        "quoted_name",
        "compound_katakana_name",
        "katakana_title_name",
    }
    if set(score_evidence) & strong_evidence:
        return True
    if term_type == "person" and KATAKANA_COMPOUND_NAME_RE.fullmatch(term):
        return True
    if evidence == "standalone" and len(KANJI_RE.findall(term)) == len(term) and 2 <= len(term) <= 12:
        return True
    return evidence in ("speaker", "quoted", "katakana_compound", "katakana_title")


def source_target_compatible(source: str, target: str, term_type: str) -> bool:
    source_kanji = set(KANJI_RE.findall(source))
    target_chars = set(KANJI_RE.findall(target))
    if len(source_kanji) >= 2 and target_chars and source_kanji.isdisjoint(target_chars):
        return False
    return not (term_type == "person" and target in BAD_TARGETS)


def guess_target(term: str, translated: str, original: str = "", evidence: str = "") -> str:
    """Guess a target only when the source position gives a safe local alignment."""
    stripped = (original or "").strip()
    speaker = SPEAKER_NAME_RE.match(original or "")
    aligned = stripped in (term, strip_honorific(term))
    if speaker and strip_honorific(speaker.group(1).strip()) == strip_honorific(term):
        aligned = True
    if evidence not in ("standalone", "speaker", "katakana_compound", "katakana_title") or not aligned:
        return ""
    for chunk in CN_CHUNK_RE.findall(translated):
        cleaned = chunk
        changed = True
        while changed:
            changed = False
            for suffix in CN_SUFFIXES:
                if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 1:
                    cleaned = cleaned[: -len(suffix)]
                    changed = True
                    break
        if 1 < len(cleaned) <= 10:
            return cleaned
    return ""


def build_aliases(source: str, target: str, term_type: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    base = strip_honorific(source)
    if base != source:
        aliases[source] = target
        aliases[base] = target
    if term_type == "person":
        for suffix in HONORIFICS:
            aliases[f"{base}{suffix}"] = target
        if 3 <= len(base) <= 5 and len(KANJI_RE.findall(base)) >= 2 and len(target) >= 2:
            if len(base) == 3:
                source_parts = [base[:1], base[1:]]
                target_parts = [target[:1], target[1:]]
            else:
                source_parts = [base[:2], base[2:]]
                target_parts = [target[:2], target[2:]]
            for source_part, target_part in zip(source_parts, target_parts):
                if source_part and target_part:
                    aliases[source_part] = target_part
                    for suffix in HONORIFICS:
                        aliases[f"{source_part}{suffix}"] = target_part
    return aliases


__all__ = [
    "CANDIDATE_POLICY_VERSION",
    "KATAKANA_NAME_RE",
    "build_aliases",
    "classify_term",
    "extract_terms",
    "guess_target",
    "has_enough_confirmed_evidence",
    "is_ignored_term",
    "is_preseed_worthy",
    "is_valid_target",
    "looks_like_name",
    "score_term",
    "source_target_compatible",
    "strip_honorific",
]
