from __future__ import annotations

import json

import pytest


def test_composition_plan_preserves_layout_and_records_dependency_hashes():
    from translation.analysis import apply_mtool_compositions, build_mtool_composition_plan

    items = [
        ("こんにちは", "こんにちは"),
        ("世界", "世界"),
        ("\u3000こんにちは\r\n  世界 \n", "\u3000こんにちは\r\n  世界 \n"),
    ]
    plan = build_mtool_composition_plan(items)

    assert set(plan.entries) == {2}
    assert plan.entries[2].dependency_indexes == (0, 1)
    assert plan.contexts_for_child(0)[0]["text"] == "\u3000こんにちは\n  世界 \n"
    assert plan.contexts_for_child(0)[0]["line"] == 1
    assert plan.contexts_for_child(1)[0]["line"] == 2
    assert plan.repair_parent_for_child(0) == plan.entries[2]
    assert plan.extract_child_translations(
        plan.entries[2],
        "\u3000\u4f60\u597d\r\n  \u4e16\u754c \n",
    ) == {
        0: "\u4f60\u597d",
        1: "\u4e16\u754c",
    }
    assert plan.extract_child_translations(
        plan.entries[2],
        "\u4f60\u597d\n\u4e16\u754c\n\u591a\u4f59",
    ) == {}

    translated_items = [
        ("こんにちは", "你好"),
        ("世界", "世间"),
        items[2],
    ]
    records: list[dict] = []
    processed = apply_mtool_compositions(
        plan,
        translated_items=translated_items,
        checkpoint_entries={
            (0, 0): {"status": "translated"},
            (1, 0): {"status": "translated_needs_review"},
        },
        file_path="game.json",
        progress_records=records,
        processed_targets=2,
        total_targets=3,
        progress_callback=None,
        save_record=lambda _path, target, **record: target.append(record),
        mark_dirty=lambda: None,
        emit_progress=lambda *args, **kwargs: None,
        progress_status=lambda status: status,
    )

    assert processed == 3
    assert translated_items[2][1] == "\u3000你好\r\n  世间 \n"
    assert records[0]["status"] == "translated_needs_review"
    assert records[0]["entry_classification"] == "composed_multiline"
    assert records[0]["composition_version"] == plan.version
    assert len(records[0]["dependencies"]) == 2
    assert len(records[0]["dependency_fingerprint"]) == 64


def test_composition_plan_rejects_ambiguous_normalized_standalone_keys():
    from translation.analysis import build_mtool_composition_plan

    items = [
        ("猫", "猫"),
        (" 猫 ", " 猫 "),
        ("犬", "犬"),
        ("猫\n犬", "猫\n犬"),
    ]

    plan = build_mtool_composition_plan(items)

    assert plan.entries == {}
    assert plan.contexts_by_child == {}


def test_composition_keeps_authoritative_preserved_multiline_parent():
    from translation.analysis import apply_mtool_compositions, build_mtool_composition_plan

    items = [
        ("gl_FragColor = color;", "gl_FragColor = color;"),
        ("\n\ngl_FragColor = color;", "\n\ngl_FragColor = color;"),
    ]
    plan = build_mtool_composition_plan(items)
    translated_items = list(items)
    records: list[dict] = []

    processed = apply_mtool_compositions(
        plan,
        translated_items=translated_items,
        checkpoint_entries={
            (0, 0): {"status": "preserved"},
            (1, 0): {"status": "preserved"},
        },
        file_path="game.json",
        progress_records=records,
        processed_targets=2,
        total_targets=2,
        progress_callback=None,
        save_record=lambda _path, target, **record: target.append(record),
        mark_dirty=lambda: None,
        emit_progress=lambda *args, **kwargs: None,
        progress_status=lambda status: status,
    )

    assert plan.is_composed_parent(1)
    assert processed == 2
    assert translated_items == items
    assert records == []


def test_neighbor_context_plan_is_bounded_and_respects_existing_context():
    from translation.analysis import build_mtool_neighbor_context_plan

    items = [
        ("\u300c\u524d\u3005\u306e\u53f0\u8a5e\u300d", ""),
        ("\u300c\u524d\u306e\u53f0\u8a5e\u300d", ""),
        ("\u300c\u5bfe\u8c61\u306e\u53f0\u8a5e\u300d", ""),
        ("\u300c\u6b21\u306e\u53f0\u8a5e\u300d", ""),
        ("\u300c\u6b21\u3005\u306e\u53f0\u8a5e\u300d", ""),
    ]

    plan = build_mtool_neighbor_context_plan(
        items,
        radius=2,
        context_max_chars=200,
        min_dialogue_items=3,
    )

    contexts = plan.contexts_for_child(2)
    assert [context["text"] for context in contexts] == [
        items[0][0],
        items[1][0],
        items[3][0],
        items[4][0],
    ]
    assert [context["offset"] for context in contexts] == [-2, -1, 1, 2]
    assert [context["source_index"] for context in contexts] == [0, 1, 3, 4]
    assert {context["context_kind"] for context in contexts} == {"scene_neighbor"}

    excluded = build_mtool_neighbor_context_plan(
        items,
        excluded_child_indexes={2},
        radius=2,
        context_max_chars=200,
        min_dialogue_items=3,
    )
    assert excluded.contexts_by_child == {}

    too_small = build_mtool_neighbor_context_plan(
        items,
        radius=2,
        context_max_chars=10,
        min_dialogue_items=3,
    )
    assert too_small.contexts_by_child == {}


def test_neighbor_context_plan_rejects_ui_boundaries_and_keeps_multiline_neighbor():
    from translation.analysis import build_mtool_neighbor_context_plan

    ui_items = [
        ("\u300c\u524d\u3005\u300d", ""),
        ("\u3010\u30e1\u30cb\u30e5\u30fc\u3011", ""),
        ("\u300c\u5bfe\u8c61\u300d", ""),
        ("\u300c\u6b21\u300d", ""),
        ("\u300c\u6b21\u3005\u300d", ""),
    ]
    assert build_mtool_neighbor_context_plan(
        ui_items,
        context_max_chars=200,
    ).contexts_by_child == {}

    multiline_items = [
        ("\u300c\u524d\u3005\u306e\n\u53f0\u8a5e\u300d", ""),
        ("\u300c\u524d\u306e\u53f0\u8a5e\u300d", ""),
        ("\u300c\u5bfe\u8c61\u306e\u53f0\u8a5e\u300d", ""),
        ("\u300c\u6b21\u306e\u53f0\u8a5e\u300d", ""),
        ("\u300c\u6b21\u3005\u306e\u53f0\u8a5e\u300d", ""),
    ]
    contexts = build_mtool_neighbor_context_plan(
        multiline_items,
        context_max_chars=200,
    ).contexts_for_child(2)[0]
    assert contexts["text"] == multiline_items[0][0]
    assert contexts["line"] == 1
    assert contexts["offset"] == -2


def test_context_payload_deduplicates_blocks_and_forces_structured_protocol():
    from translation.batching import (
        build_batch_payload,
        build_batch_system_prompt,
        pack_api_candidate_batches,
        resolve_parallel_candidate_protocol,
    )

    context = {"text": "赤い花\n青い空", "line": 1, "parent_index": 2}
    candidates = [
        {
            "i": 0,
            "idx": 0,
            "source": "赤い花",
            "text": "赤い花",
            "protected": "赤い花",
            "short_label": True,
            "contexts": [context],
        },
        {
            "i": 1,
            "idx": 1,
            "source": "青い空",
            "text": "青い空",
            "protected": "青い空",
            "short_label": True,
            "contexts": [dict(context, line=2)],
        },
    ]

    payload = json.loads(build_batch_payload(candidates, compact=True))
    assert payload["contexts"] == [[0, "赤い花\n青い空"]]
    assert payload["items"][0][3]["context_refs"] == [[0, 1]]
    assert payload["items"][1][3]["context_refs"] == [[0, 2]]
    assert "read-only contexts" in build_batch_system_prompt(compact=True, include_context=True)
    assert resolve_parallel_candidate_protocol(
        "line",
        "line",
        candidates,
        {"line_for_short_only": True, "short_line_max_chars": 80},
    ) == "json"

    batches = pack_api_candidate_batches(
        candidates,
        batch_size=40,
        max_batch_chars=20,
        batch_cfg={"short_line_max_chars": 80},
    )
    assert len(batches) == 1


def test_neighbor_context_payload_deduplicates_sources_and_preserves_offsets():
    from translation.batching import build_batch_payload

    shared = {
        "text": "\u300c\u5171\u901a\u306e\u524d\u53f0\u8a5e\u300d",
        "line": 1,
        "offset": -1,
        "context_kind": "scene_neighbor",
    }
    candidates = [
        {
            "i": 0,
            "text": "\u300c\u5bfe\u8c61\u4e00\u300d",
            "contexts": [
                shared,
                {
                    "text": "\u300c\u6b21\u306e\u53f0\u8a5e\u300d",
                    "line": 1,
                    "offset": 1,
                    "context_kind": "scene_neighbor",
                },
            ],
        },
        {
            "i": 1,
            "text": "\u300c\u5bfe\u8c61\u4e8c\u300d",
            "contexts": [
                dict(shared, offset=-2),
            ],
        },
    ]

    payload = json.loads(build_batch_payload(candidates, compact=True))

    assert payload["contexts"] == [
        [0, "\u300c\u5171\u901a\u306e\u524d\u53f0\u8a5e\u300d"],
        [1, "\u300c\u6b21\u306e\u53f0\u8a5e\u300d"],
    ]
    assert payload["items"][0][3]["context_refs"] == [[0, 1, -1], [1, 1, 1]]
    assert payload["items"][1][3]["context_refs"] == [[0, 1, -2]]


def test_parallel_workflow_routes_adult_neighbor_context_to_sensitive_model(
    monkeypatch,
    tmp_path,
):
    import config
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    old_dir = checkpoint.CHECKPOINT_DIR
    old_provider = config.DEFAULT_CONFIG.get("model_provider")
    old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
    checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
    config.DEFAULT_CONFIG["model_provider"] = "api"
    config.DEFAULT_CONFIG["batch_translation"].update({
        "api_parallel_enabled": True,
        "api_event_driven_enabled": True,
        "api_concurrency": 2,
        "api_max_retries": 0,
        "api_model_routing_enabled": True,
        "api_fast_model": "api:fast",
        "api_quality_model": "api:quality",
        "api_sensitive_routing_enabled": True,
        "api_sensitive_model": "api:minimax-m3",
        "api_sensitive_repair_enabled": False,
        "api_fast_categories": ["short_label"],
        "api_quality_retry_issue_types": ["identical_japanese_source"],
        "json_batch_size": 40,
        "max_batch_chars": 4000,
        "protocol": "line",
        "compact_json_protocol": True,
        "line_for_short_only": True,
        "short_line_max_chars": 80,
        "mtool_composition_enabled": False,
        "mtool_neighbor_context_enabled": True,
        "mtool_neighbor_context_radius": 2,
        "mtool_neighbor_context_max_chars": 320,
        "mtool_neighbor_context_min_dialogue_items": 3,
    })
    try:
        sources = [
            "\u300c\u524d\u3005\u306e\u53f0\u8a5e\u300d",
            "\u300c\u524d\u306e\u53f0\u8a5e\u300d",
            "\u300c\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b\u300d",
            "\u300c\u6b21\u306e\u53f0\u8a5e\u300d",
            "\u300c\u6b21\u3005\u306e\u53f0\u8a5e\u300d",
        ]
        path = tmp_path / "sample.json"
        path.write_text(
            json.dumps({source: source for source in sources}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls: list[tuple[str, object]] = []

        def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
            payload = json.loads(text)
            calls.append((model, payload))
            items = payload["items"] if isinstance(payload, dict) else payload
            translated = []
            for item in items:
                item_id, source = item[0], item[1]
                output = (
                    source
                    if model == "api:minimax-m3"
                    else "__SYM_0__译文__SYM_1__"
                )
                translated.append([item_id, output])
            return json.dumps(translated, ensure_ascii=False)

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        TranslationPipeline(
            model="api:quality",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path))

        assert {model for model, _payload in calls} == {"api:quality", "api:minimax-m3"}
        assert len(calls) == 2
        adult_payload = next(payload for model, payload in calls if model == "api:minimax-m3")
        assert isinstance(adult_payload, dict)
        assert len(adult_payload["contexts"]) == 4
        assert adult_payload["items"][0][3]["context_refs"] == [
            [0, 1, -2],
            [1, 1, -1],
            [2, 1, 1],
            [3, 1, 2],
        ]
        adult_entry = checkpoint.get_entry(str(path), 2, 0)
        assert adult_entry["model_identifier"] == "api:minimax-m3"
        assert adult_entry["sensitive_adult"] is True
        assert adult_entry["context_kinds"] == ["scene_neighbor"]
        assert adult_entry["status"] == "review_required"
        assert {issue["type"] for issue in adult_entry["issues"]} == {
            "untranslated_japanese",
        }
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["model_provider"] = old_provider
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_sensitive_route_uses_quality_model_for_final_isolated_retry(
    monkeypatch,
    tmp_path,
):
    import config
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    old_dir = checkpoint.CHECKPOINT_DIR
    old_provider = config.DEFAULT_CONFIG.get("model_provider")
    old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
    checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
    config.DEFAULT_CONFIG["model_provider"] = "api"
    config.DEFAULT_CONFIG["batch_translation"].update({
        "api_parallel_enabled": True,
        "api_event_driven_enabled": True,
        "api_concurrency": 2,
        "api_max_retries": 0,
        "api_model_routing_enabled": True,
        "api_fast_model": "api:fast",
        "api_quality_model": "api:quality",
        "api_sensitive_routing_enabled": True,
        "api_sensitive_model": "api:minimax-m3",
        "api_sensitive_repair_enabled": True,
        "api_sensitive_repair_batch_size": 5,
        "api_sensitive_repair_max_batch_chars": 1000,
        "api_sensitive_repair_single_retry": True,
        "api_sensitive_cross_model_retry_enabled": True,
        "api_sensitive_repair_issue_types": ["untranslated_japanese"],
        "api_fast_categories": ["short_label"],
        "api_quality_retry_issue_types": ["identical_japanese_source"],
        "json_batch_size": 40,
        "max_batch_chars": 4000,
        "protocol": "line",
        "compact_json_protocol": True,
        "line_for_short_only": True,
        "short_line_max_chars": 80,
        "mtool_composition_enabled": False,
        "mtool_neighbor_context_enabled": True,
        "mtool_neighbor_context_radius": 2,
        "mtool_neighbor_context_max_chars": 320,
        "mtool_neighbor_context_min_dialogue_items": 3,
    })
    try:
        sources = [
            "\u300c\u524d\u3005\u306e\u53f0\u8a5e\u300d",
            "\u300c\u524d\u306e\u53f0\u8a5e\u300d",
            "\u300c\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b\u300d",
            "\u300c\u6b21\u306e\u53f0\u8a5e\u300d",
            "\u300c\u6b21\u3005\u306e\u53f0\u8a5e\u300d",
        ]
        path = tmp_path / "sample.json"
        path.write_text(
            json.dumps({source: source for source in sources}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls: list[tuple[str, object]] = []
        sensitive_calls = 0

        def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
            nonlocal sensitive_calls
            payload = json.loads(text)
            calls.append((model, payload))
            items = payload["items"] if isinstance(payload, dict) else payload
            if model == "api:minimax-m3":
                sensitive_calls += 1
            translated = []
            for item in items:
                item_id = item[0] if isinstance(item, list) else item["i"]
                source = item[1] if isinstance(item, list) else item["text"]
                if model == "api:minimax-m3" and sensitive_calls < 3:
                    output = source
                elif model == "api:minimax-m3":
                    output = source.replace(
                        "\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b",
                        "\u6211\u8981\u5185\u5c04\u4e86",
                    )
                elif "\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b" in source:
                    output = source.replace(
                        "\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b",
                        "\u6211\u8981\u5185\u5c04\u4e86",
                    )
                else:
                    output = "__SYM_0__译文__SYM_1__"
                translated.append([item_id, output])
            return json.dumps(translated, ensure_ascii=False)

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        TranslationPipeline(
            model="api:quality",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path))

        assert [model for model, _payload in calls].count("api:minimax-m3") == 2
        assert [model for model, _payload in calls].count("api:quality") == 2
        repair_payloads = [
            payload
            for _model, payload in calls
            if isinstance(payload, dict)
            and payload["items"][0][3].get("issues")
        ]
        assert len(repair_payloads) == 2
        assert repair_payloads[0]["items"][0][3]["previous"]
        assert repair_payloads[1]["items"][0][3]["previous"] == ""
        assert repair_payloads[0]["items"][0][3]["issues"] == [
            "untranslated_japanese"
        ]

        adult_entry = checkpoint.get_entry(str(path), 2, 0)
        assert adult_entry["translated"] == "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d"
        assert adult_entry["status"] == "translated"
        assert adult_entry["model_identifier"] == "api:quality"
        assert adult_entry["sensitive_adult"] is True
        assert adult_entry["sensitive_repair_round"] == 2
        assert adult_entry["retry_count"] == 2
        assert adult_entry["batch_id"].startswith("api_event_sensitive_r2_")
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["model_provider"] = old_provider
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_sensitive_route_repairs_failed_child_through_full_parent(
    monkeypatch,
    tmp_path,
):
    import config
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    old_dir = checkpoint.CHECKPOINT_DIR
    old_provider = config.DEFAULT_CONFIG.get("model_provider")
    old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
    checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
    config.DEFAULT_CONFIG["model_provider"] = "api"
    config.DEFAULT_CONFIG["batch_translation"].update({
        "api_parallel_enabled": True,
        "api_event_driven_enabled": True,
        "api_concurrency": 2,
        "api_max_retries": 0,
        "api_model_routing_enabled": True,
        "api_fast_model": "api:fast",
        "api_quality_model": "api:quality",
        "api_sensitive_routing_enabled": True,
        "api_sensitive_model": "api:minimax-m3",
        "api_sensitive_repair_enabled": True,
        "api_sensitive_repair_batch_size": 5,
        "api_sensitive_repair_max_batch_chars": 1000,
        "api_sensitive_repair_single_retry": True,
        "api_sensitive_cross_model_retry_enabled": True,
        "api_sensitive_parent_repair_enabled": True,
        "api_sensitive_parent_repair_max_chars": 2400,
        "api_sensitive_repair_issue_types": ["untranslated_japanese"],
        "api_fast_categories": ["short_label"],
        "api_quality_retry_issue_types": ["identical_japanese_source"],
        "json_batch_size": 40,
        "max_batch_chars": 4000,
        "protocol": "line",
        "compact_json_protocol": True,
        "line_for_short_only": True,
        "short_line_max_chars": 80,
        "mtool_composition_enabled": True,
        "mtool_context_max_chars": 1200,
        "mtool_context_max_per_item": 2,
        "mtool_neighbor_context_enabled": False,
    })
    try:
        child = "\u300c\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b\u300d"
        parent = child + "\n"
        path = tmp_path / "sample.json"
        output_path = tmp_path / "sample.translated.json"
        path.write_text(
            json.dumps({child: child, parent: parent}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls: list[tuple[str, object]] = []

        def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
            payload = json.loads(text)
            calls.append((model, payload))
            items = payload["items"] if isinstance(payload, dict) else payload
            translated = []
            for item in items:
                item_id = item[0] if isinstance(item, list) else item["i"]
                source = item[1] if isinstance(item, list) else item["text"]
                metadata = item[3] if isinstance(item, list) and len(item) > 3 else {}
                issues = metadata.get("issues", [])
                if "composed_child_repair" in issues:
                    output = source.replace(
                        "\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b",
                        "\u6211\u8981\u5185\u5c04\u4e86",
                    )
                else:
                    output = source
                translated.append([item_id, output])
            return json.dumps(translated, ensure_ascii=False)

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        TranslationPipeline(
            model="api:quality",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path), str(output_path))

        assert [model for model, _payload in calls] == [
            "api:minimax-m3",
            "api:minimax-m3",
            "api:quality",
            "api:quality",
        ]
        parent_payload = calls[-1][1]
        parent_items = (
            parent_payload["items"]
            if isinstance(parent_payload, dict)
            else parent_payload
        )
        assert parent_items[0][3]["issues"] == [
            "composed_child_repair"
        ]

        child_entry = checkpoint.get_entry(str(path), 0, 0)
        assert child_entry["translated"] == "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d"
        assert child_entry["status"] == "translated"
        assert child_entry["model_identifier"] == "api:quality"
        assert child_entry["sensitive_repair_round"] == 3
        assert child_entry["sensitive_parent_repair"] is True
        assert child_entry["sensitive_parent_index"] == 1
        assert child_entry["retry_count"] == 3
        assert child_entry["batch_id"].startswith(
            "api_event_sensitive_parent_"
        )

        translated = dict(parse_json(str(output_path)))
        assert translated[child] == "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d"
        assert translated[parent] == "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d\n"
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["model_provider"] = old_provider
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


@pytest.mark.parametrize("terminal_failure", ["line_break", "symbol"])
def test_sensitive_parent_failure_gets_one_isolated_quality_terminal_retry(
    monkeypatch,
    tmp_path,
    terminal_failure,
):
    import config
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    old_dir = checkpoint.CHECKPOINT_DIR
    old_provider = config.DEFAULT_CONFIG.get("model_provider")
    old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
    checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
    config.DEFAULT_CONFIG["model_provider"] = "api"
    config.DEFAULT_CONFIG["batch_translation"].update({
        "api_parallel_enabled": True,
        "api_event_driven_enabled": True,
        "api_concurrency": 2,
        "api_max_retries": 0,
        "api_model_routing_enabled": True,
        "api_fast_model": "api:fast",
        "api_quality_model": "api:quality",
        "api_sensitive_routing_enabled": True,
        "api_sensitive_model": "api:minimax-m3",
        "api_sensitive_repair_enabled": True,
        "api_sensitive_repair_batch_size": 5,
        "api_sensitive_repair_max_batch_chars": 1000,
        "api_sensitive_repair_single_retry": True,
        "api_sensitive_cross_model_retry_enabled": True,
        "api_sensitive_parent_repair_enabled": True,
        "api_sensitive_parent_repair_max_chars": 2400,
        "api_sensitive_repair_issue_types": [
            "untranslated_japanese",
            "line_break_preservation",
            "symbol_preservation",
        ],
        "api_fast_categories": ["short_label"],
        "api_quality_retry_issue_types": [
            "line_break_preservation",
            "symbol_preservation",
        ],
        "json_batch_size": 40,
        "max_batch_chars": 4000,
        "protocol": "line",
        "compact_json_protocol": True,
        "line_for_short_only": True,
        "short_line_max_chars": 80,
        "mtool_composition_enabled": True,
        "mtool_context_max_chars": 1200,
        "mtool_context_max_per_item": 2,
        "mtool_neighbor_context_enabled": False,
    })
    try:
        child = "\u300c\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b\u300d"
        parent = child + "\n"
        path = tmp_path / "sample.json"
        output_path = tmp_path / "sample.translated.json"
        path.write_text(
            json.dumps({child: child, parent: parent}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls: list[tuple[str, object]] = []

        def fake_translate(
            model,
            text,
            system_prompt=None,
            terminology=None,
            options=None,
            **kwargs,
        ):
            payload = json.loads(text)
            calls.append((model, payload))
            items = payload["items"] if isinstance(payload, dict) else payload
            translated = []
            for item in items:
                item_id = item[0] if isinstance(item, list) else item["i"]
                source = item[1] if isinstance(item, list) else item["text"]
                metadata = (
                    item[3]
                    if isinstance(item, list) and len(item) > 3
                    else {}
                )
                issues = metadata.get("issues", [])
                if "sensitive_parent_repair_failed" in issues:
                    output = source.replace(
                        "\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b",
                        "\u6211\u8981\u5185\u5c04\u4e86",
                    )
                elif "composed_child_repair" in issues:
                    output = source
                elif model == "api:quality" and issues:
                    output = source.replace(
                        "\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b",
                        (
                            "\u6211\u8981\u5185\u5c04\u4e86\n\u4e32\u5165"
                            if terminal_failure == "line_break"
                            else "\u6211\u8981\u5185\u5c04\u4e86"
                        ),
                    )
                    if terminal_failure == "symbol":
                        output = output.replace("__SYM_0__", "")
                else:
                    output = source
                translated.append([item_id, output])
            return json.dumps(translated, ensure_ascii=False)

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        TranslationPipeline(
            model="api:quality",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path), str(output_path))

        assert [model for model, _payload in calls] == [
            "api:minimax-m3",
            "api:minimax-m3",
            "api:quality",
            "api:quality",
            "api:quality",
        ]
        terminal_payload = calls[-1][1]
        assert not isinstance(terminal_payload, dict) or (
            "contexts" not in terminal_payload
        )
        terminal_items = (
            terminal_payload["items"]
            if isinstance(terminal_payload, dict)
            else terminal_payload
        )
        assert "sensitive_parent_repair_failed" in (
            terminal_items[0][3]["issues"]
        )

        child_entry = checkpoint.get_entry(str(path), 0, 0)
        assert child_entry["translated"] == (
            "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d"
        )
        assert child_entry["status"] == "translated"
        assert child_entry["model_identifier"] == "api:quality"
        assert child_entry["sensitive_repair_round"] == 4
        assert child_entry["retry_count"] == 4
        assert child_entry["batch_id"].startswith(
            "api_event_sensitive_terminal_"
        )

        translated = dict(parse_json(str(output_path)))
        assert translated[child] == "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d"
        assert translated[parent] == (
            "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d\n"
        )
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["model_provider"] = old_provider
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_parallel_mtool_workflow_translates_children_once_and_recomposes_parent(
    monkeypatch,
    tmp_path,
):
    import config
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    old_dir = checkpoint.CHECKPOINT_DIR
    old_provider = config.DEFAULT_CONFIG.get("model_provider")
    old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
    checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
    config.DEFAULT_CONFIG["model_provider"] = "api"
    config.DEFAULT_CONFIG["batch_translation"].update(
        {
            "api_parallel_enabled": True,
            "api_concurrency": 2,
            "api_max_retries": 0,
            "api_model_routing_enabled": False,
            "json_batch_size": 40,
            "max_batch_chars": 4000,
            "protocol": "line",
            "compact_json_protocol": True,
            "line_for_short_only": True,
            "short_line_max_chars": 80,
            "mtool_composition_enabled": True,
            "mtool_context_max_chars": 1200,
            "mtool_context_max_per_item": 2,
        }
    )
    try:
        source_items = {
            "赤い花": "赤い花",
            "青い空": "青い空",
            "赤い花\r\n　青い空": "赤い花\r\n　青い空",
        }
        path = tmp_path / "sample.json"
        path.write_text(json.dumps(source_items, ensure_ascii=False), encoding="utf-8")
        calls: list[dict] = []

        def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
            payload = json.loads(text)
            calls.append(payload)
            assert payload["contexts"] == [[0, "赤い花\n　青い空"]]
            translations = {"赤い花": "红花", "青い空": "蓝天"}
            return json.dumps(
                [[item[0], translations[item[1]]] for item in payload["items"]],
                ensure_ascii=False,
            )

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        TranslationPipeline(
            model="api:test",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path))

        output = dict(parse_json(str(path).replace(".json", ".translated.json")))
        assert len(calls) == 1
        assert output == {
            "赤い花": "红花",
            "青い空": "蓝天",
            "赤い花\r\n　青い空": "红花\r\n　蓝天",
        }
        parent = checkpoint.get_entry(str(path), 2, 0)
        assert parent["entry_classification"] == "composed_multiline"
        assert parent["model_identifier"] == "deterministic-composition"
        assert [dependency["row"] for dependency in parent["dependencies"]] == [0, 1]
        original_fingerprint = parent["dependency_fingerprint"]

        checkpoint_data = checkpoint.load_checkpoint(str(path))
        checkpoint_data["entries"]["0_0"]["translated"] = "绯红花"
        checkpoint_data["entries"]["0_0"]["output_translation"] = "绯红花"
        checkpoint.save_checkpoint(str(path), checkpoint_data)
        calls.clear()
        TranslationPipeline(
            model="api:test",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path))

        resumed_output = dict(parse_json(str(path).replace(".json", ".translated.json")))
        assert calls == []
        assert resumed_output["赤い花\r\n　青い空"] == "绯红花\r\n　蓝天"
        resumed_parent = checkpoint.get_entry(str(path), 2, 0)
        assert resumed_parent["dependency_fingerprint"] != original_fingerprint
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["model_provider"] = old_provider
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_parent_first_translates_scene_lines_once_and_checkpoints_children(
    monkeypatch,
    tmp_path,
):
    import config
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    old_dir = checkpoint.CHECKPOINT_DIR
    old_provider = config.DEFAULT_CONFIG.get("model_provider")
    old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
    checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
    config.DEFAULT_CONFIG["model_provider"] = "api"
    config.DEFAULT_CONFIG["batch_translation"].update({
        "api_parallel_enabled": True,
        "api_event_driven_enabled": True,
        "api_concurrency": 2,
        "api_max_retries": 0,
        "api_model_routing_enabled": False,
        "json_batch_size": 40,
        "max_batch_chars": 4000,
        "protocol": "line",
        "compact_json_protocol": True,
        "line_for_short_only": True,
        "short_line_max_chars": 80,
        "mtool_composition_enabled": True,
        "mtool_parent_first_enabled": True,
        "mtool_parent_first_max_chars": 2400,
        "mtool_context_max_chars": 1200,
        "mtool_context_max_per_item": 2,
        "mtool_neighbor_context_enabled": False,
    })
    try:
        red = "赤い花"
        blue = "青い空"
        parent = red + "\n　" + blue
        path = tmp_path / "sample.json"
        path.write_text(
            json.dumps({red: red, blue: blue, parent: parent}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls: list[dict] = []

        def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
            payload = json.loads(text)
            calls.append(payload)
            assert set(payload) == {"parents"}
            scene = payload["parents"][0]
            assert [(line["i"], line["target"]) for line in scene["lines"]] == [
                (0, True),
                (1, True),
            ]
            return json.dumps({
                "parents": [{
                    "i": scene["i"],
                    "lines": [
                        {"i": 0, "t": "红花"},
                        {"i": 1, "t": "蓝天"},
                    ],
                }],
            }, ensure_ascii=False)

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        TranslationPipeline(
            model="api:test",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path))

        assert len(calls) == 1
        output = dict(parse_json(str(path).replace(".json", ".translated.json")))
        assert output == {
            red: "红花",
            blue: "蓝天",
            parent: "红花\n　蓝天",
        }
        for row, parent_index in ((0, 2), (1, 2)):
            entry = checkpoint.get_entry(str(path), row, 0)
            assert entry["status"] == "translated"
            assert entry["parent_first"] is True
            assert entry["parent_first_index"] == parent_index
            assert entry["batch_id"].startswith("api_parent_first_")
        composed = checkpoint.get_entry(str(path), 2, 0)
        assert composed["entry_classification"] == "composed_multiline"
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["model_provider"] = old_provider
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_parent_protocol_rejects_extra_ids_and_allows_missing_line_fallback():
    from translation.batching import BatchTranslationError, parse_parent_batch_response

    partial = parse_parent_batch_response(
        '{"parents":[{"i":0,"lines":[{"i":10,"t":"红花"}]}]}',
        {0: [10, 11]},
    )
    assert json.loads(partial[0]) == {"10": "红花"}

    with pytest.raises(BatchTranslationError, match="unexpected parent line index"):
        parse_parent_batch_response(
            '{"parents":[{"i":0,"lines":[{"i":99,"t":"污染"}]}]}',
            {0: [10, 11]},
        )


def test_parent_first_falls_back_only_missing_or_invalid_children():
    from translation.batching import BatchJob, BatchResult
    from translation.workflow.json_parallel import _finish_parent_first_result

    valid = {"i": 0, "idx": 10, "source": "赤い花", "parent_first": True}
    missing = {"i": 1, "idx": 11, "source": "青い空", "parent_first": True}
    parent = {
        "i": 0,
        "idx": 20,
        "scene_targets": [valid, missing],
    }
    job = BatchJob(
        batch_id="api_parent_first_000000",
        candidates=[parent],
        protocol="parent_json",
        model="api:test",
    )
    result = BatchResult(
        batch_id=job.batch_id,
        translations={0: '{"10":"红花"}'},
        error=None,
        attempts=1,
        elapsed_seconds=0.1,
    )

    class Pipeline:
        @staticmethod
        def _finish_batch_translation(candidate, translated):
            return translated, "translated", []

    accepted, payloads, fallback = _finish_parent_first_result(
        Pipeline(),
        job,
        result,
    )
    assert [candidate["idx"] for candidate in accepted] == [10]
    assert payloads == {10: ("红花", "translated", [])}
    assert [candidate["idx"] for candidate in fallback] == [11]


def test_content_rejection_fallback_removes_read_only_context():
    from translation.workflow.json_parallel import _fast_fallback_group

    captured: list[list[dict]] = []

    class FakePipeline:
        @staticmethod
        def _translate_json_candidates(candidates, *args, **kwargs):
            captured.append(candidates)
            return {candidate["idx"]: ("译文", "translated", []) for candidate in candidates}

    candidates = [
        {
            "i": 0,
            "idx": 4,
            "source": "赤い花",
            "contexts": [{"text": "赤い花\n青い空", "line": 1}],
        }
    ]
    payloads, errors = _fast_fallback_group(
        FakePipeline(),
        candidates,
        "game.json",
        {},
        "api:minimax-m3",
        RuntimeError("content rejected"),
    )

    assert "contexts" not in captured[0][0]
    assert candidates[0]["contexts"]
    assert payloads[4][0] == "译文"
    assert set(errors) == {4}
