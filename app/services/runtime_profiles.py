from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from fastapi import HTTPException

from translation.config import (
    batch_translation_config,
    default_model,
    disabled_models,
    model_provider,
    third_party_api_config,
)


QUALITY_PRIMARY_MODEL = "api:qwen3.7-plus"
QUALITY_FAST_MODEL = "api:minimax-m3"
PROFILE_NAMES = {
    "quality_first": "联网 · 质量优先",
    "single_model": "联网 · 单模型",
    "local": "本地 · Ollama",
    "custom": "高级 · 自定义路由",
    "checkpoint": "按检查点恢复",
}
CUSTOM_OPTION_KEYS = {
    "protocol",
    "json_batch_size",
    "max_batch_chars",
    "api_concurrency",
    "api_parallel_enabled",
    "api_event_driven_enabled",
    "api_adaptive_concurrency_enabled",
    "api_model_routing_enabled",
    "api_fast_model",
    "api_quality_model",
    "api_sensitive_routing_enabled",
    "api_sensitive_model",
}


def canonical_model_id(model: str | None, provider: str | None = None) -> str:
    value = str(model or "").strip()
    selected_provider = str(provider or "").strip().lower()
    if not value:
        return ""
    if value.startswith(("api:", "ollama:")):
        return value
    if selected_provider == "api":
        return f"api:{value}"
    if selected_provider == "ollama":
        return f"ollama:{value}"
    configured_provider = model_provider()
    if configured_provider == "api":
        return f"api:{value}"
    if configured_provider == "ollama":
        return f"ollama:{value}"
    return value


def configured_default_model() -> str:
    return canonical_model_id(default_model(), model_provider())


def api_configuration_status() -> dict[str, Any]:
    cfg = third_party_api_config()
    key_env = str(cfg.get("api_key_env") or "THIRD_PARTY_API_KEY")
    key_configured = bool(cfg.get("api_key") or os.environ.get(key_env))
    style = str(cfg.get("style") or "openai")
    base_url_configured = bool(str(cfg.get("base_url") or "").strip()) or style == "opencode_go"
    return {
        "configured": key_configured and base_url_configured,
        "key_configured": key_configured,
        "base_url_configured": base_url_configured,
        "style": style,
        "health": "configured" if key_configured and base_url_configured else "unconfigured",
        "health_note": "模型列表来自配置；为避免产生用量，未发送推理请求。",
    }


def _base_profile_config() -> dict[str, Any]:
    return deepcopy(batch_translation_config())


def resolve_execution_profile(
    profile: str,
    model: str | None,
    provider: str | None = None,
    options: dict[str, Any] | None = None,
    *,
    enforce_enabled: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    profile = str(profile or "quality_first").strip().lower()
    if profile not in PROFILE_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown execution profile: {profile}")

    selected_model = canonical_model_id(model, provider)
    cfg = _base_profile_config()
    raw_options = dict(options or {})

    if profile == "quality_first":
        selected_model = QUALITY_PRIMARY_MODEL
        cfg.update({
            "enabled": True,
            "protocol": "line",
            "line_for_short_only": True,
            "json_batch_size": 40,
            "max_batch_chars": 4000,
            "api_parallel_enabled": True,
            "api_event_driven_enabled": True,
            "api_concurrency": 10,
            "api_adaptive_concurrency_enabled": True,
            "api_model_routing_enabled": True,
            "api_fast_model": QUALITY_FAST_MODEL,
            "api_quality_model": QUALITY_PRIMARY_MODEL,
            "api_sensitive_routing_enabled": True,
            "api_sensitive_model": QUALITY_FAST_MODEL,
        })
    elif profile == "single_model":
        if not selected_model.startswith("api:") or selected_model == "api:":
            raise HTTPException(status_code=400, detail="Online single-model profile requires an API model")
        cfg.update({
            "protocol": "line",
            "line_for_short_only": True,
            "json_batch_size": 40,
            "max_batch_chars": 4000,
            "api_parallel_enabled": True,
            "api_event_driven_enabled": False,
            "api_concurrency": 10,
            "api_adaptive_concurrency_enabled": False,
            "api_model_routing_enabled": False,
            "api_fast_model": "",
            "api_quality_model": "",
            "api_sensitive_routing_enabled": False,
            "api_sensitive_model": "",
        })
    elif profile == "local":
        if not selected_model.startswith("ollama:") or selected_model == "ollama:":
            raise HTTPException(status_code=400, detail="Local profile requires an Ollama model")
        cfg.update({
            "api_parallel_enabled": False,
            "api_event_driven_enabled": False,
            "api_adaptive_concurrency_enabled": False,
            "api_model_routing_enabled": False,
            "api_fast_model": "",
            "api_quality_model": "",
            "api_sensitive_routing_enabled": False,
            "api_sensitive_model": "",
        })
    elif profile == "custom":
        if not selected_model:
            raise HTTPException(status_code=400, detail="Custom profile requires a primary model")
        unknown = sorted(set(raw_options) - CUSTOM_OPTION_KEYS)
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unsupported custom profile options: {', '.join(unknown)}")
        cfg.update({key: raw_options[key] for key in CUSTOM_OPTION_KEYS if key in raw_options})
        _validate_custom_config(cfg)
    elif profile == "checkpoint":
        checkpoint_batch = raw_options.get("batch_translation")
        if not isinstance(checkpoint_batch, dict):
            raise HTTPException(status_code=400, detail="Checkpoint profile requires saved batch configuration")
        cfg.update(checkpoint_batch)

    if enforce_enabled:
        routed_models = [
            selected_model,
            str(cfg.get("api_fast_model") or ""),
            str(cfg.get("api_quality_model") or ""),
            str(cfg.get("api_sensitive_model") or ""),
        ]
        for routed_model in dict.fromkeys(filter(None, routed_models)):
            _require_enabled_model(routed_model)
    summary = profile_summary(profile, selected_model, cfg)
    return selected_model, cfg, summary


def _require_enabled_model(model: str) -> None:
    canonical = canonical_model_id(model)
    if not canonical.startswith(("api:", "ollama:")):
        return
    provider, name = canonical.split(":", 1)
    if name in set(disabled_models(provider)):
        raise HTTPException(
            status_code=409,
            detail=f"Model is disabled in settings: {canonical}",
        )


def _validate_custom_config(cfg: dict[str, Any]) -> None:
    for field, minimum, maximum in (
        ("json_batch_size", 1, 100),
        ("max_batch_chars", 500, 20000),
        ("api_concurrency", 1, 30),
    ):
        try:
            value = int(cfg.get(field))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{field} must be an integer") from exc
        if value < minimum or value > maximum:
            raise HTTPException(status_code=400, detail=f"{field} must be between {minimum} and {maximum}")
        cfg[field] = value
    if cfg.get("protocol") not in ("json", "line"):
        raise HTTPException(status_code=400, detail="protocol must be json or line")
    for key in ("api_fast_model", "api_quality_model", "api_sensitive_model"):
        value = str(cfg.get(key) or "")
        if value:
            cfg[key] = canonical_model_id(value)


def profile_summary(profile: str, primary_model: str, cfg: dict[str, Any]) -> dict[str, Any]:
    routes = [{"role": "普通文本", "model": primary_model}]
    if cfg.get("api_model_routing_enabled") and cfg.get("api_fast_model"):
        routes.insert(0, {"role": "短标签", "model": str(cfg["api_fast_model"])})
    if cfg.get("api_sensitive_routing_enabled") and cfg.get("api_sensitive_model"):
        routes.append({"role": "敏感文本", "model": str(cfg["api_sensitive_model"])})
    if cfg.get("api_quality_model"):
        routes.append({"role": "质量修复", "model": str(cfg["api_quality_model"])})
    return {
        "id": profile,
        "name": PROFILE_NAMES[profile],
        "primary_model": primary_model,
        "privacy": "local" if primary_model.startswith("ollama:") else "online",
        "routes": routes,
        "concurrency": int(cfg.get("api_concurrency", 1) or 1) if primary_model.startswith("api:") else 1,
        "batch_size": int(cfg.get("json_batch_size", 1) or 1),
        "max_batch_chars": int(cfg.get("max_batch_chars", 0) or 0),
        "protocol": str(cfg.get("protocol") or "json"),
        "event_driven": bool(cfg.get("api_event_driven_enabled")),
        "adaptive_concurrency": bool(cfg.get("api_adaptive_concurrency_enabled")),
    }


def public_runtime_configuration(models: list[dict[str, Any]]) -> dict[str, Any]:
    ids = {str(item.get("name") or "") for item in models}
    api_status = api_configuration_status()
    local_models = sorted(model for model in ids if model.startswith("ollama:"))
    quality_models_present = QUALITY_PRIMARY_MODEL in ids and QUALITY_FAST_MODEL in ids
    return {
        "default_model": configured_default_model(),
        "default_provider": model_provider(),
        "providers": {
            "api": api_status,
            "ollama": {
                "configured": bool(local_models),
                "health": "available" if local_models else "offline",
                "model_count": len(local_models),
                "health_note": "本地模型列表由 Ollama 实时返回。",
            },
        },
        "profiles": [
            {
                **profile_summary(
                    "quality_first",
                    QUALITY_PRIMARY_MODEL,
                    resolve_execution_profile(
                        "quality_first",
                        None,
                        enforce_enabled=False,
                    )[1],
                ),
                "available": api_status["configured"] and quality_models_present,
                "recommended": True,
                "description": "短标签与敏感文本使用 MiniMax，普通文本与质量修复使用 Qwen。",
            },
            {
                "id": "single_model",
                "name": PROFILE_NAMES["single_model"],
                "privacy": "online",
                "available": api_status["configured"] and any(model.startswith("api:") for model in ids),
                "recommended": False,
                "description": "所有文本只使用你选择的一个 API 模型，不启用跨模型路由。",
            },
            {
                "id": "local",
                "name": PROFILE_NAMES["local"],
                "privacy": "local",
                "available": bool(local_models),
                "recommended": False,
                "description": "文本只发送到本机 Ollama；速度与质量取决于本地模型和硬件。",
            },
            {
                "id": "custom",
                "name": PROFILE_NAMES["custom"],
                "privacy": "online",
                "available": api_status["configured"],
                "recommended": False,
                "description": "显式配置 API 主模型、路由模型和批处理参数。",
            },
        ],
    }


__all__ = [
    "PROFILE_NAMES",
    "QUALITY_FAST_MODEL",
    "QUALITY_PRIMARY_MODEL",
    "api_configuration_status",
    "canonical_model_id",
    "configured_default_model",
    "profile_summary",
    "public_runtime_configuration",
    "resolve_execution_profile",
]
