from __future__ import annotations

import pytest


def _candidate(idx: int, source: str, *, short_label: bool) -> dict:
    return {
        "i": 99,
        "idx": idx,
        "source": source,
        "text": source,
        "protected": source,
        "short_label": short_label,
    }


def test_api_candidate_packing_separates_routes_and_reindexes_dense():
    from translation.batching import (
        api_job_uses_fast_model,
        candidate_batch_category,
        pack_api_candidate_batches,
    )

    short_a = _candidate(0, "フィーネ", short_label=True)
    dialogue = _candidate(1, "「ここに来てください」と彼女は言った。", short_label=False)
    short_b = _candidate(2, "支払う", short_label=True)
    narrative = _candidate(3, "長い物語です。" * 30, short_label=False)
    cfg = {"short_line_max_chars": 20, "long_text_min_chars": 120}

    batches = pack_api_candidate_batches(
        [short_a, dialogue, short_b, narrative],
        batch_size=40,
        max_batch_chars=4000,
        batch_cfg=cfg,
    )

    assert [[candidate["idx"] for candidate in batch] for batch in batches] == [[0, 2], [1], [3]]
    assert [[candidate["i"] for candidate in batch] for batch in batches] == [[0, 1], [0], [0]]
    assert [candidate_batch_category(batch[0], cfg) for batch in batches] == [
        "short_label",
        "dialogue",
        "long_narrative",
    ]
    routed_cfg = dict(cfg, api_fast_categories=["short_label", "prose"])
    assert api_job_uses_fast_model(batches[0], routed_cfg)
    assert not api_job_uses_fast_model(batches[1], routed_cfg)


def test_dialogue_category_uses_boundaries_and_read_only_context():
    from translation.batching import candidate_batch_category

    cfg = {"short_line_max_chars": 20, "long_text_min_chars": 120}
    quoted_ui_term = _candidate(
        0,
        "\u3010\u5f13\u3011\u88c5\u5099\u4e2d\u300e\u30dd\u30a4\u30f3\u30c8\u30b7\u30e7\u30c3\u30c8\u300f\u3092\u53d6\u5f97\u3059\u308b\u3002",
        short_label=False,
    )
    dialogue_fragment = _candidate(
        1,
        "\u3053\u3053\u304b\u3089\u5148\u306f\u7d9a\u304d\u3067\u3059\u300d",
        short_label=False,
    )
    context_fragment = _candidate(
        2,
        "\u3053\u3053\u304b\u3089\u5148\u306f\u7d9a\u304d\u3067\u3059",
        short_label=False,
    )
    context_fragment["contexts"] = [{
        "text": "\u300c\u6700\u521d\u306e\u884c\n\u3053\u3053\u304b\u3089\u5148\u306f\u7d9a\u304d\u3067\u3059\u300d",
        "line": 2,
    }]
    parenthesized_ui = _candidate(
        3,
        "\u30a2\u30ca\u30eb\u30c7\u30a3\u30eb\u30c9\uff08\u30d0\u30ea\u30a2\u30f3\u30c8\uff09",
        short_label=False,
    )

    assert candidate_batch_category(quoted_ui_term, cfg) == "prose"
    assert candidate_batch_category(dialogue_fragment, cfg) == "dialogue"
    assert candidate_batch_category(context_fragment, cfg) == "dialogue"
    assert candidate_batch_category(parenthesized_ui, cfg) == "prose"


def test_explicit_adult_detection_is_high_confidence_and_uses_context():
    from translation.classification import (
        candidate_has_explicit_adult_content,
        has_explicit_adult_content,
    )

    assert has_explicit_adult_content("\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b")
    assert candidate_has_explicit_adult_content({
        "source": "\u300c\u3084\u3081\u3066\u300d",
        "contexts": [{"text": "\u7cbe\u6db2\u3092\u5410\u304d\u51fa\u3057\u305f", "line": 1}],
    })
    assert not has_explicit_adult_content("\u5974\u96b7\u5e02\u5834")
    assert not has_explicit_adult_content("\u80f8\u3092\u5f35\u3063\u3066\u9032\u3080")
    assert has_explicit_adult_content("\u30d5\u30a7\u30e9\u3092\u3059\u308b")
    assert not has_explicit_adult_content("\u30d5\u30a7\u30e9\u30fc\u30ea\u306b\u4e57\u308b")
    assert has_explicit_adult_content(
        "\u30b1\u30c4\u306e\u7a74\u72af\u3055\u308c\u3066\u598a\u5a20\u3057\u305f"
    )
    assert candidate_has_explicit_adult_content({
        "source": "\u300c\u3044\u3001\u3044\u3084\u3041\u3042\u3042\u3042\u3042\u3042\u2661",
        "contexts": [{
            "text": (
                "\u300c\u3044\u3001\u3044\u3084\u3041\u3042\u3042\u3042\u3042\u3042\u2661\n"
                "\u3053\u308c\u4ee5\u4e0a\u611f\u5ea6\u3092\u3042\u3052\u3089\u308c\u305f\u3089"
                "\u6211\u6162\u3067\u304d\u306a\u3044\u2661"
            ),
            "line": 1,
        }],
    })
    assert candidate_has_explicit_adult_content({
        "source": "\u307e\u309b\u305f\u309b\u30a4\u30af\u309b\u3045\u3045\u3046\u309b\u3046\u309b\u3046\u3046\u3046\u2661\u2661\u2661\u300d",
    })
    assert candidate_has_explicit_adult_content({
        "source": "\u300c\u3042\u309b\u3042\u309b\u3042\u309b\u3041\u3041\u3041\u3042\u3042\u3042\u30c3\uff01\uff1f\u2661\u2661",
        "contexts": [{
            "text": (
                "\u300c\u3042\u309b\u3042\u309b\u3042\u309b\u3041\u3041\u3041\u3042\u3042\u3042\u30c3\uff01\uff1f\u2661\u2661\n"
                "\u305d\u3093\u306a\u3053\u3068\u8a00\u308f\u306a\u3044\u3067\u3047\u3048\u3048\u3048\u2661\u2661\u2661\u300d"
            ),
            "line": 1,
        }],
    })
    assert not has_explicit_adult_content("\u3042\u308a\u304c\u3068\u3046\u2661")
    assert not has_explicit_adult_content("\u5927\u597d\u304d\u3060\u3088\u2661\u2661")
    assert not has_explicit_adult_content("\u884c\u304f\u3088\u2661")
    assert not has_explicit_adult_content("\u5974\u96b7\u5e02\u5834")
    assert candidate_has_explicit_adult_content({
        "source": "\uff08\u3084\u3063\u3071\u308a\u79c1\u306b\u306f\u30e0\u30ea\u2026\u2026\u3002",
        "contexts": [{
            "text": (
                "\uff08\u3084\u3063\u3071\u308a\u79c1\u306b\u306f\u30e0\u30ea\u2026\u2026\u3002\n"
                "\u79c1\u306f\u5974\u96b7\u3068\u3057\u3066\u751f\u304d\u308b\u306e\u304c"
                "\u304a\u4f3c\u5408\u3044\u3060\u3063\u305f\u3093\u3060\u3041\u2661\uff09"
            ),
            "line": 1,
        }],
    })


def test_sensitive_adult_batches_are_isolated_and_use_configured_model():
    from translation.batching import (
        candidate_needs_quality_model_retry,
        pack_api_candidate_batches,
        select_api_job_model,
        select_api_job_options,
    )

    safe = _candidate(
        0,
        "\u300c\u3053\u3053\u3067\u5f85\u3063\u3066\u3044\u308b\u300d",
        short_label=False,
    )
    adult = _candidate(
        1,
        "\u300c\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b\u300d",
        short_label=False,
    )
    cfg = {
        "api_model_routing_enabled": True,
        "api_fast_model": "api:fast",
        "api_quality_model": "api:quality",
        "api_sensitive_routing_enabled": True,
        "api_sensitive_model": "api:minimax-m3",
        "api_fast_categories": ["short_label"],
        "api_quality_retry_issue_types": ["untranslated_japanese"],
        "quality_num_predict": 4096,
        "short_line_max_chars": 80,
        "long_text_min_chars": 120,
    }

    batches = pack_api_candidate_batches(
        [safe, adult],
        batch_size=40,
        max_batch_chars=4000,
        batch_cfg=cfg,
    )

    assert [[candidate["idx"] for candidate in batch] for batch in batches] == [[0], [1]]
    assert not batches[0][0]["sensitive_adult"]
    assert batches[1][0]["sensitive_adult"]
    assert select_api_job_model(batches[0], cfg, default_model="api:default") == "api:quality"
    assert select_api_job_model(batches[1], cfg, default_model="api:default") == "api:minimax-m3"
    assert select_api_job_options(batches[0], {"num_predict": 2048}, cfg)["num_predict"] == 4096
    assert select_api_job_options(batches[1], {"num_predict": 2048}, cfg)["num_predict"] == 2048
    assert not candidate_needs_quality_model_retry(
        batches[1][0],
        "review_required",
        [{"type": "untranslated_japanese"}],
        cfg,
    )


def test_sensitive_repair_selector_is_issue_bounded_and_stops_on_terminal_errors():
    from translation.batching import candidate_needs_sensitive_repair

    adult = _candidate(
        0,
        "\u300c\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b\u300d",
        short_label=False,
    )
    safe = _candidate(
        1,
        "\u300c\u3053\u3053\u3067\u5f85\u3063\u3066\u3044\u308b\u300d",
        short_label=False,
    )
    cfg = {
        "api_sensitive_routing_enabled": True,
        "api_sensitive_repair_enabled": True,
        "api_sensitive_repair_issue_types": [
            "untranslated_japanese",
            "suspicious_artifact",
        ],
    }

    assert candidate_needs_sensitive_repair(
        adult,
        "review_required",
        [{"type": "untranslated_japanese"}],
        cfg,
        repair_round=1,
    )
    assert candidate_needs_sensitive_repair(
        adult,
        "translated_needs_review",
        [{"type": "suspicious_artifact"}],
        cfg,
        repair_round=1,
    )
    assert not candidate_needs_sensitive_repair(
        adult,
        "translated_needs_review",
        [{"type": "suspicious_artifact"}],
        cfg,
        repair_round=2,
    )
    assert not candidate_needs_sensitive_repair(
        safe,
        "review_required",
        [{"type": "untranslated_japanese"}],
        cfg,
        repair_round=1,
    )
    assert not candidate_needs_sensitive_repair(
        adult,
        "review_required",
        [
            {"type": "untranslated_japanese"},
            {"type": "api_batch_transport_error"},
        ],
        cfg,
        repair_round=1,
    )


def test_api_candidate_packing_counts_unique_templates_for_capacity():
    from translation.batching import pack_api_candidate_batches, prepare_model_candidate

    candidates = [
        prepare_model_candidate(batch_i=0, idx=0, source="\u52a0\u8b770"),
        prepare_model_candidate(batch_i=1, idx=1, source="\u52a0\u8b771"),
        prepare_model_candidate(batch_i=2, idx=2, source="\u52a0\u8b772"),
        prepare_model_candidate(batch_i=3, idx=3, source="\u653b\u64830"),
    ]

    batches = pack_api_candidate_batches(
        candidates,
        batch_size=1,
        max_batch_chars=4000,
        batch_cfg={"short_line_max_chars": 80},
    )

    assert [[candidate["idx"] for candidate in batch] for batch in batches] == [
        [0, 1, 2],
        [3],
    ]
    assert [[candidate["i"] for candidate in batch] for batch in batches] == [
        [0, 1, 2],
        [0],
    ]


def test_line_batch_missing_item_retries_missing_and_adjacent_items():
    from translation.batching import BatchTranslationError, parse_line_batch_response

    with pytest.raises(BatchTranslationError) as exc_info:
        parse_line_batch_response(
            "0\t零\n1\t一\n3\t三\n4\t四",
            {0, 1, 2, 3, 4},
        )

    error = exc_info.value
    assert error.partial_results == {0: "零", 4: "四"}
    assert error.retry_indexes == {1, 2, 3}


def test_truncated_json_batch_salvages_complete_items_and_retries_only_missing():
    from translation.batching import BatchTranslationError, parse_batch_response

    response = '{"items":[{"i":0,"t":"零"},{"i":1,"t":"一"},{"i":2,"t":"未完成'
    with pytest.raises(BatchTranslationError) as exc_info:
        parse_batch_response(response, {0, 1, 2})

    assert exc_info.value.partial_results == {0: "零", 1: "一"}
    assert exc_info.value.retry_indexes == {2}


def test_structural_batch_errors_skip_identical_scheduler_retries():
    from translation.batching import BatchJob, BatchTranslationError, run_concurrent_batches

    calls = 0

    def fail_structurally(_job):
        nonlocal calls
        calls += 1
        raise BatchTranslationError("missing batch indexes")

    results = list(run_concurrent_batches(
        [BatchJob("batch-1", [], "line")],
        1,
        fail_structurally,
        max_retries=3,
        retry_backoff_seconds=[0],
    ))

    assert calls == 1
    assert results[0].attempts == 1
    assert isinstance(results[0].error, BatchTranslationError)


def test_dynamic_batch_scheduler_prioritizes_followups():
    from translation.batching import BatchJob, run_dynamic_batches

    execution_order: list[str] = []
    callback_order: list[str] = []

    def translate(job):
        execution_order.append(job.batch_id)
        return {0: job.batch_id}

    def on_result(job, result):
        callback_order.append(result.batch_id)
        if job.batch_id == "initial-a":
            return [
                BatchJob(
                    "followup-a",
                    [{"i": 0}],
                    "json",
                    priority=0,
                )
            ]
        return []

    results = run_dynamic_batches(
        [
            BatchJob("initial-a", [{"i": 0}], "json"),
            BatchJob("initial-b", [{"i": 0}], "json"),
        ],
        1,
        translate,
        on_result,
        max_retries=0,
    )

    assert execution_order == ["initial-a", "followup-a", "initial-b"]
    assert callback_order == execution_order
    assert [result.batch_id for result in results] == execution_order


def test_dynamic_scheduler_enforces_independent_model_windows():
    import threading

    from translation.batching import (
        BatchJob,
        ModelAdmissionPolicy,
        run_dynamic_batches,
    )

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = {"qwen": 0, "minimax": 0}
    peaks = {"qwen": 0, "minimax": 0}

    def translate(job):
        model = str(job.model)
        with lock:
            active[model] += 1
            peaks[model] = max(peaks[model], active[model])
        if job.batch_id in {"q1", "m1"}:
            barrier.wait(timeout=2)
        with lock:
            active[model] -= 1
        return {0: job.batch_id}

    policy = ModelAdmissionPolicy(
        initial_by_model={"qwen": 1, "minimax": 1},
        maximum_by_model={"qwen": 2, "minimax": 2},
        increase_every=100,
    )
    results = run_dynamic_batches(
        [
            BatchJob("q1", [{"i": 0}], "json", model="qwen"),
            BatchJob("q2", [{"i": 0}], "json", model="qwen"),
            BatchJob("m1", [{"i": 0}], "json", model="minimax"),
            BatchJob("m2", [{"i": 0}], "json", model="minimax"),
        ],
        1,
        translate,
        lambda _job, _result: [],
        max_retries=0,
        admission_policy=policy,
    )

    assert len(results) == 4
    assert peaks == {"qwen": 1, "minimax": 1}
    snapshot = policy.snapshot()
    assert snapshot["models"]["qwen"]["peak_active"] == 1
    assert snapshot["models"]["minimax"]["peak_active"] == 1


def test_model_admission_limits_inflight_characters_per_model():
    from translation.batching import BatchJob, BatchResult, ModelAdmissionPolicy

    policy = ModelAdmissionPolicy(
        initial_by_model={"qwen": 4, "minimax": 4},
        maximum_by_model={"qwen": 8, "minimax": 8},
        maximum_inflight_chars_by_model={"qwen": 4000, "minimax": 8000},
    )
    qwen_first = BatchJob(
        "q1",
        [{"i": 0, "protected": "文" * 3000}],
        "json",
        model="qwen",
    )
    qwen_second = BatchJob(
        "q2",
        [{"i": 0, "protected": "字" * 2000}],
        "json",
        model="qwen",
    )
    minimax = BatchJob(
        "m1",
        [{"i": 0, "protected": "語" * 5000}],
        "json",
        model="minimax",
    )

    assert policy.can_submit(qwen_first)
    policy.submitted(qwen_first)
    assert not policy.can_submit(qwen_second)
    assert policy.can_submit(minimax)
    policy.submitted(minimax)

    policy.completed(
        qwen_first,
        BatchResult("q1", {0: "译文"}, None, 1, 0.1),
    )
    assert policy.can_submit(qwen_second)
    policy.completed(
        minimax,
        BatchResult("m1", {0: "译文"}, None, 1, 0.1),
    )

    states = policy.snapshot()["models"]
    assert states["qwen"]["maximum_inflight_chars"] == 4000
    assert states["qwen"]["peak_active_chars"] == 3000
    assert states["minimax"]["maximum_inflight_chars"] == 8000
    assert states["minimax"]["peak_active_chars"] == 5000


def test_model_admission_allows_one_oversized_job_without_deadlock():
    from translation.batching import BatchJob, ModelAdmissionPolicy

    policy = ModelAdmissionPolicy(
        initial_by_model={"qwen": 2},
        maximum_inflight_chars_by_model={"qwen": 1000},
    )
    oversized = BatchJob(
        "q1",
        [{"i": 0, "protected": "文" * 1200}],
        "json",
        model="qwen",
    )
    follower = BatchJob(
        "q2",
        [{"i": 0, "protected": "字"}],
        "json",
        model="qwen",
    )

    assert policy.can_submit(oversized)
    policy.submitted(oversized)
    assert not policy.can_submit(follower)


def test_adaptive_model_window_grows_after_clean_successes():
    from translation.batching import (
        BatchJob,
        ModelAdmissionPolicy,
        run_dynamic_batches,
    )

    policy = ModelAdmissionPolicy(
        initial_by_model={"qwen": 1},
        maximum_by_model={"qwen": 3},
        increase_every=1,
    )
    run_dynamic_batches(
        [
            BatchJob(f"q{index}", [{"i": 0}], "json", model="qwen")
            for index in range(4)
        ],
        1,
        lambda job: {0: job.batch_id},
        lambda _job, _result: [],
        max_retries=0,
        admission_policy=policy,
    )

    state = policy.snapshot()["models"]["qwen"]
    assert state["window"] == 3
    assert state["peak_window"] == 3
    assert state["increases"] == 2


def test_transport_retry_shrinks_only_affected_model_window():
    from translation.batching import (
        BatchJob,
        ModelAdmissionPolicy,
        run_dynamic_batches,
    )

    qwen_calls = 0

    class Congestion(RuntimeError):
        retryable = True
        status_code = 503

    def translate(job):
        nonlocal qwen_calls
        if job.model == "qwen":
            qwen_calls += 1
            if qwen_calls == 1:
                raise Congestion("busy")
        return {0: job.batch_id}

    policy = ModelAdmissionPolicy(
        initial_by_model={"qwen": 4, "minimax": 4},
        maximum_by_model={"qwen": 8, "minimax": 8},
        increase_every=100,
        decrease_factor=0.5,
    )
    run_dynamic_batches(
        [
            BatchJob("q1", [{"i": 0}], "json", model="qwen"),
            BatchJob("m1", [{"i": 0}], "json", model="minimax"),
        ],
        1,
        translate,
        lambda _job, _result: [],
        max_retries=1,
        retry_backoff_seconds=[0],
        admission_policy=policy,
    )

    states = policy.snapshot()["models"]
    assert states["qwen"]["window"] == 2
    assert states["qwen"]["retry_results"] == 1
    assert states["qwen"]["decreases"] == 1
    assert states["minimax"]["window"] == 4
    assert states["minimax"]["decreases"] == 0


def test_structural_translation_error_does_not_reduce_model_window():
    from translation.batching import (
        BatchJob,
        BatchTranslationError,
        ModelAdmissionPolicy,
        run_dynamic_batches,
    )

    policy = ModelAdmissionPolicy(
        initial_by_model={"qwen": 4},
        maximum_by_model={"qwen": 8},
    )

    def translate(_job):
        raise BatchTranslationError("missing structured IDs")

    run_dynamic_batches(
        [BatchJob("q1", [{"i": 0}], "json", model="qwen")],
        1,
        translate,
        lambda _job, _result: [],
        max_retries=2,
        retry_backoff_seconds=[0],
        admission_policy=policy,
    )

    state = policy.snapshot()["models"]["qwen"]
    assert state["window"] == 4
    assert state["decreases"] == 0
    assert state["errors"] == 1


def test_structural_translation_error_does_not_interrupt_clean_growth():
    from translation.batching import (
        BatchJob,
        BatchTranslationError,
        ModelAdmissionPolicy,
        run_dynamic_batches,
    )

    policy = ModelAdmissionPolicy(
        initial_by_model={"qwen": 1},
        maximum_by_model={"qwen": 2},
        increase_every=2,
    )

    def translate(job):
        if job.batch_id == "structural":
            raise BatchTranslationError("missing structured IDs")
        return {0: job.batch_id}

    run_dynamic_batches(
        [
            BatchJob("clean-1", [{"i": 0}], "json", model="qwen"),
            BatchJob("structural", [{"i": 0}], "json", model="qwen"),
            BatchJob("clean-2", [{"i": 0}], "json", model="qwen"),
        ],
        1,
        translate,
        lambda _job, _result: [],
        max_retries=0,
        admission_policy=policy,
    )

    state = policy.snapshot()["models"]["qwen"]
    assert state["window"] == 2
    assert state["increases"] == 1


def test_quality_repair_failures_split_and_clear_rejected_draft():
    from translation.batching import BatchJob
    from translation.workflow.json_parallel import _event_quality_followups

    candidates = [
        dict(
            _candidate(index, f"\u30c6\u30ad\u30b9\u30c8{index}", short_label=False),
            i=index,
            quality_retry={"previous": "\u574f\u8bd1\u6587", "issues": ["line_break_preservation"]},
            quality_repair_depth=0,
            quality_repair_fresh=False,
        )
        for index in range(4)
    ]
    job = BatchJob(
        "api_event_quality_r1_test",
        candidates,
        "json",
        model="api:qwen",
    )
    payloads = {
        index: (
            f"\u4e32\u884c\u6c61\u67d3{index}",
            "review_required",
            [{"type": "line_break_preservation"}],
        )
        for index in range(4)
    }

    accepted, accepted_payloads, followups = _event_quality_followups(
        job,
        payloads,
        quality_model="api:qwen",
        quality_options={},
        batch_cfg={
            "api_quality_recursive_repair_enabled": True,
            "api_quality_recursive_max_depth": 6,
            "api_quality_recursive_fresh_single": True,
            "api_quality_recursive_issue_types": ["line_break_preservation"],
            "api_quality_retry_issue_types": ["line_break_preservation"],
        },
    )

    assert accepted == []
    assert accepted_payloads == {}
    assert [len(item.candidates) for item in followups] == [2, 2]
    assert all(
        candidate["quality_retry"]["previous"] == ""
        and candidate["quality_repair_depth"] == 1
        for item in followups
        for candidate in item.candidates
    )


def test_quality_repair_uses_one_final_fresh_single_retry():
    from translation.batching import BatchJob
    from translation.workflow.json_parallel import _event_quality_followups

    candidate = dict(
        _candidate(7, "\u30c6\u30ad\u30b9\u30c8", short_label=False),
        i=0,
        contexts=[
            {"text": "\u7d9a\u304d\u306e\u53f0\u8a5e", "line": 8},
        ],
        quality_retry={"previous": "\u574f\u8bd1\u6587", "issues": ["line_break_preservation"]},
        quality_repair_depth=2,
        quality_repair_fresh=False,
    )
    payload = {
        7: (
            "\u30c6\u30ad\u30b9\u30c8",
            "review_required",
            [{"type": "line_break_preservation"}],
        )
    }
    cfg = {
        "api_quality_recursive_repair_enabled": True,
        "api_quality_recursive_max_depth": 6,
        "api_quality_recursive_fresh_single": True,
        "api_quality_recursive_issue_types": ["line_break_preservation"],
        "api_quality_retry_issue_types": ["line_break_preservation"],
    }

    accepted, accepted_payloads, followups = _event_quality_followups(
        BatchJob("api_event_quality_r3_test", [candidate], "json", model="api:qwen"),
        payload,
        quality_model="api:qwen",
        quality_options={},
        batch_cfg=cfg,
    )

    assert accepted == []
    assert accepted_payloads == {}
    assert len(followups) == 1
    fresh = followups[0].candidates[0]
    assert fresh["quality_repair_fresh"] is True
    assert fresh["quality_repair_context_isolated"] is True
    assert fresh["quality_retry"]["previous"] == ""
    assert "contexts" not in fresh

    final_accepted, final_payloads, final_followups = _event_quality_followups(
        followups[0],
        payload,
        quality_model="api:qwen",
        quality_options={},
        batch_cfg=cfg,
    )
    assert final_accepted == followups[0].candidates
    assert final_payloads == payload
    assert final_followups == []

    semantic_candidate = dict(
        candidate,
        quality_retry={
            "previous": "\u574f\u8bd1\u6587",
            "issues": ["honorific_rendering_review"],
        },
    )
    semantic_payload = {
        7: (
            "\u8bd1\u6587",
            "translated_needs_review",
            [{"type": "honorific_rendering_review"}],
        )
    }
    semantic_cfg = {
        **cfg,
        "api_quality_recursive_issue_types": [
            "honorific_rendering_review",
            "line_break_preservation",
        ],
        "api_quality_retry_issue_types": [
            "honorific_rendering_review",
            "line_break_preservation",
        ],
    }
    _accepted, _payloads, semantic_followups = _event_quality_followups(
        BatchJob(
            "api_event_quality_r3_semantic",
            [semantic_candidate],
            "json",
            model="api:qwen",
        ),
        semantic_payload,
        quality_model="api:qwen",
        quality_options={},
        batch_cfg=semantic_cfg,
    )
    assert semantic_followups[0].candidates[0]["contexts"] == candidate["contexts"]
    assert not semantic_followups[0].candidates[0][
        "quality_repair_context_isolated"
    ]

    structural_after_semantic = {
        7: (
            "\u4e32\u5165\u4e86\u4e0b\u4e00\u53e5\n\u4e0b\u4e00\u53e5",
            "review_required",
            [{"type": "line_break_preservation"}],
        )
    }
    _accepted, _payloads, isolated_followups = _event_quality_followups(
        semantic_followups[0],
        structural_after_semantic,
        quality_model="api:qwen",
        quality_options={},
        batch_cfg=semantic_cfg,
    )
    isolated = isolated_followups[0].candidates[0]
    assert isolated["quality_repair_fresh"] is True
    assert isolated["quality_repair_context_isolated"] is True
    assert "contexts" not in isolated

    final_accepted, final_payloads, final_followups = _event_quality_followups(
        isolated_followups[0],
        structural_after_semantic,
        quality_model="api:qwen",
        quality_options={},
        batch_cfg=semantic_cfg,
    )
    assert final_accepted == isolated_followups[0].candidates
    assert final_payloads == structural_after_semantic
    assert final_followups == []


def test_permanent_api_errors_skip_scheduler_retries():
    from translation.batching import BatchJob, run_concurrent_batches

    calls = 0

    class PermanentError(RuntimeError):
        retryable = False

    def fail_permanently(_job):
        nonlocal calls
        calls += 1
        raise PermanentError("bad request")

    results = list(run_concurrent_batches(
        [BatchJob("batch-1", [], "json")],
        1,
        fail_permanently,
        max_retries=3,
        retry_backoff_seconds=[0],
    ))

    assert calls == 1
    assert results[0].attempts == 1
    assert isinstance(results[0].error, PermanentError)


def test_api_client_marks_content_rejection_as_permanent(monkeypatch):
    import requests

    from translation.models import api_client

    class FakeResponse:
        status_code = 400
        text = '{"message":"DataInspectionFailed: inappropriate content"}'

        def raise_for_status(self):
            raise requests.HTTPError("400 Client Error", response=self)

    with pytest.raises(api_client.APIRequestError) as exc_info:
        api_client._raise_for_status_with_body(FakeResponse())

    assert exc_info.value.retryable is False
    assert exc_info.value.content_rejected is True

    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise exc_info.value

    monkeypatch.setattr(api_client, "translate_once", fail_once)
    with pytest.raises(api_client.APIRequestError):
        api_client.translate("model", "text")
    assert calls == 1


def test_api_client_marks_monthly_quota_exhaustion_as_permanent():
    import requests

    from translation.models import api_client

    class FakeResponse:
        status_code = 429
        text = '{"error":{"type":"GoUsageLimitError","message":"Monthly usage limit reached"}}'

        def raise_for_status(self):
            raise requests.HTTPError("429 Too Many Requests", response=self)

    with pytest.raises(api_client.APIRequestError) as exc_info:
        api_client._raise_for_status_with_body(FakeResponse())

    assert exc_info.value.quota_exhausted is True
    assert exc_info.value.retryable is False


def test_api_client_parses_retry_after_seconds_and_http_date():
    from datetime import datetime, timezone

    from translation.models import api_client

    assert api_client._retry_after_seconds("7") == 7.0
    assert api_client._retry_after_seconds(
        "Fri, 24 Jul 2026 12:00:10 GMT",
        now=datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc),
    ) == 10.0
    assert api_client._retry_after_seconds("invalid") is None


def test_scheduler_honors_retry_after_over_local_backoff(monkeypatch):
    from translation.batching import BatchJob, run_concurrent_batches
    import translation.batching.scheduler as scheduler

    calls = 0
    sleeps: list[float] = []

    class RateLimited(RuntimeError):
        retryable = True
        retry_after_seconds = 7.0

    def fail_once(_job):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimited("slow down")
        return {0: "完成"}

    monkeypatch.setattr(scheduler.time, "sleep", sleeps.append)
    results = list(run_concurrent_batches(
        [BatchJob("batch-1", [{"i": 0}], "json")],
        1,
        fail_once,
        max_retries=1,
        retry_backoff_seconds=[2],
    ))

    assert sleeps == [7.0]
    assert results[0].error is None
    assert results[0].attempts == 2


def test_translation_phase_timer_tracks_first_request_to_last_response(monkeypatch):
    import translation.usage as usage
    import translation.usage.tracker as tracker

    ticks = iter([10.0, 11.0, 14.5, 15.0])
    monkeypatch.setattr(tracker.time, "perf_counter", lambda: next(ticks))
    usage.reset()
    usage.record_request_start()
    usage.record_request_start()
    usage.record_response_received()
    usage.record_response_received()

    snapshot = usage.snapshot()
    assert snapshot["request_calls"] == 2
    assert snapshot["translation_phase_seconds"] == 5.0
    usage.reset()


def test_request_latency_metrics_are_grouped_by_provider_and_model(monkeypatch):
    import translation.usage as usage
    import translation.usage.tracker as tracker

    ticks = iter([1.0, 3.0, 4.0, 9.0])
    monkeypatch.setattr(tracker.time, "perf_counter", lambda: next(ticks))
    usage.reset()
    first = usage.record_request_start("api", "quality")
    usage.record_response_received("api", "quality", first)
    second = usage.record_request_start("api", "quality")
    usage.record_response_received("api", "quality", second)

    metrics = usage.snapshot()["request_latency_seconds"]["api"]["models"]["quality"]
    assert metrics == {
        "count": 2,
        "total": 7.0,
        "mean": 3.5,
        "min": 2.0,
        "p50": 3.5,
        "p95": 4.85,
        "max": 5.0,
    }
    usage.reset()


def test_recursive_split_reindexes_each_retry_batch():
    from translation.batching import BatchTranslationError, translate_candidates_with_split

    calls: list[list[int]] = []
    candidates = [_candidate(idx, f"項目{idx}", short_label=True) for idx in range(4)]

    def translate_raw(batch, _options, _protocol, _model):
        calls.append([candidate["i"] for candidate in batch])
        if len(batch) > 1:
            raise BatchTranslationError("split")
        return {0: f"译文{batch[0]['idx']}"}

    translated = translate_candidates_with_split(
        candidates,
        batch_options={},
        batch_protocol="line",
        model="api:test",
        translate_raw=translate_raw,
        finish_candidate=lambda _candidate, text: (text, "translated", []),
        fallback_candidate=lambda candidate, _exc: {
            candidate["idx"]: (candidate["source"], "review_required", [])
        },
    )

    assert calls[0] == [0, 1, 2, 3]
    assert all(indexes == list(range(len(indexes))) for indexes in calls)
    assert {idx: payload[0] for idx, payload in translated.items()} == {
        0: "译文0",
        1: "译文1",
        2: "译文2",
        3: "译文3",
    }


def test_batch_result_aggregates_confirmed_term_backfill_once():
    from translation.batching import apply_batch_translation_results

    class FakeGlossary:
        @staticmethod
        def auto_extract(source, translated):
            return [{"source": source, "target": translated}]

    candidates = [
        {"idx": 0, "source": "フィーネ"},
        {"idx": 1, "source": "ジーク"},
    ]
    translated_items = [("フィーネ", "フィーネ"), ("ジーク", "ジーク")]
    backfills: list[list[dict]] = []

    processed, changed = apply_batch_translation_results(
        candidates=candidates,
        translated_payloads={
            0: ("菲妮", "translated", []),
            1: ("吉克", "translated", []),
        },
        translated_items=translated_items,
        processed_targets=0,
        total_targets=2,
        progress_callback=None,
        file_path="sample.json",
        mtool=True,
        progress_records=[],
        glossary=FakeGlossary(),
        mark_dirty=lambda: None,
        emit_progress=lambda *args, **kwargs: None,
        progress_status=lambda status: status,
        apply_confirmed_terms_to_outputs=lambda _path, terms: backfills.append(terms),
    )

    assert processed == 2
    assert changed is True
    assert backfills == [[
        {"source": "フィーネ", "target": "菲妮"},
        {"source": "ジーク", "target": "吉克"},
    ]]


def test_fast_model_review_result_is_retried_in_parallel_by_quality_model(monkeypatch, tmp_path):
    import json

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
        "api_fast_categories": ["short_label"],
        "json_batch_size": 2,
        "max_batch_chars": 4000,
        "protocol": "line",
        "compact_json_protocol": False,
        "line_for_short_only": True,
        "short_line_max_chars": 20,
    })
    try:
        path = tmp_path / "sample.json"
        path.write_text(
            json.dumps({"フィーネ": "フィーネ", "ジーク": "ジーク"}, ensure_ascii=False),
            encoding="utf-8",
        )
        calls: list[str] = []

        def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
            calls.append(model)
            if model == "api:fast":
                return "0\t菲妮\n1\tジーク"
            payload = json.loads(text)
            assert [item["text"] for item in payload] == ["ジーク"]
            return json.dumps([{"i": 0, "t": "吉克"}], ensure_ascii=False)

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        TranslationPipeline(
            model="api:quality",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path))

        assert calls == ["api:fast", "api:quality"]
        assert dict(parse_json(str(path).replace(".json", ".translated.json"))) == {
            "フィーネ": "菲妮",
            "ジーク": "吉克",
        }
        entries = checkpoint.load_checkpoint(str(path))["entries"].values()
        assert {entry["status"] for entry in entries} == {"translated"}
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["model_provider"] = old_provider
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_quality_model_review_result_gets_one_bounded_quality_retry(monkeypatch, tmp_path):
    import json

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
        "api_fast_categories": ["short_label"],
        "api_quality_retry_issue_types": ["identical_japanese_source"],
        "json_batch_size": 2,
        "max_batch_chars": 4000,
        "protocol": "line",
        "compact_json_protocol": False,
        "line_for_short_only": True,
        "short_line_max_chars": 10,
    })
    try:
        source = "これは十分に長い説明文なので品質モデルを使用します"
        path = tmp_path / "sample.json"
        path.write_text(json.dumps({source: source}, ensure_ascii=False), encoding="utf-8")
        calls: list[str] = []
        payloads: list[list[dict[str, object]]] = []

        def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
            calls.append(model)
            payloads.append(json.loads(text))
            translated = source if len(calls) == 1 else "这是一段足够长的说明文字，因此使用质量模型。"
            return json.dumps([{"i": 0, "t": translated}], ensure_ascii=False)

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        TranslationPipeline(
            model="api:quality",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(path))

        assert calls == ["api:quality", "api:quality"]
        assert payloads[1][0]["review"]["previous"] == source
        assert payloads[1][0]["review"]["issues"]
        assert dict(parse_json(str(path).replace(".json", ".translated.json")))[source] == (
            "这是一段足够长的说明文字，因此使用质量模型。"
        )
        entry = next(iter(checkpoint.load_checkpoint(str(path))["entries"].values()))
        assert entry["status"] == "translated"
        assert entry["retry_count"] == 0
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["model_provider"] = old_provider
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_failed_parallel_fallback_cannot_be_downgraded_to_preserved():
    from translation.batching import BatchJob, BatchResult
    from translation.workflow.json_parallel import _finish_api_batch_result

    candidate = _candidate(0, "翻訳する", short_label=True)

    class FakePipeline:
        @staticmethod
        def _translate_json_candidates(*args, **kwargs):
            return {0: ("翻訳する", "review_required", [{"type": "untranslated_japanese"}])}

        @staticmethod
        def _status_for_output(source, translated, issues):
            return "preserved" if source == translated else "translated_needs_review"

    payloads = _finish_api_batch_result(
        FakePipeline(),
        BatchJob("batch", [dict(candidate, i=0)], "json", model="api:test", options={}),
        BatchResult("batch", {}, RuntimeError("failed"), 1, 0.1),
        "sample.json",
        {},
    )

    assert payloads[0][1] == "review_required"


def test_structural_single_fallback_keeps_the_explicit_job_model():
    from translation.workflow.batch_adapter import (
        translate_single_candidate_after_batch_failure,
    )

    candidate = dict(
        _candidate(
            0,
            "\u300c\u4e2d\u51fa\u3057\u3057\u3066\u3084\u308b\u300d",
            short_label=False,
        ),
        i=0,
    )

    class FakePipeline:
        model = "api:qwen"
        system_prompt = "translate"
        calls: list[str] = []

        @staticmethod
        def _compose_system_prompt(base_prompt, term_hits=None, strict=False):
            return base_prompt

        @classmethod
        def _batch_translate_call(cls, model, text, system_prompt, options):
            cls.calls.append(model)
            return "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d"

        @staticmethod
        def _finish_batch_translation(candidate, translated):
            return translated, "translated", []

        @staticmethod
        def _status_for_output(source, translated, issues):
            return "translated_needs_review" if issues else "translated"

        @staticmethod
        def _translate_cell_with_meta(*args, **kwargs):
            raise AssertionError("must not fall back through the pipeline default model")

    payloads = translate_single_candidate_after_batch_failure(
        FakePipeline(),
        candidate,
        "sample.json",
        RuntimeError("bad JSON"),
        model="api:minimax-m3",
        options={"num_predict": 2048},
    )

    assert FakePipeline.calls == ["api:minimax-m3"]
    assert payloads[0][0] == "\u300c\u6211\u8981\u5185\u5c04\u4e86\u300d"
    assert payloads[0][1] == "translated_needs_review"
    assert {issue["type"] for issue in payloads[0][2]} == {"batch_fallback"}


def test_quality_content_rejection_routes_batch_to_fast_model_for_review():
    from translation.batching import BatchJob, BatchResult
    from translation.workflow.json_parallel import _finish_api_batch_result

    candidate = dict(_candidate(0, "翻訳する", short_label=False), i=0)

    class ContentRejected(RuntimeError):
        retryable = False
        content_rejected = True

    class FakePipeline:
        calls = []

        @classmethod
        def _translate_json_candidates(cls, candidates, file_path, options, protocol, model=None):
            cls.calls.append((protocol, model, [item["idx"] for item in candidates]))
            return {0: ("进行翻译", "translated", [])}

        @staticmethod
        def _status_for_output(source, translated, issues):
            return "translated_needs_review" if issues else "translated"

    payloads = _finish_api_batch_result(
        FakePipeline(),
        BatchJob("batch", [candidate], "json", model="api:quality", options={}),
        BatchResult("batch", {}, ContentRejected("blocked"), 1, 0.1),
        "sample.json",
        {},
        {
            "api_model_routing_enabled": True,
            "api_fast_model": "api:fast",
            "api_quality_model": "api:quality",
        },
    )

    assert FakePipeline.calls == [("json", "api:fast", [0])]
    assert payloads[0][0] == "进行翻译"
    assert payloads[0][1] == "translated_needs_review"
    assert {issue["type"] for issue in payloads[0][2]} == {"api_content_filter_fallback"}


def test_quality_content_rejection_bisects_and_falls_back_only_rejected_leaf():
    from translation.batching import BatchJob, BatchResult
    from translation.workflow.json_parallel import _finish_api_batch_result

    candidates = [
        dict(_candidate(idx, "blocked" if idx == 2 else f"source-{idx}", short_label=False), i=idx)
        for idx in range(4)
    ]

    class ContentRejected(RuntimeError):
        retryable = False
        content_rejected = True
        status_code = 400

    class FakePipeline:
        quality_calls: list[list[int]] = []
        fast_calls: list[list[int]] = []

        @classmethod
        def _translate_json_candidate_batch_raw(cls, group, options, protocol, model=None):
            indexes = [item["idx"] for item in group]
            cls.quality_calls.append(indexes)
            if any(item["source"] == "blocked" for item in group):
                raise ContentRejected("blocked")
            return {item["i"]: f"quality-{item['idx']}" for item in group}

        @classmethod
        def _translate_json_candidates(cls, group, file_path, options, protocol, model=None):
            cls.fast_calls.append([item["idx"] for item in group])
            return {
                item["idx"]: (f"fast-{item['idx']}", "translated", [])
                for item in group
            }

        @staticmethod
        def _finish_batch_translation(candidate, translated):
            return translated, "translated", []

        @staticmethod
        def _status_for_output(source, translated, issues):
            return "translated_needs_review" if issues else "translated"

    payloads = _finish_api_batch_result(
        FakePipeline(),
        BatchJob("batch", candidates, "json", model="api:quality", options={}),
        BatchResult("batch", {}, ContentRejected("blocked batch"), 1, 0.1),
        "sample.json",
        {},
        {
            "api_model_routing_enabled": True,
            "api_fast_model": "api:fast",
            "api_quality_model": "api:quality",
            "api_content_split_max_depth": 3,
        },
    )

    assert FakePipeline.quality_calls == [[0, 1], [2, 3], [2], [3]]
    assert FakePipeline.fast_calls == [[2]]
    assert payloads[0] == ("quality-0", "translated", [])
    assert payloads[1] == ("quality-1", "translated", [])
    assert payloads[3] == ("quality-3", "translated", [])
    assert payloads[2][0] == "fast-2"
    assert payloads[2][1] == "translated_needs_review"
    assert {issue["type"] for issue in payloads[2][2]} == {"api_content_filter_fallback"}


def test_quality_content_rejection_split_depth_is_bounded():
    from translation.batching import BatchJob, BatchResult
    from translation.workflow.json_parallel import _finish_api_batch_result

    candidates = [
        dict(_candidate(idx, f"blocked-{idx}", short_label=False), i=idx)
        for idx in range(4)
    ]

    class ContentRejected(RuntimeError):
        retryable = False
        content_rejected = True
        status_code = 400

    class FakePipeline:
        quality_calls: list[list[int]] = []
        fast_calls: list[list[int]] = []

        @classmethod
        def _translate_json_candidate_batch_raw(cls, group, options, protocol, model=None):
            cls.quality_calls.append([item["idx"] for item in group])
            raise ContentRejected("blocked")

        @classmethod
        def _translate_json_candidates(cls, group, file_path, options, protocol, model=None):
            cls.fast_calls.append([item["idx"] for item in group])
            return {
                item["idx"]: (f"fast-{item['idx']}", "translated", [])
                for item in group
            }

        @staticmethod
        def _status_for_output(source, translated, issues):
            return "translated_needs_review"

    payloads = _finish_api_batch_result(
        FakePipeline(),
        BatchJob("batch", candidates, "json", model="api:quality", options={}),
        BatchResult("batch", {}, ContentRejected("blocked batch"), 1, 0.1),
        "sample.json",
        {},
        {
            "api_model_routing_enabled": True,
            "api_fast_model": "api:fast",
            "api_quality_model": "api:quality",
            "api_content_split_max_depth": 1,
        },
    )

    assert FakePipeline.quality_calls == [[0, 1], [2, 3]]
    assert FakePipeline.fast_calls == [[0, 1], [2, 3]]
    assert set(payloads) == {0, 1, 2, 3}


def test_terminal_api_transport_error_does_not_recursively_split_batch():
    from translation.batching import BatchJob, BatchResult, needs_quality_model_retry
    from translation.workflow.json_parallel import _finish_api_batch_result

    candidates = [
        dict(_candidate(0, "翻訳する", short_label=False), i=0),
        dict(_candidate(1, "保存する", short_label=False), i=1),
    ]

    class QuotaError(RuntimeError):
        retryable = False
        quota_exhausted = True

    class FakePipeline:
        @staticmethod
        def _translate_json_candidates(*args, **kwargs):
            raise AssertionError("transport errors must not be split into more API calls")

        @staticmethod
        def _status_for_output(source, translated, issues):
            return "review_required"

    payloads = _finish_api_batch_result(
        FakePipeline(),
        BatchJob("batch", candidates, "json", model="api:quality", options={}),
        BatchResult("batch", {}, QuotaError("monthly quota reached"), 1, 0.1),
        "sample.json",
        {},
        {},
    )

    assert set(payloads) == {0, 1}
    assert all(payload[1] == "review_required" for payload in payloads.values())
    assert all(
        {issue["type"] for issue in payload[2]} == {"api_quota_exhausted"}
        for payload in payloads.values()
    )
    assert not needs_quality_model_retry(
        "review_required",
        payloads[0][2],
        {"api_quality_retry_issue_types": []},
    )


def test_identical_eligible_output_with_issues_stays_review_required():
    from translation.checkpoint.store import normalize_status
    from translation.quality import status_for_output

    issues = [{"type": "untranslated_japanese"}]
    assert status_for_output("翻訳する", "翻訳する", issues) == "review_required"
    assert normalize_status(
        "translated",
        issues,
        translated="翻訳する",
        original="翻訳する",
    ) == "review_required"


def test_model_bound_identical_kanji_output_is_review_required_and_quality_retried():
    from translation.batching import needs_quality_model_retry
    from translation.quality import status_for_output, translation_issues

    issues = translation_issues("少年", "少年", short_label=True)
    status = status_for_output("少年", "少年", issues)

    assert {issue["type"] for issue in issues} == {"identical_japanese_source"}
    assert status == "review_required"
    assert needs_quality_model_retry(
        status,
        issues,
        {"api_quality_retry_issue_types": ["identical_japanese_source"]},
    )


def test_default_fast_model_quality_retry_covers_language_quality_failures():
    from translation.settings import DEFAULT_CONFIG

    retry_types = set(DEFAULT_CONFIG["batch_translation"]["api_quality_retry_issue_types"])
    recursive_types = set(
        DEFAULT_CONFIG["batch_translation"]["api_quality_recursive_issue_types"]
    )

    assert {
        "english_residue",
        "suspicious_artifact",
        "honorific_rendering_review",
    }.issubset(retry_types)
    assert "honorific_rendering_review" in recursive_types
    assert {
        "length_expansion",
        "short_label_expansion",
    }.issubset(
        set(
            DEFAULT_CONFIG["batch_translation"][
                "api_sensitive_repair_issue_types"
            ]
        )
    )


def test_checkpoint_resume_requires_exact_source_and_run_context():
    import hashlib

    from translation.checkpoint import is_resumable_entry

    source = "食べる"
    context = {
        "translation_direction": "ja-Hans",
        "prompt_version": "prompt-a",
        "glossary_version": "terms-a",
        "model_configuration": {"provider": "api", "model": "quality", "temperature": 0},
    }
    entry = {
        "status": "translated",
        "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        **context,
    }

    assert is_resumable_entry(entry, source=source, **context)
    assert not is_resumable_entry(entry, source="食べない", **context)
    for key, changed in (
        ("translation_direction", "en-Hans"),
        ("prompt_version", "prompt-b"),
        ("glossary_version", "terms-b"),
        ("model_configuration", {"provider": "api", "model": "fast", "temperature": 0}),
    ):
        changed_context = dict(context, **{key: changed})
        assert not is_resumable_entry(entry, source=source, **changed_context)


def test_sensitive_routing_and_neighbor_context_are_resume_semantics():
    from translation.checkpoint import build_resume_model_configuration

    batch = {
        "api_event_driven_enabled": True,
        "api_sensitive_routing_enabled": True,
        "api_sensitive_model": "api:minimax-m3",
        "api_sensitive_repair_enabled": True,
        "api_sensitive_repair_batch_size": 5,
        "api_sensitive_repair_max_batch_chars": 1000,
        "api_sensitive_repair_single_retry": True,
        "api_sensitive_repair_issue_types": [
            "untranslated_japanese",
            "model_refusal",
        ],
        "mtool_neighbor_context_enabled": True,
        "mtool_neighbor_context_radius": 2,
        "mtool_neighbor_context_max_chars": 120,
        "mtool_neighbor_context_min_dialogue_items": 3,
    }
    configured = build_resume_model_configuration(
        {"provider": "api", "model": "qwen3.7-plus"},
        batch,
        think=False,
        fallback_models=[],
    )

    assert configured["batch_translation"] == dict(
        batch,
        api_sensitive_repair_issue_types=[
            "model_refusal",
            "untranslated_japanese",
        ],
    )
    changed = build_resume_model_configuration(
        {"provider": "api", "model": "qwen3.7-plus"},
        dict(batch, mtool_neighbor_context_max_chars=160),
        think=False,
        fallback_models=[],
    )
    assert changed != configured
    static = build_resume_model_configuration(
        {"provider": "api", "model": "qwen3.7-plus"},
        dict(batch, api_event_driven_enabled=False),
        think=False,
        fallback_models=[],
    )
    assert static != configured


def test_glossary_version_changes_only_when_enforced_terms_change(tmp_path):
    from translator.glossary import Glossary

    glossary = Glossary(file_path=str(tmp_path / "glossary.json"))
    assert glossary.version() == "0"
    glossary.candidates["フィーネ"] = {"status": "candidate", "target": "菲妮"}
    assert glossary.version() == "0"
    glossary.add("フィーネ", "菲妮", "person")
    confirmed_version = glossary.version()
    assert confirmed_version != "0"
    glossary.add("ジーク", "吉克", "person")
    assert glossary.version() != confirmed_version


def test_batch_resume_retranslates_after_prompt_change(monkeypatch, tmp_path):
    import json

    import config
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    old_dir = checkpoint.CHECKPOINT_DIR
    old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
    checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
    config.DEFAULT_CONFIG["batch_translation"].update({
        "enabled": True,
        "api_parallel_enabled": False,
        "json_batch_size": 10,
        "max_batch_chars": 4000,
        "protocol": "json",
    })
    try:
        path = tmp_path / "sample.json"
        path.write_text(json.dumps({"食べる": "食べる"}, ensure_ascii=False), encoding="utf-8")
        calls: list[str] = []

        def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
            calls.append(system_prompt or "")
            payload = json.loads(text)
            return json.dumps([{"i": item["i"], "t": "吃"} for item in payload], ensure_ascii=False)

        monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
        glossary_path = str(tmp_path / "glossary.json")
        TranslationPipeline(
            model="api:test",
            system_prompt="prompt A",
            glossary=Glossary(file_path=glossary_path),
        ).translate_file(str(path))
        TranslationPipeline(
            model="api:test",
            system_prompt="prompt A",
            glossary=Glossary(file_path=glossary_path),
        ).translate_file(str(path))
        TranslationPipeline(
            model="api:test",
            system_prompt="prompt B",
            glossary=Glossary(file_path=glossary_path),
        ).translate_file(str(path))

        assert len(calls) == 2
        entry = checkpoint.get_entry(str(path), 0, 0)
        assert entry is not None
        assert entry["prompt_version"].startswith("prompt-")
        assert entry["model_configuration"]["batch_translation"]["protocol"] == "json"
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_offline_scheduler_profile_reports_mixed_batch_elimination(tmp_path):
    import json

    from tools.profile_batch_scheduler import profile_scheduler

    path = tmp_path / "sample.json"
    long_narrative = "これは長い物語です。" * 20
    path.write_text(json.dumps({
        "支払う": "支払う",
        "「ここへ来てください」と彼女は言った。": "「ここへ来てください」と彼女は言った。",
        "先に進む": "先に進む",
        long_narrative: long_narrative,
        "EV001": "EV001",
    }, ensure_ascii=False), encoding="utf-8")

    result = profile_scheduler(
        path,
        batch_size=40,
        max_batch_chars=4000,
        short_line_max_chars=80,
        long_text_min_chars=120,
        fast_categories=["short_label"],
    )

    assert result["model_bound_items"] == 4
    assert result["legacy_contiguous"]["mixed_category_batches"] == 1
    assert result["homogeneous"]["mixed_category_batches"] == 0
    protocol_chars = result["quality_json_protocol_chars"]
    assert protocol_chars["saved"] == protocol_chars["verbose"] - protocol_chars["compact"]
    assert result["homogeneous"]["fast_items"] == 2
    assert result["homogeneous"]["quality_items"] == 2


def test_mtool_analysis_uses_current_file_name_evidence(tmp_path):
    import json

    from translation.analysis import classify_mtool_file, collect_model_candidates
    from translation.terminology import Glossary

    path = tmp_path / "sample.json"
    path.write_text(json.dumps({
        "太郎：行くぞ": "太郎：行くぞ",
        "太郎：待て": "太郎：待て",
        "太郎": "太郎",
        "奴隷市場": "奴隷市場",
    }, ensure_ascii=False), encoding="utf-8")
    glossary = Glossary.in_memory()

    classification = classify_mtool_file(path, glossary=glossary)
    candidates = collect_model_candidates(
        path,
        glossary=glossary,
        limit=10,
        batch_size=10,
        max_batch_chars=4000,
    )

    assert glossary.is_identified_kanji_name("太郎")
    assert classification["classes"]["deterministic"] == 1
    assert {candidate["source"] for candidate in candidates} == {
        "太郎：行くぞ",
        "太郎：待て",
        "奴隷市場",
    }


def test_generic_katakana_subject_never_auto_confirms_as_person(tmp_path):
    from translator.glossary import Glossary

    glossary = Glossary(file_path=str(tmp_path / "glossary.json"))
    source = "\u30e2\u30f3\u30b9\u30bf\u30fc\u304c\u6765\u305f"
    for _ in range(3):
        assert glossary.auto_extract(source, "\u602a\u7269\u6765\u4e86") == []
    assert "\u30e2\u30f3\u30b9\u30bf\u30fc" not in glossary.terms


def test_speaker_name_requires_repeated_consistent_target_evidence(tmp_path):
    from translator.glossary import Glossary

    glossary = Glossary(file_path=str(tmp_path / "glossary.json"))
    source = "\u30d5\u30a3\u30fc\u30cd\uff1a\u6765\u305f"
    assert glossary.auto_extract(source, "\u5c0f\u660e\uff1a\u6765\u4e86") == []
    assert glossary.auto_extract(source, "\u83f2\u59ae\uff1a\u6765\u4e86") == []
    assert glossary.auto_extract(source, "\u83f2\u59ae\uff1a\u6765\u4e86") == []
    confirmed = glossary.auto_extract(source, "\u83f2\u59ae\uff1a\u6765\u4e86")

    assert confirmed
    assert glossary.terms["\u30d5\u30a3\u30fc\u30cd"] == "\u83f2\u59ae"


def test_frozen_glossary_collects_evidence_without_changing_enforced_terms(tmp_path):
    from translation.terminology import Glossary

    path = str(tmp_path / "glossary.json")
    glossary = Glossary(file_path=path)
    sources = [
        "\u30d5\u30a3\u30fc\u30cd\uff1a\u6765\u305f",
        "\u30d5\u30a3\u30fc\u30cd\uff1a\u7b11\u3063\u305f",
    ]
    glossary.preseed_from_sources(sources)
    frozen_version = glossary.freeze()
    frozen_mappings = list(glossary.iter_mappings())

    for _ in range(4):
        assert glossary.auto_extract(
            "\u30d5\u30a3\u30fc\u30cd\uff1a\u6765\u305f",
            "\u83f2\u59ae\uff1a\u6765\u4e86",
        ) == []

    assert glossary.frozen
    assert glossary.version() == frozen_version
    assert glossary.iter_mappings() == frozen_mappings
    assert "\u30d5\u30a3\u30fc\u30cd" not in glossary.terms
    assert glossary.candidates["\u30d5\u30a3\u30fc\u30cd"]["targets"]["\u83f2\u59ae"] >= 4

    glossary.save()
    reloaded = Glossary(file_path=path)
    assert reloaded.candidates["\u30d5\u30a3\u30fc\u30cd"]["status"] == "candidate"
    assert "\u30d5\u30a3\u30fc\u30cd" not in reloaded.terms


def test_frozen_glossary_rejects_explicit_mapping_mutation(tmp_path):
    from translation.terminology import Glossary

    glossary = Glossary(file_path=str(tmp_path / "glossary.json"))
    glossary.freeze()

    with pytest.raises(RuntimeError, match="while frozen"):
        glossary.add("\u30d5\u30a3\u30fc\u30cd", "\u83f2\u59ae", "person")
    with pytest.raises(RuntimeError, match="while frozen"):
        glossary.remove("\u30d5\u30a3\u30fc\u30cd")


def test_batch_prompts_preserve_honorific_meaning_and_kanji_names():
    from translation.batching import build_batch_system_prompt, build_line_batch_system_prompt

    for prompt in (build_batch_system_prompt(), build_line_batch_system_prompt()):
        assert "honorifics contextually" in prompt
        assert "do not silently drop" in prompt
        assert "kanji proper names exactly" in prompt
        assert "Do not invent names" in prompt
        assert "meaningful vocalizations" in prompt
    repair_prompt = build_batch_system_prompt(include_review=True)
    assert "untranslated_japanese" in repair_prompt
    assert "instead of copying kana" in repair_prompt
    assert "composed_child_repair" in repair_prompt
    assert "keeping every original line" in repair_prompt


def test_runtime_protection_covers_numbers_line_breaks_and_validates_order():
    from translation.protection import (
        protect_runtime_tokens,
        restore_runtime_tokens,
        strip_foreign_runtime_placeholders,
        validate_runtime_tokens,
    )

    source = "\u653b\u6483\u529b\u304c120\u304b\u3089150%\u306b\u306a\u308b\r\n\u6b21\u306e\u884c"
    protected, tokens = protect_runtime_tokens(source)

    assert {token.value for token in tokens} == {"120", "150%", "\r\n"}
    assert "120" not in protected
    assert "150%" not in protected
    assert "\r\n" not in protected
    assert validate_runtime_tokens(protected, tokens, protected) == []
    assert restore_runtime_tokens(protected, tokens) == source
    assert [token.token for token in tokens] == [
        token.token for token in sorted(tokens, key=lambda item: protected.find(item.token))
    ]

    source_order = sorted(tokens, key=lambda item: protected.find(item.token))
    reversed_tokens = " ".join(token.token for token in reversed(source_order))
    issues = validate_runtime_tokens(reversed_tokens, tokens, protected)
    assert issues and issues[0]["type"] == "runtime_token_preservation"

    duplicated = protected + tokens[0].token
    issues = validate_runtime_tokens(duplicated, tokens, protected)
    assert "duplicated=" in issues[0]["message"]

    collision_source = "__KEEP_0__の値は120\n次"
    collision_protected, collision_tokens = protect_runtime_tokens(
        collision_source
    )
    assert "__KEEP_0__" in collision_protected
    assert "__KEEP_0__" not in {
        token.token for token in collision_tokens
    }
    assert restore_runtime_tokens(
        collision_protected,
        collision_tokens,
    ) == collision_source

    assert strip_foreign_runtime_placeholders(
        "译文__KEEP_0____KEEP_99__",
        "",
    ) == "译文"
    assert strip_foreign_runtime_placeholders(
        "保留__KEEP_0____KEEP_0__",
        "原文__KEEP_0__",
    ) == "保留__KEEP_0__"


def test_model_source_normalizes_generic_japanese_numeric_ordinals_before_protection():
    from translation.batching import prepare_model_candidate
    from translation.classification import normalize_model_source

    assert normalize_model_source("\uff11\u3064\u76ee\u306e\u5b9d\u7389") == "\u7b2c\uff11\u4e2a\u5b9d\u7389"
    assert normalize_model_source("3\u56de\u76ee\u306e\u6226\u95d8") == "\u7b2c3\u6b21\u6226\u95d8"
    assert normalize_model_source("2\u4eba\u76ee\u306e\u65c5\u4eba") == "\u7b2c2\u4e2a\u4eba\u65c5\u4eba"

    candidate = prepare_model_candidate(
        batch_i=0,
        idx=0,
        source=(
            "\u300e\uff11\u3064\u76ee\u306e\u5b9d\u7389\u3092\u624b\u306b\u5165\u308c\u305f\u3002\n"
            "\u3000\u6b21\u3078\u9032\u3080\u300f"
        ),
    )

    assert "\u3064\u76ee" not in candidate["protected"]
    assert "\u7b2c__KEEP_0__\u4e2a\u5b9d\u7389" in candidate["protected"]
    assert [token.token for token in candidate["runtime_tokens"]] == ["__KEEP_0__", "__KEEP_1__"]
    assert [token.value for token in candidate["runtime_tokens"]] == ["\uff11", "\n"]


def test_manual_benchmark_can_force_local_provider(monkeypatch, tmp_path, capsys):
    import json
    import sys

    import tools.benchmark_manual_trans_file as benchmark

    path = tmp_path / "sample.json"
    path.write_text(json.dumps({"EV001": "EV001"}), encoding="utf-8")
    providers: list[str] = []
    monkeypatch.setattr(benchmark, "set_model_provider", providers.append)
    monkeypatch.setattr(sys, "argv", [
        "benchmark_manual_trans_file.py",
        "--file",
        str(path),
        "--provider",
        "ollama",
        "--sample-size",
        "0",
    ])

    assert benchmark.main() == 0
    assert providers == ["ollama"]
    assert '"total_items": 1' in capsys.readouterr().out


def test_model_request_reuses_protected_numeric_template_per_batch():
    import json

    from translation.batching import prepare_model_candidate, translate_candidate_batch_raw

    candidates = [
        prepare_model_candidate(batch_i=0, idx=10, source="\u52a0\u8b770"),
        prepare_model_candidate(batch_i=1, idx=11, source="\u52a0\u8b771"),
    ]
    payload_sizes: list[int] = []

    def fake_translate(_model, payload, _system_prompt, _options):
        items = json.loads(payload)
        payload_sizes.append(len(items))
        return json.dumps({"items": [{"i": 0, "t": "\u52a0\u62a4__KEEP_0__"}]}, ensure_ascii=False)

    translated = translate_candidate_batch_raw(
        "test",
        candidates,
        translator=fake_translate,
        options={},
        protocol="json",
    )

    assert payload_sizes == [1]
    assert translated == {0: "\u52a0\u62a4__KEEP_0__", 1: "\u52a0\u62a4__KEEP_0__"}


def test_template_reuse_expands_structural_retry_indexes(monkeypatch):
    import translation.batching.execution as execution
    from translation.batching import BatchTranslationError, prepare_model_candidate

    candidates = [
        prepare_model_candidate(batch_i=0, idx=10, source="\u52a0\u8b770"),
        prepare_model_candidate(batch_i=1, idx=11, source="\u52a0\u8b771"),
        prepare_model_candidate(batch_i=2, idx=12, source="\u653b\u64830"),
    ]

    def fail_with_partial(*args, **kwargs):
        raise BatchTranslationError(
            "missing template",
            partial_results={0: "\u52a0\u62a4__KEEP_0__"},
            retry_indexes={1},
        )

    monkeypatch.setattr(execution, "translate_batch", fail_with_partial)
    with pytest.raises(BatchTranslationError) as exc_info:
        execution.translate_candidate_batch_raw(
            "test",
            candidates,
            translator=lambda *args: "",
            options={},
            protocol="json",
        )

    assert exc_info.value.partial_results == {
        0: "\u52a0\u62a4__KEEP_0__",
        1: "\u52a0\u62a4__KEEP_0__",
    }
    assert exc_info.value.retry_indexes == {2}


def test_review_report_contains_only_explicit_review_queue(monkeypatch, tmp_path):
    import json

    import translation.review.report as report

    monkeypatch.setattr(report.checkpoint, "load_checkpoint", lambda _file_path: {
        "model": "api:quality",
        "prompt_version": "prompt-a",
        "glossary_version": "terms-a",
        "entries": {
            "0_0": {"row": 0, "col": 0, "original": "済み", "translated": "完成", "status": "translated"},
            "1_0": {
                "row": 1,
                "col": 0,
                "original": "要確認",
                "translated": "需要确认",
                "status": "translated_needs_review",
                "issues": [{"type": "term_preservation"}],
            },
            "2_0": {
                "row": 2,
                "col": 0,
                "original": "失敗",
                "translated": "失敗",
                "status": "review_required",
                "issues": [{"type": "untranslated_japanese"}],
            },
        },
    })
    monkeypatch.setattr(
        report,
        "build_review_summary",
        lambda _file_path: type("Summary", (), {"as_dict": lambda self: {"review_queue_size": 2}})(),
    )
    output = tmp_path / "game.translated.json"

    report_path = report.write_review_report("game.json", str(output))
    payload = json.loads((tmp_path / "game.translated.review.json").read_text(encoding="utf-8"))

    assert report_path == str(tmp_path / "game.translated.review.json")
    assert payload["summary"]["review_queue_size"] == 2
    assert [item["entry_id"] for item in payload["items"]] == ["1_0", "2_0"]
    assert payload["items"][0]["issues"][0]["type"] == "term_preservation"


def test_evidence_backed_kanji_name_is_protected_inside_sentence():
    from translation.batching import prepare_model_candidate
    from translation.protection import restore_protected_translation
    from translation.terminology import Glossary

    glossary = Glossary.in_memory()
    glossary.candidates["\u592a\u90ce"] = {
        "type": "person",
        "status": "candidate",
        "evidence": ["speaker_position", "standalone_line", "whole_file_preseed"],
    }
    source = "\u592a\u90ce\uff1a\u884c\u304f\u305e"
    candidate = prepare_model_candidate(
        batch_i=0,
        idx=0,
        source=source,
        glossary=glossary,
    )

    assert candidate["protected"].startswith("__PERSON_0__")
    assert candidate["term_hits"] == [{
        "source": "\u592a\u90ce",
        "target": "\u592a\u90ce",
        "owner": "\u592a\u90ce",
        "type": "person",
    }]
    restored, issues, missing = restore_protected_translation(
        glossary=glossary,
        original_text=source,
        prepared_text=candidate["prepared"],
        protected_text=candidate["protected"],
        translated="__PERSON_0__\uff1a\u8981\u8d70\u4e86",
        symbol_tokens=candidate["symbol_tokens"],
        term_tokens=candidate["term_tokens"],
        runtime_tokens=candidate["runtime_tokens"],
        term_hits=candidate["term_hits"],
    )

    assert restored == "\u592a\u90ce\uff1a\u8981\u8d70\u4e86"
    assert issues == []
    assert missing == []


def test_repeated_kanji_label_prefix_is_not_a_name_without_second_evidence():
    from translation.classification import deterministic_translation
    from translation.terminology import Glossary

    glossary = Glossary.in_memory()
    glossary.preseed_from_sources([
        "\u6c34\u7740",
        "\u6c34\u7740\uff1a\u4e73\u9996\u3044\u3058\u308a",
        "\u6c34\u7740\uff1a\u3044\u305f\u305a\u3089\u8131\u304c\u305b",
    ])

    assert not glossary.is_identified_kanji_name("\u6c34\u7740")
    assert deterministic_translation("\u6c34\u7740", glossary=glossary) == ""
    assert glossary.find_hits("\u6c34\u7740\uff1a\u4e73\u9996\u3044\u3058\u308a") == []


def test_runtime_wrapped_term_placeholder_restores_before_term_resolution():
    from translation.batching import prepare_model_candidate
    from translation.protection import restore_protected_translation
    from translation.terminology import Glossary

    glossary = Glossary.in_memory()
    glossary.candidates["\u6c34\u7740"] = {
        "type": "person",
        "status": "candidate",
        "evidence": ["speaker_position", "standalone_line"],
    }
    source = "H4\uff1aA\u6c34\u7740\u30bb\u30af\u30cf\u30e9\u2460"
    candidate = prepare_model_candidate(
        batch_i=0,
        idx=0,
        source=source,
        glossary=glossary,
    )

    assert candidate["protected"].startswith("__KEEP_0__")
    assert candidate["term_tokens"] == [("__PERSON_0__", "\u6c34\u7740", "\u6c34\u7740")]
    restored, issues, missing = restore_protected_translation(
        glossary=glossary,
        original_text=source,
        prepared_text=candidate["prepared"],
        protected_text=candidate["protected"],
        translated="__KEEP_0__\uff1a__KEEP_1__\u6027\u9a9a\u6270\u2460",
        symbol_tokens=candidate["symbol_tokens"],
        term_tokens=candidate["term_tokens"],
        runtime_tokens=candidate["runtime_tokens"],
        term_hits=candidate["term_hits"],
    )

    assert restored == "H4\uff1aA\u6c34\u7740\u6027\u9a9a\u6270\u2460"
    assert issues == []
    assert missing == []


def test_honorific_quality_check_flags_silent_drop_without_mechanical_mapping():
    from translation.quality import translation_issues

    dropped = translation_issues("\u30d5\u30a3\u30fc\u30cd\u69d8\u306f\u6765\u305f", "\u83f2\u59ae\u6765\u4e86")
    rendered = translation_issues("\u30d5\u30a3\u30fc\u30cd\u69d8\u306f\u6765\u305f", "\u83f2\u59ae\u5927\u4eba\u6765\u4e86")
    standalone_pronoun = translation_issues("\u541b\u306f\u6765\u305f", "\u4f60\u6765\u4e86")

    assert any(issue["type"] == "honorific_rendering_review" for issue in dropped)
    assert not any(issue["type"] == "honorific_rendering_review" for issue in rendered)
    assert not any(issue["type"] == "honorific_rendering_review" for issue in standalone_pronoun)


def test_honorific_quality_check_accepts_natural_collective_rendering():
    from translation.quality import translation_issues

    collective_issues = translation_issues("皆さん、ありがとう", "大家，谢谢")
    role_issues = translation_issues("王様は大きいですね", "国王真强壮呢")

    assert not any(issue["type"] == "honorific_rendering_review" for issue in collective_issues)
    assert not any(issue["type"] == "honorific_rendering_review" for issue in role_issues)


def test_honorific_quality_check_accepts_first_person_status_rendering():
    from translation.quality import translation_issues

    issues = translation_issues(
        "\u4ffa\u69d8\u306f\u3053\u3046\u898b\u3048\u3066\u512a\u3057\u3044\u3093\u3067\u306a",
        "\u522b\u770b\u6211\u8fd9\u6837\uff0c\u672c\u5927\u7237\u53ef\u662f\u5f88\u6e29\u67d4\u7684",
    )

    assert not any(issue["type"] == "honorific_rendering_review" for issue in issues)


@pytest.mark.parametrize(
    ("source", "translation"),
    [
        ("\u8cb4\u69d8\u306f\u4f55\u8005\u3060", "\u4f60\u8fd9\u5bb6\u4f19\u662f\u4ec0\u4e48\u4eba"),
        ("\u8d64\u3061\u3083\u3093\u3092\u80b2\u3066\u308b", "\u629a\u517b\u5b9d\u5b9d"),
        ("\u524d\u56de\u3068\u540c\u69d8\u306b", "\u548c\u4e0a\u6b21\u4e00\u6837"),
        ("\u5909\u306a\u7d0b\u69d8\u304c\u51fa\u3066\u304d\u305f", "\u51fa\u73b0\u4e86\u5947\u602a\u7684\u7eb9\u8def"),
        ("\u3054\u6101\u50b7\u3055\u307e", "\u771f\u66ff\u4f60\u611f\u5230\u9057\u61be"),
        ("\u4eca\u3061\u3083\u3093\u3068\u5c65\u3044\u3066\u3044\u308b", "\u73b0\u5728\u6b63\u597d\u597d\u7a7f\u7740"),
        ("\u79c1\u3061\u3083\u3093\u3068\u98f2\u3093\u3060", "\u6211\u786e\u5b9e\u597d\u597d\u559d\u4e86"),
        ("\u6298\u89d2\u541b\u306e\u305f\u3081\u306b", "\u6211\u597d\u4e0d\u5bb9\u6613\u624d\u4e3a\u4e86\u4f60"),
        ("\u624b\u3092\u51fa\u3055\u3093\u3068\u7d04\u675f\u3057\u305f", "\u7b54\u5e94\u4e86\u4e0d\u51fa\u624b"),
        ("\u304a\u7236\u3055\u3093\u304c\u6765\u305f", "\u7238\u7238\u6765\u4e86"),
        ("\u304a\u6bcd\u3055\u3093\u304c\u6765\u305f", "\u6bcd\u4eb2\u6765\u4e86"),
        ("\u7686\u69d8\u3001\u3042\u308a\u304c\u3068\u3046", "\u611f\u8c22\u5404\u4f4d"),
        ("\u304a\u5ba2\u69d8\u304c\u6765\u305f", "\u5ba2\u4eba\u6765\u4e86"),
        ("\u304a\u5ba2\u3055\u3093\u3082\u5165\u308c\u308b", "\u60a8\u4e5f\u80fd\u8fdb\u6765"),
        ("\u65e6\u90a3\u69d8\u304c\u547c\u3093\u3067\u3044\u308b", "\u8001\u7237\u5728\u53eb\u4f60"),
        ("\u304a\u5ac1\u3055\u3093\u306b\u306a\u308b", "\u6210\u4e3a\u59bb\u5b50"),
        ("\u304a\u59c9\u3061\u3083\u3093\u304c\u6765\u305f", "\u59d0\u59d0\u6765\u4e86"),
    ],
)
def test_honorific_quality_check_ignores_lexicalized_words_and_accepts_natural_roles(
    source,
    translation,
):
    from translation.quality import translation_issues

    issues = translation_issues(source, translation)

    assert not any(
        issue["type"] == "honorific_rendering_review"
        for issue in issues
    )


@pytest.mark.parametrize(
    ("source", "translation"),
    [
        ("\u5fa1\u5b50\u69d8\u304c\u6765\u305f", "\u5fa1\u5b50\u6765\u4e86"),
        ("\u30d5\u30a3\u30fc\u30cd\u3061\u3083\u3093\u304c\u6765\u305f", "\u83f2\u59ae\u6765\u4e86"),
        ("\u4ffa\u69d8\u306f\u5f37\u3044", "\u6211\u5f88\u5f3a"),
    ],
)
def test_honorific_quality_check_keeps_true_title_and_intimacy_losses(
    source,
    translation,
):
    from translation.quality import translation_issues

    issues = translation_issues(source, translation)

    assert any(
        issue["type"] == "honorific_rendering_review"
        for issue in issues
    )


def test_dialogue_brackets_are_protected_and_restored_exactly():
    from translation.protection import protect_symbols, restore_symbols

    source = "「こんにちは」"
    protected, tokens = protect_symbols(source)
    assert protected == "__SYM_0__こんにちは__SYM_1__"

    restored, issues = restore_symbols(
        source,
        protected,
        "__SYM_0__你好__SYM_1__",
        tokens,
    )

    assert restored == "「你好」"
    assert issues == []


def test_symbol_restore_discards_model_added_protected_symbols():
    from translation.protection import protect_symbols, restore_symbols

    source = "\u300c\u3042\u2661\u2661\u300d"
    protected, tokens = protect_symbols(source)
    translated = "__SYM_0__\u554a__SYM_1____SYM_2__\u2661\u2661\u2661__SYM_3__"

    restored, issues = restore_symbols(
        source,
        protected,
        translated,
        tokens,
    )

    assert restored == "\u300c\u554a\u2661\u2661\u300d"
    assert issues == []

    rebuilt, rebuild_issues = restore_symbols(
        source,
        protected,
        "\u554a\u2661\u2661\u2661",
        tokens,
    )

    assert rebuilt == "\u300c\u554a\u2661\u2661\u300d"
    assert [issue["type"] for issue in rebuild_issues] == ["symbol_preservation"]

    duplicated = "".join(token.token for token in tokens) * 2
    deduplicated, duplicate_issues = restore_symbols(
        source,
        protected,
        "\u554a" + duplicated,
        tokens,
    )

    assert deduplicated == "\u300c\u554a\u2661\u2661\u300d"
    assert "__SYM_" not in deduplicated
    assert [issue["type"] for issue in duplicate_issues] == ["symbol_preservation"]


def test_symbol_restore_discards_foreign_placeholders_when_source_has_no_symbols():
    from translation.protection import restore_symbols

    restored, issues = restore_symbols(
        "「もしかしたら、",
        "「もしかしたら、",
        "「话说，__SYM_1____SYM_2__",
        [],
    )

    assert restored == "「话说，"
    assert issues == []


def test_symbol_restore_discards_unknown_placeholder_during_rebuild():
    from translation.protection import protect_symbols, restore_symbols

    source = "「こんにちは」"
    protected, tokens = protect_symbols(source)
    restored, issues = restore_symbols(
        source,
        protected,
        "__SYM_0__你好__SYM_1____SYM_99__",
        tokens,
    )

    assert restored == source[0] + "你好" + source[-1]
    assert "__SYM_" not in restored
    assert issues == []


def test_mtool_batch_finish_does_not_add_or_truncate_line_breaks():
    from translation.batching import finish_batch_translation, prepare_model_candidate
    from translation.terminology import Glossary

    source = "これは一行の長い説明文です。"
    translated = "这是一段应当保持单行且不能因为界面宽度限制而被自动换行或截断的完整中文说明。"
    candidate = prepare_model_candidate(batch_i=0, idx=0, source=source)
    candidate["preserve_source_layout"] = True

    finished, status, _issues = finish_batch_translation(
        candidate,
        translated,
        glossary=Glossary.in_memory(),
        restore_func=lambda original, prepared, protected, text, *args: (text, [], []),
        pollution_issues_func=lambda source_text, target_text: [],
        status_for_output_func=lambda source_text, target_text, issues: "translated",
    )

    assert finished == translated
    assert "\n" not in finished
    assert status == "translated"


def test_json_batch_parser_rejects_duplicate_ids_and_extra_fields():
    from translation.batching import BatchTranslationError, parse_batch_response

    with pytest.raises(BatchTranslationError, match="duplicate batch index"):
        parse_batch_response(
            '{"items":[{"i":0,"t":"甲"},{"i":0,"t":"乙"},{"i":1,"t":"丙"}]}',
            {0, 1},
        )
    with pytest.raises(BatchTranslationError, match="unexpected batch item fields"):
        parse_batch_response(
            '{"items":[{"i":0,"t":"甲","note":"extra"}]}',
            {0},
        )
    with pytest.raises(BatchTranslationError, match="unexpected batch response fields"):
        parse_batch_response(
            '{"items":[{"i":0,"t":"甲"}],"note":"extra"}',
            {0},
        )


def test_compact_json_batch_uses_stable_ids_and_compact_terms():
    import json

    from translation.batching import translate_batch

    observed: dict[str, object] = {}

    def fake_translate(_model, payload, system_prompt, _options):
        observed["payload"] = json.loads(payload)
        observed["prompt"] = system_prompt
        return json.dumps([[0, "\u653b\u51fb"], [1, "\u83f2\u59ae\u5927\u4eba"]], ensure_ascii=False)

    translated = translate_batch(
        "test",
        [
            {"i": 0, "text": "\u653b\u6483"},
            {
                "i": 1,
                "text": "\u30d5\u30a3\u30fc\u30cd\u69d8",
                "terms": [{"source": "\u30d5\u30a3\u30fc\u30cd", "target": "\u83f2\u59ae"}],
            },
        ],
        translator=fake_translate,
        options={"compact_json_protocol": True},
    )

    assert observed["payload"] == [
        [0, "\u653b\u6483"],
        [1, "\u30d5\u30a3\u30fc\u30cd\u69d8", [["\u30d5\u30a3\u30fc\u30cd", "\u83f2\u59ae"]]],
    ]
    assert '[[0,"translation"],[1,"translation"]]' in str(observed["prompt"])
    assert translated == {0: "\u653b\u51fb", 1: "\u83f2\u59ae\u5927\u4eba"}


def test_compact_json_quality_retry_carries_previous_draft_and_issue_types():
    import json

    from translation.batching import translate_batch

    observed: dict[str, object] = {}

    def fake_translate(_model, payload, system_prompt, _options):
        observed["payload"] = json.loads(payload)
        observed["prompt"] = system_prompt
        return '[[0,"\u672c\u5927\u7237\u5f88\u6e29\u67d4"]]'

    translated = translate_batch(
        "test",
        [{
            "i": 0,
            "text": "\u4ffa\u69d8\u306f\u512a\u3057\u3044",
            "quality_retry": {
                "previous": "\u6211\u5f88\u6e29\u67d4",
                "issues": ["honorific_rendering_review"],
            },
        }],
        translator=fake_translate,
        options={"compact_json_protocol": True},
    )

    assert observed["payload"] == [[
        0,
        "\u4ffa\u69d8\u306f\u512a\u3057\u3044",
        [],
        {
            "previous": "\u6211\u5f88\u6e29\u67d4",
            "issues": ["honorific_rendering_review"],
        },
    ]]
    assert "correct every listed issue" in str(observed["prompt"])
    assert "do not reduce an honorific-bearing title or role" in str(observed["prompt"])
    assert translated == {0: "\u672c\u5927\u7237\u5f88\u6e29\u67d4"}


def test_compact_json_batch_parser_rejects_duplicate_and_malformed_pairs():
    from translation.batching import BatchTranslationError, parse_batch_response

    with pytest.raises(BatchTranslationError, match="duplicate batch index"):
        parse_batch_response('[[0,"\u7532"],[0,"\u4e59"]]', {0})
    with pytest.raises(BatchTranslationError, match="exactly id and translation"):
        parse_batch_response('[[0,"\u7532","extra"]]', {0})
    with pytest.raises(BatchTranslationError, match="missing batch indexes"):
        parse_batch_response('[[0,"\u7532"]]', {0, 1})


def test_truncated_compact_json_salvages_complete_pairs_only():
    from translation.batching import BatchTranslationError, parse_batch_response

    with pytest.raises(BatchTranslationError) as exc_info:
        parse_batch_response('[[0,"\u7532"],[1,"\u4e59"],[2,"unfinished', {0, 1, 2})

    assert exc_info.value.partial_results == {0: "\u7532", 1: "\u4e59"}
    assert exc_info.value.retry_indexes == {2}


def test_glossary_mapping_cache_rebuilds_only_after_mapping_change():
    from translation.terminology import Glossary

    glossary = Glossary.in_memory()
    glossary.candidates["\u592a\u90ce"] = {
        "type": "person",
        "evidence": ["speaker_position", "standalone_line"],
    }

    first = glossary.iter_mappings()
    second = glossary.iter_mappings()
    glossary.add("\u30d5\u30a3\u30fc\u30cd", "\u83f2\u59ae", "person")
    third = glossary.iter_mappings()

    assert first is second
    assert third is not first
    assert ("\u592a\u90ce", "\u592a\u90ce", "\u592a\u90ce", "person") in third
    assert any(
        source == "\u30d5\u30a3\u30fc\u30cd" and target == "\u83f2\u59ae"
        for source, target, _owner, _type in third
    )


def test_quality_checks_numeric_and_line_break_sequences():
    from translation.quality import translation_issues

    clean = translation_issues("120\u304b\u3089150%\r\n\u6b21", "\u4ece120\u5230150%\r\n\u4e0b\u4e00\u9879")
    changed_number = translation_issues("120\u304b\u3089150%", "\u4ece120\u5230160%")
    changed_break = translation_issues("\u4e00\r\n\u4e8c", "\u4e00\n\u4e8c")

    assert not any(issue["type"] == "numeric_preservation" for issue in clean)
    assert not any(issue["type"] == "line_break_preservation" for issue in clean)
    assert any(issue["type"] == "numeric_preservation" for issue in changed_number)
    assert any(issue["type"] == "line_break_preservation" for issue in changed_break)


def test_quality_rejects_punctuation_only_translation():
    from translation.quality import translation_issues

    issues = translation_issues("手に入れてください」", "」")
    assert "model_refusal" in {issue["type"] for issue in issues}


def test_quality_allows_source_resource_identifiers_in_mixed_text():
    from translation.quality import translation_issues

    issues = translation_issues(
        "【膣内の状態】\nx-230\ny-580",
        "【阴道内状态】\nx-230\ny-580",
    )
    assert "english_residue" not in {issue["type"] for issue in issues}
    leaked = translation_issues("状態", "状态 x-230")
    assert "english_residue" in {issue["type"] for issue in leaked}


def test_quality_allows_source_english_credit_names_but_flags_internal_terms():
    from translation.quality import translation_issues

    clean = translation_issues(
        "\u30c6\u30fc\u30de\u30bd\u30f3\u30b0\uff1aTrial & Error",
        "\u4e3b\u9898\u66f2\uff1aTrial & Error",
    )
    leaked = translation_issues(
        "\u6c34\u7740\u30a4\u30d9\u30f3\u30c8",
        "__PERSON_0__\u4e8b\u4ef6",
    )

    assert "english_residue" not in {issue["type"] for issue in clean}
    assert "term_placeholder_leak" in {issue["type"] for issue in leaked}
