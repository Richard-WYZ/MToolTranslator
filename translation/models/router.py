from __future__ import annotations

from typing import Any

from translation.config import default_model, disabled_models, model_provider, third_party_api_config
from translation.models import api_client, ollama_client


API_PREFIX = "api:"
OLLAMA_PREFIX = "ollama:"


def _provider_for_model(model: str | None) -> str:
    model = model or ""
    if model.startswith(API_PREFIX):
        return "api"
    if model.startswith(OLLAMA_PREFIX):
        return "ollama"
    return model_provider()


def _clean_model(model: str | None) -> str:
    model = model or default_model()
    if model.startswith(API_PREFIX):
        return model[len(API_PREFIX):]
    if model.startswith(OLLAMA_PREFIX):
        return model[len(OLLAMA_PREFIX):]
    return model


def model_configuration(model: str | None = None) -> dict[str, Any]:
    provider = _provider_for_model(model)
    clean_model = _clean_model(model)
    cfg = {"provider": provider, "model": clean_model}
    if provider == "api":
        api_cfg = third_party_api_config()
        cfg["base_url"] = api_cfg.get("base_url", "")
        cfg["api_key_env"] = api_cfg.get("api_key_env", "THIRD_PARTY_API_KEY")
    return cfg


def list_models(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    disabled_api = set(disabled_models("api"))
    disabled_ollama = set(disabled_models("ollama"))
    try:
        for item in ollama_client.list_models():
            local_item = dict(item)
            raw_name = str(local_item.get("name", ""))
            name = raw_name[len(OLLAMA_PREFIX):] if raw_name.startswith(OLLAMA_PREFIX) else raw_name
            local_item["name"] = OLLAMA_PREFIX + name if name else ""
            local_item["provider"] = "ollama"
            local_item["enabled"] = name not in disabled_ollama
            if include_disabled or local_item["enabled"]:
                models.append(local_item)
    except Exception:
        pass
    for item in api_client.list_models():
        raw_name = str(item.get("name", ""))
        name = raw_name[len(API_PREFIX):] if raw_name.startswith(API_PREFIX) else raw_name
        if name:
            item = dict(item)
            item["name"] = API_PREFIX + name
        item["provider"] = "api"
        item["enabled"] = name not in disabled_api
        if include_disabled or item["enabled"]:
            models.append(item)
    return models


def translate_once(
    model: str,
    text: str,
    system_prompt: str = "",
    terminology: Any = None,
    timeout: int = 60,
    options: dict[str, Any] | None = None,
    think: Any = None,
    response_format: Any = None,
) -> str:
    provider = _provider_for_model(model)
    clean_model = _clean_model(model)
    client = api_client if provider == "api" else ollama_client
    return client.translate_once(
        clean_model,
        text,
        system_prompt=system_prompt,
        terminology=terminology,
        timeout=timeout,
        options=options,
        think=think,
        response_format=response_format,
    )


def translate(
    model: str,
    text: str,
    system_prompt: str = "",
    terminology: Any = None,
    timeout: int = 60,
    options: dict[str, Any] | None = None,
    think: Any = None,
    response_format: Any = None,
) -> str:
    provider = _provider_for_model(model)
    clean_model = _clean_model(model)
    client = api_client if provider == "api" else ollama_client
    return client.translate(
        clean_model,
        text,
        system_prompt=system_prompt,
        terminology=terminology,
        timeout=timeout,
        options=options,
        think=think,
        response_format=response_format,
    )


def get_system_prompts() -> dict[str, str]:
    return ollama_client.get_system_prompts()


__all__ = [
    "API_PREFIX",
    "OLLAMA_PREFIX",
    "get_system_prompts",
    "list_models",
    "model_configuration",
    "translate",
    "translate_once",
]
