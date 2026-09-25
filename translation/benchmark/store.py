from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from copy import deepcopy
from typing import Any

from common.config_paths import runtime_model_benchmark_path
from translation.benchmark.suite import SUITE_VERSION
from translation.config import third_party_api_config


STORE_VERSION = 1
_LOCK = threading.Lock()


def _context_hash(provider: str, models: list[str]) -> str:
    api = third_party_api_config() if provider == "api" else {}
    secret = str(api.get("api_key") or "")
    resolved_protocols: dict[str, str] = {}
    if provider == "api":
        try:
            from translation.models.api_client import model_protocol

            resolved_protocols = {
                model: model_protocol(api, model.removeprefix("api:"))
                for model in models
            }
        except Exception:
            resolved_protocols = {}
    payload = {
        "provider": provider,
        "models": sorted(str(model) for model in models),
        "style": str(api.get("style") or ""),
        "base_url": str(api.get("base_url") or ""),
        "model_protocols": dict(api.get("model_protocols") or {}),
        "resolved_protocols": resolved_protocols,
        "secret_hash": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        "suite_version": SUITE_VERSION,
    }
    serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_benchmark() -> dict[str, Any]:
    path = runtime_model_benchmark_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {"version": STORE_VERSION, "result": None, "applied": None}
    if not isinstance(payload, dict):
        return {"version": STORE_VERSION, "result": None, "applied": None}
    result = payload.get("result")
    if isinstance(result, dict):
        provider = str(result.get("provider") or "api")
        models = [str(model) for model in result.get("models") or []]
        result["stale"] = result.get("context") != _context_hash(provider, models)
    return payload


def save_benchmark(result: dict[str, Any]) -> bool:
    stored = deepcopy(result)
    stored["context"] = _context_hash(
        str(stored.get("provider") or "api"),
        [str(model) for model in stored.get("models") or []],
    )
    with _LOCK:
        payload = load_benchmark()
        payload["version"] = STORE_VERSION
        payload["result"] = stored
        if not isinstance(payload.get("applied"), dict) or payload["applied"].get("context") != stored["context"]:
            payload["applied"] = None
        return _write(payload)


def apply_strategy(strategy: str) -> dict[str, str]:
    selected = str(strategy or "balanced").strip().lower()
    with _LOCK:
        payload = load_benchmark()
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("stale"):
            raise ValueError("Benchmark result is missing or stale")
        profiles = (result.get("recommendations") or {}).get("profiles") or {}
        if (result.get("recommendations") or {}).get("auto_applicable") is False:
            raise ValueError("No model passed the reliability gate")
        profile = profiles.get(selected)
        if not isinstance(profile, dict):
            raise ValueError(f"Unknown benchmark strategy: {selected}")
        payload["applied"] = {"strategy": selected, "profile": dict(profile), "context": result.get("context")}
        if not _write(payload):
            raise OSError("Unable to persist benchmark selection")
        return dict(profile)


def applied_profile() -> dict[str, str]:
    payload = load_benchmark()
    result = payload.get("result")
    applied = payload.get("applied")
    if not isinstance(result, dict) or result.get("stale") or not isinstance(applied, dict):
        return {}
    if applied.get("context") != result.get("context"):
        return {}
    profile = applied.get("profile")
    return dict(profile) if isinstance(profile, dict) else {}


def _write(payload: dict[str, Any]) -> bool:
    path = runtime_model_benchmark_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temp_path = stream.name
        os.replace(temp_path, path)
        return True
    except OSError:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        return False


__all__ = ["applied_profile", "apply_strategy", "load_benchmark", "save_benchmark"]
