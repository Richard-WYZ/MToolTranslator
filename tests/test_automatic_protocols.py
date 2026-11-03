from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from app.services import model_status
from app.services import settings as settings_service
from translation.models import api_client
from translation.models import protocols


@pytest.fixture
def automatic_connection(monkeypatch, tmp_path):
    for name in ("THIRD_PARTY_API_BASE_URL", "THIRD_PARTY_API_KEY", "THIRD_PARTY_API_STYLE"):
        monkeypatch.delenv(name, raising=False)
    cfg = {
        "style": "openai", "preset": "openai", "profile_id": "test-profile",
        "protocol_mode": "auto", "base_url": "https://provider.invalid/v1",
        "api_key": "offline-test-key", "model_endpoints": {}, "model_protocols": {},
    }
    monkeypatch.setattr(api_client, "_api_config", lambda: dict(cfg))
    monkeypatch.setattr(model_status, "third_party_api_config", lambda: dict(cfg))
    monkeypatch.setattr(settings_service, "third_party_api_config", lambda: dict(cfg))
    monkeypatch.setattr(model_status, "runtime_model_status_path", lambda: tmp_path / ".status.json")
    return cfg


class ProtocolFailure(RuntimeError):
    def __init__(self, status, body):
        super().__init__(body)
        self.status_code = status
        self.response_body = body


def test_unknown_model_uses_one_real_request_without_a_probe(monkeypatch, automatic_connection):
    calls = []
    monkeypatch.setattr(api_client, "_translate_with_protocol", lambda protocol, *args, **kwargs: calls.append(protocol) or "你好")

    assert api_client.translate_once("new-model", "こんにちは") == "你好"
    assert calls == ["chat_completions"]
    assert model_status.model_protocol("api:new-model") == "chat_completions"


def test_automatic_mode_ignores_old_manual_overrides(monkeypatch, automatic_connection):
    automatic_connection["model_protocols"] = {"new-model": "responses"}
    calls = []
    monkeypatch.setattr(api_client, "_translate_with_protocol", lambda protocol, *args, **kwargs: calls.append(protocol) or "你好")

    assert api_client.translate_once("new-model", "こんにちは", protocol_override="messages", probe_all_protocols=True) == "你好"
    assert calls == ["chat_completions"]


def test_explicit_legacy_mode_retains_manual_api_contract(monkeypatch, automatic_connection):
    automatic_connection["protocol_mode"] = "legacy"
    automatic_connection["model_protocols"] = {"new-model": "responses"}
    calls = []
    monkeypatch.setattr(api_client, "_translate_with_protocol", lambda protocol, *args, **kwargs: calls.append(protocol) or "你好")

    assert api_client.model_protocol(automatic_connection, "new-model") == "responses"
    assert api_client.translate_once("new-model", "こんにちは", protocol_override="messages") == "你好"
    assert calls == ["messages"]
    assert model_status.model_protocol("api:new-model") == ""


def test_explicit_server_protocol_instruction_is_learned_from_the_real_request(monkeypatch, automatic_connection):
    calls = []
    def translate(protocol, *args, **kwargs):
        calls.append(protocol)
        if protocol != "responses":
            raise ProtocolFailure(400, "Unsupported API format; use /responses")
        return "你好"
    monkeypatch.setattr(api_client, "_translate_with_protocol", translate)

    assert api_client.translate_once("new-model", "こんにちは") == "你好"
    assert calls == ["chat_completions", "responses"]
    assert api_client.translate_once("new-model", "こんにちは") == "你好"
    assert calls == ["chat_completions", "responses", "responses"]


@pytest.mark.parametrize("failure", [
    ProtocolFailure(401, "unsupported protocol authentication_error"),
    ProtocolFailure(403, "unsupported endpoint"),
    ProtocolFailure(408, "unsupported endpoint"),
    ProtocolFailure(429, "unsupported protocol insufficient_quota"),
    ProtocolFailure(503, "Endpoint is unavailable"),
    ProtocolFailure(400, "DataInspectionFailed: content_filter; unsupported protocol"),
    ProtocolFailure(400, "invalid_request_error: invalid temperature"),
    ProtocolFailure(404, "model_not_found"),
    ProtocolFailure(422, "invalid request parameter"),
    requests.Timeout("request timed out"),
    requests.ConnectionError("connection failed"),
    RuntimeError("Third-party API returned empty content"),
])
def test_auto_does_not_change_protocol_for_non_protocol_failures(monkeypatch, automatic_connection, failure):
    automatic_connection["style"] = "opencode_go"
    calls = []
    def translate(protocol, *args, **kwargs):
        calls.append(protocol)
        raise failure
    monkeypatch.setattr(api_client, "_translate_with_protocol", translate)

    with pytest.raises(type(failure)):
        api_client.translate_once("new-model", "こんにちは", probe_all_protocols=True)
    assert calls == ["chat_completions"]
    assert failure.protocol_attempts == ("chat_completions",)
    assert model_status.model_protocol("api:new-model") == ""


def test_catalog_metadata_is_normalized_without_extra_discovery_requests(monkeypatch, automatic_connection):
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"data": [
                {"id": "response-only", "supported_endpoint_types": ["openai-response", "embeddings"]},
                {"id": "claude-wire", "supported_endpoint_types": ["anthropic"]},
                {"id": "annotated", "model_info": {"supported_endpoints": ["/v1/responses"]}},
                {"id": "unknown"},
            ]}
    monkeypatch.setattr(api_client, "transport_request", lambda *args, **kwargs: calls.append(args) or Response())

    models = api_client.discover_models()
    assert len(calls) == 1
    assert calls[0][1:] == ("get", "https://provider.invalid/v1/models")
    assert models[0]["supported_endpoint_types"] == ["responses"]
    assert models[1]["supported_endpoint_types"] == ["messages"]
    assert models[2]["supported_endpoint_types"] == ["responses"]
    assert "supported_endpoint_types" not in models[3]
    automatic_connection["model_endpoints"] = {
        item["name"]: item.get("supported_endpoint_types", []) for item in models
    }
    assert api_client.model_protocol(automatic_connection, "response-only") == "responses"
    assert api_client.model_protocol(automatic_connection, "claude-wire") == "messages"
    assert api_client.model_protocol(automatic_connection, "unknown") == "chat_completions"


def test_full_endpoint_url_supplies_a_hint_and_real_success_has_priority(automatic_connection):
    automatic_connection["base_url"] = "https://provider.invalid/v1/messages"
    assert api_client.model_protocol(automatic_connection, "unknown") == "messages"
    model_status.record_model_protocol("api:unknown", "responses")
    assert api_client.model_protocol(automatic_connection, "unknown") == "responses"


@pytest.mark.parametrize("field,value", [
    ("protocol_mode", "legacy"), ("model_endpoints", {"unknown": ["responses"]}),
    ("profile_id", "second-profile"), ("disable_thinking", False),
])
def test_protocol_cache_expires_with_automatic_configuration_context(automatic_connection, field, value):
    model_status.record_model_protocol("api:unknown", "messages")
    assert model_status.model_protocol("api:unknown") == "messages"
    automatic_connection[field] = value
    assert model_status.model_protocol("api:unknown") == ""


def test_status_file_cache_reuses_reads_and_detects_external_changes(monkeypatch, automatic_connection):
    model_status.record_model_protocol("api:unknown", "messages")
    path = model_status.runtime_model_status_path()
    original_read = Path.read_text
    reads = []
    def read_text(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original_read(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_text)
    assert model_status.model_protocol("api:unknown") == "messages"
    assert model_status.model_protocol("api:unknown") == "messages"
    assert len(reads) == 1
    replacement = {
        "version": 1, "models": {"api:unknown": {"_capabilities": {
            "context": model_status._provider_context("api"), "protocol": "responses",
        }}},
    }
    path.write_text(json.dumps(replacement), encoding="utf-8")
    assert model_status.model_protocol("api:unknown") == "responses"
    assert len(reads) == 2


def test_normal_translation_basic_and_adult_tests_share_automatic_selection(monkeypatch, automatic_connection):
    automatic_connection["model_endpoints"] = {"new-model": ["responses"]}
    calls = []
    def translate(protocol, cfg, base, key, model, text, **kwargs):
        calls.append(protocol)
        return "登场人物全是成年人。他把阴茎插入她的阴道进行性交。" if text == settings_service.ADULT_TEST_SOURCE else "你好"
    monkeypatch.setattr(api_client, "_translate_with_protocol", translate)

    assert api_client.translate_once("new-model", "こんにちは") == "你好"
    normal = settings_service.test_connection("api", "api:new-model", {}, protocol="messages")
    adult = settings_service.test_connection("api", "api:new-model", {}, test_kind="adult", protocol="chat_completions")
    assert normal["protocol_mode"] == adult["protocol_mode"] == "auto"
    assert normal["protocol"] == adult["protocol"] == "responses"
    assert adult["nsfw_supported"] is True
    assert calls == ["responses", "responses", "responses"]


def test_unknown_endpoint_metadata_is_not_treated_as_a_text_protocol():
    assert protocols.normalize_endpoint_types(["gemini", "embeddings", "openai-response-compact", "invented"]) == []
    assert protocols.normalize_endpoint_types("responses") == []


@pytest.mark.parametrize("failure", [
    ProtocolFailure(405, "Method Not Allowed"),
    ProtocolFailure(404, "404 page not found"),
    ProtocolFailure(404, '{"detail":"Not Found"}'),
])
def test_explicit_router_endpoint_rejection_can_use_the_next_protocol(monkeypatch, automatic_connection, failure):
    calls = []
    def translate(protocol, *args, **kwargs):
        calls.append(protocol)
        if protocol == "chat_completions":
            raise failure
        return "你好"
    monkeypatch.setattr(api_client, "_translate_with_protocol", translate)
    assert api_client.translate_once("unknown", "こんにちは") == "你好"
    assert calls == ["chat_completions", "messages"]
