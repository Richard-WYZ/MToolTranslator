from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.files import create_router


@pytest.mark.parametrize("status", ["starting", "finalizing"])
def test_prepare_and_finalize_block_import_and_export(tmp_path, monkeypatch, status):
    import app.routes.files as routes

    path = tmp_path / "source.json"
    path.write_text('{"123":"123"}', encoding="utf-8")
    task = SimpleNamespace(status=status, file_path=str(path))
    monkeypatch.setattr(routes, "translation_output_state", lambda _: {"ready": True, "dirty": True})
    app = FastAPI()
    app.include_router(create_router(
        upload_dir=tmp_path, tasks={"active": task}, get_task_for_file=lambda _: task,
        ai_review_tasks={},
    ))
    with TestClient(app) as client:
        state = client.get("/api/export/status", params={"file_path": str(path)}).json()
        assert state["translation_active"] and not state["ready"]
        assert client.post("/api/export", json={
            "session_id": "session", "file_path": str(path), "file_type": "json",
            "column_mappings": [],
        }).status_code == 409
        assert client.post("/api/import", files={
            "file": ("other.json", b'{"456":"456"}', "application/json"),
        }).status_code == 409
    assert path.read_text(encoding="utf-8") == '{"123":"123"}'
