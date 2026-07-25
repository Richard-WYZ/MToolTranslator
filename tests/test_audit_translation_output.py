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
