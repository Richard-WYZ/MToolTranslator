from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.schemas import SettingsUpdateRequest
from app.services import settings as service
from translation import settings as translation_settings


def request(**changes):
    values = {
        "provider": "api", "api_profile_id": "legacy", "api_profile_name": "Saved provider",
        "api_preset": "openai", "api_style": "openai", "api_base_url": "https://provider.invalid/v1",
        "api_models": ["model-a"], "default_model": "api:model-a", "api_key_action": "keep",
        "api_protocol_mode": "auto",
    }
    values.update(changes)
    return SettingsUpdateRequest(**values)


def prepare(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "MODEL_PROVIDER=api\nDEFAULT_MODEL=api:model-a\nTHIRD_PARTY_API_STYLE=openai\n"
        "THIRD_PARTY_API_BASE_URL=https://provider.invalid/v1\nTHIRD_PARTY_API_MODELS=model-a\n"
        "THIRD_PARTY_API_KEY=saved-test-key\n", encoding="utf-8",
    )
    monkeypatch.setattr(service, "runtime_env_path", lambda: path)
    translation_settings.reload_settings_from_env(path)
    return path


def test_ordinary_settings_keep_the_saved_key_and_endpoint_metadata(monkeypatch, tmp_path):
    from translation.models import api_client

    path = prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(api_client, "list_models", lambda: (_ for _ in ()).throw(AssertionError("configuration reads must not use the model client")))
    try:
        service.save_settings(request(api_model_endpoints={"model-a": ["/v1/responses", "anthropic"]}), {})
        result = service.save_settings(request(api_profile_name="Renamed", api_key="unconfirmed-input"), {})
        values = translation_settings._load_dotenv(path)
        profiles = json.loads(values["THIRD_PARTY_API_PROFILES"])
        assert values["THIRD_PARTY_API_KEY"] == "saved-test-key"
        assert profiles[0]["model_endpoints"] == {"model-a": ["responses", "messages"]}
        assert result["settings"]["api"]["protocol_mode"] == "auto"
        assert result["settings"]["api_profiles"][0]["name"] == "Renamed"
        assert "saved-test-key" not in str(result)
        assert "unconfirmed-input" not in str(result)
    finally:
        translation_settings.reload_settings_from_env()


def test_settings_read_modify_write_is_serialized_with_key_replacement(monkeypatch, tmp_path):
    path = prepare(monkeypatch, tmp_path)
    first_write = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_read = threading.Event()
    original_write = service._atomic_write
    original_read = service._file_values

    def paused_write(target, text):
        if threading.current_thread().name.startswith("replace-key"):
            first_write.set()
            assert release_first.wait(5)
        original_write(target, text)

    def recorded_read(target):
        if threading.current_thread().name.startswith("ordinary-save"):
            second_read.set()
        return original_read(target)

    def ordinary_save():
        second_started.set()
        return service.save_settings(request(api_profile_name="Latest name"), {})

    monkeypatch.setattr(service, "_atomic_write", paused_write)
    monkeypatch.setattr(service, "_file_values", recorded_read)
    try:
        with ThreadPoolExecutor(1, thread_name_prefix="replace-key") as first, ThreadPoolExecutor(1, thread_name_prefix="ordinary-save") as second:
            replacing = first.submit(service.save_settings, request(api_key_action="replace", api_key="replacement-test-key"), {})
            assert first_write.wait(5)
            ordinary = second.submit(ordinary_save)
            assert second_started.wait(5)
            try:
                assert not second_read.wait(0.1), "An ordinary save must not read the old key during another settings write"
            finally:
                release_first.set()
            assert replacing.result(timeout=5)["ok"]
            assert ordinary.result(timeout=5)["ok"]
        values = translation_settings._load_dotenv(path)
        assert values["THIRD_PARTY_API_KEY"] == "replacement-test-key"
        assert json.loads(values["THIRD_PARTY_API_PROFILES"])[0]["name"] == "Latest name"
    finally:
        release_first.set()
        translation_settings.reload_settings_from_env()
