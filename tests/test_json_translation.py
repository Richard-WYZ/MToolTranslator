"""Test JSON translation support in TranslationPipeline."""
import os
import sys
import json
import tempfile
import shutil
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Monkey-patch checkpoint dir BEFORE importing pipeline
import translator.checkpoint as cp

TMPDIR = tempfile.mkdtemp()
CHECKPOINT_DIR = os.path.join(TMPDIR, ".checkpoints")
os.makedirs(CHECKPOINT_DIR, exist_ok=True)
cp.CHECKPOINT_DIR = CHECKPOINT_DIR

from translator.pipeline import TranslationPipeline


class MockPipeline(TranslationPipeline):
    """Pipeline that mocks translate_cell to avoid real Ollama calls."""

    def __init__(self):
        super().__init__(model="test-model")
        self.call_count = 0
        self.called_with = []

    def translate_cell(self, text, row_idx, col_idx):
        self.call_count += 1
        self.called_with.append((text, row_idx, col_idx))
        return f"TRANSLATED:{text}"


def make_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_dict_mtool_format():
    """MTool JSON uses keys as source text and updates only values."""
    path = os.path.join(TMPDIR, "mtool.json")
    make_json(path, {
        "こんにちは": "こんにちは",
        "世界": "世界",
    })

    pipe = MockPipeline()
    result = pipe.translate_file(path)
    assert len(result) == 2, f"Expected 2 items, got {len(result)}"

    out_path = path.replace(".json", ".translated.json")
    out_data = read_json(out_path)
    assert list(out_data.keys()) == ["こんにちは", "世界"]
    assert out_data["こんにちは"] == "TRANSLATED:こんにちは"
    assert out_data["世界"] == "TRANSLATED:世界"

    assert pipe.call_count == 2, f"Expected 2 calls, got {pipe.call_count}"
    print("  PASS: dict mtool format")


def test_list_format():
    """List JSON is outside the current MTool-only contract."""
    path = os.path.join(TMPDIR, "list.json")
    make_json(path, ["item1", "item2", 123, False])

    pipe = MockPipeline()
    with pytest.raises(ValueError):
        pipe.translate_file(path)


def test_checkpoint_resume():
    """Second run should skip already-translated items."""
    path = os.path.join(TMPDIR, "resume.json")
    make_json(path, {"a": "alpha", "b": "beta", "c": "gamma"})

    pipe = MockPipeline()
    # First run: translate all 3
    pipe.translate_file(path)
    first_calls = pipe.call_count
    assert first_calls == 3, f"Expected 3 calls, got {first_calls}"

    # Second run: all should be checkpointed
    pipe.call_count = 0
    pipe.translate_file(path)
    assert pipe.call_count == 0, f"Expected 0 calls on resume, got {pipe.call_count}"
    print("  PASS: checkpoint resume")


def test_empty_string_skipped():
    """Empty values do not make an MTool source key ineligible."""
    path = os.path.join(TMPDIR, "empty.json")
    make_json(path, {"あ": "あ", "い": "", "う": "う"})

    pipe = MockPipeline()
    pipe.translate_file(path)
    assert pipe.call_count == 3, f"Expected 3 calls, got {pipe.call_count}"

    out_path = path.replace(".json", ".translated.json")
    out_data = read_json(out_path)
    assert out_data["あ"] == "TRANSLATED:あ"
    assert out_data["い"] == "TRANSLATED:い"
    assert out_data["う"] == "TRANSLATED:う"
    print("  PASS: empty value translated by key")


def test_progress_callback():
    """Progress callback receives expected row/col/status."""
    path = os.path.join(TMPDIR, "progress.json")
    make_json(path, {"x": "hello", "y": "world"})

    events = []

    def cb(payload):
        events.append({
            "row": payload["row"],
            "col": payload["col"],
            "status": payload["status"],
            "processed": payload["processed"],
            "total": payload["total"],
        })

    pipe = MockPipeline()
    pipe.translate_file(path, progress_callback=cb)

    # Should have 2 events per item: translating + translated = 4 total
    assert len(events) == 4, f"Expected 4 progress events, got {len(events)}"
    # Row indices should be 0 and 1 (not CSV row numbers)
    rows = [e["row"] for e in events]
    assert 0 in rows and 1 in rows, f"Rows should include 0 and 1, got {rows}"
    # Col should always be 0 for JSON
    assert all(e["col"] == 0 for e in events), "All JSON progress should use col=0"
    print("  PASS: progress callback")


def test_default_output_path():
    """_default_output_path should produce .translated.json for .json files."""
    pipe = MockPipeline()
    result = pipe._default_output_path("/some/path/foo.json")
    assert result == "/some/path/foo.translated.json", f"Got {result}"
    print("  PASS: default output path")


def test_partial_resume():
    """Interrupt mid-way, verify checkpoint only covers completed items."""
    import translator.checkpoint as cp_local

    # Use a temp dir unique to this test to avoid state leakage
    test_tmpdir = tempfile.mkdtemp()
    cp_local.CHECKPOINT_DIR = os.path.join(test_tmpdir, ".checkpoints")
    os.makedirs(cp_local.CHECKPOINT_DIR, exist_ok=True)

    path = os.path.join(test_tmpdir, "partial.json")
    make_json(path, {"キー1": "キー1", "キー2": "キー2", "キー3": "キー3"})

    pipe = MockPipeline()
    # Translate (this will translate all 3)
    pipe.translate_file(path)

    # Manually clear checkpoint for k2_0 to simulate incomplete
    cpp = cp_local.get_checkpoint_path(path)
    cp_data = json.load(open(cpp, "r", encoding="utf-8"))
    del cp_data["entries"]["1_0"]  # remove k2 (index 1)
    cp_data["stats"]["completed"] = len(cp_data["entries"])
    json.dump(cp_data, open(cpp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # Re-read to verify checkpoint was modified
    check2 = cp_local.load_progress(path)
    assert (0, 0) in check2, "k1 checkpoint should still exist"
    assert (1, 0) not in check2, "k2 checkpoint should be removed"
    assert (2, 0) in check2, "k3 checkpoint should still exist"

    # Re-run: should translate k2 only (index 1)
    pipe.call_count = 0
    pipe.called_with = []
    pipe.translate_file(path)
    assert pipe.call_count == 1, f"Expected 1 call (partial resume), got {pipe.call_count}"
    assert pipe.called_with[0][0] == "キー2", f"Expected 'キー2', got {pipe.called_with[0][0]}"
    print("  PASS: partial resume")

    # Restore global checkpoint dir
    cp_local.CHECKPOINT_DIR = CHECKPOINT_DIR
    shutil.rmtree(test_tmpdir)


def test_json_review_read_save_and_stats():
    """Review API should read and edit .translated.json directly."""
    from fastapi.testclient import TestClient
    from translator import checkpoint
    import main

    path = os.path.join(TMPDIR, "review.json")
    make_json(path, {"hello": "hello", "": "", "world": "world"})
    out_path = path.replace(".json", ".translated.json")
    make_json(out_path, {"hello": "浣犲ソ", "": "", "world": "涓栫晫"})
    checkpoint.save_progress(path, 0, 0, "hello", "浣犲ソ", status="done")
    checkpoint.save_progress(
        path,
        2,
        0,
        "world",
        "world",
        status="failed_refusal",
        issues=[{"type": "model_refusal", "message": "refused"}],
    )

    client = TestClient(main.app)
    row0 = client.get("/api/review", params={"file_path": path, "row": 0})
    assert row0.status_code == 200
    payload = row0.json()
    assert payload["file_type"] == "json"
    assert payload["header"] == ["Key", "Value"]
    assert payload["columns"][0]["key"] == "hello"
    assert payload["columns"][0]["translated"] == "浣犲ソ"

    stats = client.get("/api/review/stats", params={"file_path": path})
    assert stats.status_code == 200
    stats_payload = stats.json()
    assert stats_payload["total"] == 2
    assert stats_payload["violations_count"] == 1

    save = client.post(
        "/api/review/save",
        json={"file_path": path, "row": 2, "col": 0, "text": "\u4e16\u754c\u5df2\u6821\u5bf9"},
    )
    assert save.status_code == 200
    out_data = read_json(out_path)
    assert out_data["world"] == "\u4e16\u754c\u5df2\u6821\u5bf9"
    entry = checkpoint.get_entry(path, 2, 0)
    assert entry["status"] == "translated"
    assert entry["issues"] == []


def test_pipeline_output_cell_sync_prevents_writer_overwrite():
    pytest.skip("CSV writer workflow was removed; only MTool JSON output is supported.")
    from translator.pipeline import TranslationPipeline
    from translator.writer import TranslationWriter
    from parser.csv_parser import parse_csv

    with tempfile.TemporaryDirectory(prefix="writer_sync_") as tmpdir:
        out_path = os.path.join(tmpdir, "sample.translated.csv")
        data = {"header": ["jp"], "rows": [["old"]], "column_count": 1}
        pipeline = TranslationPipeline()
        pipeline._writer = TranslationWriter("csv", data, out_path)

        assert pipeline.update_output_cell(0, 0, "manual edit")
        pipeline.flush_writer()

        parsed = parse_csv(out_path)
        assert parsed["rows"][0][0] == "manual edit"


def test_glossary_terms_are_restored_from_placeholders(monkeypatch):
    from translator.glossary import Glossary
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append(text)
        if "__PERSON_0__" in text:
            return "__PERSON_0__\u6765\u4e86"
        if "__TERM_0__" in text:
            return "__TERM_0__\u6765\u4e86"
        return "\u4ed6\u6765\u4e86"

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    glossary = Glossary()
    glossary.add("\u30ad\u30e2\u7537", "\u53d8\u6001\u7537")
    pipeline = TranslationPipeline(glossary=glossary)

    translated = pipeline.translate_cell("\u30ad\u30e2\u7537\u304c\u6765\u305f", 0, 0)
    assert translated == "\u53d8\u6001\u7537\u6765\u4e86"
    assert any("__PERSON_0__" in call or "__TERM_0__" in call for call in calls)


def test_pending_review_cells_do_not_count_as_violations():
    pytest.skip("CSV review workflow was removed; only MTool JSON review is supported.")
    from fastapi.testclient import TestClient
    from parser.csv_parser import serialize_csv
    import main

    with tempfile.TemporaryDirectory(prefix="pending_review_") as tmpdir:
        path = os.path.join(tmpdir, "sample.csv")
        out_path = os.path.join(tmpdir, "sample.translated.csv")
        long_untranslated = "\u3042" * 80
        data = {"header": ["jp"], "rows": [[long_untranslated]], "column_count": 1}
        serialize_csv(data, path)
        serialize_csv(data, out_path)

        client = TestClient(main.app)
        row = client.get("/api/review", params={"file_path": path, "row": 0})
        assert row.status_code == 200
        payload = row.json()
        assert payload["columns"][0]["status"] == "pending"
        assert payload["columns"][0]["violations"] == []

        stats = client.get("/api/review/stats", params={"file_path": path})
        assert stats.status_code == 200
        assert stats.json()["violations_count"] == 0


def test_review_list_filter_and_jump_by_source_row():
    pytest.skip("CSV review workflow was removed; only MTool JSON review is supported.")
    from fastapi.testclient import TestClient
    from parser.csv_parser import serialize_csv
    from translator import checkpoint
    import main

    with tempfile.TemporaryDirectory(prefix="review_list_") as tmpdir:
        path = os.path.join(tmpdir, "sample.csv")
        out_path = os.path.join(tmpdir, "sample.translated.csv")
        serialize_csv({"header": ["jp"], "rows": [["a"], ["b"], ["c"], ["d"]], "column_count": 1}, path)
        serialize_csv({"header": ["jp"], "rows": [["A"], ["B"], ["C"], ["D"]], "column_count": 1}, out_path)
        checkpoint.save_progress(path, 0, 0, "a", "A", status="done")
        checkpoint.save_progress(path, 1, 0, "b", "B", status="failed_refusal", issues=[{"type": "model_refusal", "message": "refused"}])
        checkpoint.save_progress(path, 2, 0, "c", "C", status="done")
        checkpoint.save_progress(path, 3, 0, "d", "D", status="done", issues=[{"type": "english_residue", "message": "English remains"}])

        client = TestClient(main.app)
        all_rows = client.get("/api/review/list", params={"file_path": path, "offset": 0, "limit": 2, "filter": "all"})
        assert all_rows.status_code == 200
        payload = all_rows.json()
        assert payload["matched_total"] == 4
        assert [item["row"] for item in payload["items"]] == [0, 1]

        issue_rows = client.get("/api/review/list", params={"file_path": path, "offset": 0, "limit": 20, "filter": "issues"})
        assert issue_rows.status_code == 200
        assert [item["row"] for item in issue_rows.json()["items"]] == [1, 3]

        jump = client.get("/api/review/jump", params={"file_path": path, "row": 3, "limit": 1, "filter": "issues"})
        assert jump.status_code == 200
        assert jump.json()["found"] is True
        assert jump.json()["offset"] == 1

        missing = client.get("/api/review/jump", params={"file_path": path, "row": 2, "limit": 20, "filter": "issues"})
        assert missing.status_code == 200
        assert missing.json()["found"] is False


def test_asar_internal_files_share_glossary_path():
    pytest.skip("ASAR glossary sharing was removed with ASAR workflow support.")
    from translator import checkpoint

    p1 = os.path.join("tmp_uploads", "sess", "extracted", "a.csv")
    p2 = os.path.join("tmp_uploads", "sess", "extracted", "nested", "b.json")
    assert checkpoint.get_glossary_path(p1) == checkpoint.get_glossary_path(p2)


def test_subject_only_name_with_inconsistent_targets_stays_unconfirmed():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    src = "\u30bf\u30ed\u30a6\u304c\u6765\u305f"
    assert glossary.auto_extract(src, "\u5c0f\u660e\u6765\u4e86") == []
    assert glossary.auto_extract(src, "\u592a\u90ce\u6765\u4e86") == []
    assert "\u30bf\u30ed\u30a6" not in glossary.terms


def test_term_edit_backfills_candidate_aliases():
    pytest.skip("CSV term backfill workflow was removed; only MTool JSON outputs are updated.")
    from parser.csv_parser import parse_csv, serialize_csv
    from translator import checkpoint
    import main

    with tempfile.TemporaryDirectory(prefix="term_backfill_") as tmpdir:
        path = os.path.join(tmpdir, "sample.csv")
        out_path = os.path.join(tmpdir, "sample.translated.csv")
        src = "\u30bf\u30ed\u30a6\u304c\u6765\u305f"
        serialize_csv({"header": ["jp"], "rows": [[src]], "column_count": 1}, path)
        serialize_csv({"header": ["jp"], "rows": [["\u5c0f\u660e\u6765\u4e86"]], "column_count": 1}, out_path)
        checkpoint.save_progress(path, 0, 0, src, "\u5c0f\u660e\u6765\u4e86", status="done")

        changed = main._apply_term_edit_to_outputs(
            path,
            "\u30bf\u30ed\u30a6",
            "",
            "\u30bf\u30ed\u30a6",
            "\u592a\u90ce",
            aliases=["\u5c0f\u660e"],
        )
        assert changed == 1
        assert parse_csv(out_path)["rows"][0][0] == "\u592a\u90ce\u6765\u4e86"
        assert checkpoint.get_entry(path, 0, 0)["translated"] == "\u592a\u90ce\u6765\u4e86"


def test_bad_auto_terms_do_not_enter_confirmed_glossary():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    bad_pairs = [
        ("\u30ad\u30e2\u3044", "\\u53d8\\u6001\\u7537"),
        ("\u30a2\u30f3\u30bf\u306f\uff1f", "\u4ec0\u4e48"),
        ("\u30ca\u30ec\u30fc\u30b7\u30e7\u30f3\uff1a\u958b\u59cb", "\u65c1\u767d"),
        ("\u30ab\u30bf", "\u5168\u5c40\u4e73\u5934\u5316"),
        ("\u30ab\u30bf\u30ab\u30ca", "\u6211\u8981\u6740\u4e86\u4f60"),
    ]
    for source, target in bad_pairs:
        glossary.auto_extract(source, target)
        glossary.auto_extract(source, target)

    assert "\u30ad\u30e2" not in glossary.terms
    assert "\u30a2\u30f3\u30bf" not in glossary.terms
    assert "\u30ca\u30ec\u30fc\u30b7\u30e7\u30f3" not in glossary.terms
    assert "\u30ab\u30bf" not in glossary.terms
    assert "\u30ab\u30bf\u30ab\u30ca" not in glossary.terms


def test_prune_removes_existing_bad_glossary_terms():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    glossary.add("\u30ab\u30bf\u30ab\u30ca", "\u6211\u8981\u6740\u4e86\u4f60")
    glossary.add("\u30e2\u30fc\u30ea\u30fb\u30b4\u30fc\u30eb\u30c9", "\u83ab\u91cc")
    assert glossary.prune_invalid_terms() == 2
    assert glossary.terms == {}


def test_katakana_compound_name_is_not_split_into_bad_candidates():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    source = "\u30e2\u30fc\u30ea\u30fb\u30b4\u30fc\u30eb\u30c9"
    glossary.auto_extract(source, "\u83ab\u91cc")
    glossary.auto_extract(source, "\u83ab\u91cc")

    assert "\u30e2\u30fc\u30ea" not in glossary.candidates
    assert "\u30b4\u30fc\u30eb\u30c9" not in glossary.candidates
    assert source not in glossary.terms
    assert glossary.as_payload()["candidates"] == {}


def test_prune_removes_low_evidence_katakana_confirmed_terms():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    source = "\u30ab\u30bf\u30ab\u30ca"
    glossary.add(source, "\u4e0d\u6b63\u786e")
    glossary.candidates[source]["score"] = 0.45
    glossary.candidates[source]["evidence"] = ["katakana_name", "person_like"]

    assert glossary.prune_invalid_terms() == 1
    assert source not in glossary.terms


def test_standalone_action_label_does_not_auto_confirm_as_term():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    source = "\u653b\u6483\u6280"
    target = "\u653b\u51fb\u6280"
    glossary.auto_extract(source, target)
    glossary.auto_extract(source, target)

    assert source not in glossary.terms


def test_term_alias_backfill_does_not_replace_target_substrings():
    from translator.pipeline import TranslationPipeline

    confirmed = [{
        "source": "\u89e6\u624b\u59e6",
        "target": "\u89e6\u624b\u5978",
        "aliases": ["\u89e6", "\u624b\u5978", "\u89e6\u624b\u5978"],
    }]

    assert TranslationPipeline._apply_term_aliases(
        "\u89e6\u624b\u59e6",
        "\u89e6\u624b\u5978",
        confirmed,
    ) == "\u89e6\u624b\u5978"


def test_suspicious_model_artifacts_are_reported():
    from translator.quality import suspicious_artifacts

    artifacts = suspicious_artifacts('\u7cbe\u7075\u00b7\u9b54},"\u722a\u5978"\u7248')
    assert "}" in artifacts
    assert ',"' in artifacts


def test_resume_done_checkpoint_corrects_deterministic_json_value(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="resume_fix_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        try:
            path = os.path.join(tmpdir, "sample.json")
            out_path = os.path.join(tmpdir, "sample.translated.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"13": "13", "\u30a8\u30eb\u30d5A": "\u30a8\u30eb\u30d5A"}, f, ensure_ascii=False)
            checkpoint.init_checkpoint(path, total=2, file_type="json")
            checkpoint.save_progress(path, 0, 0, "13", "\u7b2c13\u5173", status="done", json_key="13", mtool=True)

            def fail_translate(*args, **kwargs):
                raise AssertionError("done deterministic resume should not call model")

            monkeypatch.setattr("translator.pipeline.translate", fail_translate)
            pipeline = TranslationPipeline()
            items = parse_json(path)
            mtool = pipeline._is_mtool_json(items)
            assert mtool
            completed = checkpoint.load_progress(path)
            key, value = items[0]
            source_text = pipeline._json_source_text(key, value, mtool)
            translated = completed[(0, 0)]["translated"]
            deterministic = pipeline._deterministic_translation(source_text)
            if deterministic and deterministic != translated:
                checkpoint.save_progress(path, 0, 0, source_text, deterministic, status="done", json_key=str(key), mtool=mtool)
                items[0] = (key, deterministic)
            from parser.json_parser import serialize_json
            serialize_json(items, out_path)

            assert json.load(open(out_path, encoding="utf-8"))["13"] == "13"
            assert checkpoint.get_entry(path, 0, 0)["translated"] == "13"
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir


def test_target_conflict_prevents_wrong_name_auto_confirm():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    protagonist = "\u30ad\u30e2\u7537\u304c\u6765\u305f"
    glossary.auto_extract(protagonist, "\u53d8\u6001\u7537\u6765\u4e86")
    glossary.auto_extract(protagonist, "\u53d8\u6001\u7537\u6765\u4e86")
    assert "\u30ad\u30e2\u7537" not in glossary.terms

    wrong_name = "\u767d\u5cf0\u9e97\u83ef\uff1a\u6765\u305f"
    glossary.auto_extract(wrong_name, "\u53d8\u6001\u7537\u6765\u4e86")
    glossary.auto_extract(wrong_name, "\u53d8\u6001\u7537\u6765\u4e86")
    assert "\u767d\u5cf0\u9e97\u83ef" not in glossary.terms


def test_non_japanese_ui_word_is_preserved_without_model(monkeypatch):
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    def fake_translate(model, text, system_prompt=None, terminology=None):
        raise AssertionError("fixed UI words should not call the model")

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    pipeline = TranslationPipeline()
    assert pipeline.translate_cell("Continue", 0, 0) == "Continue"
    assert pipeline.translate_cell("\u30b3\u30f3\u30c6\u30a3\u30cb\u30e5\u30fc", 0, 0) == "\u7ee7\u7eed"


def test_numeric_only_cell_is_preserved_without_model(monkeypatch):
    from translator.pipeline import TranslationPipeline
    from translator.quality import english_residue
    import translator.pipeline as pipeline_mod

    def fake_translate(model, text, system_prompt=None, terminology=None):
        raise AssertionError("numeric-only cells should not call the model")

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    pipeline = TranslationPipeline()
    assert pipeline.translate_cell("13", 0, 0) == "13"
    assert pipeline.translate_cell("0013", 0, 0) == "0013"
    assert pipeline.translate_cell("12.5%", 0, 0) == "12.5%"
    assert pipeline.translate_cell("\uff11\uff13", 0, 0) == "\uff11\uff13"
    assert pipeline.translate_cell("EV002", 0, 0) == "EV002"
    assert english_residue("EV002") == []


def test_glossary_only_short_label_translates_without_model(monkeypatch):
    from translator.glossary import Glossary
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    def fake_translate(model, text, system_prompt=None, terminology=None):
        raise AssertionError("glossary-only labels should not call the model")

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    glossary = Glossary()
    glossary.add("\u30a8\u30eb\u30d5", "\u7cbe\u7075")
    pipeline = TranslationPipeline(glossary=glossary)
    assert pipeline.translate_cell("\u30a8\u30eb\u30d5A", 0, 0) == "\u7cbe\u7075A"
    assert pipeline.translate_cell("\u30a8\u30eb\u30d5B", 1, 0) == "\u7cbe\u7075B"


def test_common_japanese_menu_actions_translate_without_model(monkeypatch):
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod
    from translator.label_rules import deterministic_translation

    def fake_translate(model, text, system_prompt=None, terminology=None):
        raise AssertionError("known short menu actions should not call the model")

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    pipeline = TranslationPipeline()
    assert pipeline.translate_cell("\u8aad\u3093\u3067\u307f\u308b", 0, 0) == "\u8bfb\u8bfb\u770b"
    assert deterministic_translation("\u8056\u6c34\u3092\u4f7f\u3046") == ""


def test_untranslated_japanese_is_not_marked_as_model_refusal(monkeypatch):
    from translator.glossary import Glossary
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    def fake_translate(model, text, system_prompt=None, terminology=None):
        return "\u8b0e\u7bb1\u3092\u4f7f\u3046"

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    monkeypatch.setattr(pipeline_mod, "retry_with_fallback", lambda *args, **kwargs: {"status": "NEEDS_REVIEW"})
    monkeypatch.setattr(pipeline_mod, "chunk_translate", lambda *args, **kwargs: "\u8b0e\u7bb1\u3092\u4f7f\u3046")
    pipeline = TranslationPipeline(glossary=Glossary(file_path=os.path.join(tempfile.mkdtemp(), "empty.json")))
    translated, status, issues = pipeline._translate_cell_with_meta("\u8b0e\u7bb1\u3092\u4f7f\u3046", 0, 0, "")
    assert translated == "\u8b0e\u7bb1\u3092\u4f7f\u3046"
    assert status == "review_required"
    assert [issue for issue in issues if issue["type"] == "untranslated_japanese"]
    assert not [issue for issue in issues if issue["type"] == "model_refusal"]


def test_english_fixed_word_is_rewritten_but_key_label_is_kept(monkeypatch):
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append(system_prompt or "")
        return "\u6309 A Continue"

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    pipeline = TranslationPipeline()
    translated, status, issues = pipeline._translate_cell_with_meta("Press A to Continue", 0, 0, "")
    assert status == "preserved"
    assert translated == "Press A to Continue"
    assert issues == []
    assert calls == []


def test_common_english_residue_is_auto_rewritten(monkeypatch):
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    def fake_translate(model, text, system_prompt=None, terminology=None):
        return "\u591a\u534a\u662f\u57ce\u4e0b\u753a\u3002\u9690\u7ea6\u6709\u5e26Continue\u7684\u9ec4\u5c4b\u9876\u7684\u623f\u5b50"

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    pipeline = TranslationPipeline()
    translated, status, issues = pipeline._translate_cell_with_meta("\u591a\u5206\u3001\u57ce\u4e0b\u753a\u3002\u8a1b\u308a\u306e\u3042\u308b\u9ec4\u8272\u3044\u5c4b\u6839\u306e\u5bb6", 0, 0, "")
    assert status == "translated"
    assert "Continue" not in translated
    assert "\u7ee7\u7eed" in translated
    assert not [issue for issue in issues if issue["type"] == "english_residue"]


def test_context_dependent_english_residue_is_left_for_review(monkeypatch):
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    def fake_translate(model, text, system_prompt=None, terminology=None):
        return "\u591a\u534a\u662f\u57ce\u4e0b\u753a\u3002\u9690\u7ea6\u6709\u5e26accent\u7684\u9ec4\u5c4b\u9876\u7684\u623f\u5b50"

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    pipeline = TranslationPipeline()
    translated, status, issues = pipeline._translate_cell_with_meta("\u591a\u5206\u3001\u57ce\u4e0b\u753a\u3002\u8a1b\u308a\u306e\u3042\u308b\u9ec4\u8272\u3044\u5c4b\u6839\u306e\u5bb6", 0, 0, "")
    assert status == "translated_needs_review"
    assert "accent" in translated
    assert [issue for issue in issues if issue["type"] == "english_residue"]


def test_wrong_kanji_name_translation_stays_candidate_or_rejected():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    source = "\u767d\u5cf0\u9e97\u83ef"
    glossary.auto_extract(source, "\u53d8\u6001\u7537")
    glossary.auto_extract(source, "\u53d8\u6001\u7537")
    assert source not in glossary.terms
    assert glossary.candidates[source]["status"] == "rejected"


def test_person_aliases_match_given_name_with_honorific():
    from translator.glossary import Glossary

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    glossary.add("\u767d\u5cf0\u9e97\u83ef", "\u767d\u5cf0\u4e3d\u534e", term_type="person")
    hits = glossary.find_hits("\u9e97\u83ef\u3061\u3083\u3093\u304c\u6765\u305f")
    assert {"source": "\u9e97\u83ef\u3061\u3083\u3093", "target": "\u4e3d\u534e", "owner": "\u767d\u5cf0\u9e97\u83ef", "type": "person"} in hits


def test_fast_cleanup_does_not_spawn_background_cleanup(monkeypatch):
    import main

    def fail_thread(*args, **kwargs):
        raise AssertionError("fast cleanup must not start a background cleanup thread")

    monkeypatch.setattr(main.threading, "Thread", fail_thread)
    result = main.cleanup_translation(main.CleanupRequest(file_path="missing.csv", fast=True))
    assert result["ok"] is True
    assert result["scheduled"] is False


if __name__ == "__main__":
    print("test_dict_mtool_format...")
    test_dict_mtool_format()

    print("test_list_format...")
    test_list_format()

    print("test_checkpoint_resume...")
    test_checkpoint_resume()

    print("test_empty_string_skipped...")
    test_empty_string_skipped()

    print("test_progress_callback...")
    test_progress_callback()

    print("test_default_output_path...")
    test_default_output_path()

    print("test_partial_resume...")
    test_partial_resume()

    # Cleanup
    shutil.rmtree(TMPDIR)
    cp.CHECKPOINT_DIR = ".checkpoints"

    print("\nAll JSON translation tests PASSED")

