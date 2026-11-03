from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.review import create_router
from app.services.files import translated_path
from translation import checkpoint
from translation.input import load_json_items
from translation.output import write_json_items


@pytest.fixture
def review_project(tmp_path):
    source = str(tmp_path / "原生恢复冲突.json")
    text = "今日はいい天気です。"
    write_json_items([(text, text)], source)
    output = Path(translated_path(source))
    write_json_items([(text, "最初机器译文")], str(output))
    checkpoint.save_progress(source, 0, 0, text, "最初机器译文", status="translated_needs_review")
    app = FastAPI()
    app.include_router(create_router(tasks={}))
    with TestClient(app) as client:
        yield client, source, output, text


@pytest.mark.parametrize("action", ["draft", "accept", "preserve"])
def test_stale_native_recovery_cannot_overwrite_a_newly_saved_translation(review_project, action):
    client, source, output, original = review_project
    common = {"file_path": source, "row": 0, "col": 0, "expected_original": original}
    saved = client.post("/api/review/save", json={
        **common, "text": "已经保存的新译文", "action": "accept", "expected_translation": "最初机器译文",
    })
    assert saved.status_code == 200
    saved_output = output.read_bytes()
    saved_checkpoint = checkpoint.get_entry(source, 0, 0)

    # A failed native recovery-cache clear leaves this old base translation.
    # The source is unchanged, so only the output-version guard can reject it.
    restored = client.post("/api/review/save", json={
        **common, "text": "旧原生草稿", "action": action, "expected_translation": "最初机器译文",
    })
    assert restored.status_code == 409
    assert "译文已变化" in restored.json()["detail"]
    assert output.read_bytes() == saved_output
    assert checkpoint.get_entry(source, 0, 0) == saved_checkpoint
    assert load_json_items(source) == [(original, original)]


def test_matching_output_version_and_legacy_unversioned_request_remain_supported(review_project):
    client, source, output, original = review_project
    common = {"file_path": source, "row": 0, "col": 0, "expected_original": original, "action": "draft"}
    first = client.post("/api/review/save", json={
        **common, "text": "新的草稿译文", "expected_translation": "最初机器译文",
    })
    assert first.status_code == 200
    second = client.post("/api/review/save", json={**common, "text": "兼容旧客户端的保存"})
    assert second.status_code == 200
    assert load_json_items(str(output)) == [(original, "兼容旧客户端的保存")]
