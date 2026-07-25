from __future__ import annotations

import re


JP_NAME_RE = re.compile("[\u30a1-\u30fa\u30fc]{2,20}|[\u3400-\u4dbf\u4e00-\u9fff]{2,8}")
JP_PARTICLE_RE = re.compile("(\u306f|\u304c|\u3092|\u306b|\u3078|\u3068|\u3067|\u306e|\u3082|\u3084|\u304b|\u304b\u3089|\u307e\u3067|\u3088\u308a)")
CN_HAN_RE = re.compile("[\u4e00-\u9fff]")

COMMON_SOURCE_TERMS = {
    "\u4eba\u9593",
    "\u4eba\u9593\u65cf",
    "\u5973",
    "\u7537",
    "\u5b50\u4f9b",
    "\u5c11\u5973",
    "\u5c11\u5e74",
    "\u5974\u96b7",
    "\u5974\u96b7\u7a2e\u65cf",
    "\u5974\u96b7\u5e02\u5834",
    "\u5974\u96b7\u7d0b",
    "\u5e02\u5834",
    "\u7a2e\u65cf",
    "\u56fd",
    "\u738b\u56fd",
    "\u8857",
    "\u753a",
    "\u6751",
    "\u68ee",
    "\u57ce",
    "\u529b",
    "\u30ec\u30d9\u30eb",
}

COMMON_CN_TITLES = {
    "\u5973\u795e",
    "\u795e\u5b50",
    "\u5fa1\u5b50",
    "\u5723\u5973",
    "\u5723\u5b50",
    "\u56fd\u738b",
    "\u738b\u5b50",
    "\u516c\u4e3b",
    "\u5973\u738b",
    "\u738b\u5973",
    "\u9b54\u738b",
    "\u8001\u5e08",
    "\u5b66\u8005",
    "\u58eb\u5175",
    "\u9a91\u58eb",
}

CN_HONORIFICS = ("\u5927\u4eba", "\u5148\u751f", "\u5c0f\u59d0", "\u965b\u4e0b", "\u6bbf\u4e0b")
CONTEXTUAL_MARKERS = (
    "\u6210\u4e3a",
    "\u53d8\u6210",
    "\u4f5c\u4e3a",
    "\u7684",
    "\u4e3a\u4e86",
    "\u56e0\u4e3a",
    "\u5927\u4eba",
    "\u965b\u4e0b",
    "\u6bbf\u4e0b",
)


def glossary_term_pollution_issues(source: str, target: str, term_type: str = "") -> list[dict[str, str]]:
    """Validate a proposed glossary mapping before it can become enforced policy."""
    source = (source or "").strip()
    target = (target or "").strip()
    issues: list[dict[str, str]] = []
    if not source or not target:
        return issues

    if _source_is_common_term(source) and _target_has_contextual_expansion(source, target):
        issues.append({
            "type": "glossary_contextual_expansion",
            "message": f"Common source term {source!r} maps to an over-specific contextual phrase {target!r}.",
        })

    if _source_is_common_term(source) and _target_honorific_names(target):
        issues.append({
            "type": "glossary_proper_name_pollution",
            "message": f"Common source term {source!r} maps to a target containing an unsupported proper name.",
        })

    if len(source) <= 3 and len(target) > max(6, len(source) * 3) and _target_has_contextual_expansion(source, target):
        issues.append({
            "type": "glossary_short_term_expansion",
            "message": f"Short glossary term {source!r} expands into a long contextual phrase.",
        })

    return _dedupe_issues(issues)


def translation_pollution_issues(
    source: str,
    translated: str,
    *,
    glossary_mappings: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Detect glossary/context pollution in an accepted translation."""
    source = source or ""
    translated = translated or ""
    issues: list[dict[str, str]] = []
    if not source.strip() or not translated.strip():
        return issues

    for mapping in glossary_mappings or []:
        src = str(mapping.get("source") or "")
        tgt = str(mapping.get("target") or "")
        typ = str(mapping.get("type") or "")
        if not src or not tgt or tgt not in translated or src in source:
            continue
        if (
            typ in ("person", "place", "organization", "title")
            and _target_used_as_honorific_name(tgt, translated)
            and not _source_contains_mapping_for_target(source, tgt, glossary_mappings or [])
        ):
            issues.append({
                "type": "unsupported_glossary_name",
                "message": f"Target contains confirmed term {tgt!r}, but source does not contain {src!r}.",
            })

    honorific_names = _target_honorific_names(translated)
    if honorific_names and not _source_can_support_target_names(source, honorific_names, glossary_mappings or []):
        issues.append({
            "type": "unsupported_proper_name",
            "message": "Translated text contains a proper-name honorific without source evidence: "
            + ", ".join(honorific_names[:5]),
        })

    if _source_is_common_term(source.strip()) and _target_has_contextual_expansion(source, translated):
        issues.append({
            "type": "contextual_term_pollution",
            "message": "Common source term was translated as an over-specific contextual phrase.",
        })

    return _dedupe_issues(issues)


def _source_is_common_term(source: str) -> bool:
    stripped = source.strip()
    if stripped in COMMON_SOURCE_TERMS:
        return True
    if 1 < len(stripped) <= 4 and not JP_PARTICLE_RE.search(stripped):
        return True
    return False


def _target_has_contextual_expansion(source: str, target: str) -> bool:
    han_count = len(CN_HAN_RE.findall(target))
    source_len = max(1, len(source.strip()))
    if source.strip() in COMMON_SOURCE_TERMS:
        return han_count > max(5, source_len * 2) and any(marker in target for marker in CONTEXTUAL_MARKERS)
    return han_count > max(6, source_len * 3) and any(marker in target for marker in CONTEXTUAL_MARKERS)


def _target_honorific_names(text: str) -> list[str]:
    names: list[str] = []
    for suffix in CN_HONORIFICS:
        start = 0
        while True:
            idx = text.find(suffix, start)
            if idx < 0:
                break
            prefix = _han_prefix(text[:idx])
            for length in range(min(4, len(prefix)), 1, -1):
                candidate = prefix[-length:]
                if candidate and candidate not in COMMON_CN_TITLES:
                    names.append(candidate + suffix)
                    break
            start = idx + len(suffix)
    return sorted(set(names))


def _han_prefix(text: str) -> str:
    chars: list[str] = []
    for ch in reversed(text):
        if CN_HAN_RE.fullmatch(ch):
            chars.append(ch)
        else:
            break
    return "".join(reversed(chars))


def _source_can_support_target_names(
    source: str,
    target_names: list[str],
    glossary_mappings: list[dict[str, str]],
) -> bool:
    for mapping in glossary_mappings:
        src = str(mapping.get("source") or "")
        tgt = str(mapping.get("target") or "")
        if src and tgt and src in source and any(name.startswith(tgt) for name in target_names):
            return True
    return _source_has_name_evidence(source)


def _source_has_name_evidence(source: str) -> bool:
    if not JP_NAME_RE.search(source):
        return False
    if source.strip() in COMMON_SOURCE_TERMS:
        return False
    return True


def _target_used_as_honorific_name(target: str, translated: str) -> bool:
    if not target or target in COMMON_CN_TITLES:
        return False
    if any((target + suffix) in translated for suffix in CN_HONORIFICS):
        return True
    return any(name.startswith(target) for name in _target_honorific_names(translated))


def _source_contains_mapping_for_target(source: str, target: str, mappings: list[dict[str, str]]) -> bool:
    for mapping in mappings:
        src = str(mapping.get("source") or "")
        tgt = str(mapping.get("target") or "")
        if src and tgt == target and src in source:
            return True
    return False


def _dedupe_issues(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.get("type", ""), issue.get("message", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


__all__ = ["glossary_term_pollution_issues", "translation_pollution_issues"]
