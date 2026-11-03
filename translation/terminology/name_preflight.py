"""Whole-file, cross-model confirmation of recurring katakana character names."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Callable

from translation.terminology.candidate_policy import (
    KATAKANA_NAME_RE,
    QUOTED_NAME_RE,
    SPEAKER_NAME_RE,
    SUBJECT_KATAKANA_RE,
)


NAME_PREFLIGHT_VERSION = "name-preflight-v2-focused-retry"
ModelCall = Callable[[str, str, str, dict[str, Any]], str]


def collect_name_proposals(sources: list[str], glossary: Any, limit: int = 64) -> list[dict[str, Any]]:
    """Collect recurring terms with independent name-like contexts, not translations."""
    observed: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "contexts": [], "evidence": set()}
    )
    for source in sources:
        if not source:
            continue
        stripped = source.strip()
        quoted = set(QUOTED_NAME_RE.findall(source))
        speaker_match = SPEAKER_NAME_RE.match(source)
        speaker = speaker_match.group(1) if speaker_match else ""
        subjects = set(SUBJECT_KATAKANA_RE.findall(source))
        seen: set[str] = set()
        for match in KATAKANA_NAME_RE.finditer(source):
            term = match.group(0)
            if (
                term in seen or term in glossary.terms or glossary._is_ignored_term(term)
                or (glossary.candidates.get(term, {}) or {}).get("status") == "rejected"
            ):
                continue
            seen.add(term)
            item = observed[term]
            item["count"] += 1
            evidence = item["evidence"]
            if stripped == term:
                evidence.add("standalone")
            if term in quoted:
                evidence.add("quoted")
            if term == speaker:
                evidence.add("speaker")
            if term in subjects:
                evidence.add("subject")
            kind = (
                "speaker" if term == speaker else
                "quoted" if term in quoted else
                "subject" if term in subjects else
                "standalone" if stripped == term else "other"
            )
            contexts = item["contexts"]
            sample = source[:200]
            score = (
                (3 if kind in {"speaker", "quoted", "subject"} else 0)
                + (2 if 12 <= len(source) <= 160 else 0)
                + (1 if re.search(r"[。！？!?、]", source) else 0)
                - (3 if re.search(re.escape(term) + r"\d", source) else 0)
                - (3 if "（演出）" in source or source.startswith("▼") else 0)
            )
            if sample not in {context[1] for context in contexts}:
                contexts.append((kind, sample, score))
                contexts.sort(key=lambda context: -context[2])
                del contexts[16:]
    proposals = []
    for term, item in observed.items():
        evidence = item["evidence"]
        if item["count"] < 5 or not (
            evidence & {"speaker", "quoted"}
            or {"standalone", "subject"} <= evidence
        ):
            continue
        # Distinct positions matter more than adjacent duplicate source rows.
        contexts = sorted(item["contexts"], key=lambda context: (
            {"speaker": 0, "quoted": 1, "subject": 2, "other": 3, "standalone": 4}.get(context[0], 5),
            -context[2],
        ))
        selected: list[str] = []
        kinds: set[str] = set()
        for kind, text, _score in contexts:
            if kind not in kinds and text not in selected:
                selected.append(text)
                kinds.add(kind)
            if len(selected) >= 4:
                break
        for _kind, text, _score in contexts:
            if len(selected) >= 4:
                break
            if text not in selected:
                selected.append(text)
        proposals.append({
            "source": term,
            "count": item["count"],
            "examples": selected,
        })
    return sorted(proposals, key=lambda item: (-item["count"], item["source"]))[:limit]


def _parse_decisions(response: str, expected: int) -> dict[int, dict[str, Any]]:
    text = str(response or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict) or set(data) != {"terms"} or not isinstance(data["terms"], list):
        return {}
    decisions: dict[int, dict[str, Any]] = {}
    for item in data["terms"]:
        if not isinstance(item, dict) or set(item) != {"i", "person", "target", "phonetic"}:
            return {}
        index = item["i"]
        if type(index) is not int or index < 0 or index >= expected or index in decisions:
            return {}
        if type(item["person"]) is not bool or type(item["phonetic"]) is not bool or not isinstance(item["target"], str):
            return {}
        decisions[index] = item
    return decisions if len(decisions) == expected else {}


def run_name_preflight(
    *,
    sources: list[str],
    glossary: Any,
    primary_model: str,
    verifier_model: str,
    call_model: ModelCall,
) -> list[dict[str, str]]:
    """Confirm only matching phonetic name decisions from distinct models."""
    if not primary_model or not verifier_model or primary_model == verifier_model:
        return []
    proposals = collect_name_proposals(sources, glossary)
    confirmed: list[dict[str, str]] = []
    retry_items: list[dict[str, Any]] = []

    def decide_group(group: list[dict[str, Any]], *, focused: bool) -> None:
        payload = {"terms": [dict(item, i=index) for index, item in enumerate(group)]}
        instructions = (
            "Identify character/person names from these Japanese game contexts. "
            "Common UI words, roles, verbs, and ordinary nouns are not names even when capitalized or standalone. "
            "For a person name, propose a concise Chinese phonetic transliteration; never translate its literal meaning "
            "(for example, do not turn a name meaning sun into the common noun sunlight). "
            "Do not append honorifics or invent a surname. If uncertain, person=false. "
            'Return JSON only: {"terms":[{"i":0,"person":true,"target":"译名","phonetic":true}]}. '
            "Use an empty target and phonetic=false for nonnames."
        )
        if focused:
            instructions += " Consider each term independently using all its examples; transliterate a character's spoken name, not its dictionary meaning."
        options = {"temperature": 0, "num_predict": 2048}
        try:
            primary = _parse_decisions(
                call_model(primary_model, json.dumps(payload, ensure_ascii=False), instructions, options),
                len(group),
            )
            if not primary:
                return
            verifier = _parse_decisions(
                call_model(verifier_model, json.dumps(payload, ensure_ascii=False),
                           instructions,
                           options),
                len(group),
            )
        except Exception:
            return
        for index, item in enumerate(group):
            first = primary[index]
            second = verifier.get(index)
            source = item["source"]
            agreed_person = bool(second and all(
                decision["person"] and decision["phonetic"]
                for decision in (first, second)
            ))
            target = first["target"].strip()
            if (
                not agreed_person
                or target != second["target"].strip()
                or not re.fullmatch(r"[\u3400-\u9fff]{1,8}", target)
                or not glossary._passes_term_review(source, target, "person")
            ):
                if first["person"] or (second and second["person"]):
                    info = glossary.candidates.setdefault(source, {})
                    info["status"] = "needs_review"
                    info["type"] = "person"
                    info["count"] = max(int(info.get("count", 0)), int(item["count"]))
                    info["evidence"] = list(dict.fromkeys([
                        *(info.get("evidence", []) or []), "preflight_name_conflict",
                    ]))
                    if not focused and int(item["count"]) >= 10:
                        retry_items.append(item)
                continue
            info = glossary.candidates.setdefault(source, {})
            info["count"] = max(int(info.get("count", 0)), int(item["count"]))
            info["type"] = "person"
            info["evidence"] = list(dict.fromkeys([
                *(info.get("evidence", []) or []), "preflight_verified_person",
            ]))
            glossary.add(source, target, "person")
            confirmed.append({"source": source, "target": target})

    for start in range(0, len(proposals), 10):
        decide_group(proposals[start:start + 10], focused=False)
    # Large mixed-name batches can produce inconsistent decisions even at
    # temperature zero. Give frequent, disputed names one smaller independent
    # cross-model pass; a disagreement still remains reviewable, not enforced.
    for start in range(0, min(len(retry_items), 16), 4):
        decide_group(retry_items[start:start + 4], focused=True)
    return confirmed


__all__ = ["NAME_PREFLIGHT_VERSION", "collect_name_proposals", "run_name_preflight"]
