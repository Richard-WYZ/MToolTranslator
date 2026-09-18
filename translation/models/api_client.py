from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from translation.config import third_party_api_config
import translation.usage as token_usage
from translation.models.transport import connection_scope, request as transport_request


OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_LOW_REASONING_EFFORT = "low"
OPENCODE_GO_LOW_THINKING_BUDGET = 1024
LOW_THINKING_TRANSLATION_SUFFIX = (
    "\n\nTranslation-only constraint: use the minimum necessary reasoning. "
    "Do not analyze, explain, summarize, critique, or discuss the task. "
    "Return only the requested translation or required structured output, "
    "with no preamble or reasoning text."
)
OPENCODE_GO_CHAT_MODELS = {
    "glm-5.3-flash",
    "glm-5.3",
    "glm-5.2",
    "glm-5.1",
    "kimi-k3",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "longcat-2.0",
    "deepseek-v4.1-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
    "mimo-v2.5",
    "mimo-v2.5-pro",
    "hy4-preview",
    "hy3",
}
OPENCODE_GO_MESSAGES_MODELS = {
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "qwen3.8-max",
    "qwen3.8-flash",
    "qwen3.7-max",
    "qwen3.7-plus",
    "qwen3.6-plus",
    "union-alpha",
}
OPENCODE_GO_RESPONSES_MODELS = {
    "grok-4.6",
    "gpt-5.6-luna",
    "muse-spark-1.3-contributor",
    "muse-spark-1.2-contributor",
}
OPENCODE_GO_MODELS = sorted(
    OPENCODE_GO_CHAT_MODELS
    | OPENCODE_GO_MESSAGES_MODELS
    | OPENCODE_GO_RESPONSES_MODELS
)
KNOWN_ENDPOINT_SUFFIXES = ("/chat/completions", "/messages", "/responses", "/models")
SUPPORTED_PROTOCOLS = ("chat_completions", "messages", "responses")


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


def _persist_thinking_mode(model: str, mode: str) -> None:
    """Best-effort persistence of a provider capability discovered at runtime."""
    try:
        from app.services.model_status import record_model_thinking_mode

        record_model_thinking_mode(f"api:{_api_model_id(model)}", mode)
    except Exception:
        # Translation must not fail because capability bookkeeping failed.
        return


def _stored_thinking_mode(model: str) -> str:
    try:
        from app.services.model_status import model_thinking_mode

        return model_thinking_mode(f"api:{_api_model_id(model)}")
    except Exception:
        return ""


def _request_headers(key: str, *, style: str, auth_header: str = "Authorization") -> dict[str, str]:
    """Build provider headers, including the current OpenCode Go session key."""
    headers = {
        auth_header: f"Bearer {key}" if auth_header == "Authorization" else key,
        "Content-Type": "application/json",
    }
    if style == "opencode_go":
        # OpenCode Go requires this header for request routing.  Keep the
        # identifier bounded to one physical request so a failed request
        # cannot be replayed as an accidental continuation.
        headers["x-opencode-session"] = str(uuid.uuid4())
    return headers


def _endpoint_url(base_url: str, endpoint: str) -> str:
    base = base_url.rstrip("/")
    for suffix in KNOWN_ENDPOINT_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/{endpoint.lstrip('/')}"


def _endpoint_for_model(cfg: dict[str, Any], model: str) -> str:
    protocol = model_protocol(cfg, model)
    return {
        "chat_completions": "chat/completions",
        "messages": "messages",
        "responses": "responses",
    }[protocol]


def _normalize_protocol(value: Any) -> str:
    rendered = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "chat": "chat_completions",
        "chat/completions": "chat_completions",
        "openai": "chat_completions",
        "anthropic": "messages",
        "response": "responses",
    }
    return aliases.get(rendered, rendered)


def model_protocol(cfg: dict[str, Any], model: str) -> str:
    """Resolve one model's transport protocol without leaking policy above transport."""
    style = _api_style(cfg)
    if style in ("anthropic", "messages"):
        return "messages"
    if style in ("response", "responses"):
        return "responses"
    if style == "opencode_go":
        model_id = _api_model_id(model)
        overrides = cfg.get("model_protocols") or {}
        if isinstance(overrides, dict):
            override = _normalize_protocol(overrides.get(model_id))
            if override in SUPPORTED_PROTOCOLS:
                return override
        try:
            from app.services.model_status import model_protocol as stored_model_protocol

            stored = _normalize_protocol(stored_model_protocol(f"api:{model_id}"))
        except Exception:
            stored = ""
        if stored in SUPPORTED_PROTOCOLS:
            return stored
        if model_id in OPENCODE_GO_RESPONSES_MODELS:
            return "responses"
        if model_id in OPENCODE_GO_MESSAGES_MODELS:
            return "messages"
        return "chat_completions"
    return "chat_completions"


def _persist_model_protocol(model: str, protocol: str) -> None:
    try:
        from app.services.model_status import record_model_protocol

        record_model_protocol(f"api:{_api_model_id(model)}", protocol)
    except Exception:
        return


def _is_explicit_protocol_mismatch(exc: Exception) -> bool:
    body = str(getattr(exc, "response_body", "") or "").lower()
    message = str(exc).lower()
    markers = (
        "not supported for format",
        "unsupported api format",
        "unsupported endpoint",
        "use the responses api",
        "use /responses",
        "use /messages",
    )
    return any(marker in body or marker in message for marker in markers)


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


def _translation_system_prompt(system_prompt: str, thinking_mode: str) -> str:
    if thinking_mode != "low":
        return system_prompt
    if LOW_THINKING_TRANSLATION_SUFFIX.strip() in system_prompt:
        return system_prompt
    return f"{system_prompt}{LOW_THINKING_TRANSLATION_SUFFIX}" if system_prompt else LOW_THINKING_TRANSLATION_SUFFIX.lstrip()


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
    response = transport_request("api", "get",
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
    thinking_mode: str = "disabled",
) -> str:
    url = _endpoint_url(base_url, "chat/completions")
    headers = _request_headers(key, style=_api_style(cfg))
    model_id = _api_model_id(model) if _api_style(cfg) == "opencode_go" else model
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": _build_messages(
            text,
            system_prompt=_translation_system_prompt(system_prompt, thinking_mode),
            terminology=terminology,
        ),
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
    if _api_style(cfg) == "opencode_go":
        payload["reasoning_effort"] = (
            OPENCODE_GO_LOW_REASONING_EFFORT
            if thinking_mode == "low"
            else "none"
        )

    request_started = token_usage.record_request_start("api", model_id)
    try:
        resp = transport_request("api", "post", url, headers=headers, json=payload, timeout=(10, timeout))
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
    thinking_mode: str = "disabled",
) -> str:
    url = _endpoint_url(base_url, "messages")
    headers = {
        "x-api-key": key,
        "anthropic-version": str(cfg.get("anthropic_version") or "2023-06-01"),
        "x-opencode-session": str(uuid.uuid4()),
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
        payload["system"] = _translation_system_prompt(system_prompt, thinking_mode)
    if _api_style(cfg) == "opencode_go":
        payload["thinking"] = (
            {"type": "enabled", "budget_tokens": OPENCODE_GO_LOW_THINKING_BUDGET}
            if thinking_mode == "low"
            else {"type": "disabled"}
        )
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
        resp = transport_request("api", "post", url, headers=headers, json=payload, timeout=(10, timeout))
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


def _responses_translate_once(
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
    thinking_mode: str = "disabled",
) -> str:
    url = _endpoint_url(base_url, "responses")
    headers = _request_headers(key, style=_api_style(cfg))
    model_id = _api_model_id(model) if _api_style(cfg) == "opencode_go" else model
    payload: dict[str, Any] = {
        "model": model_id,
        "input": _build_user_text(text, terminology=terminology),
    }
    if system_prompt:
        payload["instructions"] = _translation_system_prompt(system_prompt, thinking_mode)
    if options and options.get("num_predict"):
        payload["max_output_tokens"] = int(options["num_predict"])
    else:
        payload["max_output_tokens"] = 2048
    if response_format:
        payload["text"] = {"format": response_format}
    if _api_style(cfg) == "opencode_go":
        payload["reasoning"] = {
            "effort": OPENCODE_GO_LOW_REASONING_EFFORT
            if thinking_mode == "low"
            else "none"
        }

    request_started = token_usage.record_request_start("api", model_id)
    try:
        resp = transport_request("api", "post", url, headers=headers, json=payload, timeout=(10, timeout))
    finally:
        token_usage.record_response_received("api", model_id, request_started)
    _raise_for_status_with_body(resp)
    data = resp.json()
    token_usage.record("api", model_id, data.get("usage"))
    content = str(data.get("output_text") or "").strip()
    if not content:
        pieces: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                    pieces.append(str(block.get("text") or ""))
        content = "".join(pieces).strip()
    if not content:
        raise RuntimeError("Third-party Responses API returned empty content")
    return content


def _translate_with_protocol(
    protocol: str,
    cfg: dict[str, Any],
    base_url: str,
    key: str,
    model: str,
    text: str,
    *,
    system_prompt: str,
    terminology: Any,
    timeout: int,
    options: dict[str, Any] | None,
    response_format: Any,
    thinking_mode: str,
) -> str:
    if protocol == "messages":
        return _anthropic_translate_once(
            cfg, base_url, key, model, text,
            system_prompt=system_prompt, terminology=terminology,
            timeout=timeout, options=options, thinking_mode=thinking_mode,
        )
    if protocol == "responses":
        return _responses_translate_once(
            cfg, base_url, key, model, text,
            system_prompt=system_prompt, terminology=terminology,
            timeout=timeout, options=options, response_format=response_format,
            thinking_mode=thinking_mode,
        )
    return _openai_translate_once(
        cfg, base_url, key, model, text,
        system_prompt=system_prompt, terminology=terminology,
        timeout=timeout, options=options, response_format=response_format,
        thinking_mode=thinking_mode,
    )


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

    if _api_style(cfg) != "opencode_go":
        return _translate_with_protocol(
            model_protocol(cfg, model), cfg, base_url, key, model, text,
            system_prompt=system_prompt, terminology=terminology,
            timeout=timeout, options=options, response_format=response_format,
            thinking_mode="disabled",
        )

    stored_mode = _stored_thinking_mode(model)
    preferred_mode = stored_mode if stored_mode in {"disabled", "low"} else "disabled"
    # Two attempts with the current mode, then one bounded fallback with the
    # lowest known thinking setting.  A model previously discovered to need
    # thinking is never silently switched back to disabled thinking.
    modes = (
        ["low", "low", "low"]
        if preferred_mode == "low"
        else ["disabled", "disabled", "low"]
    )
    last_error: Exception | None = None
    preferred_protocol = model_protocol(cfg, model)
    protocols = [preferred_protocol]
    explicitly_overridden = _api_model_id(model) in dict(cfg.get("model_protocols") or {})
    last_error: Exception | None = None
    for thinking_mode in modes:
        try:
            output = _translate_with_protocol(
                protocols[-1], cfg, base_url, key, model, text,
                system_prompt=system_prompt, terminology=terminology,
                timeout=timeout, options=options, response_format=response_format,
                thinking_mode=thinking_mode,
            )
            _persist_model_protocol(model, protocols[-1])
            if thinking_mode == "low" and stored_mode != "low":
                _persist_thinking_mode(model, "low")
            return output
        except Exception as exc:
            last_error = exc
            if (
                not explicitly_overridden
                and _is_explicit_protocol_mismatch(exc)
                and len(protocols) < len(SUPPORTED_PROTOCOLS)
            ):
                next_protocol = next(
                    candidate for candidate in SUPPORTED_PROTOCOLS
                    if candidate not in protocols
                )
                protocols.append(next_protocol)
            # The policy is deliberately bounded: one retry with the original
            # mode, then one retry with the lowest supported thinking mode.
            continue
    raise last_error or RuntimeError("Third-party API translation failed")


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
                retry_after = getattr(exc, "retry_after_seconds", None)
                delay = retry_after if retry_after is not None else 2 ** attempt
                time.sleep(max(0.0, float(delay)))
    raise last_error or RuntimeError("Third-party API translation failed")


__all__ = [
    "APIRequestError",
    "OPENCODE_GO_MODELS",
    "OPENCODE_GO_RESPONSES_MODELS",
    "connection_scope",
    "list_models",
    "model_protocol",
    "translate",
    "translate_once",
]
