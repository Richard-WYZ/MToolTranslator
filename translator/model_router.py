"""Compatibility exports for the migrated translation model router."""

from translation.models.router import (
    API_PREFIX,
    get_system_prompts,
    list_models,
    model_configuration,
    translate,
    translate_once,
)

__all__ = [
    "API_PREFIX",
    "get_system_prompts",
    "list_models",
    "model_configuration",
    "translate",
    "translate_once",
]
