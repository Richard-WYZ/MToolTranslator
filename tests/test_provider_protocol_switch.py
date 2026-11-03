from __future__ import annotations

import json

from app.schemas import SettingsUpdateRequest
from app.services import model_status
from app.services import settings as service
from translation import settings
from translation.models import api_client
from translation.models.providers import profile_env_values, saved_api_profiles


def test_flat_profile_migration_preserves_mode_and_supported_endpoints():
    profiles, active = saved_api_profiles({
        "THIRD_PARTY_API_PROTOCOL_MODE": "legacy",
        "THIRD_PARTY_API_MODEL_ENDPOINTS": json.dumps({"api:shared": ["openai-response", "anthropic"]}),
        "THIRD_PARTY_API_MODEL_PROTOCOLS": '{"shared":"messages"}',
    })
    assert active == "legacy"
    assert profiles[0]["protocol_mode"] == "legacy"
    assert profiles[0]["model_endpoints"] == {"shared": ["responses", "messages"]}
    flattened = profile_env_values(profiles[0])
    assert flattened["THIRD_PARTY_API_PROTOCOL_MODE"] == "legacy"
    assert json.loads(flattened["THIRD_PARTY_API_MODEL_ENDPOINTS"]) == profiles[0]["model_endpoints"]


def test_old_profiles_without_metadata_clear_previously_active_protocol_fields():
    flattened = profile_env_values({"id": "old-profile", "models": ["shared"]})
    assert flattened["THIRD_PARTY_API_PROTOCOL_MODE"] == "auto"
    assert json.loads(flattened["THIRD_PARTY_API_MODEL_ENDPOINTS"]) == {}


def test_switching_same_named_models_restores_each_profiles_protocol_and_metadata(monkeypatch, tmp_path):
    for key in service.EDITABLE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    monkeypatch.setattr(service, "runtime_env_path", lambda: path)
    monkeypatch.setattr(model_status, "runtime_model_status_path", lambda: tmp_path / ".status.json")
    common = {
        "provider": "api", "api_style": "openai", "api_preset": "openai",
        "api_base_url": "https://provider.invalid/v1", "api_models": ["shared"],
        "default_model": "api:shared", "api_key_action": "replace",
    }
    try:
        service.save_settings(SettingsUpdateRequest(
            **common, api_profile_id="first", api_key="offline-first-key",
            api_protocol_mode="auto", api_model_endpoints={"shared": ["responses"]},
        ), {})
        model_status.record_model_protocol("api:shared", "responses")
        first_context = model_status._provider_context("api")
        service.save_settings(SettingsUpdateRequest(
            **common, api_profile_id="second", api_key="offline-second-key",
            api_protocol_mode="legacy", api_model_endpoints={"shared": ["messages"]},
            api_model_protocols={"shared": "messages"},
        ), {})
        assert api_client.model_protocol(settings.DEFAULT_CONFIG["third_party_api"], "shared") == "messages"
        model_status.record_model_protocol("api:shared", "messages")
        for profile_id, mode, endpoint, key in (
            ("first", "auto", "responses", "offline-first-key"),
            ("second", "legacy", "messages", "offline-second-key"),
            ("first", "auto", "responses", "offline-first-key"),
        ):
            result = service.activate_api_profile(profile_id, {})
            cfg = settings.DEFAULT_CONFIG["third_party_api"]
            assert cfg["protocol_mode"] == mode
            assert cfg["model_endpoints"] == {"shared": [endpoint]}
            assert cfg["api_key"] == key
            assert model_status.model_protocol("api:shared") == endpoint
            assert api_client.model_protocol(cfg, "shared") == endpoint
            assert "offline-" not in json.dumps(result)
        assert model_status._provider_context("api") == first_context
    finally:
        settings.reload_settings_from_env()
