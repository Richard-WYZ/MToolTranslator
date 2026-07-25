from __future__ import annotations

from translation.config import default_model
from translation.models import get_system_prompts, list_models, translate as model_translate


def available_models() -> list[dict]:
    return list_models()


def system_prompts() -> dict[str, str]:
    return get_system_prompts()


def translate_text(text: str, model: str | None = None, provider: str | None = None, prompt_style: str | None = "professional") -> str:
    prompts = get_system_prompts()
    system_prompt = prompts.get(prompt_style or "professional", "")
    selected_model = model or default_model()
    if (provider or "").lower() == "api" and not selected_model.startswith("api:"):
        selected_model = "api:" + selected_model
    return model_translate(selected_model, text, system_prompt=system_prompt)
