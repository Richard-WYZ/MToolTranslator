"""Short lived admission and cleanup coordination for file tasks.

The translation and AI review registries are intentionally kept separate for
their APIs, but admission must inspect them as one resource.  This coordinator
holds its lock only while checking and registering a task; model work and
thread joins happen after the lock is released.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Mapping


TRANSLATION_ACTIVE_STATUSES = {"starting", "running", "paused", "stopping", "finalizing"}
AI_REVIEW_ACTIVE_STATUSES = {"preparing", "reviewing", "verifying", "applying", "finalizing", "stopping"}


class TaskCoordinator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reserved_files: set[str] = set()
        # The desktop translation queue has a single global translation slot.
        # Keep a reservation while the caller registers its task so two
        # concurrent starts cannot both observe an empty registry.
        self._translation_admission_reserved = False

    @staticmethod
    def _path_key(file_path: str) -> str:
        # Windows paths are case-insensitive; normcase also normalizes the
        # separator form so aliases cannot bypass a reservation.
        return os.path.normcase(os.path.abspath(str(file_path)))

    @classmethod
    def _matches(cls, task: Any, file_path: str, statuses: set[str]) -> bool:
        return (
            cls._path_key(str(getattr(task, "file_path", ""))) == cls._path_key(file_path)
            and str(getattr(task, "status", "")) in statuses
        )

    def active_translation(self, tasks: Mapping[str, Any], file_path: str) -> Any | None:
        return next((task for task in tasks.values() if self._matches(task, file_path, TRANSLATION_ACTIVE_STATUSES)), None)

    @staticmethod
    def active_any(tasks: Mapping[str, Any] | None, statuses: set[str]) -> Any | None:
        if not tasks:
            return None
        return next((task for task in tasks.values() if str(getattr(task, "status", "")) in statuses), None)

    def active_review(self, tasks: Mapping[str, Any], file_path: str) -> Any | None:
        return next((task for task in tasks.values() if self._matches(task, file_path, AI_REVIEW_ACTIVE_STATUSES)), None)

    @contextmanager
    def admission(
        self,
        *,
        file_path: str,
        translation_tasks: Mapping[str, Any] | None = None,
        ai_review_tasks: Mapping[str, Any] | None = None,
        kind: str,
    ) -> Iterator[None]:
        """Atomically check the file and reserve it for a new task.

        Callers must insert their task in the supplied registry inside this
        context.  The context deliberately does not wait for or start work;
        those operations belong after the lock is released.
        """
        absolute = self._path_key(file_path)
        with self._lock:
            if absolute in self._reserved_files:
                raise RuntimeError("A file operation is already in progress")
            if kind == "translation":
                if self._translation_admission_reserved or (
                    translation_tasks and self.active_any(translation_tasks, TRANSLATION_ACTIVE_STATUSES)
                ):
                    raise RuntimeError("A translation task is already active")
                if ai_review_tasks and self.active_review(ai_review_tasks, file_path):
                    raise RuntimeError("Cannot start translation while AI review is active")
            elif kind == "ai_review":
                if translation_tasks and self.active_translation(translation_tasks, file_path):
                    raise RuntimeError("Finish or stop the active translation task before AI review")
                if ai_review_tasks and self.active_review(ai_review_tasks, file_path):
                    raise RuntimeError("An AI review task is already active for this file")
            else:
                raise ValueError(f"Unsupported task kind: {kind}")
            # The caller inserts its task while this reservation is held. This
            # closes the check/register race between the translation and AI
            # review registries, including two concurrent callers.
            self._reserved_files.add(absolute)
            if kind == "translation":
                self._translation_admission_reserved = True
            try:
                # Keep the coordinator lock through the caller's tiny
                # register-only section. This serializes registry insertion
                # with other admission scans; callers must start workers only
                # after leaving this context.
                yield
            finally:
                self._reserved_files.discard(absolute)
                if kind == "translation":
                    self._translation_admission_reserved = False

    @contextmanager
    def cleanup_reservation(self, file_path: str) -> Iterator[None]:
        """Reserve a file while a cancellable cleanup waits and deletes state."""
        absolute = self._path_key(file_path)
        with self._lock:
            if absolute in self._reserved_files:
                raise RuntimeError("A file operation is already in progress")
            self._reserved_files.add(absolute)
        try:
            yield
        finally:
            with self._lock:
                self._reserved_files.discard(absolute)

    @contextmanager
    def file_operation(self, file_path: str) -> Iterator[None]:
        """Serialize short state-changing operations for one file."""
        with self._lock:
            yield


_DEFAULT_COORDINATOR = TaskCoordinator()


def default_task_coordinator() -> TaskCoordinator:
    return _DEFAULT_COORDINATOR


__all__ = [
    "AI_REVIEW_ACTIVE_STATUSES",
    "TRANSLATION_ACTIVE_STATUSES",
    "TaskCoordinator",
    "default_task_coordinator",
]
