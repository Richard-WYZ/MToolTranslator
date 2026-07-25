from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


SEMANTIC_BATCH_KEYS = (
    "protocol",
    "compact_json_protocol",
    "json_batch_size",
    "max_batch_chars",
    "num_predict",
    "response_format",
    "temperature",
    "api_content_split_max_depth",
    "api_event_driven_enabled",
    "api_adaptive_concurrency_enabled",
    "api_model_concurrency_initial",
    "api_model_concurrency_max",
    "api_model_inflight_chars_max",
    "api_adaptive_default_maximum",
    "api_default_inflight_chars_max",
    "api_concurrency_increase_every",
    "api_concurrency_decrease_factor",
    "glossary_freeze_during_translation",
    "api_model_routing_enabled",
    "api_fast_model",
    "api_quality_model",
    "api_sensitive_routing_enabled",
    "api_sensitive_model",
    "api_sensitive_repair_enabled",
    "api_sensitive_repair_batch_size",
    "api_sensitive_repair_max_batch_chars",
    "api_sensitive_repair_single_retry",
    "api_sensitive_parent_repair_enabled",
    "api_sensitive_parent_repair_max_chars",
    "api_sensitive_repair_issue_types",
    "api_fast_categories",
    "api_quality_retry_issue_types",
    "api_quality_recursive_repair_enabled",
    "api_quality_recursive_max_depth",
    "api_quality_recursive_fresh_single",
    "api_quality_recursive_issue_types",
    "quality_num_predict",
    "line_for_short_only",
    "short_line_max_chars",
    "long_text_min_chars",
    "mtool_composition_enabled",
    "mtool_parent_first_enabled",
    "mtool_parent_first_max_chars",
    "mtool_context_max_chars",
    "mtool_context_max_per_item",
    "mtool_neighbor_context_enabled",
    "mtool_neighbor_context_radius",
    "mtool_neighbor_context_max_chars",
    "mtool_neighbor_context_min_dialogue_items",
)


def stable_fingerprint(value: Any, *, prefix: str = "") -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}" if prefix else digest


def build_resume_model_configuration(
    base_configuration: dict[str, Any],
    batch_configuration: dict[str, Any],
    *,
    think: Any,
    fallback_models: Iterable[Any],
) -> dict[str, Any]:
    semantic_batch = {
        key: _normalized_config_value(key, batch_configuration[key])
        for key in SEMANTIC_BATCH_KEYS
        if key in batch_configuration
    }
    return {
        **base_configuration,
        "think": think,
        "fallback_models": [str(model) for model in fallback_models],
        "batch_translation": semantic_batch,
    }


def build_prompt_version(prompt_payload: dict[str, Any]) -> str:
    return stable_fingerprint(prompt_payload, prefix="prompt-")


def _normalized_config_value(key: str, value: Any) -> Any:
    if key in (
        "api_fast_categories",
        "api_quality_retry_issue_types",
        "api_quality_recursive_issue_types",
        "api_sensitive_repair_issue_types",
    ):
        if isinstance(value, str):
            return sorted(item.strip() for item in value.split(",") if item.strip())
        return sorted(str(item).strip() for item in value or [] if str(item).strip())
    return value


__all__ = [
    "SEMANTIC_BATCH_KEYS",
    "build_prompt_version",
    "build_resume_model_configuration",
    "stable_fingerprint",
]
