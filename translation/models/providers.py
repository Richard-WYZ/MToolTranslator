"""Provider presets and saved connection profiles, without translation policy."""
from __future__ import annotations

import json
from typing import Any

from translation.models.protocols import normalize_endpoint_types


PROVIDER_PRESETS = (
    {"id": "opendesign_go", "name": "OpenDesign Go", "style": "openai",
     "base_url": "https://amr-link.open-design.ai/v1", "disable_thinking": True},
    {"id": "opencode_go", "name": "OpenCode Go", "style": "opencode_go",
     "base_url": "https://opencode.ai/zen/go/v1", "disable_thinking": True},
    {"id": "openai", "name": "OpenAI 兼容 / 自定义", "style": "openai",
     "base_url": "", "disable_thinking": True},
    {"id": "anthropic", "name": "Anthropic 兼容 / 自定义", "style": "anthropic",
     "base_url": "", "disable_thinking": True},
    {"id": "responses", "name": "Responses 兼容 / 自定义", "style": "responses",
     "base_url": "", "disable_thinking": True},
)


def provider_preset(preset_id: str) -> dict[str, Any]:
    return next((dict(item) for item in PROVIDER_PRESETS if item["id"] == preset_id), {})


def saved_api_profiles(values: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
    """Read local profiles; legacy flat settings become a reversible first profile."""
    raw = values.get("THIRD_PARTY_API_PROFILES", "").strip()
    if raw:
        try:
            profiles = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid saved API provider profiles JSON") from exc
        if not isinstance(profiles, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
            for item in profiles
        ):
            raise ValueError("Saved API provider profiles must be a list with unique IDs")
        ids = [item["id"] for item in profiles]
        if len(set(ids)) != len(ids):
            raise ValueError("Saved API provider profile IDs must be unique")
        if profiles:
            active = values.get("THIRD_PARTY_API_ACTIVE_PROFILE", "") or ids[0]
            if active not in ids:
                raise ValueError("The active API provider profile does not exist")
            return profiles, active
    style = values.get("THIRD_PARTY_API_STYLE", "openai")
    if values.get("OPENCODE_GO", "").lower() in {"1", "true", "on", "yes"}:
        style = "opencode_go"
    preset = values.get("THIRD_PARTY_API_PRESET") or (style if provider_preset(style) else "openai")
    def split(name: str, fallback: str = "") -> list[str]:
        return [item.strip() for item in (values.get(name) or fallback).split(",") if item.strip()]
    try:
        protocols = json.loads(values.get("THIRD_PARTY_API_MODEL_PROTOCOLS") or "{}")
    except ValueError:
        protocols = {}
    try:
        endpoints = json.loads(values.get("THIRD_PARTY_API_MODEL_ENDPOINTS") or "{}")
    except ValueError:
        endpoints = {}
    endpoints = {
        str(model).strip().removeprefix("api:"): normalized
        for model, declared in endpoints.items()
        if (normalized := normalize_endpoint_types(declared))
    } if isinstance(endpoints, dict) else {}
    profile = {
        "id": "legacy", "name": provider_preset(preset).get("name", "原有 API 配置"),
        "preset": preset, "style": style,
        "base_url": values.get("THIRD_PARTY_API_BASE_URL") or values.get("OPENCODE_GO_BASE_URL", ""),
        "api_key": values.get("THIRD_PARTY_API_KEY") or values.get("OPENCODE_GO_API_KEY", ""),
        "models": split("THIRD_PARTY_API_MODELS", values.get("OPENCODE_GO_MODELS", "")),
        "disabled_models": split("THIRD_PARTY_API_DISABLED_MODELS"),
        "model_protocols": protocols if isinstance(protocols, dict) else {},
        "protocol_mode": values.get("THIRD_PARTY_API_PROTOCOL_MODE") or "auto",
        "model_endpoints": endpoints,
        "default_model": values.get("DEFAULT_MODEL", ""),
        "disable_thinking": values.get("THIRD_PARTY_API_DISABLE_THINKING", "true").lower() not in {"false", "0", "no", "off"},
    }
    return [profile], "legacy"


def profile_env_values(profile: dict[str, Any]) -> dict[str, str]:
    """Flatten an active profile for existing API clients and CLI workflows."""
    return {
        "THIRD_PARTY_API_PRESET": str(profile.get("preset") or "openai"),
        "THIRD_PARTY_API_STYLE": str(profile.get("style") or "openai"),
        "THIRD_PARTY_API_BASE_URL": str(profile.get("base_url") or ""),
        "THIRD_PARTY_API_KEY": str(profile.get("api_key") or ""),
        "THIRD_PARTY_API_MODELS": ",".join(profile.get("models") or []),
        "THIRD_PARTY_API_DISABLED_MODELS": ",".join(profile.get("disabled_models") or []),
        "THIRD_PARTY_API_MODEL_PROTOCOLS": json.dumps(profile.get("model_protocols") or {}, ensure_ascii=True, separators=(",", ":")),
        "THIRD_PARTY_API_PROTOCOL_MODE": str(profile.get("protocol_mode") or "auto"),
        "THIRD_PARTY_API_MODEL_ENDPOINTS": json.dumps(profile.get("model_endpoints") or {}, ensure_ascii=True, separators=(",", ":")),
        "THIRD_PARTY_API_DISABLE_THINKING": "true" if profile.get("disable_thinking", True) else "false",
        # Explicitly retire legacy aliases, which otherwise override the new profile.
        "OPENCODE_GO": "false", "OPENCODE_GO_BASE_URL": "", "OPENCODE_GO_API_KEY": "", "OPENCODE_GO_MODELS": "",
    }
