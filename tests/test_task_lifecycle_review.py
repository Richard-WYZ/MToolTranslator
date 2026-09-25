from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_task_coordinator_admission_is_atomic_per_file():
    from app.services.task_coordinator import TaskCoordinator

    coordinator = TaskCoordinator()
    translations: dict[str, object] = {}
    source = r"C:\sessions\game.json"
    other = r"C:\sessions\other.json"

    with coordinator.admission(
        file_path=source,
        translation_tasks=translations,
        ai_review_tasks={},
        kind="translation",
    ):
        translations["one"] = SimpleNamespace(file_path=source, status="starting")
        with pytest.raises(RuntimeError, match="already in progress"):
            with coordinator.admission(
                file_path=source.upper(),
                translation_tasks=translations,
                ai_review_tasks={},
                kind="translation",
            ):
                pass

        # The desktop translation worker is globally single threaded, so a
        # second file is blocked by the registered translation as well.
        with pytest.raises(RuntimeError, match="translation task is already active"):
            with coordinator.admission(
                file_path=other,
                translation_tasks=translations,
                ai_review_tasks={},
                kind="translation",
            ):
                pass

        # AI review remains file-scoped and may inspect another file.
        with coordinator.admission(
            file_path=other,
            translation_tasks=translations,
            ai_review_tasks={},
            kind="ai_review",
        ):
            pass


def test_cleanup_preserves_state_when_worker_does_not_stop(tmp_path: Path):
    from app.routes.translation_state import cleanup_translation_state
    from app.schemas import CleanupRequest
    from app.services.task_coordinator import TaskCoordinator
    from app.services.files import translated_path

    source = tmp_path / "game.json"
    source.write_text('{"こんにちは":"こんにちは"}', encoding="utf-8")
    output = Path(translated_path(str(source)))
    output.write_text('{"こんにちは":"你好"}', encoding="utf-8")

    class SlowTask:
        file_path = str(source)
        status = "running"
        has_unexported_result = True

        def cancel(self):
            self.status = "stopping"

        def wait_for_stop(self, timeout=5):
            assert timeout == 5
            return False

    task = SlowTask()
    with pytest.raises(HTTPException) as raised:
        cleanup_translation_state(
            CleanupRequest(file_path=str(source)),
            tasks={"task": task},
            ai_review_tasks={},
            coordinator=TaskCoordinator(),
        )

    assert raised.value.status_code == 409
    assert output.is_file()


def test_cleanup_deletes_state_only_after_worker_stop(tmp_path: Path):
    from app.routes.translation_state import cleanup_translation_state
    from app.schemas import CleanupRequest
    from app.services.files import translated_path
    from app.services.task_coordinator import TaskCoordinator

    source = tmp_path / "game.json"
    source.write_text('{"こんにちは":"こんにちは"}', encoding="utf-8")
    output = Path(translated_path(str(source)))
    output.write_text('{"こんにちは":"你好"}', encoding="utf-8")
    events: list[str] = []

    class FinishedTask:
        file_path = str(source)
        status = "running"
        has_unexported_result = True

        def cancel(self):
            events.append("cancel")
            self.status = "stopping"

        def wait_for_stop(self, timeout=5):
            events.append("wait")
            return True

    result = cleanup_translation_state(
        CleanupRequest(file_path=str(source)),
        tasks={"task": FinishedTask()},
        ai_review_tasks={},
        coordinator=TaskCoordinator(),
    )

    assert events == ["cancel", "wait"]
    assert not output.exists()
    assert str(output) in result["deleted"]


def test_cleanup_waits_for_terminal_task_writer_before_delete(tmp_path: Path):
    from app.routes.translation_state import cleanup_translation_state
    from app.schemas import CleanupRequest
    from app.services.files import translated_path
    from app.services.task_coordinator import TaskCoordinator

    source = tmp_path / "game.json"
    source.write_text('{"こんにちは":"こんにちは"}', encoding="utf-8")
    output = Path(translated_path(str(source)))
    output.write_text('{"こんにちは":"你好"}', encoding="utf-8")

    class WriterStillRunning:
        file_path = str(source)
        status = "error"
        has_unexported_result = True

        def wait_for_stop(self, timeout=5):
            return False

    with pytest.raises(HTTPException) as raised:
        cleanup_translation_state(
            CleanupRequest(file_path=str(source)),
            tasks={"failed": WriterStillRunning()},
            ai_review_tasks={},
            coordinator=TaskCoordinator(),
        )

    assert raised.value.status_code == 409
    assert output.is_file()


def test_cleanup_rejects_task_id_from_another_file(tmp_path: Path):
    from app.routes.translation_state import cleanup_translation_state
    from app.schemas import CleanupRequest
    from app.services.files import translated_path
    from app.services.task_coordinator import TaskCoordinator

    source = tmp_path / "game.json"
    other = tmp_path / "other.json"
    source.write_text('{"こんにちは":"こんにちは"}', encoding="utf-8")
    other.write_text('{"さようなら":"さようなら"}', encoding="utf-8")
    output = Path(translated_path(str(source)))
    output.write_text('{"こんにちは":"你好"}', encoding="utf-8")

    class ActiveTask:
        def __init__(self, file_path):
            self.file_path = str(file_path)
            self.status = "running"
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def wait_for_stop(self, timeout=5):
            return True

    target_task = ActiveTask(source)
    other_task = ActiveTask(other)
    with pytest.raises(HTTPException) as raised:
        cleanup_translation_state(
            CleanupRequest(file_path=str(source), task_id="other"),
            tasks={"target": target_task, "other": other_task},
            ai_review_tasks={},
            coordinator=TaskCoordinator(),
        )

    assert raised.value.status_code == 409
    assert not target_task.cancelled
    assert not other_task.cancelled
    assert output.is_file()


def test_translation_task_marks_runtime_initialization_failure_terminal(monkeypatch, tmp_path: Path):
    import app.services.translation_tasks as task_module
    from app.services.translation_tasks import TranslationTask

    def fail_runtime(_request):
        raise RuntimeError("runtime setup failed")

    monkeypatch.setattr(task_module, "TranslationRuntime", fail_runtime)
    task = TranslationTask(
        task_id="runtime-failure",
        file_path=str(tmp_path / "game.json"),
        model="api:test",
        prompt_style="professional",
        translate_columns=[1],
    )

    task.start()
    assert task._thread is not None
    task._thread.join(timeout=2)

    assert not task._thread.is_alive()
    assert task.status == "error"
    assert task.error == "runtime setup failed"
    assert task.finished_at > 0


def test_translation_task_preserves_pause_and_resume_during_startup(tmp_path: Path):
    import threading

    from app.services.translation_tasks import TranslationTask

    task = TranslationTask(
        task_id="startup-control",
        file_path=str(tmp_path / "game.json"),
        model="api:test",
        prompt_style="professional",
        translate_columns=[1],
    )
    task.status = "starting"

    task.pause()
    assert task.status == "paused"
    assert task._pause_requested is True

    task.resume()
    assert task.status == "starting"
    assert task._pause_requested is False

    class Runtime:
        def __init__(self):
            self.events = []
            self.pause_entered = threading.Event()
            self.release_pause = threading.Event()

        def pause(self):
            self.events.append("pause")
            self.pause_entered.set()
            assert self.release_pause.wait(1)

        def resume(self):
            self.events.append("resume")

    runtime = Runtime()
    task.runtime = runtime
    task.status = "running"
    pausing = threading.Thread(target=task.pause)
    pausing.start()
    assert runtime.pause_entered.wait(1)
    resuming = threading.Thread(target=task.resume)
    resuming.start()
    # resume cannot overtake the in-flight pause while the task lock is held.
    assert resuming.is_alive()
    runtime.release_pause.set()
    pausing.join(timeout=1)
    resuming.join(timeout=1)
    assert runtime.events == ["pause", "resume"]


def test_ai_review_task_reports_its_own_usage_tracker(monkeypatch, tmp_path: Path):
    import app.services.ai_review_tasks as task_module
    from app.services.ai_review_tasks import AIReviewTask
    from translation.review.ai import AIReviewModels
    from translation import usage

    def fake_run_ai_review(**_kwargs):
        usage.record("api", "review-model", {"prompt_tokens": 4, "completion_tokens": 3})
        return {"counts": {}, "total": 0}

    monkeypatch.setattr(task_module, "run_ai_review", fake_run_ai_review)
    monkeypatch.setattr(task_module, "update_ai_review_session_status", lambda *args, **kwargs: None)
    task = AIReviewTask(
        task_id="review-usage",
        file_path=str(tmp_path / "game.json"),
        items=[],
        models=AIReviewModels("review-model", "verify-model", "review-model", "verify-model"),
    )

    task._run()

    assert task.status == "completed"
    assert task.token_usage["total_tokens"] == 7
    assert task.token_usage["by_provider"]["api"]["models"]["review-model"]["calls"] == 1


def test_connection_scope_owns_provider_sessions(monkeypatch):
    import translation.models.transport as transport

    created = []

    class FakeSession:
        def __init__(self):
            self.closed = False
            self.calls = []
            created.append(self)

        def get(self, url, **kwargs):
            self.calls.append(("get", url, kwargs))
            return "response"

        def close(self):
            self.closed = True

    monkeypatch.setattr(transport.requests, "Session", FakeSession)

    with transport.connection_scope() as scope:
        assert transport.request("api", "get", "https://example.test") == "response"
        assert scope.api.calls[0][1] == "https://example.test"
        transport.request("ollama", "get", "http://localhost:11434/api/tags")
        with transport.connection_scope() as nested:
            assert nested is scope

    assert len(created) == 2
    assert all(session.closed for session in created)
