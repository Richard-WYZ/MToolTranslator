from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.schemas import SettingsUpdateRequest
from app.services import model_status
from app.services import settings as service
from translation import settings
from translation.models import api_client
from translation.models.providers import profile_env_values, saved_api_profiles


@pytest.fixture
def profile_env(monkeypatch, tmp_path):
    for name in service.EDITABLE_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    monkeypatch.setattr(service, "runtime_env_path", lambda: path)
    monkeypatch.setattr(model_status, "runtime_model_status_path", lambda: tmp_path / ".status.json")
    yield path
    settings.reload_settings_from_env()


def request(**overrides):
    values = {
        "provider": "api", "api_profile_id": "design", "api_profile_name": "设计接口",
        "api_preset": "opendesign_go", "api_style": "openai",
        "api_models": ["shared-model", "other-model"], "default_model": "api:shared-model",
        "api_key_action": "replace", "api_key": "design-private-key",
    }
    values.update(overrides)
    return SettingsUpdateRequest(**values)


def test_profile_create_preserves_legacy_and_roundtrip_secrets(profile_env):
    profile_env.write_text(
        "# original\nOPENCODE_GO=true\nOPENCODE_GO_API_KEY=old-private-key\n"
        "OPENCODE_GO_MODELS=shared-model\nDEFAULT_MODEL=api:shared-model\n",
        encoding="utf-8",
    )
    result = service.save_settings(request(), {})
    assert result["settings"]["active_api_profile"] == "design"
    assert result["settings"]["api"]["base_url"] == "https://amr-link.open-design.ai/v1"
    assert {p["id"] for p in result["settings"]["api_profiles"]} == {"legacy", "design"}
    assert "private-key" not in json.dumps(result)
    profiles, active = saved_api_profiles(settings._load_dotenv(profile_env))
    assert active == "design"
    assert profiles[0]["api_key"] == "old-private-key"
    assert settings.DEFAULT_CONFIG["third_party_api"]["style"] == "openai"
    assert settings.DEFAULT_CONFIG["third_party_api"]["api_key"] == "design-private-key"
    assert "# original" in profile_env.read_text(encoding="utf-8")

    restored = service.activate_api_profile("legacy", {})
    assert restored["settings"]["api"]["style"] == "opencode_go"
    assert settings.DEFAULT_CONFIG["third_party_api"]["api_key"] == "old-private-key"
    service.activate_api_profile("design", {})
    assert settings.DEFAULT_CONFIG["third_party_api"]["api_key"] == "design-private-key"


def test_new_profile_keep_never_reuses_another_provider_secret(profile_env):
    service.save_settings(request(), {})
    result = service.save_settings(request(
        api_profile_id="second", api_key_action="keep", api_key="",
    ), {})
    assert result["settings"]["api"]["api_key_configured"] is False
    assert settings.DEFAULT_CONFIG["third_party_api"]["api_key"] == ""
    service.activate_api_profile("design", {})
    assert settings.DEFAULT_CONFIG["third_party_api"]["api_key"] == "design-private-key"


def test_empty_connection_can_be_saved_before_catalog_discovery(profile_env):
    result = service.save_settings(request(api_models=[], default_model=""), {})
    assert result["settings"]["api"]["models"] == []
    assert result["settings"]["default_model"] == "api:"
    from app.services.runtime_profiles import resolve_execution_profile
    with pytest.raises(HTTPException) as error:
        resolve_execution_profile("quality_first", None)
    assert error.value.status_code == 409


def test_activation_is_atomic_when_busy_missing_or_environment_overridden(monkeypatch, profile_env):
    service.save_settings(request(), {})
    original = profile_env.read_bytes()
    for profile_id, tasks, code in (
        ("legacy", {"review": SimpleNamespace(status="reviewing")}, 409),
        ("missing", {}, 404),
    ):
        with pytest.raises(HTTPException) as error:
            service.activate_api_profile(profile_id, tasks)
        assert error.value.status_code == code
        assert profile_env.read_bytes() == original
    monkeypatch.setenv("THIRD_PARTY_API_BASE_URL", "https://override.invalid/v1")
    with pytest.raises(HTTPException) as error:
        service.activate_api_profile("legacy", {})
    assert error.value.status_code == 409
    assert "environment" in error.value.detail
    assert profile_env.read_bytes() == original


def test_malformed_profiles_are_never_overwritten(profile_env):
    profile_env.write_text("THIRD_PARTY_API_PROFILES={broken\n", encoding="utf-8")
    original = profile_env.read_bytes()
    with pytest.raises(HTTPException):
        service.save_settings(request(), {})
    assert profile_env.read_bytes() == original


def test_opencode_adapter_is_rejected_for_other_presets_without_writing(profile_env):
    service.save_settings(request(), {})
    original = profile_env.read_bytes()
    for preset in ("opendesign_go", "openai", "anthropic", "responses"):
        with pytest.raises(HTTPException) as error:
            service.save_settings(request(api_preset=preset, api_style="opencode_go"), {})
        assert error.value.status_code == 400
        assert "OpenCode Go" in error.value.detail
        assert profile_env.read_bytes() == original


def test_legacy_opencode_style_without_preset_remains_supported(profile_env):
    result = service.save_settings(request(api_preset=None, api_style="opencode_go"), {})
    assert result["settings"]["api"]["preset"] == "opencode_go"
    assert result["settings"]["api"]["style"] == "opencode_go"


def test_model_test_history_and_capabilities_return_when_provider_returns(monkeypatch, profile_env):
    config = {"style": "openai", "base_url": "https://first.invalid/v1", "api_key": "first"}
    monkeypatch.setattr(model_status, "third_party_api_config", lambda: dict(config))
    model_status.record_model_test("api:shared", "basic", "available")
    model_status.record_model_protocol("api:shared", "responses")
    model_status.record_model_thinking_mode("api:shared", "low")
    config["base_url"] = "https://second.invalid/v1"
    assert model_status.model_protocol("api:shared") == ""
    assert model_status.model_thinking_mode("api:shared") == ""
    model_status.record_model_test("api:shared", "basic", "unavailable")
    model_status.record_model_protocol("api:shared", "chat_completions")
    config["base_url"] = "https://first.invalid/v1"
    assert model_status.public_model_statuses()["api:shared"]["basic"]["status"] == "available"
    assert model_status.public_model_statuses()["api:shared"]["basic"]["stale"] is False
    assert model_status.model_protocol("api:shared") == "responses"
    assert model_status.model_thinking_mode("api:shared") == "low"


def test_same_endpoint_and_key_profiles_have_independent_test_and_benchmark_context(profile_env):
    from translation.benchmark import store

    service.save_settings(request(), {})
    model_status.record_model_test("api:shared-model", "basic", "available")
    model_status.record_model_protocol("api:shared-model", "responses")
    first_context = store._context_hash("api", ["api:shared-model"])
    service.save_settings(request(api_profile_id="second", api_profile_name="同地址第二套配置"), {})
    assert model_status.public_model_statuses()["api:shared-model"]["basic"]["stale"] is True
    assert model_status.model_protocol("api:shared-model") == ""
    assert store._context_hash("api", ["api:shared-model"]) != first_context
    model_status.record_model_test("api:shared-model", "basic", "unavailable")
    service.activate_api_profile("design", {})
    restored = model_status.public_model_statuses()["api:shared-model"]["basic"]
    assert restored["status"] == "available"
    assert restored["stale"] is False
    assert model_status.model_protocol("api:shared-model") == "responses"
    assert store._context_hash("api", ["api:shared-model"]) == first_context


@pytest.mark.parametrize("field,value", [
    ("profile_id", "second-profile"),
    ("preset", "anthropic"),
    ("disable_thinking", False),
    ("model_protocols", {"shared": "responses"}),
])
def test_capability_and_benchmark_results_become_stale_when_call_configuration_changes(
    monkeypatch, profile_env, field, value,
):
    from translation.benchmark import store

    config = {
        "style": "openai", "preset": "opendesign_go", "profile_id": "first-profile",
        "base_url": "https://same.invalid/v1", "api_key": "same-private-key",
        "disable_thinking": True, "model_protocols": {},
    }
    monkeypatch.setattr(model_status, "third_party_api_config", lambda: dict(config))
    monkeypatch.setattr(store, "third_party_api_config", lambda: dict(config))
    monkeypatch.setattr(store, "runtime_model_benchmark_path", lambda: profile_env.parent / ".benchmark.json")
    model_status.record_model_test("api:shared", "basic", "available", thinking_mode="low")
    model_status.record_model_protocol("api:shared", "chat_completions")
    assert store.save_benchmark({"provider": "api", "models": ["api:shared"]}) is True
    assert store.load_benchmark()["result"]["stale"] is False
    config[field] = value
    assert model_status.public_model_statuses()["api:shared"]["basic"]["stale"] is True
    assert model_status.model_protocol("api:shared") == ""
    assert model_status.model_thinking_mode("api:shared") == ""
    assert store.load_benchmark()["result"]["stale"] is True


def test_benchmark_default_update_is_restored_with_profile(profile_env):
    service.save_settings(request(), {})
    service.set_default_model_setting("api:other-model", {})
    service.activate_api_profile("legacy", {})
    service.activate_api_profile("design", {})
    assert service.public_settings()["default_model"] == "api:other-model"


def test_opendesign_never_uses_opencode_model_protocols_or_session(monkeypatch, profile_env):
    config = {"style": "openai", "preset": "opendesign_go", "api_key": "private"}
    assert api_client._base_url(config) == "https://amr-link.open-design.ai/v1"
    assert api_client.model_protocol(config, "minimax-m3") == "chat_completions"
    assert "x-opencode-session" not in api_client._request_headers("private", style="openai")
    captured = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "你好"}}]}
    monkeypatch.setattr(api_client, "transport_request", lambda *a, **kw: captured.append(kw) or Response())
    output = api_client._openai_translate_once(config, api_client._base_url(config), "private", "minimax-m3", "こんにちは")
    assert output == "你好"
    assert captured[0]["json"]["reasoning_effort"] == "none"
    assert "x-opencode-session" not in captured[0]["headers"]


def test_managed_quality_profile_uses_active_catalog_and_rejects_old_routes(profile_env):
    service.save_settings(request(), {})
    from app.services.runtime_profiles import resolve_execution_profile
    model, config, _ = resolve_execution_profile("quality_first", None)
    assert model == "api:shared-model"
    assert config["api_model_routing_enabled"] is False
    assert config["api_sensitive_model"] == ""
    with pytest.raises(HTTPException) as error:
        resolve_execution_profile("single_model", "api:minimax-m3")
    assert "active provider" in error.value.detail
