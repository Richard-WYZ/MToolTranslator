from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Callable, Iterable

from translation.config import (
    default_model,
    default_system_prompt,
    fallback_chunk_strategy,
    fallback_models as configured_fallback_models,
    fallback_prompt_names,
    system_prompts,
)
from translation.models.router import translate
from translation.quality.refusal import is_refusal


def chunk_translate(
    model: str,
    text: str,
    system_prompt: str,
    max_chars: int = 50,
    overlap: int = 10,
    terminology=None,
    translator: Callable[..., str] | None = None,
) -> str:
    """Translate long text by sentence-aware chunks, preserving source chunks on local failure."""
    if translator is None:
        translator = translate
    if not text:
        return ""

    if len(text) <= max_chars:
        return translator(model, text, system_prompt=system_prompt, terminology=terminology)

    sentence_boundaries = re.compile(r"([。！？.!?；\n]+)")
    parts = sentence_boundaries.split(text)

    sentences: list[str] = []
    i = 0
    while i < len(parts):
        sentence = parts[i]
        if i + 1 < len(parts) and sentence_boundaries.match(parts[i + 1]):
            sentence += parts[i + 1]
            i += 2
        else:
            i += 1
        if sentence:
            sentences.append(sentence)

    chunks: list[str] = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) <= max_chars:
            current_chunk += sentence
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(sentence) > max_chars:
                start = 0
                while start < len(sentence):
                    end = min(start + max_chars, len(sentence))
                    chunks.append(sentence[start:end])
                    start = end - overlap if end < len(sentence) else end
            else:
                current_chunk = sentence
    if current_chunk:
        chunks.append(current_chunk)

    translated_parts: list[str] = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        try:
            result = translator(
                model,
                chunk,
                system_prompt=system_prompt,
                terminology=terminology,
            )
            if is_refusal(result):
                if len(chunk) > max_chars // 2:
                    sub_result = chunk_translate(
                        model,
                        chunk,
                        system_prompt,
                        max_chars=max_chars // 2,
                        overlap=overlap // 2 if overlap > 1 else 0,
                        terminology=terminology,
                        translator=translator,
                    )
                    if not is_refusal(sub_result):
                        translated_parts.append(sub_result)
                        continue
                translated_parts.append(chunk)
                continue
            translated_parts.append(result)
        except Exception:
            translated_parts.append(chunk)

    return "".join(translated_parts)


def log_retry_stats(file_path: str, stats: dict) -> None:
    log_dir = os.path.join(".omo", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "refusal_retries.jsonl")

    if "timestamp" not in stats or not stats["timestamp"]:
        stats["timestamp"] = datetime.now(timezone.utc).isoformat()

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(stats, ensure_ascii=False) + "\n")
    except OSError:
        return


def retry_with_fallback(
    text: str,
    model: str | None = None,
    system_prompt: str | None = None,
    terminology=None,
    translator: Callable[..., str] | None = None,
    fallback_models: Iterable[str] | None = None,
    attempt: int = 0,
    file_path: str = "",
    row: int = 0,
    col: int = 0,
) -> dict:
    """Retry translation through prompt/model fallback layers."""
    if translator is None:
        translator = translate

    prompts_cfg = system_prompts()
    primary = model or default_model()
    professional_prompt = system_prompt or default_system_prompt("professional")
    uncensored_prompt = prompts_cfg.get("uncensored", professional_prompt)

    prompts: list[str] = [professional_prompt]
    for prompt_name in fallback_prompt_names():
        prompt_text = prompts_cfg.get(prompt_name)
        if prompt_text and prompt_text not in prompts:
            prompts.append(prompt_text)
    if uncensored_prompt not in prompts:
        prompts.append(uncensored_prompt)

    models: list[str] = [primary]
    for fallback in fallback_models or configured_fallback_models():
        if fallback not in models:
            models.append(fallback)

    strategies: list[str] = []
    attempts_made = 0
    max_attempts = max(3, len(prompts) + max(0, len(models) - 1) + 1)

    for prompt in prompts:
        if attempts_made >= max_attempts:
            break
        try:
            result = translator(
                primary,
                text,
                system_prompt=prompt,
                terminology=terminology,
            )
            attempts_made += 1
            strategies.append(f"prompt_switch:{prompt[:20]}...")
            if result and not is_refusal(result, original=text):
                return {"status": "SUCCESS", "translation": result}
        except Exception:
            attempts_made += 1
            strategies.append(f"prompt_switch_error:{prompt[:20]}...")

    for fallback_model in models[1:]:
        if attempts_made >= max_attempts:
            break
        try:
            result = translator(
                fallback_model,
                text,
                system_prompt=professional_prompt,
                terminology=terminology,
            )
            attempts_made += 1
            strategies.append(f"model_switch:{fallback_model}")
            if result and not is_refusal(result, original=text):
                return {"status": "SUCCESS", "translation": result}
        except Exception:
            attempts_made += 1
            strategies.append(f"model_switch_error:{fallback_model}")

    if attempts_made < max_attempts:
        chunk_strategy = fallback_chunk_strategy()
        max_chars = chunk_strategy.get("max_chars", 50)
        overlap = chunk_strategy.get("overlap", 10)
        try:
            result = chunk_translate(
                primary,
                text,
                professional_prompt,
                max_chars=max_chars,
                overlap=overlap,
                terminology=terminology,
                translator=translator,
            )
            attempts_made += 1
            strategies.append("chunk_translate")
            if result and not is_refusal(result, original=text):
                return {"status": "SUCCESS", "translation": result}
        except Exception:
            attempts_made += 1
            strategies.append("chunk_translate_error")

    if file_path:
        log_retry_stats(
            file_path,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "file": file_path,
                "row": row,
                "col": col,
                "attempts": attempts_made,
                "strategies": strategies,
                "final_status": "NEEDS_REVIEW",
            },
        )

    return {
        "status": "NEEDS_REVIEW",
        "original": text,
        "reason": "max_retries_exceeded",
    }


__all__ = ["chunk_translate", "log_retry_stats", "retry_with_fallback"]
