from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.config_paths import runtime_model_status_path
from translation.config import ollama_host, third_party_api_config


_LOCK = threading.Lock()
_VERSION = 1


def _empty_store() -> dict[str, Any]:
    return {"version": _VERSION, "models": {}}


def _read_store() -> dict[str, Any]:
    path = runtime_model_status_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _empty_store()
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), dict):
        return _empty_store()
    return payload


def _write_store(payload: dict[str, Any]) -> bool:
    path = runtime_model_status_path()
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
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temp_path = Path(stream.name)
        os.replace(temp_path, path)
        return True
    except OSError:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        return False


def _provider_context(provider: str) -> str:
    if provider == "api":
        config = third_party_api_config()
        secret_hash = hashlib.sha256(
            str(config.get("api_key") or "").encode("utf-8")
        ).hexdigest()
        values = {
            "provider": "api",
            "style": str(config.get("style") or ""),
            "base_url": str(config.get("base_url") or ""),
            "secret_hash": secret_hash,
        }
    else:
        values = {"provider": "ollama", "host": ollama_host()}
    serialized = json.dumps(values, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _public_record(
    model_id: str,
    record: dict[str, Any],
    contexts: dict[str, str] | None = None,
) -> dict[str, Any]:
    provider = model_id.split(":", 1)[0]
    current_context = (contexts or {}).get(provider) or _provider_context(provider)
    return {
        "status": str(record.get("status") or "untested"),
        "tested_at": str(record.get("tested_at") or ""),
        "stale": record.get("context") != current_context,
    }


def public_model_statuses() -> dict[str, dict[str, dict[str, Any]]]:
    payload = _read_store()
    contexts = {
        "api": _provider_context("api"),
        "ollama": _provider_context("ollama"),
    }
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for model_id, tests in payload["models"].items():
        if not isinstance(model_id, str) or not isinstance(tests, dict):
            continue
        result[model_id] = {}
        for test_kind in ("basic", "adult"):
            record = tests.get(test_kind)
            if isinstance(record, dict):
                result[model_id][test_kind] = _public_record(
                    model_id, record, contexts
                )
    return result


def record_model_test(model_id: str, test_kind: str, status: str) -> dict[str, Any]:
    provider = model_id.split(":", 1)[0]
    record = {
        "status": status,
        "tested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "context": _provider_context(provider),
    }
    with _LOCK:
        payload = _read_store()
        models = payload.setdefault("models", {})
        tests = models.setdefault(model_id, {})
        tests[test_kind] = record
        persisted = _write_store(payload)
    public = _public_record(model_id, record)
    public["persisted"] = persisted
    return public


__all__ = ["public_model_statuses", "record_model_test"]
