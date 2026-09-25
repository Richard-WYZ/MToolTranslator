from __future__ import annotations


def test_mixed_kana_normalization_collapses_exact_cross_script_aliases():
    from translation.classification import normalize_mixed_kana_for_model

    cases = {
        "いやイヤ": "いや",
        "もういやイヤ": "もういや",
        "だめﾀﾞﾒ": "だめ",
        "もーりモーリ": "もーり",
        "ああああアアアア！": "ああああ！",
    }

    for source, expected in cases.items():
        result = normalize_mixed_kana_for_model(source)
        assert result.text == expected
        assert result.changed
        assert result.spans[0].kind == "cross_script_duplicate"


def test_mixed_kana_normalization_preserves_normal_boundaries_and_stutters():
    from translation.classification import normalize_mixed_kana_for_model

    sources = [
        "私はハーフ",
        "あなたもモーリ",
        "かわいいイラスト",
        "触手がおオマンコに入って",
        "そ、そんな",
        "いっッ！",
        "きゃキャ",
    ]

    for source in sources:
        result = normalize_mixed_kana_for_model(source)
        assert result.text == source
        assert not result.changed


def test_model_candidate_uses_normalized_view_without_changing_authoritative_source():
    from translation.batching import prepare_model_candidate

    source = "もういやイヤ！"
    candidate = prepare_model_candidate(batch_i=0, idx=4, source=source)

    assert candidate["source"] == source
    assert candidate["model_source"] == "もういや！"
    assert candidate["protected"] == "もういや！"
    assert candidate["source_normalization"]["version"].startswith("mixed-kana-v1-")
    assert candidate["source_normalization"]["mixed_kana"] == [
        {
            "start": 4,
            "end": 6,
            "kept": "いや",
            "removed": "イヤ",
            "reading": "いや",
            "kind": "cross_script_duplicate",
        }
    ]


def test_quality_prompt_forbids_double_translation_of_cross_script_aliases():
    from translation.quality import quality_prompt_rules

    prompt = quality_prompt_rules()
    assert "adjacent hiragana and katakana" in prompt
    assert "translate the meaning once" in prompt


def test_batch_result_records_mixed_kana_model_view_for_resume_audit():
    from translation.batching import prepare_model_candidate
    from translation.batching.results import apply_batch_translation_results

    class Glossary:
        @staticmethod
        def auto_extract(_source, _translated):
            return []

    source = "もういやイヤ！"
    candidate = prepare_model_candidate(batch_i=0, idx=0, source=source)
    records = []
    translated_items = [(source, source)]

    processed, glossary_changed = apply_batch_translation_results(
        candidates=[candidate],
        translated_payloads={0: ("不要了！", "translated", [])},
        translated_items=translated_items,
        processed_targets=0,
        total_targets=1,
        progress_callback=None,
        file_path="sample.json",
        mtool=True,
        progress_records=records,
        glossary=Glossary(),
        mark_dirty=lambda: None,
        emit_progress=lambda *args, **kwargs: None,
        progress_status=lambda status: status,
        apply_confirmed_terms_to_outputs=lambda *args, **kwargs: None,
    )

    assert processed == 1
    assert not glossary_changed
    assert records[0]["original"] == source
    assert records[0]["model_source"] == "もういや！"
    assert records[0]["source_normalization"]["mixed_kana"][0]["removed"] == "イヤ"
