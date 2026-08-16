from __future__ import annotations

import json
from pathlib import Path

import pytest

from translation import checkpoint
from translation.output import default_output_path, write_json_items
from translation.review.ai import (
    AIReviewModels,
    apply_ai_review_records,
    build_deterministic_reclassification_records,
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
        "reclassified": 0,
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


def test_ai_review_normalizes_small_tsu_in_otherwise_chinese_candidate(tmp_path, isolated_checkpoint_dir):
    source_path, output_path, items = _write_source_and_output(tmp_path, 1)

    def fake_translate(model, text, system_prompt, options):
        if "independent" in system_prompt:
            return json.dumps({
                "items": [{"i": 0, "choice": "candidate", "resolved": ["style_review"]}],
            })
        return json.dumps({
            "items": [{"i": 0, "decision": "revise", "t": "您好っ__KEEP_0__"}],
        }, ensure_ascii=False)

    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from translation.review.ai import begin_ai_review_session

    begin_ai_review_session(task_id="small-tsu-task", file_path=source_path, models=models, items=items, auto_apply=True)
    result = run_ai_review(
        task_id="small-tsu-task",
        file_path=source_path,
        items=items,
        models=models,
        output_path=output_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
        translator=fake_translate,
    )

    assert result["counts"]["fixed"] == 1
    assert result["counts"]["applied"] == 1
    assert result["records"][0]["after"] == "您好0"
    assert result["records"][0]["remaining_issues"] == []
    assert json.loads(Path(output_path).read_text(encoding="utf-8"))["こんにちは0"] == "您好0"


def test_ai_review_applies_narrative_request_refusal_wording(tmp_path, isolated_checkpoint_dir):
    source = "の頼みをついつい断り切れなかったのだ。"
    translated = "终究没能拒绝她的请求。"
    source_path = tmp_path / "ManualTransFile.json"
    source_path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
    output_path = default_output_path(str(source_path))
    write_json_items([(source, source)], output_path)
    checkpoint.init_checkpoint(str(source_path), total=1, model="api:original", translate_columns=[1], file_type="json")
    checkpoint.save_progress(
        str(source_path), 0, 0, source, source,
        status="review_required",
        issues=[{"type": "model_refusal", "message": "old broad refusal rule"}],
    )
    items = [{
        "row": 0,
        "source": source,
        "current": source,
        "status": "review_required",
        "issues": [{"type": "model_refusal", "message": "old broad refusal rule"}],
        "issue_types": ["model_refusal"],
        "neighbors": [],
        "entry_classification": "prose",
        "model_identifier": "api:original",
        "sensitive": False,
    }]

    def fake_translate(model, text, system_prompt, options):
        if "independent" in system_prompt:
            return json.dumps({"items": [{"i": 0, "choice": "candidate", "resolved": []}]})
        return json.dumps({"items": [{"i": 0, "decision": "revise", "t": translated}]}, ensure_ascii=False)

    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from translation.review.ai import begin_ai_review_session

    begin_ai_review_session(
        task_id="narrative-refusal",
        file_path=str(source_path),
        models=models,
        items=items,
        auto_apply=True,
    )
    result = run_ai_review(
        task_id="narrative-refusal",
        file_path=str(source_path),
        items=items,
        models=models,
        output_path=output_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
        translator=fake_translate,
    )

    assert result["counts"]["fixed"] == 1
    assert result["counts"]["applied"] == 1
    assert result["counts"]["unresolved"] == 0
    assert json.loads(Path(output_path).read_text(encoding="utf-8"))[source] == translated


def test_ai_review_reclassifies_stale_code_and_isolates_hard_retry(
    tmp_path,
    isolated_checkpoint_dir,
):
    code_required = ".setAnimation(0, '能力変化セラ1');"
    code_hidden = ".setAnimation(0, 'カーソル1');"
    prose = "テストモードです。"
    contaminated = "输出至テストモードです。"
    repaired = "这是测试模式。"
    source_path = tmp_path / "ManualTransFile.json"
    source_path.write_text(
        json.dumps({value: value for value in (code_required, code_hidden, prose)}, ensure_ascii=False),
        encoding="utf-8",
    )
    output_path = default_output_path(str(source_path))
    write_json_items([
        (code_required, code_required),
        (code_hidden, ".setAnimation(0, '光标1');"),
        (prose, contaminated),
    ], output_path)
    checkpoint.init_checkpoint(
        str(source_path), total=3, model="api:original", translate_columns=[1], file_type="json"
    )
    checkpoint.save_progress_many(str(source_path), [
        {
            "row": 0,
            "col": 0,
            "original": code_required,
            "translated": code_required,
            "status": "review_required",
            "issues": [{"type": "untranslated_japanese"}],
            "entry_classification": "short_label",
        },
        {
            "row": 1,
            "col": 0,
            "original": code_hidden,
            "translated": ".setAnimation(0, '光标1');",
            "status": "translated",
            "issues": [],
            "entry_classification": "short_label",
        },
        {
            "row": 2,
            "col": 0,
            "original": prose,
            "translated": contaminated,
            "status": "review_required",
            "issues": [{"type": "untranslated_japanese"}],
            "entry_classification": "multiline",
        },
    ])
    cp_entries = checkpoint.load_checkpoint(str(source_path))["entries"]
    reclassification_records = build_deterministic_reclassification_records(
        [code_required, code_hidden, prose],
        [code_required, ".setAnimation(0, '光标1');", contaminated],
        cp_entries,
    )
    assert {record["row"] for record in reclassification_records} == {0, 1}

    items = [
        {
            "row": 0,
            "source": code_required,
            "current": code_required,
            "status": "review_required",
            "issues": [{"type": "untranslated_japanese"}],
            "issue_types": ["untranslated_japanese"],
            "neighbors": [],
            "entry_classification": "short_label",
            "model_identifier": "api:original",
            "sensitive": False,
        },
        {
            "row": 2,
            "source": prose,
            "current": contaminated,
            "status": "review_required",
            "issues": [{"type": "untranslated_japanese"}],
            "issue_types": ["untranslated_japanese"],
            "neighbors": [],
            "entry_classification": "multiline",
            "model_identifier": "api:original",
            "sensitive": False,
        },
    ]
    primary_attempts = 0

    def fake_translate(model, text, system_prompt, options):
        nonlocal primary_attempts
        payload = json.loads(text)
        assert [item["i"] for item in payload["items"]] == [2]
        if "independent" in system_prompt:
            return json.dumps({"items": [{"i": 2, "choice": "candidate", "resolved": []}]})
        primary_attempts += 1
        if "final bounded repair" in system_prompt:
            assert payload["items"][0]["current"] == ""
            return json.dumps(
                {"items": [{"i": 2, "decision": "revise", "t": repaired}]},
                ensure_ascii=False,
            )
        return json.dumps(
            {"items": [{"i": 2, "decision": "revise", "t": contaminated}]},
            ensure_ascii=False,
        )

    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from translation.review.ai import begin_ai_review_session

    begin_ai_review_session(
        task_id="reclassify-task",
        file_path=str(source_path),
        models=models,
        items=items,
        auto_apply=True,
    )
    result = run_ai_review(
        task_id="reclassify-task",
        file_path=str(source_path),
        items=items,
        models=models,
        output_path=output_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
        translator=fake_translate,
        reclassification_records=reclassification_records,
    )

    assert primary_attempts == 2
    assert result["counts"]["reclassified"] == 2
    assert result["counts"]["fixed"] == 1
    assert result["counts"]["unresolved"] == 0
    assert result["counts"]["applied"] == 3
    output = json.loads(Path(output_path).read_text(encoding="utf-8"))
    assert output[code_required] == code_required
    assert output[code_hidden] == code_hidden
    assert output[prose] == repaired
    entries = checkpoint.load_checkpoint(str(source_path))["entries"]
    assert entries["0_0"]["status"] == "preserved"
    assert entries["1_0"]["status"] == "preserved"
    assert entries["2_0"]["status"] == "translated"

    rolled_back = rollback_ai_review_session(str(source_path), "reclassify-task", output_path)
    assert rolled_back["restored"] == 3
    restored_output = json.loads(Path(output_path).read_text(encoding="utf-8"))
    assert restored_output[code_hidden] == ".setAnimation(0, '光标1');"
    assert restored_output[prose] == contaminated


def test_ai_review_repairs_multiline_runtime_text_line_by_line(
    tmp_path,
    isolated_checkpoint_dir,
):
    source = "コンパイルモードです。\n          テストモードです。test/basic.txtを読み込みます。"
    expected = "这是编译模式。\n          这是测试模式。读取test/basic.txt。"
    source_path = tmp_path / "ManualTransFile.json"
    source_path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
    output_path = default_output_path(str(source_path))
    write_json_items([(source, source)], output_path)
    checkpoint.init_checkpoint(
        str(source_path), total=1, model="api:original", translate_columns=[1], file_type="json"
    )
    checkpoint.save_progress(
        str(source_path),
        0,
        0,
        source,
        source,
        status="review_required",
        issues=[{"type": "untranslated_japanese"}],
        entry_classification="multiline",
    )
    items = [{
        "row": 0,
        "source": source,
        "current": source,
        "status": "review_required",
        "issues": [{"type": "untranslated_japanese"}],
        "issue_types": ["untranslated_japanese"],
        "neighbors": [],
        "entry_classification": "multiline",
        "model_identifier": "api:original",
        "sensitive": False,
    }]
    saw_line_fallback = False

    def fake_translate(model, text, system_prompt, options):
        nonlocal saw_line_fallback
        payload = json.loads(text)
        if "independent" in system_prompt:
            return json.dumps({"items": [{"i": 0, "choice": "candidate", "resolved": []}]})
        if all(int(item["i"]) < 0 for item in payload["items"]):
            saw_line_fallback = True
            response_items = []
            for item in payload["items"]:
                translated = (
                    "这是编译模式。"
                    if "コンパイル" in item["source"]
                    else "          这是测试模式。读取__KEEP_0__。"
                )
                response_items.append({"i": item["i"], "decision": "revise", "t": translated})
            return json.dumps({"items": response_items}, ensure_ascii=False)
        return json.dumps({
            "items": [
                {"i": item["i"], "decision": "revise", "t": item["source"]}
                for item in payload["items"]
            ]
        }, ensure_ascii=False)

    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from translation.review.ai import begin_ai_review_session

    begin_ai_review_session(
        task_id="multiline-repair",
        file_path=str(source_path),
        models=models,
        items=items,
        auto_apply=True,
    )
    result = run_ai_review(
        task_id="multiline-repair",
        file_path=str(source_path),
        items=items,
        models=models,
        output_path=output_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
        translator=fake_translate,
    )

    assert saw_line_fallback
    assert result["counts"]["fixed"] == 1
    assert result["counts"]["unresolved"] == 0
    assert json.loads(Path(output_path).read_text(encoding="utf-8"))[source] == expected


def test_ai_review_validation_respects_authoritative_source_layout():
    from translation.review.ai import _validate_translation

    source = "一行目です。\n二行目です。\n三行目です。\n四行目です。\n五行目です。"
    translated = "第一行。\n第二行。\n第三行。\n第四行。\n第五行。"

    issues = _validate_translation(source, translated, [], short_label=False)

    assert "too_many_lines" not in {issue["type"] for issue in issues}


def test_ai_review_related_examples_use_translated_occurrences_only():
    from app.services.ai_review_tasks import _related_translation_examples

    sources = [
        "アイテムとして使用するとQホイーミを覚える。",
        "アイテムとして使用するQホイーミを覚える。",
        "Qホイーミの書",
        "別の文章",
    ]
    translated = [
        sources[0],
        "作为道具使用时，学会Q霍伊米。",
        "Qホイーミ之书",
        "另一段文字",
    ]

    examples = _related_translation_examples(0, sources[0], sources, translated)

    assert [item["row"] for item in examples] == [1]
    assert examples[0]["position"] == "related_occurrence"


def test_reclassification_preserves_evidence_backed_mixed_script_person_name():
    from translation.terminology import Glossary

    source = "陽向葵ゅか"
    glossary = Glossary.in_memory()
    glossary.candidates["陽向葵ゅ"] = {
        "count": 2,
        "targets": {},
        "target": "",
        "status": "candidate",
        "type": "person",
        "score": 0.3,
        "evidence": ["person_like"],
    }

    records = build_deterministic_reclassification_records(
        [source],
        [source],
        {
            "0_0": {
                "translated": source,
                "status": "review_required",
                "issues": [{"type": "untranslated_japanese"}],
                "entry_classification": "short_label",
            }
        },
        glossary=glossary,
    )

    assert len(records) == 1
    assert records[0]["after"] == source
    assert records[0]["final_status"] == "preserved"

    assert glossary.is_identified_person_name("陽向葵ゅか")
    assert not glossary.is_identified_person_name("陽向葵ゅかなり")


def test_reclassification_applies_safe_source_conditioned_angle_label_repair():
    source = "Aには-2に<すべて>の項目"
    current = "A包含-2至<すべて>的项目"

    records = build_deterministic_reclassification_records(
        [source],
        [current],
        {
            "0_0": {
                "translated": current,
                "status": "review_required",
                "issues": [{"type": "untranslated_japanese"}],
                "entry_classification": "multiline",
            }
        },
    )

    assert len(records) == 1
    assert records[0]["after"] == "A包含-2至<全部>的项目"
    assert records[0]["entry_classification"] == "deterministic_quality_repair"
    assert records[0]["final_status"] == "translated"


def test_ai_review_retries_verification_with_sensitive_primary_when_verifier_fails(
    tmp_path,
    isolated_checkpoint_dir,
):
    source_path, output_path, items = _write_source_and_output(tmp_path, 1)
    items[0]["sensitive"] = True
    calls: list[tuple[str, str]] = []

    def fake_translate(model, text, system_prompt, options):
        payload = json.loads(text)
        if "independent" in system_prompt:
            calls.append(("verifier", model))
            if model == "api:adult-verify":
                raise RuntimeError("temporary provider region restriction")
            return json.dumps({
                "items": [{"i": 0, "choice": "candidate", "resolved": ["style_review"]}],
            })
        calls.append(("primary", model))
        return json.dumps({
            "items": [{"i": 0, "decision": "revise", "t": "您好__KEEP_0__"}],
        }, ensure_ascii=False)

    models = AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify")
    from translation.review.ai import begin_ai_review_session

    begin_ai_review_session(task_id="fallback-task", file_path=source_path, models=models, items=items, auto_apply=True)
    result = run_ai_review(
        task_id="fallback-task",
        file_path=source_path,
        items=items,
        models=models,
        output_path=output_path,
        progress=lambda payload: None,
        cancelled=lambda: False,
        translator=fake_translate,
    )

    assert ("verifier", "api:adult-verify") in calls
    assert ("verifier", "api:adult") in calls
    assert result["counts"]["fixed"] == 1
    assert result["counts"]["applied"] == 1
    assert result["records"][0]["verifier_model"] == "api:adult"
    assert json.loads(Path(output_path).read_text(encoding="utf-8"))["こんにちは0"] == "您好0"


def test_ai_review_verifier_receives_blocking_and_advisory_candidate_issues(
    tmp_path,
    isolated_checkpoint_dir,
):
    seen_verifier_item: dict = {}

    def fake_translate(model, text, system_prompt, options):
        payload = json.loads(text)
        seen_verifier_item.update(payload["items"][0])
        return json.dumps({"items": [{"i": 0, "choice": "candidate", "resolved": []}]})

    from translation.review.ai import _verifier_batch

    _verifier_batch(
        [{"row": 0, "source": "銇撱倱銇仭銇?", "current": "浣犲ソ", "issue_types": ["style_review"]}],
        primary={
            0: {
                "translation": "鎮ㄥソ",
                "issues": [
                    {"type": "line_too_long"},
                    {"type": "untranslated_japanese"},
                ],
            }
        },
        model="api:verify",
        translator=fake_translate,
    )

    assert seen_verifier_item["candidate_blocking_issues"] == ["untranslated_japanese"]
    assert seen_verifier_item["candidate_advisory_issues"] == ["line_too_long"]


def test_ai_review_isolates_adjudication_when_verifier_selects_hard_invalid_current():
    calls: list[str] = []

    def fake_translate(model, text, system_prompt, options):
        calls.append(system_prompt)
        choice = "candidate" if "isolated adjudication" in system_prompt else "current"
        return json.dumps({"items": [{"i": 0, "choice": choice, "resolved": []}]})

    from translation.review.ai import _verifier_batch_with_fallback

    result = _verifier_batch_with_fallback(
        [{
            "row": 0,
            "source": "こんにちは",
            "current": "こんにちは",
            "issue_types": ["untranslated_japanese"],
            "short_label": False,
            "sensitive": False,
        }],
        primary={0: {"translation": "你好", "issues": []}},
        models=AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify"),
        translator=fake_translate,
    )

    assert len(calls) == 2
    assert result[0]["choice"] == "candidate"


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


def test_ai_review_excludes_derived_parent_and_recomposes_it_after_leaf_fix(
    tmp_path,
    isolated_checkpoint_dir,
):
    parent = "\u6328\u62f6\n\u6226\u95d8\u5411\u3051\u3058\u3083\u306a\u3044\u6226\u95d8\u670d\u2026\uff1f"
    greeting = "\u6328\u62f6"
    leaf = "\u6226\u95d8\u5411\u3051\u3058\u3083\u306a\u3044\u6226\u95d8\u670d\u2026\uff1f"
    source_path = tmp_path / "ManualTransFile.json"
    source_path.write_text(
        json.dumps({parent: parent, greeting: greeting, leaf: leaf}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_path = default_output_path(str(source_path))
    write_json_items(
        [(parent, f"\u95ee\u5019\n{leaf}"), (greeting, "\u95ee\u5019"), (leaf, leaf)],
        output_path,
    )
    checkpoint.init_checkpoint(
        str(source_path), total=3, model="api:original", translate_columns=[1], file_type="json"
    )
    checkpoint.save_progress_many(str(source_path), [
        {
            "row": 0,
            "col": 0,
            "original": parent,
            "translated": f"\u95ee\u5019\n{leaf}",
            "status": "review_required",
            "issues": [{
                "type": "composed_dependency_review_required",
                "message": "child requires review",
                "dependency_rows": [2],
            }],
            "entry_classification": "composed_multiline",
        },
        {
            "row": 1,
            "col": 0,
            "original": greeting,
            "translated": "\u95ee\u5019",
            "status": "translated",
        },
        {
            "row": 2,
            "col": 0,
            "original": leaf,
            "translated": leaf,
            "status": "review_required",
            "issues": [{"type": "model_refusal", "message": "model refused"}],
        },
    ])

    from app.services.ai_review_tasks import build_ai_review_items
    from app.services.review import load_review_context, matching_review_rows

    context = load_review_context(str(source_path))
    assert matching_review_rows(context, "required") == [2]
    assert context["stats"]["required_review"] == 1
    assert [
        item["row"]
        for item in build_ai_review_items(str(source_path), scope="selected", rows=[0, 2])
    ] == [2]

    fixed = "\u4e0d\u9002\u5408\u6218\u6597\u7684\u6218\u6597\u670d\u2026\uff1f"
    applied = apply_ai_review_records(
        task_id="derived-parent-task",
        file_path=str(source_path),
        output_path=output_path,
        records=[{
            "row": 2,
            "source": leaf,
            "before": leaf,
            "after": fixed,
            "before_status": "review_required",
            "before_issues": [{"type": "model_refusal", "message": "model refused"}],
            "final_status": "translated",
            "remaining_issues": [],
            "result": "fixed",
            "entry_classification": "dialogue",
            "review_model": "api:review",
            "verifier_model": "api:verify",
        }],
        models=AIReviewModels("api:review", "api:verify", "api:adult", "api:adult-verify"),
    )

    assert applied == 1
    output = json.loads(Path(output_path).read_text(encoding="utf-8"))
    assert output[leaf] == fixed
    assert output[parent] == f"\u95ee\u5019\n{fixed}"
    entries = checkpoint.load_checkpoint(str(source_path))["entries"]
    assert entries["0_0"]["status"] == "translated"
    assert entries["0_0"]["issues"] == []
