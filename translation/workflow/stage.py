from __future__ import annotations

from typing import Protocol, TypeVar


T = TypeVar("T")


class Stage(Protocol[T]):
    """A workflow step with explicit input and output context."""

    name: str

    def run(self, context: T) -> T:
        ...

