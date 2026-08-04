from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.desktop import build_close_handler
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
