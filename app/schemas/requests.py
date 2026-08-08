from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class GlossaryTermRequest(BaseModel):
    source: str
    target: str


class DynamicGlossaryRequest(BaseModel):
    file_path: str
    source: str
    target: str = ""


class PromoteGlossaryRequest(BaseModel):
    file_path: str
    source: str
    target: Optional[str] = None


class TranslateStartRequest(BaseModel):
    file_path: str
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_style: str = "professional"
    execution_profile: str = "quality_first"
    profile_options: Dict[str, Any] = {}


class PreflightRequest(BaseModel):
    file_path: str
    model: Optional[str] = None
    provider: Optional[str] = None
    execution_profile: str = "quality_first"
    profile_options: Dict[str, Any] = {}


class RecoveryResumeRequest(BaseModel):
    file_path: str
    model: Optional[str] = None
    prompt_style: str = "professional"
    execution_profile: Optional[str] = None
    profile_options: Dict[str, Any] = {}


class CleanupRequest(BaseModel):
    file_path: str
    task_id: Optional[str] = None
    fast: bool = False


class BatchTranslateStartRequest(BaseModel):
    file_paths: List[str]
    model: str
    prompt_style: str = "professional"
    translate_columns: List[int] = [0, 1]


class TranslateRequest(BaseModel):
    text: str
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_style: Optional[str] = "professional"


class SettingsUpdateRequest(BaseModel):
    provider: str
    api_style: str = "opencode_go"
    api_base_url: str = ""
    api_models: List[str] = []
    disabled_api_models: List[str] = []
    disabled_ollama_models: List[str] = []
    default_model: str
    disable_thinking: bool = True
    ollama_host: str = "http://localhost:11434"
    api_key_action: str = "keep"
    api_key: str = ""


class SettingsConnectionTestRequest(BaseModel):
    provider: str
    model: Optional[str] = None
    test_kind: Literal["basic", "adult"] = "basic"


class SettingsModelDiscoveryRequest(BaseModel):
    provider: str


class ColumnMapping(BaseModel):
    column_index: int
    action: str


class ExportRequest(BaseModel):
    session_id: str
    file_path: str
    file_type: str = "json"
    column_mappings: List[ColumnMapping] = []
    output_dir: Optional[str] = None
    output_path: Optional[str] = None


class ReviewSaveRequest(BaseModel):
    file_path: str
    row: int
    col: int
    text: str
    action: str = "accept"


class ReviewBatchSaveRequest(BaseModel):
    file_path: str
    edits: List[Dict[str, Any]]


class AIReviewRequest(BaseModel):
    file_path: str
    scope: Literal["required", "all", "selected", "filter"] = "all"
    rows: List[int] = Field(default_factory=list)
    filter: str = "issues"
    review_model: Optional[str] = "auto"
    verifier_model: Optional[str] = "auto"
    sensitive_model: Optional[str] = "auto"
    auto_apply: bool = True


class AIReviewActionRequest(BaseModel):
    file_path: str
