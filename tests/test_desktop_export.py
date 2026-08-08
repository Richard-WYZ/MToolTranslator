from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.desktop import Api, build_close_handler
from app.services.files import export_mtool_json, translation_output_state
from translation.output import default_output_path


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _RequestClient:
    def __init__(self, dirty: bool):
        self.dirty = dirty
        self.posts = []

    def get(self, url, timeout):
        return _Response({"dirty": self.dirty})

    def post(self, url, timeout):
        self.posts.append(url)
        return _Response({"ok": True})


class _Window:
    def __init__(self, confirmed: bool):
        self.confirmed = confirmed
        self.dialogs = []

    def create_confirmation_dialog(self, title, message):
        self.dialogs.append((title, message))
        return self.confirmed


def _write_translation_pair(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "session" / "ManualTransFile.json"
    source.parent.mkdir()
    source.write_text(json.dumps({"こんにちは": "こんにちは"}, ensure_ascii=False), encoding="utf-8")
    output = Path(default_output_path(str(source)))
    output.write_text(json.dumps({"こんにちは": "你好"}, ensure_ascii=False), encoding="utf-8")
    return source, output


def test_export_state_is_ready_before_review_completion_and_clean_after_export(tmp_path):
    source, _output = _write_translation_pair(tmp_path)

    before = translation_output_state(str(source))
    result = export_mtool_json(source.parent, str(source))
    after = translation_output_state(str(source))

    assert before["ready"] is True
    assert before["dirty"] is True
    assert result["ok"] is True
    assert after["ready"] is True
    assert after["dirty"] is False


def test_export_can_save_to_an_exact_user_selected_path(tmp_path):
    source, _output = _write_translation_pair(tmp_path)
    destination_dir = tmp_path / "chosen"
    destination_dir.mkdir()
    destination = destination_dir / "自定义译文.json"

    result = export_mtool_json(source.parent, str(source), output_path=str(destination))

    assert result["export_path"] == str(destination)
    assert result["filename"] == destination.name
    assert json.loads(destination.read_text(encoding="utf-8")) == {"こんにちは": "你好"}


def test_desktop_save_dialog_accepts_string_and_sequence_results(monkeypatch):
    class _DialogWindow:
        def __init__(self, result):
            self.result = result

        def create_file_dialog(self, *args, **kwargs):
            return self.result

    import app.desktop as desktop

    monkeypatch.setattr(desktop.webview, "windows", [_DialogWindow(r"C:\chosen\result.json")])
    assert Api().save_file_dialog("result.json") == r"C:\chosen\result.json"

    monkeypatch.setattr(desktop.webview, "windows", [_DialogWindow((r"D:\other\result.json",))])
    assert Api().save_file_dialog("result.json") == r"D:\other\result.json"


def test_history_delete_removes_task_state_but_preserves_source(tmp_path):
    from types import SimpleNamespace

    from app.routes.translation_state import create_router
    from translation import checkpoint
    from translation.review import review_report_path
    from translation.review.ai import ai_review_store_path

    source, output = _write_translation_pair(tmp_path)
    original_checkpoint_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        checkpoint.init_checkpoint(str(source), total=1, model="api:test", file_type="json")
        checkpoint.save_progress(str(source), 0, 0, "こんにちは", "你好", status="translated")
        report = Path(review_report_path(str(source), str(output)))
        report.write_text("{}", encoding="utf-8")
        ai_store = Path(ai_review_store_path(str(source)))
        ai_store.write_text("{}", encoding="utf-8")
        tasks = {
            "finished-task": SimpleNamespace(
                file_path=str(source),
                status="completed",
                has_unexported_result=True,
            )
        }
        api = FastAPI()
        api.include_router(create_router(tasks=tasks, batches={}, ai_review_tasks={}))

        response = TestClient(api).delete("/api/history/session", params={"file_path": str(source)})

        assert response.status_code == 200
        assert response.json()["removed_tasks"] == 1
        assert source.is_file()
        assert not output.exists()
        assert not report.exists()
        assert not ai_store.exists()
        assert not Path(checkpoint.get_checkpoint_path(str(source))).exists()
        assert tasks == {}
        assert TestClient(api).get("/api/history/sessions").json()["sessions"] == []
    finally:
        checkpoint.CHECKPOINT_DIR = original_checkpoint_dir


def test_history_delete_rejects_active_task(tmp_path):
    from types import SimpleNamespace

    from app.routes.translation_state import create_router

    source, output = _write_translation_pair(tmp_path)
    tasks = {
        "active-task": SimpleNamespace(
            file_path=str(source),
            status="running",
            has_unexported_result=True,
        )
    }
    api = FastAPI()
    api.include_router(create_router(tasks=tasks, batches={}, ai_review_tasks={}))

    response = TestClient(api).delete("/api/history/session", params={"file_path": str(source)})

    assert response.status_code == 409
    assert output.is_file()
    assert "active-task" in tasks


def test_export_status_allows_safe_snapshot_without_manual_review(tmp_path):
    import main

    source, _output = _write_translation_pair(tmp_path)
    response = TestClient(main.app).get("/api/export/status", params={"file_path": str(source)})

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["review_active"] is False


def test_desktop_close_uses_native_confirmation_and_shutdown():
    client = _RequestClient(dirty=True)
    window = _Window(confirmed=True)

    assert build_close_handler(window, client)() is True
    assert window.dialogs and window.dialogs[0][0] == "确认退出"
    assert client.posts == ["http://127.0.0.1:8000/api/desktop/shutdown"]

    cancelled_client = _RequestClient(dirty=True)
    cancelled_window = _Window(confirmed=False)
    assert build_close_handler(cancelled_window, cancelled_client)() is False
    assert cancelled_client.posts == []
