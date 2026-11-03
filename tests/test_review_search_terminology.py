from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.review import create_router
from app.services import ai_review_tasks
from app.services.files import translated_path
from app.services.glossary import apply_term_edit_to_outputs, glossary_occurrences
from app.services.review import build_review_row, load_review_context, matching_review_rows
from translation import checkpoint
from translation.output import write_json_items
from translation.terminology import Glossary


@pytest.fixture
def search_file(tmp_path):
    source_path = tmp_path / "检索 任务.json"
    sources = [f"名前{i}です。" for i in range(60)]
    sources[40] = "\\C[2]アリス\n帰ります。"
    items = [(source, source) for source in sources]
    write_json_items(items, str(source_path))
    outputs = [f"译文{i}" for i in range(60)]
    for row in (3, 35, 59):
        outputs[row] = f"特别的译文{row}"
    outputs[50] = "MixedCASE\n词语"
    write_json_items(list(zip(sources, outputs)), translated_path(str(source_path)))
    checkpoint.save_progress_many(str(source_path), [
        {
            "row": row, "col": 0, "original": source, "translated": outputs[row],
            "status": "translated_needs_review" if row == 35 else "translated",
        }
        for row, source in enumerate(sources)
    ])
    return str(source_path)


def client_for_review():
    app = FastAPI()
    app.include_router(create_router(tasks={}))
    return TestClient(app)


def test_review_search_runs_before_pagination_and_jump_uses_the_same_result(search_file):
    original = open(search_file, "rb").read()
    with client_for_review() as client:
        params = {"file_path": search_file, "filter": "all", "query": "特别", "scope": "target", "limit": 2}
        first = client.get("/api/review/list", params=params).json()
        second = client.get("/api/review/list", params={**params, "offset": 2}).json()
        assert first["matched_total"] == second["matched_total"] == 3
        assert [item["row"] for item in first["items"]] == [3, 35]
        assert [item["row"] for item in second["items"]] == [59]
        jump = client.get("/api/review/jump", params={**params, "row": 59}).json()
        assert jump["found"] and jump["offset"] == 2
        assert not client.get("/api/review/jump", params={**params, "row": 40}).json()["found"]
        filtered = client.get("/api/review/list", params={**params, "filter": "advisory"}).json()
        assert filtered["matched_total"] == 1
        assert filtered["items"][0]["row"] == 35
        assert client.get("/api/review/list", params={**params, "scope": "invalid"}).status_code == 400
    assert open(search_file, "rb").read() == original


def test_search_scope_casefold_multiline_and_snapshot_cache_invalidation(search_file):
    context = load_review_context(search_file)
    assert matching_review_rows(context, "all", "アリス", "source") == [40]
    assert matching_review_rows(context, "all", "アリス", "target") == []
    matched = matching_review_rows(context, "all", "mixedcase\n词语", "both")
    assert matched == [50]
    assert matching_review_rows(context, "all", "mixedcase\n词语", "both") is matched
    assert load_review_context(search_file) is context
    source = context["source_texts"][50]
    checkpoint.save_progress(search_file, 50, 0, source, "更新后的中文", status="translated")
    current = load_review_context(search_file)
    assert current is not context
    assert matching_review_rows(current, "all", "mixedcase", "target") == []
    assert matching_review_rows(current, "all", "更新后的中文", "target") == [50]
    assert build_review_row(current, 40)["columns"][0]["key"] == "\\C[2]アリス\n帰ります。"


def test_manual_confirmation_requires_saved_matching_evidence(search_file):
    assert build_review_row(load_review_context(search_file), 3)["columns"][0]["manual_confirmed"] is False
    with client_for_review() as client:
        params = {"file_path": search_file, "row": 3, "col": 0, "text": "手动确认的译文", "action": "accept"}
        assert client.post("/api/review/save", json=params).status_code == 200
        assert client.get("/api/review", params={"file_path": search_file, "row": 3}).json()["columns"][0]["manual_confirmed"] is True
        assert client.post("/api/review/save", json={**params, "action": "draft"}).status_code == 200
        assert client.get("/api/review", params={"file_path": search_file, "row": 3}).json()["columns"][0]["manual_confirmed"] is False


def test_restored_draft_refuses_changed_source_before_writing(search_file):
    from pathlib import Path

    output = Path(translated_path(search_file))
    before_output = output.read_bytes()
    before_checkpoint = checkpoint.get_entry(search_file, 3, 0)
    payload = {"file_path": search_file, "row": 3, "col": 0, "text": "恢复的译文草稿", "action": "draft"}
    with client_for_review() as client:
        refused = client.post("/api/review/save", json={**payload, "expected_original": "旧版本原文"})
        assert refused.status_code == 409
        assert output.read_bytes() == before_output
        assert checkpoint.get_entry(search_file, 3, 0) == before_checkpoint
        saved = client.post("/api/review/save", json={**payload, "expected_original": "名前3です。"})
        assert saved.status_code == 200
        assert saved.json()["text"] == "恢复的译文草稿"


@pytest.fixture
def term_file(tmp_path):
    path = str(tmp_path / "术语.json")
    originals = ["アリス", "アリスはアリスを呼んだ。", "アリスン", "アリス画像.png", "アリス\n待った。"]
    translations = ["爱丽丝", "爱丽丝呼唤爱丽丝。", "阿莉森", "アリス画像.png", "爱丽丝\n等待。"]
    write_json_items(list(zip(originals, originals)), path)
    write_json_items(list(zip(originals, translations)), translated_path(path))
    checkpoint.save_progress_many(path, [
        {
            "row": row, "col": 0, "original": source, "translated": translations[row],
            "status": "preserved" if row == 3 else "translated",
            "entry_classification": "composed_multiline" if row == 4 else "prose",
        }
        for row, source in enumerate(originals)
    ])
    glossary = Glossary(file_path=checkpoint.get_glossary_path(path))
    glossary.add("アリス", "爱丽丝", manual=True)
    glossary.save()
    return path


def test_term_preview_counts_real_occurrences_and_keeps_every_file_unchanged(term_file):
    from pathlib import Path

    paths = [term_file, translated_path(term_file), checkpoint.get_glossary_path(term_file)]
    before = {path: Path(path).read_bytes() for path in paths}
    preview = glossary_occurrences(term_file, "アリス", target="艾莉丝", limit=2)
    assert preview["preview_only"] and preview["target_valid"]
    assert preview["entry_count"] == 4
    assert preview["occurrence_count"] == 5
    assert preview["affected_count"] == 2
    assert preview["items"][0]["preview_translation"] == "艾莉丝"
    assert preview["items"][0]["effect"] == "replace"
    assert preview["items"][1]["effect"] == "revalidate"
    assert preview["items"][1]["translated"] == "爱丽丝呼唤爱丽丝。"
    next_page = glossary_occurrences(term_file, "アリス", target="艾莉丝", offset=2, limit=2)
    assert [item["row"] for item in next_page["items"]] == [3, 4]
    assert [item["effect"] for item in next_page["items"]] == ["preserved", "derived"]
    assert glossary_occurrences(term_file, "アリス", target="")["target_valid"] is False
    assert {path: Path(path).read_bytes() for path in paths} == before


def test_confirmed_term_backfill_refreshes_search_and_keeps_original_keys(term_file):
    before = load_review_context(term_file)
    original_keys = list(json.loads(open(term_file, encoding="utf-8").read()))
    glossary = Glossary(file_path=checkpoint.get_glossary_path(term_file))
    glossary.add("アリス", "艾莉丝", manual=True)
    glossary.save()
    assert apply_term_edit_to_outputs(term_file, "アリス", "爱丽丝", "アリス", "艾莉丝", tasks={}, defer_repair=True) == 2
    after = load_review_context(term_file)
    assert after is not before
    assert matching_review_rows(after, "all", "艾莉丝", "target") == [0]
    output = json.loads(open(translated_path(term_file), encoding="utf-8").read())
    assert list(output) == original_keys
    assert output["アリス画像.png"] == "アリス画像.png"


def test_ai_filtered_search_limits_model_and_deterministic_work(search_file, monkeypatch):
    items = ai_review_tasks.build_ai_review_items(search_file, scope="filter", filter_name="all", query="特别", search_scope="target")
    assert [item["row"] for item in items] == [35]
    monkeypatch.setattr(ai_review_tasks, "build_reclassification_records", lambda _: [{"row": 3}, {"row": 35}, {"row": 40}])
    records = ai_review_tasks._scoped_reclassification_records(search_file, scope="filter", rows=None, filter_name="all", query="特别", search_scope="target")
    assert [record["row"] for record in records] == [3, 35]
    selected = ai_review_tasks._scoped_reclassification_records(search_file, scope="selected", rows=[40], filter_name="all", query="特别", search_scope="target")
    assert [record["row"] for record in selected] == [40]
