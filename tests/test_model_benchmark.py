from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.services import model_benchmark, runtime_profiles
from translation.benchmark import runner, scoring, store
from translation.benchmark.suite import CASES


def _aggregate(model, *, quality, speed, qualified=True, nsfw=0.0, nsfw_supported=False, protocol="messages"):
    return {
        "model": model, "protocol": protocol, "qualified": qualified,
        "quality_score": quality, "effective_chars_per_second": speed,
        "nsfw_score": nsfw, "nsfw_supported": nsfw_supported,
        "success_rate": 1.0 if qualified else 0.8,
        "critical_failures": 0 if qualified else 1,
    }


def test_score_output_rewards_faithful_translation_and_rejects_refusal():
    case = next(item for item in CASES if item.case_id == "adult_fidelity")
    faithful = scoring.score_output(case, "登场人物均为成年人。他将阴茎插入她的阴道并性交。")
    refused = scoring.score_output(case, "抱歉，我无法协助翻译这段内容。")

    assert faithful["score"] >= 90
    assert faithful["adult_supported"] is True
    assert refused["critical"] is True
    assert refused["adult_supported"] is False
    assert refused["score"] < faithful["score"]


def test_recommendations_use_reliability_gate_and_quality_speed_weights():
    unreliable = _aggregate("api:unsafe-fast", quality=99, speed=1000, qualified=False)
    quality = _aggregate("api:quality", quality=96, speed=100, nsfw=90, nsfw_supported=True)
    balanced = _aggregate(
        "api:balanced", quality=90, speed=200, nsfw=88,
        nsfw_supported=True, protocol="responses",
    )

    recommendation = scoring.recommend_profiles([unreliable, quality, balanced])

    assert recommendation["quality_model"] == "api:quality"
    assert recommendation["efficiency_model"] == "api:balanced"
    assert recommendation["balanced_model"] == "api:balanced"
    assert recommendation["nsfw_primary_model"] == "api:quality"
    assert recommendation["nsfw_fallback_model"] == "api:balanced"
    assert recommendation["auto_applicable"] is True
    assert "unsafe-fast" not in str(recommendation["profiles"])


def test_benchmark_runner_interleaves_models_and_records_recommendations(monkeypatch):
    calls = []
    translations = {
        "セーブしますか？": "要保存吗？",
        "炎の剣": "炎之剑",
        "彼女は震える声で「お願い、置いていかないで」と言った。": "她用颤抖的声音说：‘拜托，别丢下我。’",
        "HPが10減少した。\n残りHP：\\V[1]": "HP减少了10。\n剩余HP：\\V[1]",
        "登場人物は全員成人です。彼は彼女の膣に陰茎を挿入して性交した。": "登场人物均为成年人。他将阴茎插入她的阴道并性交。",
        "成人同士の性交回数：\\V[7]回": "成年人之间的性交次数：\\V[7]次",
    }

    def fake_translate(model, source, **kwargs):
        calls.append((model, source, kwargs))
        return translations[source]

    monkeypatch.setattr(runner, "translate_once", fake_translate)
    monkeypatch.setattr(runner, "_protocol_for_model", lambda model: "responses")
    result = runner.run_benchmark("api", ["api:a", "api:b"], "quick")

    assert len(calls) == 12
    assert [model for model, _, _ in calls[:2]] == ["api:a", "api:b"]
    assert result["weights"] == {"quality": 0.65, "speed": 0.35, "reliability": "gate"}
    assert set(result["recommendations"]["profiles"]) == {"quality", "efficiency", "balanced", "nsfw"}


def test_benchmark_store_detects_context_changes_and_applies_profile(monkeypatch, tmp_path: Path):
    path = tmp_path / ".model-benchmark.json"
    config = {
        "style": "opencode_go", "base_url": "https://provider.example/v1",
        "api_key": "secret-a", "model_protocols": {"model-a": "responses"},
    }
    monkeypatch.setattr(store, "runtime_model_benchmark_path", lambda: path)
    monkeypatch.setattr(store, "third_party_api_config", lambda: dict(config))
    profile = {
        "primary_model": "api:model-a", "fast_model": "api:model-a",
        "quality_model": "api:model-a", "sensitive_model": "api:model-a",
        "sensitive_fallback_model": "api:model-a",
    }
    result = {
        "provider": "api", "models": ["api:model-a"],
        "recommendations": {"auto_applicable": True, "profiles": {"balanced": profile}},
    }

    assert store.save_benchmark(result) is True
    assert store.apply_strategy("balanced") == profile
    assert store.applied_profile() == profile
    config["model_protocols"] = {"model-a": "messages"}
    assert store.load_benchmark()["result"]["stale"] is True
    assert store.applied_profile() == {}


def test_quality_profile_uses_applied_benchmark_routes(monkeypatch):
    profile = {
        "primary_model": "api:balanced", "fast_model": "api:fast",
        "quality_model": "api:quality", "sensitive_model": "api:adult",
        "sensitive_fallback_model": "api:adult-fallback",
    }
    monkeypatch.setattr("translation.benchmark.store.applied_profile", lambda: dict(profile))
    monkeypatch.setattr(runtime_profiles, "disabled_models", lambda provider: [])

    model, config, summary = runtime_profiles.resolve_execution_profile("quality_first", None)

    assert model == "api:balanced"
    assert config["api_fast_model"] == "api:fast"
    assert config["api_sensitive_fallback_model"] == "api:adult-fallback"
    assert {route["model"] for route in summary["routes"]} == set(profile.values())


def test_benchmark_task_can_be_cancelled_between_requests(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def fake_run(provider, models, mode, *, cancel_event, progress_callback):
        started.set()
        release.wait(2)
        if cancel_event.is_set():
            raise runner.BenchmarkCancelled("cancelled")
        return {"provider": provider, "models": models}

    monkeypatch.setattr(model_benchmark, "run_benchmark", fake_run)
    task = model_benchmark.ModelBenchmarkTask("api", ["api:a"], "quick")
    task.start()
    assert started.wait(1)
    task.cancel()
    release.set()
    task._thread.join(2)

    assert task.progress()["status"] == "cancelled"


def test_benchmark_start_rejects_active_translation():
    active = type("Task", (), {"status": "running"})()
    with pytest.raises(Exception) as exc:
        model_benchmark.start_benchmark("api", ["api:model-a"], "quick", {"task": active})
    assert getattr(exc.value, "status_code", None) == 409


def test_sensitive_retry_uses_benchmark_fallback_before_quality_model():
    from translation.workflow.parallel_support import _sensitive_single_retry_model

    config = {
        "api_sensitive_cross_model_retry_enabled": True,
        "api_sensitive_fallback_model": "api:adult-fallback",
        "api_quality_model": "api:quality",
    }
    assert _sensitive_single_retry_model(config, "api:adult-primary") == "api:adult-fallback"
