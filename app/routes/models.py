from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from app.schemas import PreflightRequest, TranslateRequest
from app.services.files import require_mtool_json_file
from app.services.models import available_models, system_prompts, translate_text
from app.services.runtime_profiles import public_runtime_configuration, resolve_execution_profile
from translation import checkpoint
from translation.analysis import classify_mtool_file


router = APIRouter()


@router.get("/api/models")
def get_models():
    try:
        models = available_models()
        runtime = public_runtime_configuration(models)
        enriched = []
        for item in models:
            model = dict(item)
            name = str(model.get("name") or "")
            provider = str(model.get("provider") or ("api" if name.startswith("api:") else "ollama"))
            model["id"] = name
            model["provider"] = provider
            model["display_name"] = name.split(":", 1)[-1]
            model["availability"] = (
                runtime["providers"]["api"]["health"]
                if provider == "api"
                else runtime["providers"]["ollama"]["health"]
            )
            enriched.append(model)
        return {"models": enriched, "runtime": runtime}
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/translate")
def api_translate(req: TranslateRequest):
    """Single-cell translation legacy endpoint."""
    try:
        return {"status": "ok", "translation": translate_text(req.text, req.model, req.provider, req.prompt_style)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/prompts")
def get_prompts():
    return {"prompts": system_prompts()}


@router.post("/api/preflight")
def preflight(req: PreflightRequest):
    if not os.path.isfile(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")
    require_mtool_json_file(req.file_path)
    selected_model, batch_cfg, profile = resolve_execution_profile(
        req.execution_profile,
        req.model,
        req.provider,
        req.profile_options,
    )
    analysis = classify_mtool_file(req.file_path)
    deterministic = int(analysis.get("classes", {}).get("deterministic", 0) or 0)
    model_bound = sum(
        int(count or 0)
        for key, count in analysis.get("classes", {}).items()
        if key.endswith("_model")
    )
    checkpoint_data = checkpoint.load_checkpoint(req.file_path)
    checkpoint_stats = checkpoint_data.get("stats", {}) if checkpoint_data.get("version") == 2 else {}
    checkpoint_model = str(checkpoint_data.get("model") or "")
    checkpoint_model_match = bool(checkpoint_stats) and checkpoint_model == selected_model
    warnings = []
    runtime = public_runtime_configuration(available_models())
    provider_name = "ollama" if selected_model.startswith("ollama:") else "api"
    provider_status = runtime["providers"][provider_name]
    if not provider_status.get("configured"):
        warnings.append("所选提供方当前不可用或配置不完整。")
    if provider_name == "api":
        warnings.append("API 模型只验证了配置状态；预检不会发送推理请求，也不会产生模型用量。")
    if req.execution_profile == "custom":
        warnings.append("自定义参数未经过完整基准验证，请在正式翻译前先用样本测试。")
    if checkpoint_stats and not checkpoint_model_match:
        warnings.append(
            f"现有断点使用 {checkpoint_model or '未知模型'}；当前任务使用 {selected_model}，"
            "旧条目会按检查点契约重新校验，不能保证直接复用。"
        )
    return {
        "ok": not any("不可用" in warning for warning in warnings),
        "file": {
            "path": req.file_path,
            "total_entries": int(analysis.get("total_items", 0) or 0),
            "nonempty_entries": int(analysis.get("nonempty", 0) or 0),
            "model_bound_entries": model_bound,
            "deterministic_or_preserved_entries": max(0, int(analysis.get("total_items", 0) or 0) - model_bound),
            "classes": analysis.get("classes", {}),
        },
        "profile": profile,
        "checkpoint": {
            "found": bool(checkpoint_stats),
            "completed": int(checkpoint_stats.get("completed", 0) or 0),
            "total": int(checkpoint_stats.get("total", 0) or 0),
            "saved_model": checkpoint_model,
            "model_match": checkpoint_model_match,
            "reuse_status": "validation_pending" if checkpoint_model_match else "model_mismatch",
        },
        "effective_batch": {
            "concurrency": profile["concurrency"],
            "batch_size": profile["batch_size"],
            "max_batch_chars": profile["max_batch_chars"],
            "protocol": profile["protocol"],
        },
        "warnings": warnings,
    }


__all__ = ["router"]
