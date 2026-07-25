from __future__ import annotations

from common.config_paths import ensure_portable_env_file


_BOOTSTRAP_RESULT: dict[str, object] | None = None


def initialize_runtime_environment() -> dict[str, object]:
    """Prepare persistent runtime configuration before settings are imported."""
    global _BOOTSTRAP_RESULT
    if _BOOTSTRAP_RESULT is None:
        _BOOTSTRAP_RESULT = ensure_portable_env_file()
    return dict(_BOOTSTRAP_RESULT)


__all__ = ["initialize_runtime_environment"]
