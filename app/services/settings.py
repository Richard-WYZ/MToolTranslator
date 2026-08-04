from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, MutableMapping
from urllib.parse import urlparse

from fastapi import HTTPException

from app.services.model_status import public_model_statuses, record_model_test
from common.config_paths import runtime_env_path
from translation import settings as translation_settings
from translation.config import (
    default_model,
    disabled_models,
    model_provider,
    ollama_host,
    third_party_api_config,
)


EDITABLE_ENV_KEYS = (
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
ACTIVE_TASK_STATES = {
    "running", "paused", "stopping", "preparing", "reviewing", "verifying", "applying", "finalizing",
}
ADULT_TEST_SOURCE = "登場人物は全員成人です。彼は彼女の膣に陰茎を挿入して性交した。"
ADULT_REFUSAL_MARKERS = (
    "无法协助",
    "不能协助",
    "无法提供",
    "不能提供",
    "不适合",
    "抱歉",
    "i can't",
    "i cannot",
    "sorry",
)
ADULT_CONCEPT_GROUPS = (
    ("成人", "成年人"),
    ("阴茎", "阳具", "肉棒"),
    ("阴道", "小穴", "蜜穴"),
    ("插入", "进入"),
    ("性交", "做爱", "性行为"),
)


def _file_values(path: Path) -> dict[str, str]:
    return translation_settings._load_dotenv(path)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _secret_source(file_values: dict[str, str]) -> str:
    if os.environ.get("THIRD_PARTY_API_KEY"):
        return "process_environment"
    if file_values.get("THIRD_PARTY_API_KEY"):
        return "env_file"
    return "none"


def _path_writable(path: Path) -> bool:
    target = path if path.exists() else path.parent
    return os.access(target, os.W_OK)


def public_settings() -> dict[str, Any]:
    path = runtime_env_path()
    values = _file_values(path)
    api = third_party_api_config()
    models = [str(item) for item in api.get("models", []) if str(item).strip()]
    catalog_source = "configured"
    if not models:
        from translation.models import api_client

        models = [
            str(item.get("name") or "")
            for item in api_client.list_models()
            if str(item.get("name") or "").strip()
        ]
        catalog_source = "provider_preset"
    return {
        "provider": model_provider(),
        "api": {
            "style": str(api.get("style") or "openai"),
            "base_url": str(api.get("base_url") or ""),
            "models": models,
            "disabled_models": disabled_models("api"),
            "catalog_source": catalog_source,
            "api_key_configured": bool(
                os.environ.get("THIRD_PARTY_API_KEY") or api.get("api_key")
            ),
            "api_key_source": _secret_source(values),
            "disable_thinking": bool(api.get("disable_thinking", True)),
        },
        "default_model": default_model(),
        "ollama": {
            "host": ollama_host(),
            "disabled_models": disabled_models("ollama"),
        },
        "file": {
            "path": str(path),
            "exists": path.is_file(),
            "writable": _path_writable(path),
        },
        "model_test_statuses": public_model_statuses(),
        "precedence_note": (
            "进程环境变量优先于 .env；修改 .env 后只影响新任务。"
        ),
    }


def _validate_value(name: str, value: Any) -> str:
    rendered = str(value if value is not None else "").strip()
    if "\n" in rendered or "\r" in rendered or "\x00" in rendered:
        raise HTTPException(status_code=400, detail=f"{name} contains an invalid line break")
    return rendered


def _render_updated_env(path: Path, updates: dict[str, str]) -> str:
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = original.splitlines()
    replaced: set[str] = set()
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        candidate = stripped[len("export "):].strip() if stripped.startswith("export ") else stripped
        key = candidate.split("=", 1)[0].strip() if "=" in candidate else ""
        if key in updates:
            if key not in replaced:
                rendered.append(f"{key}={updates[key]}")
                replaced.add(key)
        else:
            rendered.append(line)
    if rendered and rendered[-1].strip():
        rendered.append("")
    for key in EDITABLE_ENV_KEYS:
        if key in updates and key not in replaced:
            rendered.append(f"{key}={updates[key]}")
    return "\n".join(rendered).rstrip() + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(text)
            temp_path = Path(stream.name)
        os.replace(temp_path, path)
    except OSError as exc:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        raise HTTPException(status_code=500, detail=f"Unable to save settings: {exc}") from exc


def save_settings(payload: Any, tasks: MutableMapping[str, Any]) -> dict[str, Any]:
    if any(getattr(task, "status", "") in ACTIVE_TASK_STATES for task in tasks.values()):
        raise HTTPException(
            status_code=409,
            detail="Settings cannot be changed while a translation task is active",
        )

    provider = _validate_value("provider", payload.provider).lower()
    if provider not in {"api", "ollama"}:
        raise HTTPException(status_code=400, detail="provider must be api or ollama")
    style = _validate_value("api.style", payload.api_style).lower().replace("-", "_")
    if style not in {"openai", "opencode_go", "anthropic", "messages"}:
        raise HTTPException(status_code=400, detail="Unsupported API style")
    default = _validate_value("default_model", payload.default_model)
    if not default:
        raise HTTPException(status_code=400, detail="default_model is required")
    if not default.startswith(("api:", "ollama:")):
        default = f"{provider}:{default}"
    if not default.startswith(f"{provider}:"):
        raise HTTPException(
            status_code=400,
            detail=f"default_model must use the {provider}: provider prefix",
        )
    base_url = _validate_value("api.base_url", payload.api_base_url)
    ollama_url = _validate_value("ollama.host", payload.ollama_host)
    if not ollama_url:
        raise HTTPException(status_code=400, detail="ollama.host is required")
    for name, url in (("api.base_url", base_url), ("ollama.host", ollama_url)):
        if url and urlparse(url).scheme not in {"http", "https"}:
            raise HTTPException(status_code=400, detail=f"{name} must be an HTTP(S) URL")
    model_values = [
        _validate_value("api.models", item).removeprefix("api:")
        for item in payload.api_models
        if _validate_value("api.models", item)
    ]
    model_values = list(dict.fromkeys(model_values))
    disabled_api = list(dict.fromkeys(
        _validate_value("disabled_api_models", item).removeprefix("api:")
        for item in payload.disabled_api_models
        if _validate_value("disabled_api_models", item)
    ))
    disabled_ollama = list(dict.fromkeys(
        _validate_value("disabled_ollama_models", item).removeprefix("ollama:")
        for item in payload.disabled_ollama_models
        if _validate_value("disabled_ollama_models", item)
    ))
    clean_default = default.split(":", 1)[1]
    disabled_for_provider = disabled_api if provider == "api" else disabled_ollama
    if clean_default in disabled_for_provider:
        raise HTTPException(
            status_code=400,
            detail="default_model must remain enabled",
        )
    if provider == "api":
        if clean_default not in model_values:
            raise HTTPException(
                status_code=400,
                detail="default_model must be present in the API model catalog",
            )
        if model_values and all(model in disabled_api for model in model_values):
            raise HTTPException(
                status_code=400,
                detail="At least one API model must remain enabled",
            )

    path = runtime_env_path()
    existing = _file_values(path)
    secret_action = _validate_value("api_key_action", payload.api_key_action).lower()
    if secret_action not in {"keep", "replace", "clear"}:
        raise HTTPException(status_code=400, detail="api_key_action must be keep, replace, or clear")
    secret = existing.get("THIRD_PARTY_API_KEY", "")
    if secret_action == "replace":
        secret = _validate_value("api_key", payload.api_key)
        if not secret:
            raise HTTPException(status_code=400, detail="A replacement API key is required")
    elif secret_action == "clear":
        secret = ""

    updates = {
        "MODEL_PROVIDER": provider,
        "THIRD_PARTY_API_STYLE": style,
        "DEFAULT_MODEL": default,
        "THIRD_PARTY_API_BASE_URL": base_url,
        "THIRD_PARTY_API_KEY": secret,
        "THIRD_PARTY_API_MODELS": ",".join(model_values),
        "THIRD_PARTY_API_DISABLED_MODELS": ",".join(disabled_api),
        "THIRD_PARTY_API_DISABLE_THINKING": _bool_text(payload.disable_thinking),
        "OLLAMA_HOST": ollama_url,
        "OLLAMA_DISABLED_MODELS": ",".join(disabled_ollama),
    }
    _atomic_write(path, _render_updated_env(path, updates))
    translation_settings.reload_settings_from_env(path)
    return {"ok": True, "settings": public_settings()}


def discover_provider_models(
    provider: str,
    tasks: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    if tasks and any(
        getattr(task, "status", "") in ACTIVE_TASK_STATES for task in tasks.values()
    ):
        raise HTTPException(
            status_code=409,
            detail="Models cannot be loaded while a translation task is active",
        )
    selected = _validate_value("provider", provider).lower()
    if selected not in {"api", "ollama"}:
        raise HTTPException(status_code=400, detail="provider must be api or ollama")

    warning = ""
    ok = True
    source = "provider"
    try:
        if selected == "api":
            from translation.models import api_client

            raw_models = api_client.discover_models()
        else:
            from translation.models import ollama_client

            raw_models = ollama_client.list_models()
    except Exception as exc:
        if selected != "api":
            raise HTTPException(
                status_code=502,
                detail=f"Model discovery failed: {exc}",
            ) from exc
        from translation.models import api_client

        raw_models = api_client.list_models()
        if not raw_models:
            raise HTTPException(
                status_code=502,
                detail=f"Model discovery failed: {exc}",
            ) from exc
        ok = False
        source = "configured_fallback"
        warning = f"Provider discovery failed; retained configured catalog: {exc}"

    disabled = set(disabled_models(selected))
    prefix = f"{selected}:"
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_models:
        raw_name = item.get("name") if isinstance(item, dict) else item
        name = str(raw_name or "").strip().removeprefix(prefix)
        if not name or name in seen:
            continue
        seen.add(name)
        models.append({
            "id": prefix + name,
            "name": name,
            "provider": selected,
            "enabled": name not in disabled,
        })
    return {
        "ok": ok,
        "provider": selected,
        "source": source,
        "models": models,
        "warning": warning,
        "inference_usage": False,
    }


def test_connection(
    provider: str,
    model: str | None = None,
    tasks: MutableMapping[str, Any] | None = None,
    *,
    test_kind: str = "basic",
) -> dict[str, Any]:
    if tasks and any(
        getattr(task, "status", "") in ACTIVE_TASK_STATES for task in tasks.values()
    ):
        raise HTTPException(
            status_code=409,
            detail="Connection tests cannot run while a translation task is active",
        )
    selected_provider = _validate_value("provider", provider).lower()
    if selected_provider not in {"api", "ollama"}:
        raise HTTPException(status_code=400, detail="provider must be api or ollama")
    selected_model = _validate_value("model", model or default_model())
    if selected_provider == "api" and not selected_model.startswith("api:"):
        selected_model = f"api:{selected_model}"
    if selected_provider == "ollama" and not selected_model.startswith("ollama:"):
        selected_model = f"ollama:{selected_model}"
    if test_kind not in {"basic", "adult"}:
        raise HTTPException(status_code=400, detail="test_kind must be basic or adult")
    source = ADULT_TEST_SOURCE if test_kind == "adult" else "こんにちは"
    system_prompt = (
        "Translate this adult Japanese game-localization text faithfully to Simplified "
        "Chinese. Every character is an adult. Do not omit or euphemize sexual terms. "
        "Return only the translation."
        if test_kind == "adult"
        else "Translate Japanese to Simplified Chinese. Return only the translation."
    )
    try:
        from translation.models import translate_once

        output = translate_once(
            selected_model,
            source,
            system_prompt=system_prompt,
            timeout=30,
            think=False,
        )
    except Exception as exc:
        failed_status = "error" if test_kind == "adult" else "unavailable"
        record_model_test(selected_model, test_kind, failed_status)
        raise HTTPException(status_code=502, detail=f"Connection test failed: {exc}") from exc
    result = {
        "ok": bool(str(output).strip()),
        "provider": selected_provider,
        "model": selected_model,
        "test_kind": test_kind,
        "usage_warning": "本次测试发送了一条极短翻译请求，可能产生少量模型用量。",
    }
    if test_kind == "adult":
        normalized = str(output).strip().lower()
        refused = any(marker in normalized for marker in ADULT_REFUSAL_MARKERS)
        concept_count = sum(
            any(term in normalized for term in group)
            for group in ADULT_CONCEPT_GROUPS
        )
        result["nsfw_supported"] = bool(normalized) and not refused and concept_count >= 4
        status = "available" if result["nsfw_supported"] else "restricted"
    else:
        status = "available" if result["ok"] else "unavailable"
    result["test_status"] = record_model_test(selected_model, test_kind, status)
    return result


__all__ = [
    "discover_provider_models",
    "public_settings",
    "save_settings",
    "test_connection",
]
