from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException

from app.desktop import Api, build_close_handler
from app.routes.files import create_router as create_files_router
from app.services.desktop_sources import (
    DesktopSourceRegistry,
    desktop_source_registry,
    validate_desktop_source,
)
from app.services.files import (
    export_mtool_json,
    load_session_metadata,
    save_mtool_upload,
    session_project_info,
    translation_output_state,
)
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
    source, output = _write_translation_pair(tmp_path)
    destination_dir = tmp_path / "chosen"
    destination_dir.mkdir()
    destination = destination_dir / "自定义译文.json"

    result = export_mtool_json(source.parent, str(source), output_path=str(destination))

    assert result["export_path"] == str(destination)
    assert result["filename"] == destination.name
    assert json.loads(destination.read_text(encoding="utf-8")) == {"こんにちは": "你好"}
    assert json.loads(source.read_text(encoding="utf-8")) == {"こんにちは": "こんにちは"}
    assert output.is_file()


def test_export_overwrites_real_original_with_backup_and_keeps_working_copy(tmp_path):
    original = tmp_path / "game" / "ManualTransFile.json"
    original.parent.mkdir()
    original_content = json.dumps({"こんにちは": "こんにちは"}, ensure_ascii=False).encode("utf-8")
    original.write_bytes(original_content)
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    imported = save_mtool_upload(
        upload_root,
        original.name,
        original_content,
        original_path=str(original),
    )
    working = Path(imported["saved_path"])
    output = Path(default_output_path(str(working)))
    output.write_text(json.dumps({"こんにちは": "你好"}, ensure_ascii=False), encoding="utf-8")

    result = export_mtool_json(
        working.parent,
        str(working),
        overwrite_original=True,
    )

    assert result["overwrote_original"] is True
    assert result["export_path"] == str(original)
    assert json.loads(original.read_text(encoding="utf-8")) == {"こんにちは": "你好"}
    assert json.loads(Path(f"{original}.bak").read_text(encoding="utf-8")) == {"こんにちは": "こんにちは"}
    assert json.loads(working.read_text(encoding="utf-8")) == {"こんにちは": "こんにちは"}
    assert load_session_metadata(working)["original_path"] == str(original)
    assert translation_output_state(str(working))["dirty"] is False

    output.write_text(json.dumps({"こんにちは": "您好"}, ensure_ascii=False), encoding="utf-8")
    assert translation_output_state(str(working))["dirty"] is True
    export_mtool_json(working.parent, str(working), overwrite_original=True)
    assert json.loads(original.read_text(encoding="utf-8")) == {"こんにちは": "您好"}
    assert json.loads(Path(f"{original}.bak").read_text(encoding="utf-8")) == {"こんにちは": "こんにちは"}
    assert translation_output_state(str(working))["dirty"] is False


def test_export_refuses_to_overwrite_an_original_changed_outside_the_app(tmp_path):
    original = tmp_path / "ManualTransFile.json"
    original_content = json.dumps({"こんにちは": "こんにちは"}, ensure_ascii=False).encode("utf-8")
    original.write_bytes(original_content)
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    imported = save_mtool_upload(
        upload_root,
        original.name,
        original_content,
        original_path=str(original),
    )
    working = Path(imported["saved_path"])
    Path(default_output_path(str(working))).write_text(
        json.dumps({"こんにちは": "你好"}, ensure_ascii=False),
        encoding="utf-8",
    )
    original.write_text(json.dumps({"こんにちは": "外部変更"}, ensure_ascii=False), encoding="utf-8")

    try:
        export_mtool_json(working.parent, str(working), overwrite_original=True)
        raise AssertionError("Expected the changed source file to be rejected")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "changed after import" in str(exc.detail)
    assert not Path(f"{original}.bak").exists()


def test_desktop_source_registry_uses_one_time_tokens_and_validates_content(tmp_path):
    source = tmp_path / "drag.json"
    content = b'{"a":"a"}'
    source.write_bytes(content)
    registry = DesktopSourceRegistry(ttl_seconds=10)

    registered = registry.register(str(source))
    matched = registry.wait_for_match(source.name, len(content), timeout=0)

    assert matched == registered
    consumed = registry.consume(registered.token)
    assert consumed == registered
    assert validate_desktop_source(consumed, source.name, content) == str(source.resolve())
    assert registry.consume(registered.token) is None


def test_import_route_associates_a_trusted_dragged_source_path(tmp_path):
    original = tmp_path / "original" / "ManualTransFile.json"
    original.parent.mkdir()
    content = json.dumps({"こんにちは": "こんにちは"}, ensure_ascii=False).encode("utf-8")
    original.write_bytes(content)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    desktop_source_registry.clear()
    token = desktop_source_registry.register(str(original)).token
    api = FastAPI()
    api.include_router(
        create_files_router(
            upload_dir=uploads,
            tasks={},
            get_task_for_file=lambda _path: None,
            ai_review_tasks={},
        )
    )

    response = TestClient(api).post(
        "/api/import",
        files={"file": (original.name, content, "application/json")},
        data={"source_token": token},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["original_path"] == str(original.resolve())
    assert load_session_metadata(payload["saved_path"])["original_path"] == str(original.resolve())
    assert session_project_info(payload["saved_path"])["project_display_name"] == "original"
    assert desktop_source_registry.consume(token) is None


def test_local_import_route_reads_only_a_registered_desktop_source(tmp_path):
    original = tmp_path / "selected.json"
    original.write_text(json.dumps({"一": "一"}, ensure_ascii=False), encoding="utf-8")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    desktop_source_registry.clear()
    token = desktop_source_registry.register(str(original)).token
    api = FastAPI()
    api.include_router(
        create_files_router(
            upload_dir=uploads,
            tasks={},
            get_task_for_file=lambda _path: None,
            ai_review_tasks={},
        )
    )
    client = TestClient(api)

    response = client.post("/api/import-local", json={"source_token": token})
    repeated = client.post("/api/import-local", json={"source_token": token})

    assert response.status_code == 200
    assert response.json()["original_path"] == str(original.resolve())
    assert repeated.status_code == 400


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


def test_history_delete_purges_only_an_internal_working_copy(tmp_path):
    from app.routes.translation_state import create_router

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source, output = _write_translation_pair(uploads)
    api = FastAPI()
    api.include_router(create_router(tasks={}, batches={}, ai_review_tasks={}, upload_dir=uploads))

    response = TestClient(api).delete("/api/history/session", params={"file_path": str(source)})

    assert response.status_code == 200
    assert response.json()["removed_working_source"] is True
    assert not source.exists()
    assert not output.exists()
    assert not source.parent.exists()


def test_clear_history_deletes_finished_sessions_and_skips_active_ones(tmp_path):
    from types import SimpleNamespace

    from app.routes.translation_state import create_router
    from translation import checkpoint

    finished = tmp_path / "finished.json"
    active = tmp_path / "active.json"
    for source in (finished, active):
        source.write_text(json.dumps({"一": "一"}, ensure_ascii=False), encoding="utf-8")
    original_checkpoint_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        for source in (finished, active):
            checkpoint.init_checkpoint(str(source), total=1, model="api:test", file_type="json")
            checkpoint.save_progress(str(source), 0, 0, "一", "一", status="preserved")
        tasks = {
            "finished": SimpleNamespace(file_path=str(finished), status="completed", has_unexported_result=False),
            "active": SimpleNamespace(file_path=str(active), status="running", has_unexported_result=True),
        }
        api = FastAPI()
        api.include_router(create_router(tasks=tasks, batches={}, ai_review_tasks={}))

        response = TestClient(api).post("/api/history/clear")

        payload = response.json()
        assert response.status_code == 200
        assert payload["deleted"] == 1
        assert payload["skipped"] == 1
        assert payload["failed"] == 0
        assert "finished" not in tasks
        assert "active" in tasks
        assert not Path(checkpoint.get_checkpoint_path(str(finished))).exists()
        assert Path(checkpoint.get_checkpoint_path(str(active))).exists()
        assert finished.is_file() and active.is_file()
    finally:
        checkpoint.CHECKPOINT_DIR = original_checkpoint_dir


def test_desktop_exit_state_only_blocks_active_work(tmp_path):
    from types import SimpleNamespace

    from app.routes.translation_state import create_router

    source = tmp_path / "saved.json"
    tasks = {
        "completed": SimpleNamespace(
            task_id="completed", file_path=str(source), status="completed", has_unexported_result=True,
        )
    }
    api = FastAPI()
    api.include_router(create_router(tasks=tasks, batches={}, ai_review_tasks={}))
    client = TestClient(api)

    assert client.get("/api/desktop/exit-state").json()["requires_confirmation"] is False

    tasks["active"] = SimpleNamespace(
        task_id="active", file_path=str(source), status="running", has_unexported_result=True,
    )
    payload = client.get("/api/desktop/exit-state").json()
    assert payload["requires_confirmation"] is True
    assert [item["task_id"] for item in payload["states"]] == ["active"]


def test_export_auto_finalizes_only_after_review_is_complete(tmp_path):
    from translation import checkpoint

    source, _output = _write_translation_pair(tmp_path)
    destination = tmp_path / "result.json"
    finalized = []
    original_checkpoint_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        checkpoint.init_checkpoint(str(source), total=1, model="api:test", file_type="json")
        checkpoint.save_progress(
            str(source), 0, 0, "こんにちは", "你好", status="translated_needs_review",
        )
        api = FastAPI()
        api.include_router(create_files_router(
            upload_dir=tmp_path,
            tasks={},
            get_task_for_file=lambda _path: None,
            ai_review_tasks={},
            finalize_completed_session=lambda path: finalized.append(path) or {"ok": True},
        ))
        client = TestClient(api)
        request = {
            "session_id": source.parent.name,
            "file_path": str(source),
            "file_type": "json",
            "column_mappings": [],
            "output_path": str(destination),
        }

        pending = client.post("/api/export", json=request)
        assert pending.status_code == 200
        assert pending.json()["finalization"]["eligible"] is False
        assert finalized == []

        checkpoint.save_progress(str(source), 0, 0, "こんにちは", "你好", status="translated")
        completed = client.post("/api/export", json=request)
        assert completed.status_code == 200
        finalization = completed.json()["finalization"]
        assert finalization["confirmation_required"] is True
        assert finalized == []

        cleanup = client.post("/api/export/finalize", json={
            "cleanup_token": finalization["cleanup_token"],
            "cleanup": True,
        })
        assert cleanup.status_code == 200
        assert cleanup.json()["cleaned"] is True
        assert finalized == [str(source)]
    finally:
        checkpoint.CHECKPOINT_DIR = original_checkpoint_dir


def test_browser_export_defers_final_cleanup_until_download_finishes(tmp_path):
    from translation import checkpoint

    source, _output = _write_translation_pair(tmp_path)
    finalized = []
    original_checkpoint_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        checkpoint.init_checkpoint(str(source), total=1, model="api:test", file_type="json")
        checkpoint.save_progress(str(source), 0, 0, "こんにちは", "你好", status="translated")
        api = FastAPI()
        api.include_router(create_files_router(
            upload_dir=tmp_path,
            tasks={},
            get_task_for_file=lambda _path: None,
            ai_review_tasks={},
            finalize_completed_session=lambda path: finalized.append(path) or {"ok": True},
        ))
        client = TestClient(api)

        exported = client.post("/api/export", json={
            "session_id": source.parent.name,
            "file_path": str(source),
            "file_type": "json",
            "column_mappings": [],
        })
        finalization = exported.json()["finalization"]
        assert finalization["pending_download"] is True
        assert finalized == []

        confirmed = client.post("/api/export/finalize", json={
            "cleanup_token": finalization["cleanup_token"],
            "cleanup": True,
        })
        assert confirmed.status_code == 200
        assert confirmed.json()["pending_download"] is True

        downloaded = client.get("/api/download", params={
            "path": exported.json()["export_path"],
            "cleanup_token": finalization["cleanup_token"],
        })
        assert downloaded.status_code == 200
        assert finalized == [str(source)]
    finally:
        checkpoint.CHECKPOINT_DIR = original_checkpoint_dir


def test_export_cleanup_can_be_declined_and_history_is_kept(tmp_path):
    from translation import checkpoint

    source, _output = _write_translation_pair(tmp_path)
    finalized = []
    original_checkpoint_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        checkpoint.init_checkpoint(str(source), total=1, model="api:test", file_type="json")
        checkpoint.save_progress(str(source), 0, 0, "こんにちは", "你好", status="translated")
        api = FastAPI()
        api.include_router(create_files_router(
            upload_dir=tmp_path,
            tasks={},
            get_task_for_file=lambda _path: None,
            ai_review_tasks={},
            finalize_completed_session=lambda path: finalized.append(path) or {"ok": True},
        ))
        client = TestClient(api)
        exported = client.post("/api/export", json={
            "session_id": source.parent.name,
            "file_path": str(source),
            "file_type": "json",
            "column_mappings": [],
            "output_path": str(tmp_path / "kept.json"),
        }).json()

        declined = client.post("/api/export/finalize", json={
            "cleanup_token": exported["finalization"]["cleanup_token"],
            "cleanup": False,
        })

        assert declined.status_code == 200
        assert declined.json()["kept"] is True
        assert finalized == []
        assert source.is_file()
    finally:
        checkpoint.CHECKPOINT_DIR = original_checkpoint_dir


def test_history_project_name_uses_original_directory_and_supports_custom_name(tmp_path):
    from app.routes.translation_state import create_router

    original = tmp_path / "MyGame" / "ManualTransFile.json"
    original.parent.mkdir()
    content = json.dumps({"一": "一"}, ensure_ascii=False).encode("utf-8")
    original.write_bytes(content)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    imported = save_mtool_upload(uploads, original.name, content, original_path=str(original))
    source = imported["saved_path"]
    api = FastAPI()
    api.include_router(create_router(tasks={}, batches={}, ai_review_tasks={}, upload_dir=uploads))
    client = TestClient(api)

    renamed = client.put("/api/history/session/name", json={
        "file_path": source,
        "project_name": "魔王城测试版",
    })

    assert renamed.status_code == 200
    assert renamed.json()["project_display_name"] == "魔王城测试版"
    assert renamed.json()["project_name_source"] == "custom"
    reset = client.put("/api/history/session/name", json={"file_path": source, "project_name": ""})
    assert reset.json()["project_display_name"] == "MyGame"
    assert reset.json()["project_name_source"] == "directory"


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
