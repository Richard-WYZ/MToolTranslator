from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from translation.config import third_party_api_config
import translation.usage as token_usage


OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_CHAT_MODELS = {
    "glm-5.2",
    "glm-5.1",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "mimo-v2.5",
    "mimo-v2.5-pro",
}
OPENCODE_GO_MESSAGES_MODELS = {
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "qwen3.7-max",
    "qwen3.7-plus",
    "qwen3.6-plus",
}
OPENCODE_GO_MODELS = [
    "glm-5.2",
    "glm-5.1",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "mimo-v2.5",
    "mimo-v2.5-pro",
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "qwen3.7-max",
    "qwen3.7-plus",
    "qwen3.6-plus",
]
KNOWN_ENDPOINT_SUFFIXES = ("/chat/completions", "/messages", "/models")


class APIRequestError(requests.HTTPError):
    """HTTP failure annotated for provider-neutral retry and fallback policy."""

    def __init__(self, message: str, *, response: requests.Response, body: str = "") -> None:
        super().__init__(message, response=response)
        self.status_code = int(response.status_code)
        self.response_body = body
        self.retry_after_seconds = _retry_after_seconds(
            str(response.headers.get("Retry-After", ""))
            if getattr(response, "headers", None) is not None
            else ""
        )
        lowered = body.lower()
        self.quota_exhausted = self.status_code == 429 and any(marker in lowered for marker in (
            "gousagelimiterror",
            "monthly usage limit",
            "quota exceeded",
            "insufficient_quota",
        ))
        self.retryable = (
            self.status_code >= 500 or self.status_code in {408, 409, 425, 429}
        ) and not self.quota_exhausted
        self.content_rejected = self.status_code == 400 and any(marker in lowered for marker in (
            "datainspectionfailed",
            "inappropriate content",
            "content policy",
            "content_filter",
        ))


def _retry_after_seconds(
    value: str,
    *,
    now: datetime | None = None,
) -> float | None:
    """Parse an RFC Retry-After delay-seconds or HTTP-date value."""
    rendered = str(value or "").strip()
    if not rendered:
        return None
    if rendered.isdigit():
        return float(rendered)
    try:
        target = parsedate_to_datetime(rendered)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (target - current).total_seconds())


def _raise_for_status_with_body(resp: requests.Response) -> None:
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        body = (resp.text or "").strip()
        body = body[:500]
        message = f"{exc}; response body: {body}" if body else str(exc)
        raise APIRequestError(message, response=resp, body=body) from exc


def _api_config() -> dict[str, Any]:
    return third_party_api_config()


def _api_key(cfg: dict[str, Any]) -> str:
    env_name = str(cfg.get("api_key_env") or "THIRD_PARTY_API_KEY")
    key = os.environ.get(env_name, "")
    if key:
        return key
    return str(cfg.get("api_key") or "")


def _base_url(cfg: dict[str, Any]) -> str:
    base_url = str(os.environ.get("THIRD_PARTY_API_BASE_URL") or cfg.get("base_url") or "").rstrip("/")
    if not base_url and _api_style(cfg) == "opencode_go":
        return OPENCODE_GO_BASE_URL
    return base_url


def _api_style(cfg: dict[str, Any]) -> str:
    style = str(os.environ.get("THIRD_PARTY_API_STYLE") or cfg.get("style") or "openai")
    return style.strip().lower().replace("-", "_")


def _api_model_id(model: str) -> str:
    model = (model or "").strip()
    if model.startswith("opencode-go/"):
        return model.split("/", 1)[1]
    return model


def _endpoint_url(base_url: str, endpoint: str) -> str:
    base = base_url.rstrip("/")
    for suffix in KNOWN_ENDPOINT_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/{endpoint.lstrip('/')}"


def _endpoint_for_model(cfg: dict[str, Any], model: str) -> str:
    style = _api_style(cfg)
    if style in ("anthropic", "messages"):
        return "messages"
    if style == "opencode_go":
        model_id = _api_model_id(model)
        if model_id in OPENCODE_GO_MESSAGES_MODELS:
            return "messages"
        return "chat/completions"
    return "chat/completions"


def _build_user_text(text: str, terminology: Any = None) -> str:
    if not terminology:
        return text
    term_lines: list[str] = []
    if isinstance(terminology, dict):
        term_lines = [f"{src} -> {tgt}" for src, tgt in terminology.items()]
    elif isinstance(terminology, list):
        for item in terminology:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                term_lines.append(f"{item[0]} -> {item[1]}")
            elif isinstance(item, str):
                term_lines.append(item)
    elif isinstance(terminology, str):
        term_lines.append(terminology)
    if term_lines:
        return "Terminology:\n" + "\n".join(term_lines) + "\n\nText:\n" + text
    return text


def _build_messages(text: str, system_prompt: str = "", terminology: Any = None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": _build_user_text(text, terminology=terminology)})
    return messages


def list_models() -> list[dict[str, Any]]:
    cfg = _api_config()
    models = cfg.get("models") or []
    if not models and _api_style(cfg) == "opencode_go":
        models = OPENCODE_GO_MODELS
    return [{"name": str(model), "provider": "api"} for model in models]


def discover_models(timeout: int = 30) -> list[dict[str, Any]]:
    """Fetch the provider model catalog without sending an inference request."""
    cfg = _api_config()
    base_url = _base_url(cfg)
    key = _api_key(cfg)
    if not base_url:
        raise RuntimeError("API Base URL is not configured")
    if not key:
        raise RuntimeError("API key is not configured")
    style = _api_style(cfg)
    if style in ("anthropic", "messages"):
        headers = {
            "x-api-key": key,
            "anthropic-version": str(cfg.get("anthropic_version") or "2023-06-01"),
        }
    else:
        headers = {"Authorization": f"Bearer {key}"}
    response = requests.get(
        _endpoint_url(base_url, "models"),
        headers=headers,
        timeout=(10, timeout),
    )
    _raise_for_status_with_body(response)
    payload = response.json()
    raw_models = payload.get("data") or payload.get("models") or []
    if isinstance(raw_models, dict):
        raw_models = raw_models.get("data") or raw_models.get("models") or []
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_models:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("id") or item.get("name") or item.get("model")
        else:
            name = ""
        rendered = str(name or "").strip()
        if rendered and rendered not in seen:
            seen.add(rendered)
            models.append({"name": rendered, "provider": "api"})
    if not models:
        raise RuntimeError("Provider returned an empty model catalog")
    return models


def _openai_translate_once(
    cfg: dict[str, Any],
    base_url: str,
    key: str,
    model: str,
    text: str,
    system_prompt: str = "",
    terminology: Any = None,
    timeout: int = 60,
    options: dict[str, Any] | None = None,
    response_format: Any = None,
) -> str:
    url = _endpoint_url(base_url, "chat/completions")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    model_id = _api_model_id(model) if _api_style(cfg) == "opencode_go" else model
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": _build_messages(text, system_prompt=system_prompt, terminology=terminology),
        "temperature": 0,
    }
    if options:
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        if "num_predict" in options:
            payload["max_tokens"] = options["num_predict"]
        for key_name in ("top_p", "frequency_penalty", "presence_penalty", "seed"):
            if key_name in options:
                payload[key_name] = options[key_name]
    if response_format:
        payload["response_format"] = response_format
    if _api_style(cfg) == "opencode_go" and cfg.get("disable_thinking", True):
        payload["reasoning_effort"] = "none"

    request_started = token_usage.record_request_start("api", model_id)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=(10, timeout))
    finally:
        token_usage.record_response_received("api", model_id, request_started)
    _raise_for_status_with_body(resp)
    data = resp.json()
    token_usage.record("api", model_id, data.get("usage"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Third-party API returned no choices")
    message = choices[0].get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        raise RuntimeError("Third-party API returned empty content")
    return content


def _anthropic_translate_once(
    cfg: dict[str, Any],
    base_url: str,
    key: str,
    model: str,
    text: str,
    system_prompt: str = "",
    terminology: Any = None,
    timeout: int = 60,
    options: dict[str, Any] | None = None,
) -> str:
    url = _endpoint_url(base_url, "messages")
    headers = {
        "x-api-key": key,
        "anthropic-version": str(cfg.get("anthropic_version") or "2023-06-01"),
        "Content-Type": "application/json",
    }
    model_id = _api_model_id(model) if _api_style(cfg) == "opencode_go" else model
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [{"role": "user", "content": _build_user_text(text, terminology=terminology)}],
        "max_tokens": 2048,
        "temperature": 0,
    }
    if system_prompt:
        payload["system"] = system_prompt
    if _api_style(cfg) == "opencode_go" and cfg.get("disable_thinking", True):
        payload["thinking"] = {"type": "disabled"}
    if options:
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        if "num_predict" in options:
            payload["max_tokens"] = options["num_predict"]
        for key_name in ("top_p",):
            if key_name in options:
                payload[key_name] = options[key_name]

    request_started = token_usage.record_request_start("api", model_id)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=(10, timeout))
    finally:
        token_usage.record_response_received("api", model_id, request_started)
    _raise_for_status_with_body(resp)
    data = resp.json()
    token_usage.record("api", model_id, data.get("usage"))
    blocks = data.get("content") or []
    if isinstance(blocks, str):
        content = blocks.strip()
    elif isinstance(blocks, list):
        content = "".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict)).strip()
    else:
        content = ""
    if not content:
        raise RuntimeError("Third-party API returned empty content")
    return content


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
    if not text or not text.strip():
        return ""

    cfg = _api_config()
    base_url = _base_url(cfg)
    key = _api_key(cfg)
    if not base_url:
        raise RuntimeError("Third-party API base_url is not configured")
    if not key:
        raise RuntimeError("Third-party API key is not configured")

    if _endpoint_for_model(cfg, model) == "messages":
        return _anthropic_translate_once(
            cfg,
            base_url,
            key,
            model,
            text,
            system_prompt=system_prompt,
            terminology=terminology,
            timeout=timeout,
            options=options,
        )
    return _openai_translate_once(
        cfg,
        base_url,
        key,
        model,
        text,
        system_prompt=system_prompt,
        terminology=terminology,
        timeout=timeout,
        options=options,
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
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return translate_once(
                model,
                text,
                system_prompt=system_prompt,
                terminology=terminology,
                timeout=timeout,
                options=options,
                think=think,
                response_format=response_format,
            )
        except Exception as exc:
            last_error = exc
            if getattr(exc, "retryable", True) is False:
                break
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise last_error or RuntimeError("Third-party API translation failed")


__all__ = [
    "APIRequestError",
    "OPENCODE_GO_MODELS",
    "list_models",
    "translate",
    "translate_once",
]
