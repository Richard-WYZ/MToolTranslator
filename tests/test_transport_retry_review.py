"""Focused regression tests for model transport and quality fallback policy."""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests


def test_chunk_source_is_non_overlapping_and_keeps_runtime_tokens_whole():
    from translation.quality.retry import _chunk_source

    source = "甲。乙！abcdefgh<VAR_1>ijkl %1 \\C[2]\n最后。"
    chunks = _chunk_source(source, max_chars=8)

    assert "".join(chunks) == source
    assert all(chunk for chunk in chunks)
    assert all("<VAR_1>" not in chunk or chunk.count("<VAR_1>") == 1 for chunk in chunks)
    assert not any("<VAR_1" in chunk and "<VAR_1>" not in chunk for chunk in chunks)
    assert not any("%" in chunk and "%1" not in chunk for chunk in chunks)
    assert not any("\\C[" in chunk and "]" not in chunk for chunk in chunks)


def test_chunk_source_marker_restore_does_not_cascade_on_marker_like_source_token():
    from translation.quality.retry import _chunk_source

    source = "__PROTECTED_CHUNK_1__<tag title='。'>正文。"
    assert "".join(_chunk_source(source, max_chars=8)) == source


def test_chunk_translate_rejects_nonpositive_limit_even_for_empty_text():
    from translation.quality.retry import chunk_translate

    with pytest.raises(ValueError, match="max_chars must be positive"):
        chunk_translate("model", "", "prompt", max_chars=0)


def test_chunk_translate_does_not_concatenate_overlap_twice():
    from translation.quality.retry import chunk_translate

    calls: list[str] = []

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append(text)
        return f"[{text}]"

    source = "第一句。第二句。第三句。"
    result = chunk_translate("model", source, "prompt", max_chars=5, overlap=4, translator=fake_translate)

    assert result == "[第一句。][第二句。][第三句。]"
    assert calls == ["第一句。", "第二句。", "第三句。"]


def test_fallback_callback_typeerror_is_not_retried_without_budget():
    from translation.quality.retry import RetryBudget, fallback_translate

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append((model, system_prompt))
        return ""

    def callback(*args, **kwargs):
        calls.append("callback")
        raise TypeError("callback implementation error")

    with pytest.raises(TypeError, match="implementation error"):
        fallback_translate(
            "text",
            model="primary",
            system_prompt="base",
            prompt_style="professional",
            system_prompts={"professional": "professional"},
            fallback_models=[],
            chunk_strategy={"max_chars": 8},
            file_path="game.json",
            row_idx=1,
            col_idx=0,
            compose_prompt=lambda value: value,
            translate_func=fake_translate,
            retry_with_fallback_func=callback,
            max_attempts=2,
            budget=RetryBudget(2),
        )

    assert calls == [("primary", "professional"), "callback"]


def test_fallback_reserves_budget_for_configured_model_and_skips_duplicate_style():
    from translation.quality.retry import fallback_translate

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append((model, system_prompt))
        return "成功" if model == "fallback" else ""

    result = fallback_translate(
        "text",
        model="primary",
        system_prompt="base",
        prompt_style="professional",
        system_prompts={"professional": "professional"},
        fallback_models=["primary", "fallback"],
        chunk_strategy={"max_chars": 8},
        file_path="game.json",
        row_idx=1,
        col_idx=0,
        compose_prompt=lambda value: value,
        translate_func=fake_translate,
        max_attempts=3,
    )

    assert result == "成功"
    assert calls == [("primary", "professional"), ("fallback", "base")]


def test_primary_failed_does_not_retry_or_chunk_same_model():
    from translation.quality.retry import fallback_translate

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append(model)
        return ""

    result = fallback_translate(
        "long source",
        model="primary",
        system_prompt="base",
        prompt_style="professional",
        system_prompts={"professional": "professional"},
        fallback_models=[],
        chunk_strategy={"max_chars": 3},
        file_path="game.json",
        row_idx=1,
        col_idx=0,
        compose_prompt=lambda value: value,
        translate_func=fake_translate,
        primary_failed=True,
        max_attempts=3,
    )

    assert result == ""
    assert calls == []


def test_router_translate_uses_single_request_dispatch(monkeypatch):
    from translation.models import router

    calls = []

    class Client:
        def translate_once(self, *args, **kwargs):
            calls.append("once")
            return "译文"

        def translate(self, *args, **kwargs):
            calls.append("retrying")
            raise AssertionError("client retry path must not be used by router")

    monkeypatch.setattr(router, "api_client", Client())
    monkeypatch.setattr(router, "_provider_for_model", lambda model: "api")
    monkeypatch.setattr(router, "_clean_model", lambda model: "model")
    assert router.translate("api:model", "source") == "译文"
    assert calls == ["once"]


def test_quality_retry_honors_transport_retry_after_on_canonical_router_path(monkeypatch):
    from translation.quality import retry as quality_retry

    calls = []
    sleeps = []

    class RateLimited(RuntimeError):
        retryable = True
        retry_after_seconds = 4.0

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append((model, system_prompt))
        if len(calls) == 1:
            raise RateLimited("slow down")
        return "译文"

    monkeypatch.setattr(quality_retry.time, "sleep", sleeps.append)
    result = quality_retry.retry_with_fallback(
        "source",
        model="primary",
        system_prompt="prompt",
        fallback_models=[],
        translator=fake_translate,
        max_attempts=2,
    )

    assert result["status"] == "SUCCESS"
    assert len(calls) == 2
    assert sleeps == [4.0]


def test_ollama_legacy_retry_never_dispatches_configured_cross_provider_model(monkeypatch):
    import config
    from translation.models import ollama_client

    calls = []
    old_fallbacks = list(config.DEFAULT_CONFIG.get("fallback_models", []))

    def fail_once(model, *args, **kwargs):
        calls.append(model)
        raise RuntimeError("temporary transport failure")

    config.DEFAULT_CONFIG["fallback_models"] = ["api:remote-fallback", "ollama:other-local"]
    monkeypatch.setattr(ollama_client, "translate_once", fail_once)
    monkeypatch.setattr(ollama_client.time, "sleep", lambda _: None)
    try:
        with pytest.raises(RuntimeError):
            ollama_client.translate("local-model", "source")
    finally:
        config.DEFAULT_CONFIG["fallback_models"] = old_fallbacks

    assert calls == ["local-model", "local-model", "local-model"]


def test_transport_scope_reuses_thread_sessions_and_closes_all(monkeypatch):
    from translation.models import transport

    created = []

    class FakeSession:
        def __init__(self):
            self.closed = False
            created.append(self)

        def get(self, url, **kwargs):
            return ("get", id(self), url)

        def post(self, url, **kwargs):
            return ("post", id(self), url)

        def close(self):
            self.closed = True

    monkeypatch.setattr(transport.requests, "Session", FakeSession)
    with transport.connection_scope():
        first = transport.request("api", "get", "https://example.test/a")
        second = transport.request("api", "post", "https://example.test/b")
        assert first[1] == second[1]

        ctx = contextvars.copy_context()

        def worker():
            return ctx.run(lambda: transport.request("api", "get", "https://example.test/c"))

        with ThreadPoolExecutor(max_workers=1) as pool:
            worker_result = pool.submit(worker).result()
        assert worker_result[1] != first[1]
        assert len(created) == 2
        assert not any(session.closed for session in created)
    assert all(session.closed for session in created)


def test_api_retry_honors_retry_after_and_stops_on_nonretryable(monkeypatch):
    import config
    from translation.models import api_client

    class FakeResponse:
        def __init__(self, status_code, body, headers=None):
            self.status_code = status_code
            self.text = body
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

        def json(self):
            return {"choices": [{"message": {"content": "译文"}}]}

    old_cfg = dict(config.DEFAULT_CONFIG.get("third_party_api", {}))
    config.DEFAULT_CONFIG["third_party_api"] = {
        "base_url": "https://api.example.test/v1",
        "api_key": "key",
        "api_key_env": "THIRD_PARTY_API_KEY",
        "models": ["test-model"],
    }
    monkeypatch.delenv("THIRD_PARTY_API_BASE_URL", raising=False)
    monkeypatch.delenv("THIRD_PARTY_API_KEY", raising=False)
    sleeps = []
    responses = iter([
        FakeResponse(429, '{"error":"busy"}', {"Retry-After": "7"}),
        FakeResponse(200, ""),
    ])
    monkeypatch.setattr(api_client.requests, "post", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(api_client.time, "sleep", sleeps.append)
    try:
        assert api_client.translate("test-model", "テスト") == "译文"
        assert sleeps == [7.0]

        responses = iter([FakeResponse(400, '{"error":"content_filter"}')])
        sleeps.clear()
        with pytest.raises(api_client.APIRequestError) as raised:
            api_client.translate("test-model", "テスト")
        assert raised.value.retryable is False
        assert raised.value.content_rejected is True
        assert sleeps == []
    finally:
        config.DEFAULT_CONFIG["third_party_api"] = old_cfg
