from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class TranslationRequest:
    file_path: str
    output_path: str | None = None
    model: str | None = None
    prompt_style: str = "professional"
    translate_columns: list[int] | None = None
    task_id: str = ""
    progress_callback: ProgressCallback | None = None
    glossary_path: str | None = None
    batch_config_override: dict[str, Any] | None = None


@dataclass(slots=True)
class TranslationResult:
    file_path: str
    output_path: str | None
    items: list[tuple[Any, Any]] = field(default_factory=list)
    status: str = "completed"
    review_summary: dict[str, int] = field(default_factory=dict)
    review_report_path: str | None = None


@dataclass(slots=True)
class TranslationWorkflowContext:
    request: TranslationRequest
    result: TranslationResult | None = None
    analysis: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
