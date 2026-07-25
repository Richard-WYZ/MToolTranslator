from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path


FALLBACK_CONFIG = {
    "prompts": ["professional", "academic", "uncensored"],
    "models": ["qwen3:4b-instruct", "qwen3:8b"],
    "chunk_strategy": {"max_chars": 50, "overlap": 10},
}

_BASE_DEFAULT_CONFIG = {
    "ollama_host": "http://localhost:11434",
    "model_provider": "ollama",
    "third_party_api": {
        "base_url": "",
        "api_key_env": "THIRD_PARTY_API_KEY",
        "api_key": "",
        "style": "openai",
        "anthropic_version": "2023-06-01",
        "disable_thinking": True,
        "models": [],
        "disabled_models": [],
    },
    "ollama_disabled_models": [],
    "translate_columns": [0, 1],
    "skip_columns": [],
    "max_chars_per_line": 30,
    "max_lines_per_cell": 4,
    "model": "qwen3:4b-instruct",
    "think": False,
    "fallback_models": [],
    "batch_translation": {
        "enabled": True,
        "protocol": "json",
        "compact_json_protocol": True,
        "json_batch_size": 40,
        "max_batch_chars": 4000,
        "num_predict": 2048,
        "response_format": None,
        "temperature": 0,
        "timeout": 300,
        "api_parallel_enabled": False,
        "api_event_driven_enabled": False,
        "api_concurrency": 10,
        "api_adaptive_concurrency_enabled": False,
        "api_model_concurrency_initial": {},
        "api_model_concurrency_max": {},
        "api_model_inflight_chars_max": {},
        "api_adaptive_default_maximum": 10,
        "api_default_inflight_chars_max": 40000,
        "api_concurrency_increase_every": 8,
        "api_concurrency_decrease_factor": 0.5,
        "glossary_freeze_during_translation": True,
        "api_live_output_snapshots_enabled": False,
        "api_max_retries": 2,
        "api_retry_backoff_seconds": [2, 5, 15],
        "api_content_split_max_depth": 3,
        "api_model_routing_enabled": False,
        "api_fast_model": "",
        "api_quality_model": "",
        "api_sensitive_routing_enabled": True,
        "api_sensitive_model": "api:minimax-m3",
        "api_sensitive_repair_enabled": True,
        "api_sensitive_repair_batch_size": 5,
        "api_sensitive_repair_max_batch_chars": 1000,
        "api_sensitive_repair_single_retry": True,
        "api_sensitive_cross_model_retry_enabled": True,
        "api_sensitive_parent_repair_enabled": True,
        "api_sensitive_parent_repair_max_chars": 2400,
        "api_sensitive_repair_issue_types": [
            "empty_translation",
            "untranslated_japanese",
            "identical_japanese_source",
            "model_refusal",
            "suspicious_artifact",
            "symbol_preservation",
            "runtime_token_preservation",
            "numeric_preservation",
            "line_break_preservation",
            "length_expansion",
            "short_label_expansion",
            "marker_lost",
            "version_marker_lost",
        ],
        "api_fast_categories": ["short_label"],
        "api_quality_retry_issue_types": [
            "empty_translation",
            "untranslated_japanese",
            "identical_japanese_source",
            "model_refusal",
            "english_residue",
            "suspicious_artifact",
            "honorific_rendering_review",
            "symbol_preservation",
            "term_preservation",
            "term_placeholder_leak",
            "runtime_token_preservation",
            "numeric_preservation",
            "line_break_preservation",
            "unsupported_glossary_name",
            "unsupported_proper_name",
            "contextual_term_pollution",
            "length_expansion",
            "short_label_expansion",
            "version_marker_lost",
            "marker_lost",
        ],
        "api_quality_recursive_repair_enabled": True,
        "api_quality_recursive_max_depth": 6,
        "api_quality_recursive_fresh_single": True,
        "api_quality_recursive_issue_types": [
            "empty_translation",
            "untranslated_japanese",
            "identical_japanese_source",
            "model_refusal",
            "english_residue",
            "suspicious_artifact",
            "honorific_rendering_review",
            "symbol_preservation",
            "term_preservation",
            "term_placeholder_leak",
            "runtime_token_preservation",
            "numeric_preservation",
            "line_break_preservation",
            "unsupported_glossary_name",
            "unsupported_proper_name",
            "contextual_term_pollution",
            "length_expansion",
            "short_label_expansion",
            "version_marker_lost",
            "marker_lost",
        ],
        "quality_num_predict": 4096,
        "line_for_short_only": True,
        "short_line_max_chars": 80,
        "mtool_composition_enabled": True,
        "mtool_parent_first_enabled": False,
        "mtool_parent_first_max_chars": 2400,
        "mtool_context_max_chars": 1200,
        "mtool_context_max_per_item": 2,
        "mtool_neighbor_context_enabled": True,
        "mtool_neighbor_context_radius": 2,
        "mtool_neighbor_context_max_chars": 120,
        "mtool_neighbor_context_min_dialogue_items": 3,
    },
    "system_prompts": {
        "professional": (
            "You are a professional game translator. Translate the given text "
            "into natural, idiomatic Chinese (Simplified). Preserve the original "
            "meaning, tone, and context. Keep translations concise and suitable "
            "for game UI constraints."
        ),
        "academic": (
            "You are an academic translator specializing in game localization. "
            "Provide precise, literal translations that preserve the original "
            "terminology and structure. Use formal Chinese where appropriate."
        ),
        "uncensored": (
            "You are an uncensored game translator. Translate the given text "
            "into natural Chinese (Simplified) without omitting or sanitizing "
            "any content. Preserve the original tone including colloquial, "
            "mature, or controversial elements."
        ),
    },
}

DEFAULT_CONFIG = deepcopy(_BASE_DEFAULT_CONFIG)


def _dotenv_candidates(path: str | Path = ".env") -> list[Path]:
    requested = Path(path)
    if requested.is_absolute():
        return [requested]

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / requested)
    candidates.append(Path.cwd() / requested)
    candidates.append(Path(__file__).resolve().parents[1] / requested)

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(candidate)
    return unique


def _load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    env_path = next((candidate for candidate in _dotenv_candidates(path) if candidate.exists()), None)
    if env_path is None:
        return {}

    values: dict[str, str] = {}
    with env_path.open("r", encoding="utf-8-sig") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[key] = value
    return values


def _split_models(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _model_limits(value: str) -> dict[str, int]:
    rendered = value.strip()
    if not rendered:
        return {}
    try:
        parsed = json.loads(rendered)
    except json.JSONDecodeError:
        parsed = dict(
            item.split("=", 1)
            for item in rendered.split(",")
            if "=" in item
        )
    if not isinstance(parsed, dict):
        return {}
    return {
        str(model).strip(): max(1, int(limit))
        for model, limit in parsed.items()
        if str(model).strip()
    }


def _enabled(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _apply_dotenv(
    config: dict | None = None,
    env: dict[str, str] | None = None,
) -> None:
    target_config = config if config is not None else DEFAULT_CONFIG
    if env is None:
        env = _load_dotenv()
        env.update(os.environ)
    if not env:
        return

    api_config = target_config["third_party_api"]
    scalar_values = {
        "MODEL_PROVIDER": (target_config, "model_provider"),
        "DEFAULT_MODEL": (target_config, "model"),
        "OLLAMA_HOST": (target_config, "ollama_host"),
        "THIRD_PARTY_API_STYLE": (api_config, "style"),
    }
    for env_name, (target, key) in scalar_values.items():
        if env.get(env_name):
            target[key] = env[env_name].strip()

    if env.get("THIRD_PARTY_API_DISABLE_THINKING"):
        api_config["disable_thinking"] = _enabled(env["THIRD_PARTY_API_DISABLE_THINKING"])
    if env.get("OPENCODE_GO") and _enabled(env["OPENCODE_GO"]):
        api_config["style"] = "opencode_go"

    base_url = env.get("THIRD_PARTY_API_BASE_URL") or env.get("OPENCODE_GO_BASE_URL")
    api_key = env.get("THIRD_PARTY_API_KEY") or env.get("OPENCODE_GO_API_KEY")
    models = env.get("THIRD_PARTY_API_MODELS") or env.get("OPENCODE_GO_MODELS")
    if base_url:
        api_config["base_url"] = base_url.strip()
    if api_key:
        api_config["api_key"] = api_key.strip()
    if models:
        api_config["models"] = _split_models(models)
    if env.get("THIRD_PARTY_API_DISABLED_MODELS"):
        api_config["disabled_models"] = _split_models(
            env["THIRD_PARTY_API_DISABLED_MODELS"]
        )
    if env.get("OLLAMA_DISABLED_MODELS"):
        target_config["ollama_disabled_models"] = _split_models(
            env["OLLAMA_DISABLED_MODELS"]
        )

    batch_config = target_config["batch_translation"]
    bool_values = {
        "BATCH_API_PARALLEL_ENABLED": "api_parallel_enabled",
        "BATCH_API_EVENT_DRIVEN_ENABLED": "api_event_driven_enabled",
        "BATCH_API_ADAPTIVE_CONCURRENCY_ENABLED": "api_adaptive_concurrency_enabled",
        "BATCH_GLOSSARY_FREEZE_DURING_TRANSLATION": "glossary_freeze_during_translation",
        "BATCH_API_LIVE_OUTPUT_SNAPSHOTS_ENABLED": "api_live_output_snapshots_enabled",
        "BATCH_API_MODEL_ROUTING_ENABLED": "api_model_routing_enabled",
        "BATCH_API_SENSITIVE_ROUTING_ENABLED": "api_sensitive_routing_enabled",
        "BATCH_API_SENSITIVE_REPAIR_ENABLED": "api_sensitive_repair_enabled",
        "BATCH_API_QUALITY_RECURSIVE_REPAIR_ENABLED": "api_quality_recursive_repair_enabled",
        "BATCH_API_QUALITY_RECURSIVE_FRESH_SINGLE": "api_quality_recursive_fresh_single",
        "BATCH_API_SENSITIVE_REPAIR_SINGLE_RETRY": "api_sensitive_repair_single_retry",
        "BATCH_API_SENSITIVE_CROSS_MODEL_RETRY_ENABLED": "api_sensitive_cross_model_retry_enabled",
        "BATCH_API_SENSITIVE_PARENT_REPAIR_ENABLED": "api_sensitive_parent_repair_enabled",
        "BATCH_LINE_FOR_SHORT_ONLY": "line_for_short_only",
        "BATCH_MTOOL_COMPOSITION_ENABLED": "mtool_composition_enabled",
        "BATCH_MTOOL_PARENT_FIRST_ENABLED": "mtool_parent_first_enabled",
        "BATCH_MTOOL_NEIGHBOR_CONTEXT_ENABLED": "mtool_neighbor_context_enabled",
    }
    int_values = {
        "BATCH_API_CONCURRENCY": "api_concurrency",
        "BATCH_API_ADAPTIVE_DEFAULT_MAXIMUM": "api_adaptive_default_maximum",
        "BATCH_API_DEFAULT_INFLIGHT_CHARS_MAX": "api_default_inflight_chars_max",
        "BATCH_API_CONCURRENCY_INCREASE_EVERY": "api_concurrency_increase_every",
        "BATCH_API_QUALITY_RECURSIVE_MAX_DEPTH": "api_quality_recursive_max_depth",
        "BATCH_API_MAX_RETRIES": "api_max_retries",
        "BATCH_API_SENSITIVE_REPAIR_BATCH_SIZE": "api_sensitive_repair_batch_size",
        "BATCH_API_SENSITIVE_REPAIR_MAX_BATCH_CHARS": "api_sensitive_repair_max_batch_chars",
        "BATCH_API_SENSITIVE_PARENT_REPAIR_MAX_CHARS": "api_sensitive_parent_repair_max_chars",
        "BATCH_QUALITY_NUM_PREDICT": "quality_num_predict",
        "BATCH_SHORT_LINE_MAX_CHARS": "short_line_max_chars",
        "BATCH_MTOOL_CONTEXT_MAX_CHARS": "mtool_context_max_chars",
        "BATCH_MTOOL_PARENT_FIRST_MAX_CHARS": "mtool_parent_first_max_chars",
        "BATCH_MTOOL_CONTEXT_MAX_PER_ITEM": "mtool_context_max_per_item",
        "BATCH_MTOOL_NEIGHBOR_CONTEXT_RADIUS": "mtool_neighbor_context_radius",
        "BATCH_MTOOL_NEIGHBOR_CONTEXT_MAX_CHARS": "mtool_neighbor_context_max_chars",
        "BATCH_MTOOL_NEIGHBOR_CONTEXT_MIN_DIALOGUE_ITEMS": "mtool_neighbor_context_min_dialogue_items",
        "BATCH_JSON_BATCH_SIZE": "json_batch_size",
        "BATCH_MAX_BATCH_CHARS": "max_batch_chars",
        "BATCH_NUM_PREDICT": "num_predict",
        "BATCH_TIMEOUT": "timeout",
    }
    text_values = {
        "BATCH_API_FAST_MODEL": "api_fast_model",
        "BATCH_API_QUALITY_MODEL": "api_quality_model",
        "BATCH_API_SENSITIVE_MODEL": "api_sensitive_model",
        "BATCH_PROTOCOL": "protocol",
    }
    for env_name, key in bool_values.items():
        if env.get(env_name):
            batch_config[key] = _enabled(env[env_name])
    for env_name, key in int_values.items():
        if env.get(env_name):
            batch_config[key] = int(env[env_name].strip())
    for env_name, key in text_values.items():
        if env.get(env_name):
            batch_config[key] = env[env_name].strip().lower() if key == "protocol" else env[env_name].strip()
    if env.get("BATCH_API_CONCURRENCY_DECREASE_FACTOR"):
        batch_config["api_concurrency_decrease_factor"] = float(
            env["BATCH_API_CONCURRENCY_DECREASE_FACTOR"].strip()
        )
    if env.get("BATCH_API_MODEL_CONCURRENCY_INITIAL"):
        batch_config["api_model_concurrency_initial"] = _model_limits(
            env["BATCH_API_MODEL_CONCURRENCY_INITIAL"]
        )
    if env.get("BATCH_API_MODEL_CONCURRENCY_MAX"):
        batch_config["api_model_concurrency_max"] = _model_limits(
            env["BATCH_API_MODEL_CONCURRENCY_MAX"]
        )
    if env.get("BATCH_API_MODEL_INFLIGHT_CHARS_MAX"):
        batch_config["api_model_inflight_chars_max"] = _model_limits(
            env["BATCH_API_MODEL_INFLIGHT_CHARS_MAX"]
        )
    if env.get("BATCH_API_FAST_CATEGORIES"):
        batch_config["api_fast_categories"] = _split_models(env["BATCH_API_FAST_CATEGORIES"])
    if env.get("BATCH_API_QUALITY_RECURSIVE_ISSUE_TYPES"):
        batch_config["api_quality_recursive_issue_types"] = _split_models(
            env["BATCH_API_QUALITY_RECURSIVE_ISSUE_TYPES"]
        )
    if env.get("BATCH_API_SENSITIVE_REPAIR_ISSUE_TYPES"):
        batch_config["api_sensitive_repair_issue_types"] = _split_models(
            env["BATCH_API_SENSITIVE_REPAIR_ISSUE_TYPES"]
        )


def reload_settings_from_env(path: str | Path = ".env") -> dict:
    """Reload settings in place so new tasks see saved configuration."""
    env = _load_dotenv(path)
    env.update(os.environ)
    refreshed = deepcopy(_BASE_DEFAULT_CONFIG)
    _apply_dotenv(refreshed, env)
    DEFAULT_CONFIG.clear()
    DEFAULT_CONFIG.update(refreshed)
    return DEFAULT_CONFIG


reload_settings_from_env()


__all__ = ["DEFAULT_CONFIG", "FALLBACK_CONFIG", "reload_settings_from_env"]
