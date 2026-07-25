from __future__ import annotations

from typing import Any

from translation import settings as config


def default_model() -> str:
    """Return the configured default model identifier."""
    return str(config.DEFAULT_CONFIG["model"])


def set_default_model(model: str) -> None:
    """Set the process-local default model identifier."""
    config.DEFAULT_CONFIG["model"] = model


def system_prompts() -> dict[str, Any]:
    """Return configured system prompts keyed by prompt style."""
    return config.DEFAULT_CONFIG.get("system_prompts", {})


def default_system_prompt(style: str = "professional") -> str:
    """Return the configured prompt for a style, falling back to professional."""
    prompts = system_prompts()
    return str(prompts.get(style) or prompts["professional"])


def batch_translation_config() -> dict[str, Any]:
    """Return batch translation runtime settings."""
    return config.DEFAULT_CONFIG.get("batch_translation", {})


def third_party_api_config() -> dict[str, Any]:
    """Return third-party API transport settings."""
    return dict(config.DEFAULT_CONFIG.get("third_party_api", {}) or {})


def ollama_host() -> str:
    """Return configured Ollama host URL."""
    return str(config.DEFAULT_CONFIG.get("ollama_host", "http://localhost:11434"))


def disabled_models(provider: str) -> list[str]:
    """Return disabled model names without provider prefixes."""
    selected = str(provider or "").strip().lower()
    if selected == "api":
        values = config.DEFAULT_CONFIG.get("third_party_api", {}).get(
            "disabled_models", []
        )
    elif selected == "ollama":
        values = config.DEFAULT_CONFIG.get("ollama_disabled_models", [])
    else:
        values = []
    prefix = f"{selected}:"
    return [
        str(value).strip().removeprefix(prefix)
        for value in values
        if str(value).strip()
    ]


def think_setting() -> Any:
    """Return model thinking/reasoning setting for translation calls."""
    return config.DEFAULT_CONFIG.get("think")


def model_provider() -> str:
    """Return the configured model provider name."""
    return str(config.DEFAULT_CONFIG.get("model_provider") or "ollama").lower()


def set_model_provider(provider: str) -> None:
    """Set the process-local model provider name."""
    config.DEFAULT_CONFIG["model_provider"] = provider


def fallback_models() -> list[Any]:
    """Return configured fallback model identifiers."""
    return config.DEFAULT_CONFIG.get("fallback_models", [])


def set_fallback_models(models: list[Any]) -> None:
    """Set process-local fallback model identifiers."""
    config.DEFAULT_CONFIG["fallback_models"] = list(models)


def fallback_chunk_strategy() -> dict[str, Any]:
    """Return fallback chunking strategy settings."""
    return config.FALLBACK_CONFIG.get("chunk_strategy", {})


def fallback_prompt_names() -> list[Any]:
    """Return fallback prompt style names used by retry orchestration."""
    return config.FALLBACK_CONFIG.get("prompts", [])


def output_constraints() -> tuple[int, int]:
    """Return configured max chars per line and max lines per cell."""
    return (
        int(config.DEFAULT_CONFIG.get("max_chars_per_line", 30)),
        int(config.DEFAULT_CONFIG.get("max_lines_per_cell", 4)),
    )


__all__ = [
    "batch_translation_config",
    "default_model",
    "disabled_models",
    "default_system_prompt",
    "fallback_chunk_strategy",
    "fallback_models",
    "fallback_prompt_names",
    "model_provider",
    "ollama_host",
    "output_constraints",
    "set_default_model",
    "set_fallback_models",
    "set_model_provider",
    "system_prompts",
    "third_party_api_config",
    "think_setting",
]
