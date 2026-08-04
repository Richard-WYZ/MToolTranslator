from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation import checkpoint
from translation.output import default_output_path, write_json_items
from translation.review.ai import (
    AIReviewModels,
    get_ai_review_session,
    load_ai_review_store,
    rollback_ai_review_session,
    run_ai_review,
)


@pytest.fixture
def isolated_checkpoint_dir(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "checkpoints"
    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(checkpoint_dir))
    return checkpoint_dir


def _write_source_and_output(tmp_path: Path, count: int) -> tuple[str, str, list[dict]]:
    source_path = tmp_path / "ManualTransFile.json"
    source_items = [(f"こんにちは{i}", f"こんにちは{i}") for i in range(count)]
    source_path.write_text(json.dumps(dict(source_items), ensure_ascii=False, indent=2), encoding="utf-8")
    output_path = default_output_path(str(source_path))
    output_items = [(key, f"你好{i}") for i, (key, _value) in enumerate(source_items)]
    write_json_items(output_items, output_path)
    checkpoint.init_checkpoint(str(source_path), total=count, model="api:original", translate_columns=[1], file_type="json")
    checkpoint.save_progress_many(str(source_path), [
        {
            "row": i,
            "col": 0,
            "original": key,
            "translated": f"你好{i}",
            "status": "translated_needs_review",
            "issues": [{"type": "style_review", "message": "Conservative advisory review"}],
        }
        for i, (key, _value) in enumerate(source_items)
    ])
    review_items = [
        {
            "row": i,
            "source": key,
            "current": f"你好{i}",
            "status": "translated_needs_review",
            "issues": [{"type": "style_review", "message": "Conservative advisory review"}],
            "issue_types": ["style_review"],
            "neighbors": [],
            "entry_classification": "short_label",
            "model_identifier": "api:original",
            "sensitive": False,
        }
        for i, (key, _value) in enumerate(source_items)
    ]
    return str(source_path), output_path, review_items


def test_ai_review_batches_and_confirms_existing_translations(tmp_path, isolated_checkpoint_dir):
    source_path, output_path, items = _write_source_and_output(tmp_path, 25)
    calls: list[str] = []

    def fake_translate(model, text, system_prompt, options):
        payload = json.loads(text)
        if "independent" in system_prompt:
            calls.append("verify")
            return json.dumps({
                "items": [
                    {"i": item["i"], "choice": "current", "resolved": ["style_review"]}
                    for item in payload["items"]
                ]
            }, ensure_ascii=False)
        calls.append("primary")
        return json.dumps({
            "items": [
                {"i": item["i"], "decision": "keep", "t": item["current"]}
                for item in payload["items"]
            ]
        }, ensure_ascii=False)

    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from translation.review.ai import begin_ai_review_session

    begin_ai_review_session(
        task_id="review-task",
        file_path=source_path,
        models=models,
        items=items,
        auto_apply=True,
    )
    result = run_ai_review(
        task_id="review-task",
        file_path=source_path,
        items=items,
        models=models,
        output_path=output_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
        translator=fake_translate,
    )

    assert calls.count("primary") == 2
    assert calls.count("verify") == 2
    assert result["counts"] == {
        "fixed": 0,
        "confirmed": 25,
        "unresolved": 0,
        "conflict": 0,
        "applied": 25,
    }
    assert all(entry["status"] == "translated" for entry in checkpoint.load_checkpoint(source_path)["entries"].values())
    session = get_ai_review_session(source_path, "review-task")
    assert session and session["status"] == "completed"
    assert session["work"]["primary_complete"] is True
    assert session["work"]["verifier_complete"] is True


def test_ai_review_rejects_candidate_with_hard_issue_even_if_verifier_selects_it(tmp_path, isolated_checkpoint_dir):
    source_path, output_path, items = _write_source_and_output(tmp_path, 1)

    def fake_translate(model, text, system_prompt, options):
        payload = json.loads(text)
        if "independent" in system_prompt:
            return json.dumps({"items": [{"i": 0, "choice": "candidate", "resolved": ["untranslated_japanese"]}]})
        return json.dumps({"items": [{"i": 0, "decision": "revise", "t": "こんにちは0"}]}, ensure_ascii=False)

    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from translation.review.ai import begin_ai_review_session

    begin_ai_review_session(task_id="hard-task", file_path=source_path, models=models, items=items, auto_apply=True)
    result = run_ai_review(
        task_id="hard-task",
        file_path=source_path,
        items=items,
        models=models,
        output_path=output_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
        translator=fake_translate,
    )
    assert result["counts"]["unresolved"] == 1
    assert result["counts"]["applied"] == 0
    assert json.loads(Path(output_path).read_text(encoding="utf-8"))["こんにちは0"] == "你好0"


def test_ai_review_rollback_is_conflict_safe(tmp_path, isolated_checkpoint_dir):
    source_path, output_path, items = _write_source_and_output(tmp_path, 2)

    def fake_translate(model, text, system_prompt, options):
        payload = json.loads(text)
        if "independent" in system_prompt:
            return json.dumps({"items": [{"i": item["i"], "choice": "candidate", "resolved": []} for item in payload["items"]]})
        return json.dumps({
            "items": [
                {"i": item["i"], "decision": "revise", "t": item["source"].replace("こんにちは", "您好")}
                for item in payload["items"]
            ]
        }, ensure_ascii=False)

    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from translation.review.ai import begin_ai_review_session

    begin_ai_review_session(task_id="rollback-task", file_path=source_path, models=models, items=items, auto_apply=True)
    result = run_ai_review(
        task_id="rollback-task",
        file_path=source_path,
        items=items,
        models=models,
        output_path=output_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
        translator=fake_translate,
    )
    assert result["counts"]["applied"] == 2

    output = json.loads(Path(output_path).read_text(encoding="utf-8"))
    output["こんにちは1"] = "用户后续修改"
    Path(output_path).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    rolled_back = rollback_ai_review_session(source_path, "rollback-task", output_path)
    assert rolled_back == {"ok": True, "restored": 1, "skipped": 1, "already_rolled_back": False}
    restored = json.loads(Path(output_path).read_text(encoding="utf-8"))
    assert restored["こんにちは0"] == "你好0"
    assert restored["こんにちは1"] == "用户后续修改"
    assert load_ai_review_store(source_path)["sessions"][-1]["rolled_back_at"]


def test_review_model_selection_requires_current_tests_and_uses_independent_verifier(monkeypatch):
    from app.services import ai_review_tasks as service

    monkeypatch.setattr(service, "available_models", lambda: [
        {"name": "api:qwen3.7-plus", "enabled": True},
        {"name": "api:minimax-m3", "enabled": True},
        {"name": "api:untested", "enabled": True},
    ])
    monkeypatch.setattr(service, "public_model_statuses", lambda: {
        "api:qwen3.7-plus": {
            "basic": {"status": "available", "stale": False},
            "adult": {"status": "restricted", "stale": False},
        },
        "api:minimax-m3": {
            "basic": {"status": "available", "stale": False},
            "adult": {"status": "available", "stale": False},
        },
    })
    models = service.resolve_ai_review_models(
        review_model="auto",
        verifier_model="auto",
        sensitive_model="auto",
        needs_sensitive=True,
    )
    assert models.review == "api:qwen3.7-plus"
    assert models.verifier == "api:minimax-m3"
    assert models.sensitive == "api:minimax-m3"
    assert models.sensitive_verifier == "api:minimax-m3"

    with pytest.raises(ValueError, match="not enabled and currently tested"):
        service.resolve_ai_review_models(
            review_model="api:untested",
            verifier_model="auto",
            sensitive_model="auto",
            needs_sensitive=False,
        )


def test_interrupted_ai_review_session_is_exposed_as_resumable(tmp_path, isolated_checkpoint_dir):
    source_path, _output_path, items = _write_source_and_output(tmp_path, 1)
    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from app.services.ai_review_tasks import persisted_ai_review_progress
    from translation.review.ai import begin_ai_review_session, update_ai_review_session_status

    begin_ai_review_session(task_id="resume-task", file_path=source_path, models=models, items=items, auto_apply=True)
    update_ai_review_session_status(source_path, "resume-task", "verifying")
    progress = persisted_ai_review_progress(source_path)
    assert progress
    assert progress["status"] == "interrupted"
    assert progress["can_resume"] is True
    assert progress["task_id"] == "resume-task"


def test_selected_ai_review_excludes_system_preserved_rows(tmp_path, isolated_checkpoint_dir):
    source_path, _output_path, _items = _write_source_and_output(tmp_path, 2)
    checkpoint.save_progress(
        source_path, 0, 0, "こんにちは0", "こんにちは0",
        status="preserved", entry_classification="deterministic",
    )
    from app.services.ai_review_tasks import build_ai_review_items

    items = build_ai_review_items(source_path, scope="selected", rows=[0, 1])

    assert [item["row"] for item in items] == [1]
