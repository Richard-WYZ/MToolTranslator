from __future__ import annotations

import pytest

from app.services.review import build_review_row, load_review_context, matching_review_rows
from app.services.translation_tasks import TranslationTask
from translation import checkpoint
from translation.context import TranslationResult
from translation.output import write_json_items


@pytest.fixture
def isolated_checkpoint_dir(tmp_path):
    original_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        yield
    finally:
        checkpoint.CHECKPOINT_DIR = original_dir


def _task(file_path: str) -> TranslationTask:
    return TranslationTask(
        task_id="test-task",
        file_path=file_path,
        model="api:test",
        prompt_style="professional",
        translate_columns=[1],
    )


def test_progress_snapshot_is_aggregate_only_and_does_not_persist(tmp_path):
    source_path = tmp_path / "source.json"
    write_json_items([("原文", "原文")], str(source_path))
    task = _task(str(source_path))

    class Runtime:
        calls = 0

        def token_usage(self):
            self.calls += 1
            return {"total_tokens": 12}

    runtime = Runtime()
    task.runtime = runtime
    task.status = "running"
    task.started_at = 1.0
    task._update_progress({
        "processed": 1,
        "total": 1,
        "percent": 100,
        "status": "translated",
        "original_text": "原文",
        "translated_text": "译文",
    })

    progress = task.get_progress()

    assert progress["percentage"] == 99.9
    assert progress["completion_verified"] is False
    assert progress["token_usage"] == {"total_tokens": 12}
    assert "current_original" not in progress
    assert "current_translated" not in progress
    assert runtime.calls == 1


def test_completion_requires_full_checkpoint_output_and_report(tmp_path, isolated_checkpoint_dir):
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "source.translated.json"
    report_path = tmp_path / "source.translated.review.json"
    original_items = [("一", "一"), ("", "")]
    write_json_items(original_items, str(source_path))
    write_json_items([("一", "壹"), ("", "")], str(output_path))
    report_path.write_text("{}", encoding="utf-8")
    checkpoint.save_progress(str(source_path), 0, 0, "一", "壹", status="translated")
    checkpoint.save_progress(str(source_path), 1, 0, "", "", status="preserved")
    result = TranslationResult(
        file_path=str(source_path),
        output_path=str(output_path),
        review_summary={"total": 2, "pending": 0},
        review_report_path=str(report_path),
    )
    task = _task(str(source_path))

    assert task._validate_completion(result) == 2

    sessions = checkpoint.list_translation_sessions()
    session = next(item for item in sessions if item["file_path"] == str(source_path))
    assert session["translated_needs_review"] == 0
    assert session["review_required"] == 0
    assert session["review_queue_size"] == 0

    checkpoint_data = checkpoint.load_checkpoint(str(source_path))
    checkpoint_data["entries"].pop("1_0")
    checkpoint.save_checkpoint(str(source_path), checkpoint_data)
    with pytest.raises(RuntimeError, match="未完成的检查点条目"):
        task._validate_completion(result)


def test_review_uses_checkpoint_translation_and_excludes_preserved_false_positives(
    tmp_path,
    isolated_checkpoint_dir,
):
    source_path = tmp_path / "review.json"
    long_config = "StartTime=00:00:00 EndTime=99:99:99 " * 4
    write_json_items([(long_config, long_config), ("こんにちは", "こんにちは")], str(source_path))
    checkpoint.save_progress(
        str(source_path),
        0,
        0,
        long_config,
        long_config,
        status="preserved",
        entry_classification="configuration",
    )
    checkpoint.save_progress(
        str(source_path),
        1,
        0,
        "こんにちは",
        "你好",
        status="translated_needs_review",
        issues=[{"type": "honorific_review", "message": "check context"}],
    )

    context = load_review_context(str(source_path))
    cached = load_review_context(str(source_path))
    preserved = build_review_row(context, 0)["columns"][0]
    actionable = build_review_row(context, 1)["columns"][0]

    assert cached is context
    assert preserved["status"] == "preserved"
    assert preserved["violations"] == []
    assert preserved["is_refusal"] is False
    assert actionable["translated"] == "你好"
    assert matching_review_rows(context, "issues") == [1]
    assert matching_review_rows(context, "length") == []
    assert context["stats"]["needs_review"] == 1
    assert context["stats"]["diagnostics_count"] == 0
    session = next(
        item for item in checkpoint.list_translation_sessions()
        if item["file_path"] == str(source_path)
    )
    assert session["translated_needs_review"] == 1
    assert session["review_required"] == 0
    assert session["review_queue_size"] == 1


def test_review_separates_required_advisory_and_system_preserved(
    tmp_path,
    isolated_checkpoint_dir,
):
    source_path = tmp_path / "review-categories.json"
    write_json_items(
        [("12345", "12345"), ("こんにちは", "こんにちは"), ("さようなら", "さようなら")],
        str(source_path),
    )
    checkpoint.save_progress(
        str(source_path), 0, 0, "12345", "12345",
        status="preserved", entry_classification="deterministic",
    )
    checkpoint.save_progress(
        str(source_path), 1, 0, "こんにちは", "你好",
        status="translated_needs_review",
    )
    checkpoint.save_progress(
        str(source_path), 2, 0, "さようなら", "",
        status="review_required",
        issues=[{"type": "model_refusal", "message": "empty output"}],
    )

    context = load_review_context(str(source_path))

    assert matching_review_rows(context, "required") == [2]
    assert matching_review_rows(context, "advisory") == [1]
    assert matching_review_rows(context, "preserved") == [0]
    assert matching_review_rows(context, "issues") == [1, 2]
    assert context["stats"]["required_review"] == 1
    assert context["stats"]["advisory_review"] == 1
    assert context["stats"]["system_preserved"] == 1
