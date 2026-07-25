from __future__ import annotations

import sys
from pathlib import Path

from common.paths import runtime_base_dir


PORTABLE_ENV_TEMPLATE = """# MTool 汉化工具 portable configuration
# This file is safe to edit in the application or with a text editor.

MODEL_PROVIDER=api
THIRD_PARTY_API_STYLE=opencode_go
DEFAULT_MODEL=api:qwen3.7-plus
THIRD_PARTY_API_BASE_URL=
THIRD_PARTY_API_KEY=
THIRD_PARTY_API_MODELS=
THIRD_PARTY_API_DISABLED_MODELS=
THIRD_PARTY_API_DISABLE_THINKING=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_DISABLED_MODELS=
"""


def runtime_env_path() -> Path:
    """Return the user-editable environment file used by the running app."""
    return runtime_base_dir() / ".env"


def runtime_model_status_path() -> Path:
    """Return the portable, non-secret model test history file."""
    return runtime_base_dir() / ".model-status.json"


def ensure_portable_env_file() -> dict[str, object]:
    """Create a credential-empty .env next to a frozen executable if missing."""
    path = runtime_env_path()
    if not getattr(sys, "frozen", False):
        return {"path": str(path), "created": False, "skipped": "development"}
    if path.exists():
        return {"path": str(path), "created": False, "skipped": "exists"}
    try:
        path.write_text(PORTABLE_ENV_TEMPLATE, encoding="utf-8", newline="\n")
    except OSError as exc:
        return {
            "path": str(path),
            "created": False,
            "error": str(exc),
        }
    return {"path": str(path), "created": True}


__all__ = [
    "PORTABLE_ENV_TEMPLATE",
    "ensure_portable_env_file",
    "runtime_env_path",
    "runtime_model_status_path",
]
