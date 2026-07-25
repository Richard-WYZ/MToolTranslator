from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable


COMPOSITION_VERSION = "mtool-line-dependencies-v4"
_LINE_BREAK_RE = re.compile(r"(\r\n|\r|\n)")
_LAYOUT_WHITESPACE = " \t\u3000"


@dataclass(frozen=True)
class CompositionPiece:
    literal: str = ""
    child_index: int | None = None
    prefix: str = ""
    suffix: str = ""


@dataclass(frozen=True)
class CompositionContext:
    text: str
    line: int
    parent_index: int


@dataclass(frozen=True)
class CompositionEntry:
    parent_index: int
    source: str
    pieces: tuple[CompositionPiece, ...]
    dependency_indexes: tuple[int, ...]


@dataclass
class MToolCompositionPlan:
    entries: dict[int, CompositionEntry]
    contexts_by_child: dict[int, tuple[CompositionContext, ...]]
    canonical_parent_by_child: dict[int, int]
    version: str = COMPOSITION_VERSION

    def is_composed_parent(self, index: int) -> bool:
        return index in self.entries

    def contexts_for_child(self, index: int) -> list[dict[str, Any]]:
        return [
            {
                "text": context.text,
                "line": context.line,
                "parent_index": context.parent_index,
            }
            for context in self.contexts_by_child.get(index, ())
        ]

    def repair_parent_for_child(self, index: int) -> CompositionEntry | None:
        """Select the smallest deterministic multiline parent containing a child."""
        cached_parent = self.canonical_parent_by_child.get(index)
        if cached_parent is not None:
            return self.entries.get(cached_parent)
        candidates = [
            entry
            for entry in self.entries.values()
            if index in entry.dependency_indexes
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda entry: (len(entry.source), entry.parent_index))

    @staticmethod
    def extract_child_translations(
        entry: CompositionEntry,
        translated_parent: str,
    ) -> dict[int, str]:
        """Extract line-aligned child translations from an exactly shaped parent."""
        translated_parts = _LINE_BREAK_RE.split(str(translated_parent or ""))
        if len(translated_parts) != len(entry.pieces):
            return {}

        extracted: dict[int, str] = {}
        for source_piece, translated_piece in zip(entry.pieces, translated_parts):
            if source_piece.child_index is None:
                if _has_line_break(source_piece.literal):
                    if translated_piece != source_piece.literal:
                        return {}
                elif source_piece.literal.strip(_LAYOUT_WHITESPACE):
                    return {}
                continue

            line_translation = translated_piece.strip(_LAYOUT_WHITESPACE)
            if not line_translation or _has_line_break(line_translation):
                return {}
            existing = extracted.get(source_piece.child_index)
            if existing is not None and existing != line_translation:
                return {}
            extracted[source_piece.child_index] = line_translation

        return extracted


def build_mtool_composition_plan(
    items: list[tuple[Any, Any]],
    *,
    context_max_chars: int = 1200,
    context_max_per_item: int = 2,
) -> MToolCompositionPlan:
    """Build safe single-line dependencies for exactly recomposable multiline keys."""
    sources = [str(key) for key, _value in items]
    standalone_by_normalized: dict[str, list[int]] = {}
    for index, source in enumerate(sources):
        if _has_line_break(source):
            continue
        normalized = _strip_layout(source)
        if normalized:
            standalone_by_normalized.setdefault(normalized, []).append(index)

    entries: dict[int, CompositionEntry] = {}
    pending_contexts: dict[int, list[CompositionContext]] = {}
    for parent_index, source in enumerate(sources):
        if not _has_line_break(source):
            continue
        pieces: list[CompositionPiece] = []
        dependency_indexes: list[int] = []
        line_number = 0
        valid = True
        split_parts = _LINE_BREAK_RE.split(source)
        for part_index, part in enumerate(split_parts):
            if part_index % 2:
                pieces.append(CompositionPiece(literal=part))
                continue

            line_number += 1
            prefix, core, suffix = _split_layout(part)
            if not core:
                pieces.append(CompositionPiece(literal=part))
                continue
            matches = standalone_by_normalized.get(core, [])
            if len(matches) != 1:
                valid = False
                break
            child_index = matches[0]
            if child_index == parent_index:
                valid = False
                break
            pieces.append(
                CompositionPiece(
                    child_index=child_index,
                    prefix=prefix,
                    suffix=suffix,
                )
            )
            dependency_indexes.append(child_index)
            context = _bounded_context(
                split_parts,
                target_line=line_number,
                parent_index=parent_index,
                max_chars=max(0, int(context_max_chars)),
            )
            if context is not None:
                pending_contexts.setdefault(child_index, []).append(context)

        if valid and dependency_indexes:
            entries[parent_index] = CompositionEntry(
                parent_index=parent_index,
                source=source,
                pieces=tuple(pieces),
                dependency_indexes=tuple(dict.fromkeys(dependency_indexes)),
            )

    valid_parent_indexes = set(entries)
    contexts_by_child: dict[int, tuple[CompositionContext, ...]] = {}
    context_limit = max(0, int(context_max_per_item))
    if context_limit:
        for child_index, contexts in pending_contexts.items():
            unique: list[CompositionContext] = []
            seen: set[tuple[str, int]] = set()
            for context in contexts:
                if context.parent_index not in valid_parent_indexes:
                    continue
                identity = (context.text, context.line)
                if identity in seen:
                    continue
                seen.add(identity)
                unique.append(context)
                if len(unique) >= context_limit:
                    break
            if unique:
                contexts_by_child[child_index] = tuple(unique)
    canonical_parent_by_child: dict[int, int] = {}
    candidate_parents_by_child: dict[int, list[CompositionEntry]] = {}
    for entry in entries.values():
        for child_index in entry.dependency_indexes:
            candidate_parents_by_child.setdefault(child_index, []).append(entry)
    for child_index, parent_entries in candidate_parents_by_child.items():
        selected = min(
            parent_entries,
            key=lambda entry: (len(entry.source), entry.parent_index),
        )
        canonical_parent_by_child[child_index] = selected.parent_index
    return MToolCompositionPlan(
        entries=entries,
        contexts_by_child=contexts_by_child,
        canonical_parent_by_child=canonical_parent_by_child,
    )


def apply_mtool_compositions(
    plan: MToolCompositionPlan,
    *,
    translated_items: list[tuple[Any, Any]],
    checkpoint_entries: dict[tuple[int, int], dict[str, Any]],
    file_path: str,
    progress_records: list[dict[str, Any]],
    processed_targets: int,
    total_targets: int,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    save_record: Callable[..., None],
    mark_dirty: Callable[[], None],
    emit_progress: Callable[..., None],
    progress_status: Callable[[str], str],
) -> int:
    """Recompose ready parents from final child outputs and record dependency hashes."""
    for parent_index, entry in plan.entries.items():
        existing_parent = checkpoint_entries.get((parent_index, 0))
        if (
            existing_parent
            and str(existing_parent.get("status", "")) == "preserved"
        ):
            # Non-linguistic multiline fragments may coincidentally share
            # standalone lines with other keys. Their preserved state wins.
            continue
        dependencies: list[dict[str, Any]] = []
        ready = True
        for child_index in entry.dependency_indexes:
            child_entry = checkpoint_entries.get((child_index, 0))
            if not child_entry or not str(child_entry.get("status", "")):
                ready = False
                break
            child_source = str(translated_items[child_index][0])
            child_translation = str(translated_items[child_index][1])
            dependencies.append(
                {
                    "row": child_index,
                    "source_key": child_source,
                    "source_hash": _text_hash(child_source),
                    "output_hash": _text_hash(child_translation),
                    "status": str(child_entry.get("status", "")),
                }
            )
        if not ready:
            continue

        output_parts: list[str] = []
        for piece in entry.pieces:
            if piece.child_index is None:
                output_parts.append(piece.literal)
                continue
            child_translation = str(translated_items[piece.child_index][1])
            output_parts.append(
                piece.prefix
                + child_translation.strip(_LAYOUT_WHITESPACE)
                + piece.suffix
            )
        translated = "".join(output_parts)

        review_required_rows = [
            dependency["row"]
            for dependency in dependencies
            if dependency["status"] == "review_required"
        ]
        needs_review_rows = [
            dependency["row"]
            for dependency in dependencies
            if dependency["status"] == "translated_needs_review"
        ]
        issues: list[dict[str, Any]] = []
        if review_required_rows:
            issues.append(
                {
                    "type": "composed_dependency_review_required",
                    "message": "One or more source-line translations require review.",
                    "dependency_rows": review_required_rows,
                }
            )
            status = "review_required"
        elif needs_review_rows:
            issues.append(
                {
                    "type": "composed_dependency_needs_review",
                    "message": "One or more source-line translations need review.",
                    "dependency_rows": needs_review_rows,
                }
            )
            status = "translated_needs_review"
        elif translated == entry.source:
            issues.append(
                {
                    "type": "identical_composed_japanese_source",
                    "message": "Composed translation is identical to its Japanese source.",
                }
            )
            status = "review_required"
        else:
            status = "translated"

        key, _value = translated_items[parent_index]
        translated_items[parent_index] = (key, translated)
        dependency_fingerprint = _dependency_fingerprint(dependencies)
        save_record(
            file_path,
            progress_records,
            row=parent_index,
            col=0,
            original=entry.source,
            translated=translated,
            status=status,
            issues=issues,
            json_key=str(key),
            mtool=True,
            entry_classification="composed_multiline",
            batch_id=f"composition:{plan.version}",
            model_identifier="deterministic-composition",
            retry_count=0,
            composition_version=plan.version,
            dependencies=dependencies,
            dependency_fingerprint=dependency_fingerprint,
        )
        mark_dirty()
        processed_targets += 1
        emit_progress(
            progress_callback,
            file_path,
            parent_index,
            0,
            progress_status(status),
            processed_targets,
            total_targets,
            original_text=entry.source,
            translated_text=translated,
        )
    return processed_targets


def _has_line_break(text: str) -> bool:
    return "\n" in text or "\r" in text


def _strip_layout(text: str) -> str:
    return text.strip(_LAYOUT_WHITESPACE)


def _split_layout(line: str) -> tuple[str, str, str]:
    prefix_length = len(line) - len(line.lstrip(_LAYOUT_WHITESPACE))
    suffix_length = len(line) - len(line.rstrip(_LAYOUT_WHITESPACE))
    if prefix_length == len(line):
        return line, "", ""
    suffix_start = len(line) - suffix_length if suffix_length else len(line)
    return line[:prefix_length], line[prefix_length:suffix_start], line[suffix_start:]


def _bounded_context(
    split_parts: list[str],
    *,
    target_line: int,
    parent_index: int,
    max_chars: int,
) -> CompositionContext | None:
    if max_chars <= 0:
        return None
    lines = [split_parts[index] for index in range(0, len(split_parts), 2)]
    normalized = "\n".join(lines)
    if len(normalized) <= max_chars:
        return CompositionContext(text=normalized, line=target_line, parent_index=parent_index)

    target_index = target_line - 1
    selected_start = target_index
    selected_end = target_index + 1
    if len(lines[target_index]) > max_chars:
        return None
    while True:
        candidates: list[tuple[int, int]] = []
        if selected_start > 0:
            candidates.append((selected_start - 1, selected_end))
        if selected_end < len(lines):
            candidates.append((selected_start, selected_end + 1))
        accepted: tuple[int, int] | None = None
        for start, end in candidates:
            if len("\n".join(lines[start:end])) <= max_chars:
                accepted = (start, end)
                break
        if accepted is None:
            break
        selected_start, selected_end = accepted
    text = "\n".join(lines[selected_start:selected_end])
    return CompositionContext(
        text=text,
        line=target_index - selected_start + 1,
        parent_index=parent_index,
    )


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dependency_fingerprint(dependencies: list[dict[str, Any]]) -> str:
    serialized = json.dumps(
        dependencies,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "COMPOSITION_VERSION",
    "CompositionContext",
    "CompositionEntry",
    "CompositionPiece",
    "MToolCompositionPlan",
    "apply_mtool_compositions",
    "build_mtool_composition_plan",
]
