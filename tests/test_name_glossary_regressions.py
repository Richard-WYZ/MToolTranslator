from __future__ import annotations

import json


def test_name_preflight_requires_two_matching_phonetic_decisions():
    from translation.terminology import Glossary
    from translation.terminology.name_preflight import run_name_preflight

    glossary = Glossary.in_memory()
    sources = [
        "サン", "「サン」と呼んだ", "サンが来た", "サンを待つ", "サンの姉", "サン！",
        "コール", "コールは来た", "コールが笑う", "コールの弟", "コール！",
        "イベント", "イベントは始まる", "イベントが終わる", "イベント一覧", "イベント中",
    ]
    calls = []

    def model_call(model, payload, prompt, options):
        calls.append((model, prompt))
        terms = json.loads(payload)["terms"]
        result = []
        for term in terms:
            source = term["source"]
            target = {"サン": "桑", "コール": "科尔"}.get(source, "")
            result.append({
                "i": term["i"], "person": bool(target),
                "target": target, "phonetic": bool(target),
            })
        return json.dumps({"terms": result}, ensure_ascii=False)

    confirmed = run_name_preflight(
        sources=sources, glossary=glossary,
        primary_model="api:one", verifier_model="api:two", call_model=model_call,
    )
    assert {item["source"]: item["target"] for item in confirmed} == {
        "サン": "桑", "コール": "科尔",
    }
    assert "イベント" not in glossary.terms
    assert glossary.candidates["サン"]["type"] == "person"
    assert len(calls) == 2


def test_name_preflight_rejects_disagreement_and_semantic_target():
    from translation.terminology import Glossary
    from translation.terminology.name_preflight import run_name_preflight

    glossary = Glossary.in_memory()
    sources = ["サン", "「サン」", "サンが来た", "サンを待つ", "サンの姉"]

    def model_call(model, payload, prompt, options):
        return json.dumps({"terms": [{
            "i": 0, "person": True,
            "target": "阳光" if model == "api:one" else "桑",
            "phonetic": model != "api:one",
        }]}, ensure_ascii=False)

    assert run_name_preflight(
        sources=sources, glossary=glossary,
        primary_model="api:one", verifier_model="api:two", call_model=model_call,
    ) == []
    assert glossary.terms == {}
    assert glossary.candidates["サン"]["status"] == "needs_review"
    assert glossary.unresolved_person_hits("サンが来た") == ["サン"]


def test_name_preflight_retries_frequent_disagreement_in_small_independent_batch():
    from translation.terminology import Glossary
    from translation.terminology.name_preflight import run_name_preflight

    glossary = Glossary.in_memory()
    sources = ["サン", "「サン」と呼んだ", *[f"サンが来た{i}。" for i in range(9)]]
    calls = []

    def model_call(model, payload, prompt, options):
        calls.append((model, json.loads(payload)))
        target = "阳光" if len(calls) == 1 else "桑"
        return json.dumps({"terms": [{
            "i": 0, "person": True, "target": target,
            "phonetic": target == "桑",
        }]}, ensure_ascii=False)

    confirmed = run_name_preflight(
        sources=sources, glossary=glossary,
        primary_model="api:one", verifier_model="api:two", call_model=model_call,
    )
    assert confirmed == [{"source": "サン", "target": "桑"}]
    assert [model for model, _payload in calls] == ["api:one", "api:two", "api:one", "api:two"]
    assert all("proposed_target" not in item for _model, payload in calls for item in payload["terms"])


def test_katakana_term_boundaries_multiplicity_and_honorifics():
    from translation.terminology import Glossary

    glossary = Glossary.in_memory()
    glossary.add("サン", "桑", "person")
    assert glossary.find_hits("サングイス") == []
    assert glossary.protect_terms("サングイス")[1] == []
    protected, tokens = glossary.protect_terms("サンちゃんとサン")
    assert protected == "__PERSON_0__ちゃんと__PERSON_1__"
    assert len(tokens) == 2
    assert glossary.restore_terms(protected, tokens) == "桑ちゃんと桑"


def test_repeated_name_requires_every_protected_occurrence():
    from translation.batching import prepare_model_candidate
    from translation.protection import restore_protected_translation
    from translation.terminology import Glossary

    glossary = Glossary.in_memory()
    glossary.add("サン", "桑", "person")
    candidate = prepare_model_candidate(
        batch_i=0, idx=0, source="サンとサンが来た", glossary=glossary,
    )
    restored, issues, _missing = restore_protected_translation(
        glossary=glossary, original_text=candidate["source"],
        prepared_text=candidate["prepared"], protected_text=candidate["protected"],
        translated="__PERSON_0__和他来了", symbol_tokens=candidate["symbol_tokens"],
        term_tokens=candidate["term_tokens"], runtime_tokens=candidate["runtime_tokens"],
        term_hits=candidate["term_hits"],
    )
    assert restored == "桑和他来了"
    assert "runtime_token_preservation" in {issue["type"] for issue in issues}


def test_cross_line_continuation_is_translated_as_parent():
    from translation.analysis.composition import build_mtool_composition_plan

    items = [
        ("都で商売することへの", ""),
        ("嫉妬はない。", ""),
        ("都で商売することへの\n嫉妬はない。", ""),
        ("彼は笑った。\n私は答えた。", ""),
        ("彼は笑った。", ""),
        ("私は答えた。", ""),
    ]
    plan = build_mtool_composition_plan(items)
    assert 2 not in plan.entries
    assert 3 in plan.entries


def test_unwarranted_repeated_clause_is_hard_review():
    from translation.quality import status_for_output, translation_issues

    source = "彼は行くよ！"
    target = "他马上要走了！他马上要走了！"
    issues = translation_issues(source, target)
    assert "duplicated_clause" in {issue["type"] for issue in issues}
    assert status_for_output(source, target, issues) == "review_required"
    assert "duplicated_clause" not in {
        issue["type"] for issue in translation_issues("彼は行く！彼は行く！", target)
    }


def test_code_identifiers_with_digits_are_order_sensitive():
    from translation.protection.runtime import ProtectedToken, runtime_token_kind, validate_runtime_tokens

    assert runtime_token_kind(r"\V[1]") == "movable_reference"
    assert runtime_token_kind("__PERSON_0__") == "movable_reference"
    assert runtime_token_kind(r"\C[1]") == "runtime"
    assert runtime_token_kind("12％") == "numeric"
    tokens = [ProtectedToken("__KEEP_0__", r"\C[1]"), ProtectedToken("__KEEP_1__", r"\I[2]")]
    assert validate_runtime_tokens("__KEEP_1__击败__KEEP_0__", tokens, "__KEEP_0__が__KEEP_1__")
    movable = [ProtectedToken("__KEEP_0__", r"\V[1]"), ProtectedToken("__KEEP_1__", "__PERSON_0__")]
    assert validate_runtime_tokens("__KEEP_1__击败__KEEP_0__", movable, "__KEEP_0__が__KEEP_1__") == []


def test_ascii_prefixed_japanese_plugin_tag_is_opaque():
    from translation.classification import deterministic_translation
    from translation.protection.runtime import protect_runtime_tokens
    from translation.quality import translation_issues

    source = "<HMすり抜け禁止>"
    protected, tokens = protect_runtime_tokens(source)
    assert protected == "__KEEP_0__"
    assert tokens[0].value == source
    assert deterministic_translation(source) == source
    assert "runtime_token_preservation" in {
        issue["type"] for issue in translation_issues(source, "<HM禁止穿过>")
    }


def test_multiline_text_tag_preserves_command_prefix_but_translates_body():
    from translation.protection.runtime import protect_runtime_tokens
    from translation.quality import apply_source_conditioned_fixes, translation_issues

    source = "<CategoryKey:ラムリタス>\n<text:無の魔物\n灯台に生息する>"
    protected, tokens = protect_runtime_tokens(source)
    assert "<text:" not in protected
    assert any(token.value == "<text:" for token in tokens)
    assert "無の魔物" in protected
    assert "runtime_token_preservation" in {
        issue["type"] for issue in translation_issues(source, "<CategoryKey:ラムリタス>\n<文本：虚无魔物\n栖息于灯塔>")
    }
    assert apply_source_conditioned_fixes(
        source, "<CategoryKey:ラムリタス>\n<文本：虚无魔物\n栖息于灯塔>",
    ) == "<CategoryKey:ラムリタス>\n<text:虚无魔物\n栖息于灯塔>"


def test_glossary_change_repairs_only_affected_sentence(tmp_path, monkeypatch):
    import translation.checkpoint as checkpoint
    from translation.terminology import Glossary, backfill_confirmed_terms_to_outputs
    from translation.workflow.glossary_repair import repair_pending_glossary_entries

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / ".checkpoints"))
    file_path = str(tmp_path / "input.json")
    glossary = Glossary.in_memory()
    checkpoint.init_checkpoint(file_path, total=2, file_type="json")
    checkpoint.save_progress(file_path, 0, 0, "フィーネが来た。", "菲内来了。")
    checkpoint.save_progress(file_path, 1, 0, "別の文。", "另一句。")
    glossary.candidates["フィーネ"] = {
        "count": 5, "type": "person", "evidence": ["preflight_verified_person"],
        "targets": {"菲内": 1},
    }
    glossary.add("フィーネ", "菲妮", "person")
    backfill_confirmed_terms_to_outputs(
        file_path, [{"source": "フィーネ", "target": "菲妮", "aliases": ["菲内"]}],
    )
    checkpoint.set_glossary_version(file_path, glossary.version(), update_entries=True)
    assert checkpoint.get_entry(file_path, 0, 0)["glossary_version"] != glossary.version()
    assert checkpoint.get_entry(file_path, 1, 0)["glossary_version"] == glossary.version()

    class Pipeline:
        model = "api:translator"

        def __init__(self):
            self.glossary = glossary
            self.calls = []

        def _batch_translation_config(self):
            return {"json_batch_size": 20, "max_batch_chars": 4000}

        def _check_control_flags(self):
            pass

        def _translate_json_candidates(self, candidates, path, options, protocol, model=None):
            self.calls.extend(candidate["idx"] for candidate in candidates)
            return {0: ("菲妮来了。", "translated", [])}

        def update_output_cell(self, row, col, value):
            return True

    pipeline = Pipeline()
    items = [("フィーネが来た。", "菲内来了。"), ("別の文。", "另一句。")]
    assert repair_pending_glossary_entries(pipeline, file_path, items) == 1
    assert pipeline.calls == [0]
    assert items[0][1] == "菲妮来了。"
    assert items[1][1] == "另一句。"
    repaired = checkpoint.get_entry(file_path, 0, 0)
    assert repaired["glossary_version"] == glossary.version()
    assert "glossary_pending_sources" not in repaired


def test_glossary_backfill_checks_old_sentence_even_when_new_name_appears(tmp_path, monkeypatch):
    import translation.checkpoint as checkpoint
    from translation.terminology import Glossary, backfill_confirmed_terms_to_outputs

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / ".checkpoints"))
    path = str(tmp_path / "input.json")
    source = "サン和サンが来た。"
    checkpoint.init_checkpoint(path, total=1, file_type="json", glossary_version="old")
    checkpoint.save_progress(path, 0, 0, source, "桑和森来了。")
    glossary = Glossary.in_memory()
    glossary.add("サン", "桑", "person")
    assert backfill_confirmed_terms_to_outputs(
        path, [{"source": "サン", "target": "桑", "aliases": []}],
        glossary_version=glossary.version(),
    ) == 1
    assert checkpoint.get_entry(path, 0, 0)["glossary_pending_sources"] == ["サン"]


def test_composition_retry_removes_copied_previous_line(tmp_path, monkeypatch):
    import translation.checkpoint as checkpoint
    from translation.analysis.composition import build_mtool_composition_plan
    from translation.terminology import Glossary
    from translation.workflow.composition import _repair_copied_context_lines

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / ".checkpoints"))
    path = str(tmp_path / "input.json")
    items = [
        ("旅人は静かに笑った。", "那位旅人轻声笑了。"),
        ("私は答えた。", "那位旅人轻声笑了。我回答了。"),
        ("旅人は静かに笑った。\n私は答えた。", ""),
    ]
    checkpoint.init_checkpoint(path, total=3, file_type="json")
    for row in (0, 1):
        checkpoint.save_progress(path, row, 0, items[row][0], items[row][1])

    class Pipeline:
        model = "api:translator"
        glossary = Glossary.in_memory()

        def _batch_translation_config(self):
            return {}

        def _check_control_flags(self):
            pass

        def _translate_json_candidates(self, candidates, path, options, protocol, model=None):
            return {1: ("我回答了。", "translated", [])}

        def update_output_cell(self, row, col, value):
            return True

    plan = build_mtool_composition_plan(items)
    _repair_copied_context_lines(Pipeline(), plan, path, items)
    assert items[1][1] == "我回答了。"
    assert checkpoint.get_entry(path, 1, 0)["translated"] == "我回答了。"


def test_manual_glossary_edit_marks_only_source_matched_sentence_pending(tmp_path, monkeypatch):
    import translation.checkpoint as checkpoint
    from app.services.files import translated_path
    from app.services.glossary import apply_term_edit_to_outputs
    from translation.input import load_json_items
    from translation.output import write_json_items
    from translation.terminology import Glossary

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / ".checkpoints"))
    path = str(tmp_path / "input.json")
    source_items = [("サンが来た。", "サンが来た。"), ("太陽が出た。", "太陽が出た。")]
    output_items = [(source_items[0][0], "阳光来了。阳光很好。"), (source_items[1][0], "阳光很好。")]
    write_json_items(source_items, path)
    write_json_items(output_items, translated_path(path))
    checkpoint.init_checkpoint(path, total=2, model="api:translator", file_type="json")
    checkpoint.save_progress(path, 0, 0, source_items[0][0], output_items[0][1], issues=[{
        "type": "honorific_rendering_review", "message": "Keep this review reason.",
    }])
    checkpoint.save_progress(path, 1, 0, source_items[1][0], output_items[1][1])
    glossary = Glossary(file_path=checkpoint.get_glossary_path(path))
    glossary.add("サン", "桑", "person", manual=True)
    glossary.save()

    assert apply_term_edit_to_outputs(
        path, "サン", "阳光", "サン", "桑", tasks={},
        aliases=["阳光"], defer_repair=True,
    ) == 1
    assert load_json_items(translated_path(path)) == output_items
    affected = checkpoint.get_entry(path, 0, 0)
    assert affected["glossary_pending_sources"] == ["サン"]
    assert {issue["type"] for issue in affected["issues"]} == {
        "honorific_rendering_review", "glossary_update_pending",
    }
    assert checkpoint.get_entry(path, 1, 0)["issues"] == []


def test_manual_glossary_edit_retranslates_and_recomposes_completed_file(tmp_path, monkeypatch):
    import translation.checkpoint as checkpoint
    from app.services.files import translated_path
    from app.services.glossary import apply_term_edit_to_outputs
    from translation.input import load_json_items
    from translation.output import write_json_items
    from translation.terminology import Glossary
    from translation.workflow.pipeline import TranslationPipeline

    monkeypatch.setattr(checkpoint, "CHECKPOINT_DIR", str(tmp_path / ".checkpoints"))
    path = str(tmp_path / "input.json")
    source_items = [
        ("サンが来た。", "サンが来た。"),
        ("私は笑った。", "私は笑った。"),
        ("サンが来た。\n私は笑った。", "サンが来た。\n私は笑った。"),
    ]
    output_items = [
        (source_items[0][0], "阳光来了。"),
        (source_items[1][0], "我笑了。"),
        (source_items[2][0], "阳光来了。\n我笑了。"),
    ]
    write_json_items(source_items, path)
    write_json_items(output_items, translated_path(path))
    checkpoint.init_checkpoint(path, total=3, model="api:translator", file_type="json")
    for row, (source, output) in enumerate(output_items):
        checkpoint.save_progress(
            path, row, 0, source, output,
            entry_classification="composed_multiline" if row == 2 else "dialogue",
        )
    glossary = Glossary(file_path=checkpoint.get_glossary_path(path))
    glossary.add("サン", "桑", "person", manual=True)
    glossary.save()

    calls = []
    def translate_candidates(self, candidates, file_path, options, protocol, model=None):
        calls.extend(candidate["idx"] for candidate in candidates)
        return {0: ("桑来了。", "translated", [])}
    monkeypatch.setattr(TranslationPipeline, "_translate_json_candidates", translate_candidates)

    assert apply_term_edit_to_outputs(
        path, "サン", "阳光", "サン", "桑", tasks={}, aliases=["阳光"],
    ) == 1
    assert calls == [0]
    assert [value for _key, value in load_json_items(translated_path(path))] == [
        "桑来了。", "我笑了。", "桑来了。\n我笑了。",
    ]
    assert checkpoint.get_entry(path, 0, 0)["status"] == "translated_needs_review"
    assert checkpoint.get_entry(path, 2, 0)["status"] == "translated_needs_review"
