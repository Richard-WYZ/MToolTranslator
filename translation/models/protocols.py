"""Transport protocol selection from connection hints and observed capability."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit


SUPPORTED_PROTOCOLS = ("chat_completions", "messages", "responses")
KNOWN_ENDPOINT_SUFFIXES = ("/chat/completions", "/messages", "/responses", "/models")


def normalize_protocol(value: Any) -> str:
    rendered = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "chat": "chat_completions", "chat/completions": "chat_completions",
        "openai": "chat_completions", "anthropic": "messages", "response": "responses",
        "openai_response": "responses",
    }
    path = urlsplit(rendered).path.rstrip("/")
    for suffix, protocol in (("/chat/completions", "chat_completions"), ("/messages", "messages"), ("/responses", "responses")):
        if path.endswith(suffix):
            return protocol
    return aliases.get(rendered, rendered)


def normalize_endpoint_types(value: Any) -> list[str]:
    """Accept only supported text endpoints explicitly advertised by a catalog."""
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(
        protocol for item in value if isinstance(item, str)
        and (protocol := normalize_protocol(item)) in SUPPORTED_PROTOCOLS
    ))


def catalog_endpoint_types(item: Any) -> list[str]:
    """Read New API's endpoint field or LiteLLM's model_info annotation if supplied."""
    if not isinstance(item, dict):
        return []
    declared = normalize_endpoint_types(item.get("supported_endpoint_types"))
    info = item.get("model_info")
    if not declared and isinstance(info, dict):
        declared = normalize_endpoint_types(info.get("supported_endpoints"))
    return declared


def automatic_protocols(cfg: dict[str, Any]) -> bool:
    # Calls supplying the historical bare transport dictionary retain its
    # explicit override contract. Runtime settings explicitly default to auto.
    return str(cfg.get("protocol_mode") or "legacy").strip().lower() == "auto"


def protocol_candidates(
    cfg: dict[str, Any], model: str, *, stored: str = "", provider_hint: str = "chat_completions",
) -> list[str]:
    """Rank hints without sending a probe or guessing a model from its name."""
    metadata = cfg.get("model_endpoints") or {}
    declared = normalize_endpoint_types(metadata.get(model)) if isinstance(metadata, dict) else []
    endpoint_hint = normalize_protocol(cfg.get("base_url"))
    hint = endpoint_hint if endpoint_hint in SUPPORTED_PROTOCOLS else provider_hint
    cached = normalize_protocol(stored)
    first = cached if cached in SUPPORTED_PROTOCOLS and (not declared or cached in declared) else ""
    if not first:
        first = (hint if hint in declared else declared[0]) if declared else hint
    return list(dict.fromkeys([first, *declared, *SUPPORTED_PROTOCOLS]))


def explicit_protocol_mismatch(exc: Exception) -> bool:
    body = str(getattr(exc, "response_body", "") or "").lower()
    message = str(exc).lower()
    markers = (
        "not supported for format", "unsupported api format", "unsupported_api_format",
        "unsupported protocol", "unsupported_protocol", "modelprotocolunsupported",
        "does not support this protocol", "unsupported endpoint", "unsupported_endpoint",
        "endpoint is unavailable", "endpoint unavailable", "does not support this endpoint",
        "use the responses api", "use /responses", "use /messages", "use /chat/completions",
        "unknown endpoint", "no route for", "cannot post /",
    )
    return any(marker in body or marker in message for marker in markers)


def may_change_protocol(exc: Exception, *, strict: bool, exhaustive_probe: bool = False) -> bool:
    """Protocol fallback never repairs authentication, quota, safety or service failures."""
    body = str(getattr(exc, "response_body", "") or "").lower()
    message = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if status in {401, 403, 408, 409, 425, 429} or (isinstance(status, int) and status >= 500):
        return False
    if getattr(exc, "content_rejected", False) or getattr(exc, "quota_exhausted", False):
        return False
    if any(marker in body or marker in message for marker in (
        "content_filter", "content policy", "safety policy", "policy_violation", "refusal",
        "datainspectionfailed", "inappropriate content", "authentication_error", "insufficient_quota",
    )):
        return False
    if explicit_protocol_mismatch(exc):
        return True
    if strict:
        # A generic 404/model_not_found or 400/validation error is not evidence
        # that another protocol is supported. 405 explicitly rejects POST here.
        if status == 405:
            return True
        if status == 404:
            if body.strip() == "404 page not found":
                return True
            try:
                payload = json.loads(body)
            except (TypeError, ValueError):
                return False
            return isinstance(payload, dict) and payload == {"detail": "not found"}
        return False
    if status in {404, 405, 415, 422}:
        return True
    if status == 400 and any(marker in body or marker in message for marker in (
        "invalid temperature", "invalid_request_error", "unsupported parameter",
    )):
        return True
    return exhaustive_probe


def suggested_protocol(exc: Exception) -> str:
    """Honor a positive server instruction, never an endpoint named in a refusal."""
    text = (str(getattr(exc, "response_body", "") or "") + " " + str(exc)).lower()
    for marker, protocol in (
        ("use the responses api", "responses"), ("use /responses", "responses"),
        ("use /messages", "messages"), ("use /chat/completions", "chat_completions"),
    ):
        if marker in text:
            return protocol
    return ""
