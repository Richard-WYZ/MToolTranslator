from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.services import settings as settings_service
from app.services import model_status
from common import config_paths
from translation import settings as translation_settings


ENV_KEYS = (
    "MODEL_PROVIDER",
    "THIRD_PARTY_API_STYLE",
    "DEFAULT_MODEL",
    "THIRD_PARTY_API_BASE_URL",
    "THIRD_PARTY_API_KEY",
    "THIRD_PARTY_API_MODELS",
    "THIRD_PARTY_API_DISABLED_MODELS",
    "THIRD_PARTY_API_DISABLE_THINKING",
    "OLLAMA_HOST",
    "OLLAMA_DISABLED_MODELS",
)


def _clear_supported_environment(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _request(**overrides):
    values = {
        "provider": "api",
        "api_style": "opencode_go",
        "api_base_url": "https://example.invalid/v1",
        "api_models": ["qwen-test", "minimax-test"],
        "disabled_api_models": [],
        "disabled_ollama_models": [],
        "default_model": "api:qwen-test",
        "disable_thinking": True,
        "ollama_host": "http://localhost:11434",
        "api_key_action": "keep",
        "api_key": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_portable_env_is_created_only_for_frozen_runtime(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config_paths, "runtime_base_dir", lambda: tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)

    development = config_paths.ensure_portable_env_file()
    assert development["created"] is False
    assert not (tmp_path / ".env").exists()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen = config_paths.ensure_portable_env_file()
    assert frozen["created"] is True
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "THIRD_PARTY_API_KEY=\n" in content
    assert "replace-with-your-api-key" not in content

    (tmp_path / ".env").write_text("KEEP=this\n", encoding="utf-8")
    second = config_paths.ensure_portable_env_file()
    assert second["created"] is False
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "KEEP=this\n"


def test_reload_settings_resets_removed_values_and_honors_process_environment(
    monkeypatch, tmp_path: Path
):
    _clear_supported_environment(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MODEL_PROVIDER=api\n"
        "DEFAULT_MODEL=api:file-model\n"
        "THIRD_PARTY_API_KEY=file-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEFAULT_MODEL", "api:process-model")
    try:
        translation_settings.reload_settings_from_env(env_path)
        assert translation_settings.DEFAULT_CONFIG["model_provider"] == "api"
        assert translation_settings.DEFAULT_CONFIG["model"] == "api:process-model"
        assert translation_settings.DEFAULT_CONFIG["third_party_api"]["api_key"] == "file-secret"

        monkeypatch.delenv("DEFAULT_MODEL")
        env_path.write_text("MODEL_PROVIDER=ollama\n", encoding="utf-8")
        translation_settings.reload_settings_from_env(env_path)
        assert translation_settings.DEFAULT_CONFIG["model_provider"] == "ollama"
        assert translation_settings.DEFAULT_CONFIG["third_party_api"]["api_key"] == ""
    finally:
        translation_settings.reload_settings_from_env()


def test_settings_save_preserves_comments_unknown_values_and_secret(
    monkeypatch, tmp_path: Path
):
    _clear_supported_environment(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# user comment\n"
        "UNKNOWN_SETTING=preserve-me\n"
        "THIRD_PARTY_API_KEY=existing-secret\n"
        "MODEL_PROVIDER=ollama\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_service, "runtime_env_path", lambda: env_path)
    try:
        result = settings_service.save_settings(_request(), {})
        content = env_path.read_text(encoding="utf-8")
        assert result["ok"] is True
        assert "# user comment" in content
        assert "UNKNOWN_SETTING=preserve-me" in content
        assert "THIRD_PARTY_API_KEY=existing-secret" in content
        assert "MODEL_PROVIDER=api" in content
        assert result["settings"]["api"]["api_key_configured"] is True
        assert "existing-secret" not in str(result)
    finally:
        translation_settings.reload_settings_from_env()


def test_settings_save_persists_disabled_models_and_rejects_disabled_default(
    monkeypatch, tmp_path: Path
):
    _clear_supported_environment(monkeypatch)
    env_path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "runtime_env_path", lambda: env_path)
    try:
        result = settings_service.save_settings(
            _request(
                disabled_api_models=["api:minimax-test"],
                disabled_ollama_models=["ollama:local-old"],
            ),
            {},
        )
        content = env_path.read_text(encoding="utf-8")
        assert result["ok"] is True
        assert "THIRD_PARTY_API_DISABLED_MODELS=minimax-test" in content
        assert "OLLAMA_DISABLED_MODELS=local-old" in content

        with pytest.raises(HTTPException) as exc:
            settings_service.save_settings(
                _request(disabled_api_models=["qwen-test"]),
                {},
            )
        assert exc.value.status_code == 400
        assert "remain enabled" in str(exc.value.detail)
    finally:
        translation_settings.reload_settings_from_env()


def test_settings_save_is_blocked_during_active_translation(
    monkeypatch, tmp_path: Path
):
    _clear_supported_environment(monkeypatch)
    monkeypatch.setattr(settings_service, "runtime_env_path", lambda: tmp_path / ".env")

    with pytest.raises(HTTPException) as exc:
        settings_service.save_settings(
            _request(),
            {"task": SimpleNamespace(status="running")},
        )

    assert exc.value.status_code == 409
    assert not (tmp_path / ".env").exists()


def test_model_router_filters_disabled_models_but_can_include_them(monkeypatch):
    from translation.models import router

    monkeypatch.setattr(
        router.ollama_client,
        "list_models",
        lambda: [{"name": "local-a"}, {"name": "local-b"}],
    )
    monkeypatch.setattr(
        router.api_client,
        "list_models",
        lambda: [
            {"name": "remote-a", "provider": "api"},
            {"name": "remote-b", "provider": "api"},
        ],
    )
    monkeypatch.setattr(
        router,
        "disabled_models",
        lambda provider: ["remote-b"] if provider == "api" else ["local-b"],
    )

    enabled = router.list_models()
    complete = router.list_models(include_disabled=True)

    assert [item["name"] for item in enabled] == [
        "ollama:local-a",
        "api:remote-a",
    ]
    assert {item["name"] for item in complete if not item["enabled"]} == {
        "ollama:local-b",
        "api:remote-b",
    }


def test_model_discovery_falls_back_without_inference(monkeypatch):
    from translation.models import api_client

    monkeypatch.setattr(
        api_client,
        "discover_models",
        lambda: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )
    monkeypatch.setattr(
        api_client,
        "list_models",
        lambda: [{"name": "fallback-a", "provider": "api"}],
    )
    monkeypatch.setattr(settings_service, "disabled_models", lambda provider: [])

    result = settings_service.discover_provider_models("api", {})

    assert result["ok"] is False
    assert result["source"] == "configured_fallback"
    assert result["models"][0]["id"] == "api:fallback-a"
    assert result["inference_usage"] is False


def test_api_model_discovery_uses_catalog_endpoint_without_inference(monkeypatch):
    from translation.models import api_client

    requests = []

    class Response:
        text = ""
        headers = {}
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "model-a"}, {"id": "model-b"}]}

    monkeypatch.delenv("THIRD_PARTY_API_KEY", raising=False)
    monkeypatch.delenv("THIRD_PARTY_API_BASE_URL", raising=False)
    monkeypatch.setattr(
        api_client,
        "_api_config",
        lambda: {
            "base_url": "https://provider.invalid/v1/chat/completions",
            "api_key": "secret",
            "style": "openai",
        },
    )
    monkeypatch.setattr(
        api_client.requests,
        "get",
        lambda url, **kwargs: requests.append((url, kwargs)) or Response(),
    )

    models = api_client.discover_models()

    assert [item["name"] for item in models] == ["model-a", "model-b"]
    assert requests[0][0] == "https://provider.invalid/v1/models"
    assert requests[0][1]["headers"]["Authorization"] == "Bearer secret"


def test_model_availability_test_is_explicit_and_reports_usage(monkeypatch, tmp_path):
    import translation.models

    monkeypatch.setattr(
        model_status, "runtime_model_status_path", lambda: tmp_path / ".model-status.json"
    )
    calls = []
    monkeypatch.setattr(
        translation.models,
        "translate_once",
        lambda model, text, **kwargs: calls.append((model, text, kwargs)) or "你好",
    )

    result = settings_service.test_connection("api", "api:test-model", {})

    assert result["ok"] is True
    assert result["model"] == "api:test-model"
    assert len(calls) == 1
    assert "usage_warning" in result


@pytest.mark.parametrize(
    ("translation_output", "supported"),
    [
        ("登场人物全是成年人。他把阴茎插入她的阴道进行性交。", True),
        ("抱歉，我无法协助翻译露骨的成人内容。", False),
        ("两名成年人发生了亲密行为。", False),
    ],
)
def test_adult_model_test_detects_faithful_translation_or_restriction(
    monkeypatch, tmp_path, translation_output, supported
):
    import translation.models

    monkeypatch.setattr(
        model_status, "runtime_model_status_path", lambda: tmp_path / ".model-status.json"
    )
    calls = []
    monkeypatch.setattr(
        translation.models,
        "translate_once",
        lambda model, text, **kwargs: calls.append((model, text, kwargs))
        or translation_output,
    )

    result = settings_service.test_connection(
        "api", "api:test-model", {}, test_kind="adult"
    )

    assert result["ok"] is True
    assert result["test_kind"] == "adult"
    assert result["nsfw_supported"] is supported
    assert "全員成人" in calls[0][1]
    assert "Do not omit or euphemize" in calls[0][2]["system_prompt"]
    assert len(calls) == 1


def test_model_test_history_survives_catalog_changes_and_marks_context_stale(
    monkeypatch, tmp_path
):
    path = tmp_path / ".model-status.json"
    config = {
        "style": "openai",
        "base_url": "https://provider.example/v1",
        "api_key": "secret-a",
    }
    monkeypatch.setattr(model_status, "runtime_model_status_path", lambda: path)
    monkeypatch.setattr(model_status, "third_party_api_config", lambda: dict(config))

    saved = model_status.record_model_test("api:model-a", "basic", "available")
    initial = model_status.public_model_statuses()

    assert saved["persisted"] is True
    assert initial["api:model-a"]["basic"]["status"] == "available"
    assert initial["api:model-a"]["basic"]["stale"] is False
    assert initial["api:model-a"]["basic"]["tested_at"]
    assert "api:model-new" not in initial

    config["base_url"] = "https://new-provider.example/v1"
    stale = model_status.public_model_statuses()

    assert stale["api:model-a"]["basic"]["status"] == "available"
    assert stale["api:model-a"]["basic"]["stale"] is True


def test_ollama_client_reads_reloaded_host_for_each_request(monkeypatch):
    from translation.models import ollama_client

    urls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": []}

    monkeypatch.setattr(ollama_client, "ollama_host", lambda: "http://new-host:11434")
    monkeypatch.setattr(
        ollama_client.requests,
        "get",
        lambda url, **kwargs: urls.append(url) or Response(),
    )

    assert ollama_client.list_models() == []
    assert urls == ["http://new-host:11434/api/tags"]


def test_opencode_go_uses_its_builtin_base_url_for_configuration_status(monkeypatch):
    from app.services import runtime_profiles

    monkeypatch.setattr(
        runtime_profiles,
        "third_party_api_config",
        lambda: {
            "style": "opencode_go",
            "base_url": "",
            "api_key": "configured",
            "api_key_env": "THIRD_PARTY_API_KEY",
        },
    )
    monkeypatch.delenv("THIRD_PARTY_API_KEY", raising=False)

    status = runtime_profiles.api_configuration_status()

    assert status["configured"] is True
    assert status["base_url_configured"] is True


def test_settings_api_never_returns_saved_key(monkeypatch, tmp_path: Path):
    import main

    _clear_supported_environment(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MODEL_PROVIDER=api\n"
        "THIRD_PARTY_API_BASE_URL=https://example.invalid/v1\n"
        "THIRD_PARTY_API_KEY=private-value\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_service, "runtime_env_path", lambda: env_path)
    try:
        translation_settings.reload_settings_from_env(env_path)
        response = TestClient(main.app).get("/api/settings")
        assert response.status_code == 200
        assert response.json()["api"]["api_key_configured"] is True
        assert "private-value" not in response.text
    finally:
        translation_settings.reload_settings_from_env()
