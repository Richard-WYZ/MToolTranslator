from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from translation.pollution import glossary_term_pollution_issues
from translation.terminology.candidate_policy import (
    KATAKANA_NAME_RE,
    build_aliases,
    classify_term,
    extract_terms,
    guess_target,
    has_enough_confirmed_evidence,
    is_ignored_term,
    is_preseed_worthy,
    is_valid_target,
    looks_like_name,
    score_term,
    source_target_compatible,
    strip_honorific,
)
from translation.terminology.store import read_glossary, write_glossary


class Glossary:
    _build_aliases = staticmethod(build_aliases)
    _classify_term = staticmethod(classify_term)
    _extract_terms = staticmethod(extract_terms)
    _guess_target = staticmethod(guess_target)
    _has_enough_confirmed_evidence = staticmethod(has_enough_confirmed_evidence)
    _is_ignored_term = staticmethod(is_ignored_term)
    _is_preseed_worthy = staticmethod(is_preseed_worthy)
    _is_valid_target = staticmethod(is_valid_target)
    _looks_like_name = staticmethod(looks_like_name)
    _score_term = staticmethod(score_term)
    _source_target_compatible = staticmethod(source_target_compatible)
    _strip_honorific = staticmethod(strip_honorific)

    def __init__(self, file_path: str | None = None):
        self.terms: dict[str, str] = {}
        self.candidates: dict[str, dict[str, Any]] = {}
        self.file_path = file_path or "glossary.json"
        self._prompt_cache = ""
        self._prompt_dirty = True
        self._mapping_cache: list[tuple[str, str, str, str]] = []
        self._mapping_dirty = True
        self._frozen = False
        self._frozen_version = ""
        self._frozen_mappings: list[tuple[str, str, str, str]] = []
        self.load()

    @classmethod
    def in_memory(cls) -> "Glossary":
        """Create an empty glossary without reading or writing a filesystem path."""
        glossary = cls.__new__(cls)
        glossary.terms = {}
        glossary.candidates = {}
        glossary.file_path = ""
        glossary._prompt_cache = ""
        glossary._prompt_dirty = True
        glossary._mapping_cache = []
        glossary._mapping_dirty = True
        glossary._frozen = False
        glossary._frozen_version = ""
        glossary._frozen_mappings = []
        return glossary

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> str:
        """Freeze enforced mappings while allowing candidate evidence collection."""
        if self._frozen:
            return self._frozen_version
        self._frozen_mappings = list(self.iter_mappings())
        self._frozen_version = self.version()
        self._frozen = True
        return self._frozen_version

    def thaw(self) -> None:
        self._frozen = False
        self._frozen_version = ""
        self._frozen_mappings = []
        self._mapping_dirty = True

    def add(self, japanese: str, chinese: str, term_type: str = "proper_noun") -> None:
        if self._frozen:
            raise RuntimeError("cannot change enforced glossary terms while frozen")
        japanese = (japanese or "").strip()
        chinese = (chinese or "").strip()
        if not japanese or not chinese:
            return
        self.terms[japanese] = chinese
        info = self.candidates.setdefault(japanese, {})
        info["status"] = "confirmed"
        info["target"] = chinese
        info["type"] = info.get("type") or term_type
        info["aliases"] = self._build_aliases(japanese, chinese, info["type"])
        self._prompt_dirty = True
        self._mapping_dirty = True

    def remove(self, japanese: str) -> None:
        if self._frozen:
            raise RuntimeError("cannot change enforced glossary terms while frozen")
        self.terms.pop(japanese, None)
        if japanese in self.candidates:
            self.candidates[japanese]["status"] = "rejected"
            self.candidates[japanese]["reject_reason"] = "removed"
        self._prompt_dirty = True
        self._mapping_dirty = True

    def promote(self, japanese: str, chinese: str | None = None) -> bool:
        candidate = self.candidates.get(japanese)
        target = (chinese or (candidate or {}).get("target") or "").strip()
        if not candidate or not target:
            return False
        if not self._passes_term_review(japanese, target, candidate.get("type", "proper_noun")):
            return False
        self.add(japanese, target, candidate.get("type", "proper_noun"))
        return True

    def lookup(self, text: str) -> str:
        if not self.terms or not text:
            return text
        result = text
        for src, tgt, _owner, _typ in self.iter_mappings():
            result = result.replace(src, tgt)
        return result

    def iter_mappings(self) -> list[tuple[str, str, str, str]]:
        if self._frozen:
            return self._frozen_mappings
        if not self._mapping_dirty:
            return self._mapping_cache
        mappings: list[tuple[str, str, str, str]] = []
        for src, tgt in self.terms.items():
            info = self.candidates.get(src, {}) or {}
            typ = str(info.get("type") or "proper_noun")
            mappings.append((src, tgt, src, typ))
            aliases = info.get("aliases", {}) if isinstance(info, dict) else {}
            if isinstance(aliases, dict):
                for alias_src, alias_tgt in aliases.items():
                    if alias_src and alias_tgt and alias_src != src:
                        mappings.append((str(alias_src), str(alias_tgt), src, typ))
        for source in self.candidates:
            if source not in self.terms and self.is_identified_person_name(source):
                mappings.append((source, source, source, "person"))
        mappings.sort(key=lambda item: len(item[0]), reverse=True)
        self._mapping_cache = mappings
        self._mapping_dirty = False
        return self._mapping_cache

    def find_hits(self, text: str) -> list[dict[str, str]]:
        hits: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        if not text:
            return hits
        for src, tgt, owner, typ in self.iter_mappings():
            if src in text and (src, tgt) not in seen:
                hits.append({"source": src, "target": tgt, "owner": owner, "type": typ})
                seen.add((src, tgt))
        return hits

    def protect_terms(self, text: str) -> tuple[str, list[tuple[str, str, str]]]:
        if not text:
            return text, []
        protected = text
        tokens: list[tuple[str, str, str]] = []
        for src, tgt, _owner, typ in self.iter_mappings():
            if src not in protected:
                continue
            token_type = "PERSON" if typ == "person" else "TERM"
            token = f"__{token_type}_{len(tokens)}__"
            protected = protected.replace(src, token)
            tokens.append((token, src, tgt))
        return protected, tokens

    @staticmethod
    def restore_terms(translated: str, tokens: list[tuple[str, str, str]]) -> str:
        restored = translated
        for token, _src, tgt in tokens:
            match = re.search(r"(?:TERM|PERSON)_(\d+)", token)
            idx = match.group(1) if match else ""
            restored = restored.replace(token, tgt)
            if idx:
                token_patterns = [
                    rf"_*\s*(?:TERM|PERSON)\s*[_\-\s]*{idx}\s*_+",
                    rf"\[\s*(?:TERM|PERSON)\s*[_\-\s]*{idx}\s*\]",
                    rf"\{{\{{\s*(?:TERM|PERSON)\s*[_\-\s]*{idx}\s*\}}\}}",
                    rf"<\s*(?:TERM|PERSON)\s*[_\-\s]*{idx}\s*>",
                ]
                for pattern in token_patterns:
                    restored = re.sub(pattern, tgt, restored, flags=re.IGNORECASE)
        return restored

    @staticmethod
    def missing_restored_terms(original: str, translated: str, tokens: list[tuple[str, str, str]]) -> list[dict[str, str]]:
        missing = []
        for token, src, tgt in tokens:
            if src in original and tgt not in translated:
                missing.append({"token": token, "source": src, "target": tgt})
        return missing

    def missing_hits(self, original: str, translated: str, hits: list[dict[str, str]]) -> list[dict[str, str]]:
        missing = []
        for hit in hits:
            if hit["source"] in original and hit["target"] not in translated:
                missing.append({"source": hit["source"], "target": hit["target"], "token": ""})
        return missing

    def prompt_for_hits(self, hits: list[dict[str, str]]) -> str:
        if not hits:
            return ""
        lines = []
        for hit in hits:
            lines.append(f"- {hit['source']} => {hit['target']} ({hit.get('type', 'proper_noun')})")
        return (
            "Confirmed terms appearing in the current text. Use these translations exactly; "
            "do not invent alternate names:\n" + "\n".join(lines)
        )

    def to_system_prompt(self) -> str:
        if not self._prompt_dirty:
            return self._prompt_cache
        if not self.terms:
            self._prompt_cache = ""
        else:
            lines = [f"- {src} => {tgt}" for src, tgt in sorted(self.terms.items(), key=lambda x: x[0])]
            self._prompt_cache = "Confirmed terminology:\n" + "\n".join(lines)
        self._prompt_dirty = False
        return self._prompt_cache

    def apply_post_translation(self, original: str, translated: str) -> str:
        if not original or not translated or not self.terms:
            return translated
        result = translated
        for src, tgt, _owner, _typ in self.iter_mappings():
            if src in original and tgt not in result:
                aliases = self._candidate_targets(src)
                for alias in sorted((a for a in aliases if a and a != tgt), key=len, reverse=True):
                    result = result.replace(alias, tgt)
                result = result.replace(src, tgt)
        return result

    def auto_extract(self, original: str, translated: str) -> list[dict[str, Any]]:
        confirmed: list[dict[str, Any]] = []
        if not original or not translated:
            return confirmed
        for term, evidence in self._extract_terms(original):
            if term in self.terms:
                continue
            if self._is_ignored_term(term):
                continue
            term_type = self._classify_term(term, original, evidence)
            was_identified_name = self.is_identified_kanji_name(term)
            score, score_evidence = self._score_term(term, original, evidence, term_type)
            info = self.candidates.setdefault(
                term,
                {
                    "count": 0,
                    "targets": {},
                    "target": "",
                    "status": "candidate",
                    "type": term_type,
                    "score": 0.0,
                    "evidence": [],
                },
            )
            if info.get("status") in ("confirmed", "official", "rejected"):
                continue
            info["count"] = int(info.get("count", 0)) + 1
            info["type"] = info.get("type") or term_type
            info["score"] = max(float(info.get("score", 0.0)), score)
            evidence_list = info.setdefault("evidence", [])
            for item in score_evidence:
                if item not in evidence_list:
                    evidence_list.append(item)
            if self.is_identified_kanji_name(term) != was_identified_name:
                self._mapping_dirty = True
            targets = info.setdefault("targets", {})
            target_guess = self._guess_target(term, translated, original, evidence)
            if target_guess:
                targets[target_guess] = int(targets.get(target_guess, 0)) + 1
            if targets:
                target, _freq = max(targets.items(), key=lambda item: item[1])
                info["target"] = target
                if not self._frozen and self._should_auto_confirm(term, target, info):
                    self.add(term, target, str(info.get("type") or term_type))
                    confirmed.append({
                        "source": term,
                        "target": target,
                        "aliases": list(targets.keys()) + list((self.candidates.get(term, {}) or {}).get("aliases", {}).values()),
                    })
                elif (
                    not self._frozen
                    and not self._passes_term_review(
                        term,
                        target,
                        str(info.get("type") or term_type),
                    )
                ):
                    info["status"] = "rejected"
                    info["reject_reason"] = "automatic_review_failed"
        return confirmed

    def preseed_from_sources(self, sources: list[str], min_count: int = 2) -> int:
        """Collect high-confidence source-only term candidates before translation.

        This does not confirm terms. It only gives repeated name-like candidates enough
        evidence that the first good translated target can be confirmed consistently.
        """
        observed: dict[str, dict[str, Any]] = {}
        for source in sources:
            if not source:
                continue
            for term, evidence in self._extract_terms(source):
                if self._is_ignored_term(term) or term in self.terms:
                    continue
                term_type = self._classify_term(term, source, evidence)
                score, score_evidence = self._score_term(term, source, evidence, term_type)
                if not self._is_preseed_worthy(term, evidence, score_evidence, term_type):
                    continue
                info = observed.setdefault(
                    term,
                    {
                        "count": 0,
                        "type": term_type,
                        "score": 0.0,
                        "evidence": set(),
                    },
                )
                info["count"] += 1
                info["score"] = max(float(info["score"]), score)
                info["evidence"].update(score_evidence)

        added = 0
        for term, seed in observed.items():
            if int(seed["count"]) < min_count:
                continue
            info = self.candidates.setdefault(
                term,
                {
                    "count": 0,
                    "targets": {},
                    "target": "",
                    "status": "candidate",
                    "type": seed["type"],
                    "score": 0.0,
                    "evidence": [],
                },
            )
            if info.get("status") in ("confirmed", "official", "rejected"):
                continue
            info["count"] = max(int(info.get("count", 0)), int(seed["count"]))
            info["type"] = info.get("type") or seed["type"]
            info["score"] = max(float(info.get("score", 0.0)), float(seed["score"]), 0.85)
            evidence_list = info.setdefault("evidence", [])
            for item in sorted(seed["evidence"] | {"whole_file_preseed"}):
                if item not in evidence_list:
                    evidence_list.append(item)
            added += 1
        if added:
            self._prompt_dirty = True
            self._mapping_dirty = True
        return added

    def export_json(self, file_path: str | None = None) -> None:
        target = file_path or self.file_path
        write_glossary(target, self.terms, self.candidates)

    def import_json(self, file_path: str) -> None:
        imported = read_glossary(file_path)
        if imported is not None:
            terms, candidates = imported
            self.terms.update(terms)
            self.candidates.update(candidates)
        self.prune_invalid_terms()
        self._prompt_dirty = True
        self._mapping_dirty = True

    def save(self) -> None:
        self.export_json(self.file_path)

    def load(self) -> None:
        loaded = read_glossary(self.file_path)
        if loaded is None:
            return
        self.terms, self.candidates = loaded
        self._normalize_candidate_statuses()
        self.prune_invalid_terms()
        self._prompt_dirty = True
        self._mapping_dirty = True

    def as_payload(self) -> dict[str, Any]:
        visible_candidates = {
            src: info for src, info in self.candidates.items()
            if self._candidate_is_visible(src, info)
        }
        return {"terms": self.terms, "candidates": visible_candidates}

    def version(self) -> str:
        """Return a stable version for terminology and evidence-backed name preservation."""
        if self._frozen:
            return self._frozen_version
        enforced = {
            source: {
                "target": target,
                "type": str((self.candidates.get(source, {}) or {}).get("type") or "proper_noun"),
                "status": str((self.candidates.get(source, {}) or {}).get("status") or "confirmed"),
            }
            for source, target in sorted(self.terms.items())
        }
        identified_kanji_names = sorted(
            source for source in self.candidates if self.is_identified_person_name(source)
        )
        if not enforced and not identified_kanji_names:
            return "0"
        serialized = json.dumps(
            {"terms": enforced, "identified_kanji_names": identified_kanji_names},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def is_identified_kanji_name(self, text: str) -> bool:
        """Return whether current-file evidence identifies a pure-kanji person name."""
        stripped = (text or "").strip()
        if not stripped or re.search("[\u3040-\u30ff]", stripped):
            return False
        info = self.candidates.get(stripped, {}) or {}
        evidence = {str(item) for item in info.get("evidence", []) or []}
        return str(info.get("type") or "") == "person" and bool(
            {"speaker_position", "standalone_line"} <= evidence
            or {"speaker_position", "quoted_name"} <= evidence
        )

    def is_identified_person_name(self, text: str) -> bool:
        """Recognize evidence-backed standalone names without inventing kana readings."""
        stripped = (text or "").strip()
        if not stripped or len(stripped) > 12 or re.search(r"[\s\r\n、。！？!?]", stripped):
            return False
        if self.is_identified_kanji_name(stripped):
            return True
        for suffix_length in (1, 2):
            if len(stripped) <= suffix_length:
                continue
            source = stripped[:-suffix_length]
            info = self.candidates.get(source, {}) or {}
            if not isinstance(info, dict) or str(info.get("type") or "") != "person":
                continue
            proposed_target = str(info.get("target") or "").strip()
            if proposed_target and not self._source_target_compatible(source, proposed_target, "person"):
                continue
            if int(info.get("count", 0) or 0) < 2:
                continue
            evidence = {str(item) for item in info.get("evidence", []) or []}
            strong_name_evidence = (
                {"speaker_position", "standalone_line"} <= evidence
                or {"speaker_position", "quoted_name"} <= evidence
            )
            if (
                "person_like" not in evidence
                or not strong_name_evidence
                or not stripped.startswith(source)
            ):
                continue
            suffix = stripped[-suffix_length:]
            if (
                0 < len(suffix) <= 2
                and re.fullmatch(r"[\u3040-\u309f]+", suffix)
                and len(re.findall(r"[\u3400-\u9fff]", source)) >= 2
            ):
                return True
        return False

    def prune_invalid_terms(self) -> int:
        removed = 0
        seen_targets: dict[str, str] = {}
        for src, tgt in list(self.terms.items()):
            typ = str((self.candidates.get(src, {}) or {}).get("type") or "proper_noun")
            info = self.candidates.get(src, {}) or {}
            invalid = (
                self._is_ignored_term(src)
                or not self._passes_term_review(src, tgt, typ)
                or not self._has_enough_confirmed_evidence(src, info)
                or (tgt in seen_targets and seen_targets[tgt] != src)
            )
            if invalid:
                self.terms.pop(src, None)
                if src in self.candidates:
                    self.candidates[src]["status"] = "rejected"
                    self.candidates[src]["reject_reason"] = "pruned_invalid_term"
                removed += 1
                continue
            seen_targets[tgt] = src
            if src in self.candidates:
                self.candidates[src]["aliases"] = self._build_aliases(src, tgt, typ)
        if removed:
            self._prompt_dirty = True
            self._mapping_dirty = True
        return removed

    def _passes_term_review(self, source: str, target: str, term_type: str) -> bool:
        if self._is_ignored_term(source) or not self._is_valid_target(target, source):
            return False
        if glossary_term_pollution_issues(source, target, term_type):
            return False
        if self._target_conflicts(source, target):
            return False
        if not self._source_target_compatible(source, target, term_type):
            return False
        return True

    def _should_auto_confirm(self, source: str, target: str, info: dict[str, Any]) -> bool:
        count = int(info.get("count", 0))
        score = float(info.get("score", 0.0))
        term_type = str(info.get("type") or "proper_noun")
        target_counts = {
            str(candidate): int(count)
            for candidate, count in (info.get("targets", {}) or {}).items()
            if candidate and int(count) > 0
        }
        target_evidence = int(target_counts.get(target, 0))
        total_target_evidence = sum(target_counts.values())
        if target_evidence < 2:
            return False
        if total_target_evidence and target_evidence / total_target_evidence < 0.75:
            return False
        epsilon = 1e-9
        if score + epsilon >= 0.85:
            pass
        elif count >= 2 and score + epsilon >= 0.65:
            pass
        else:
            return False
        if term_type not in ("person", "place", "item", "skill", "organization", "title", "proper_noun"):
            return False
        if not self._has_enough_confirmed_evidence(source, info):
            return False
        return self._passes_term_review(source, target, term_type)

    def _candidate_is_visible(self, source: str, info: dict[str, Any] | None) -> bool:
        if source in self.terms or not info:
            return False
        if info.get("status") in ("confirmed", "official", "rejected"):
            return False
        target = str(info.get("target") or "").strip()
        term_type = str(info.get("type") or "proper_noun")
        if target and not self._passes_term_review(source, target, term_type):
            return False
        score = float(info.get("score", 0.0))
        evidence = {str(item) for item in info.get("evidence", []) or []}
        if KATAKANA_NAME_RE.fullmatch(source) and score < 0.65:
            strong_evidence = {
                "speaker_position",
                "quoted_name",
                "compound_katakana_name",
                "katakana_title_name",
                "subject_katakana_name",
            }
            return bool(evidence & strong_evidence)
        return True

    def _target_conflicts(self, source: str, target: str) -> bool:
        for existing_source, existing_target in self.terms.items():
            if existing_source != source and existing_target == target:
                return True
        return False

    def _normalize_candidate_statuses(self) -> None:
        for info in self.candidates.values():
            if not isinstance(info, dict):
                continue
            status = info.get("status")
            if status == "pending_review":
                info["status"] = "candidate"
            elif status == "deleted":
                info["status"] = "rejected"
                info.setdefault("reject_reason", "removed")

    def _candidate_targets(self, source: str) -> list[str]:
        info = self.candidates.get(source, {}) or {}
        targets = info.get("targets", {})
        if isinstance(targets, dict):
            return [str(k) for k in targets.keys()]
        return []
