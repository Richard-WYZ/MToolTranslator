from __future__ import annotations

import json


def test_offline_audit_reports_structure_and_identical_eligible_japanese(tmp_path):
    from tools.audit_translation_output import audit_translation_output

    source_path = tmp_path / "source.json"
    output_path = tmp_path / "output.json"
    source_path.write_text(
        json.dumps({"へと向かい": "へと向かい", "EV001": "EV001"}, ensure_ascii=False),
        encoding="utf-8",
    )
    output_path.write_text(
        json.dumps({"へと向かい": "へと向かい", "EV001": "EV001"}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = audit_translation_output(source_path, output_path)

    assert report["structure"] == {
        "source_entries": 2,
        "output_entries": 2,
        "same_key_order": True,
        "missing_key_count": 0,
        "extra_key_count": 0,
        "missing_keys": [],
        "extra_keys": [],
    }
    assert report["statuses"] == {"review_required": 1, "preserved": 1}
    assert report["issue_counts"]["identical_japanese_source"] == 1
    assert "model_refusal" not in report["issue_counts"]


def test_offline_audit_does_not_flag_explicitly_preserved_empty_or_code_entries(tmp_path):
    from tools.audit_translation_output import audit_translation_output

    source_path = tmp_path / "source.json"
    output_path = tmp_path / "output.json"
    payload = {"": "", "EV001": "EV001", "assets/picture.png": "assets/picture.png"}
    source_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = audit_translation_output(source_path, output_path)

    assert report["statuses"] == {"preserved": 3}
    assert report["issue_counts"] == {}


def test_offline_audit_accepts_identical_han_text_as_usable_chinese(tmp_path):
    from tools.audit_translation_output import audit_translation_output

    source_path = tmp_path / "source.json"
    output_path = tmp_path / "output.json"
    payload = {"女神": "女神", "武器": "武器"}
    source_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = audit_translation_output(source_path, output_path)

    assert report["statuses"] == {"preserved": 2}
    assert report["issue_counts"] == {}


def test_offline_audit_deduplicates_punctuation_only_refusal_issue(tmp_path):
    from tools.audit_translation_output import audit_translation_output

    source_path = tmp_path / "source.json"
    output_path = tmp_path / "output.json"
    source_path.write_text(
        json.dumps({"手に入れてください」": "手に入れてください」"}, ensure_ascii=False),
        encoding="utf-8",
    )
    output_path.write_text(
        json.dumps({"手に入れてください」": "」"}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = audit_translation_output(source_path, output_path)

    assert report["issue_entry_count"] == 1
    assert report["issue_counts"] == {"model_refusal": 1}


def test_review_report_reconciles_quality_against_serialized_artifact(monkeypatch, tmp_path):
    import translation.checkpoint as checkpoint
    from translation.review import write_review_report

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "source.translated.json"
    source = "一行目。\n二行目。"
    source_path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
    output_path.write_text(
        json.dumps({source: "第一行。\n第二行。__KEEP__"}, ensure_ascii=False),
        encoding="utf-8",
    )
    checkpoint.init_checkpoint(str(source_path), total=1)
    checkpoint.save_progress(
        str(source_path),
        0,
        0,
        source,
        "第一行。\n第二行。",
        status="translated",
        issues=[],
        json_key=source,
    )

    report_path = write_review_report(str(source_path), str(output_path))
    report = json.loads(open(report_path, encoding="utf-8").read())

    assert report["artifact_validation"]["reconciled_entry_count"] == 1
    assert report["summary"]["review_required"] == 1
    assert report["items"][0]["translation"].endswith("__KEEP__")
    assert "internal_placeholder_leak" in {
        issue["type"] for issue in report["items"][0]["issues"]
    }

    output_path.write_text(
        json.dumps({source: "第一行。\n第二行。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    repaired_report = json.loads(
        open(write_review_report(str(source_path), str(output_path)), encoding="utf-8").read()
    )
    assert repaired_report["summary"]["review_required"] == 0
    assert repaired_report["items"] == []


def test_final_artifact_audit_does_not_trust_invalid_preserved_status(monkeypatch, tmp_path):
    import translation.checkpoint as checkpoint
    from translation.review import write_review_report

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "source.translated.json"
    source = "いらっしゃい"
    source_path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
    output_path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
    checkpoint.init_checkpoint(str(source_path), total=1)
    checkpoint.save_progress(
        str(source_path), 0, 0, source, source, status="preserved", issues=[], json_key=source
    )

    report_path = write_review_report(str(source_path), str(output_path))
    report = json.loads(open(report_path, encoding="utf-8").read())

    assert report["summary"]["review_required"] == 1
    assert {
        "identical_japanese_source",
        "untranslated_japanese",
    }.issubset({issue["type"] for issue in report["items"][0]["issues"]})


def test_final_artifact_audit_replaces_stale_output_issues_without_metadata(monkeypatch, tmp_path):
    import translation.checkpoint as checkpoint
    from translation.review import write_review_report

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "source.translated.json"
    source = "王子様の口調だ。"
    translated = "仍是王子殿下的口吻。"
    source_path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
    output_path.write_text(json.dumps({source: translated}, ensure_ascii=False), encoding="utf-8")
    checkpoint.init_checkpoint(str(source_path), total=1)
    checkpoint.save_progress(
        str(source_path),
        0,
        0,
        source,
        translated,
        status="translated_needs_review",
        issues=[
            {"type": "honorific_rendering_review", "message": "stale"},
            {"type": "api_request_fallback", "message": "diagnostic"},
        ],
        json_key=source,
    )

    report = json.loads(open(write_review_report(str(source_path), str(output_path)), encoding="utf-8").read())
    entry = checkpoint.load_checkpoint(str(source_path))["entries"]["0_0"]

    assert report["items"] == []
    assert entry["status"] == "translated"
    assert entry["issues"] == [{"type": "api_request_fallback", "message": "diagnostic"}]


def test_final_artifact_audit_clears_stale_glossary_name_alarm(monkeypatch, tmp_path):
    import translation.checkpoint as checkpoint
    from translation.review import write_review_report
    from translation.terminology import Glossary

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "source.translated.json"
    source = "ご主人さま……！"
    translated = "主人大人……！"
    source_path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
    output_path.write_text(json.dumps({source: translated}, ensure_ascii=False), encoding="utf-8")
    checkpoint.init_checkpoint(str(source_path), total=1)
    glossary = Glossary(file_path=checkpoint.get_glossary_path(str(source_path)))
    glossary.add("ヌシ", "主人", term_type="person")
    glossary.save()
    checkpoint.save_progress(
        str(source_path), 0, 0, source, translated, status="review_required",
        issues=[{
            "type": "unsupported_glossary_name",
            "message": "Target contains confirmed term '主人', but source does not contain 'ヌシちゃん'.",
        }],
        json_key=source,
    )

    report = json.loads(open(write_review_report(str(source_path), str(output_path)), encoding="utf-8").read())
    entry = checkpoint.load_checkpoint(str(source_path))["entries"]["0_0"]

    assert report["items"] == []
    assert report["summary"]["review_required"] == 0
    assert entry["status"] == "translated"
    assert entry["issues"] == []


def test_final_artifact_audit_clears_name_uncertainty_after_term_confirmation(monkeypatch, tmp_path):
    import translation.checkpoint as checkpoint
    from translation.review import write_review_report
    from translation.terminology import Glossary

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "source.translated.json"
    source = "レイスレスを倒す。"
    translated = "打倒无魂者。"
    source_path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
    output_path.write_text(json.dumps({source: translated}, ensure_ascii=False), encoding="utf-8")
    checkpoint.init_checkpoint(str(source_path), total=1)
    glossary = Glossary(file_path=checkpoint.get_glossary_path(str(source_path)))
    glossary.add("レイスレス", "无魂者", term_type="proper_noun", manual=True)
    glossary.save()
    checkpoint.save_progress(
        str(source_path), 0, 0, source, translated, status="translated_needs_review",
        issues=[
            {"type": "uncertain_person_name", "message": "A character-name transliteration needs confirmation: レイスレス"},
            {"type": "glossary_update_pending", "message": "Old term version"},
        ], json_key=source,
    )

    report = json.loads(open(write_review_report(str(source_path), str(output_path)), encoding="utf-8").read())
    entry = checkpoint.load_checkpoint(str(source_path))["entries"]["0_0"]
    assert report["items"] == []
    assert entry["status"] == "translated"
    assert entry["issues"] == []
