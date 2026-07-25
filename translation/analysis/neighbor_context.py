from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from translation.classification import (
    has_source_japanese,
    looks_like_context_boundary,
    looks_like_dialogue_boundary,
)


NEIGHBOR_CONTEXT_VERSION = "mtool-neighbor-context-v1"


@dataclass(frozen=True)
class NeighborContext:
    text: str
    line: int
    offset: int
    source_index: int


@dataclass
class MToolNeighborContextPlan:
    contexts_by_child: dict[int, tuple[NeighborContext, ...]]
    version: str = NEIGHBOR_CONTEXT_VERSION

    def contexts_for_child(self, index: int) -> list[dict[str, Any]]:
        return [
            {
                "text": context.text,
                "line": context.line,
                "offset": context.offset,
                "source_index": context.source_index,
                "context_kind": "scene_neighbor",
            }
            for context in self.contexts_by_child.get(index, ())
        ]


def build_mtool_neighbor_context_plan(
    items: list[tuple[Any, Any]],
    *,
    excluded_child_indexes: Iterable[int] = (),
    radius: int = 2,
    context_max_chars: int = 320,
    min_dialogue_items: int = 3,
) -> MToolNeighborContextPlan:
    """Build bounded read-only scene context for structurally reliable dialogue."""
    sources = [str(key) for key, _value in items]
    excluded = {int(index) for index in excluded_child_indexes}
    bounded_radius = max(1, int(radius))
    bounded_chars = max(0, int(context_max_chars))
    window_size = bounded_radius * 2 + 1
    required_dialogue = min(
        window_size,
        max(1, int(min_dialogue_items)),
    )
    contexts: dict[int, tuple[NeighborContext, ...]] = {}
    if not bounded_chars or len(sources) < window_size:
        return MToolNeighborContextPlan(contexts_by_child=contexts)

    for target_index in range(bounded_radius, len(sources) - bounded_radius):
        if target_index in excluded:
            continue
        target = sources[target_index]
        if (
            "\n" in target
            or "\r" in target
            or not looks_like_dialogue_boundary(target)
        ):
            continue

        indexes = tuple(range(target_index - bounded_radius, target_index + bounded_radius + 1))
        window = [sources[index] for index in indexes]
        if any(
            not has_source_japanese(text) or looks_like_context_boundary(text)
            for text in window
        ):
            continue
        if sum(looks_like_dialogue_boundary(text) for text in window) < required_dialogue:
            continue

        context_chars = sum(
            len(text)
            for offset, text in zip(
                range(-bounded_radius, bounded_radius + 1),
                window,
            )
            if offset
        )
        if context_chars > bounded_chars:
            continue
        contexts[target_index] = tuple(
            NeighborContext(
                text=sources[source_index],
                line=1,
                offset=source_index - target_index,
                source_index=source_index,
            )
            for source_index in indexes
            if source_index != target_index
        )

    return MToolNeighborContextPlan(contexts_by_child=contexts)
__all__ = [
    "MToolNeighborContextPlan",
    "NEIGHBOR_CONTEXT_VERSION",
    "NeighborContext",
    "build_mtool_neighbor_context_plan",
]
