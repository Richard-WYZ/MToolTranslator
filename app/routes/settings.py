from __future__ import annotations

from typing import Any, MutableMapping

from fastapi import APIRouter

from app.schemas import (
    SettingsBenchmarkApplyRequest,
    SettingsBenchmarkStartRequest,
    SettingsConnectionTestRequest,
    SettingsModelDiscoveryRequest,
    SettingsUpdateRequest,
)
from app.services.model_benchmark import (
    apply_benchmark_strategy,
    benchmark_active,
    benchmark_status,
    cancel_benchmark,
    start_benchmark,
)
from app.services.settings import (
    discover_provider_models,
    public_settings,
    save_settings,
    test_connection,
)


def create_router(*, tasks: MutableMapping[str, Any], ai_review_tasks: MutableMapping[str, Any] | None = None) -> APIRouter:
    router = APIRouter()

    def all_tasks() -> dict[str, Any]:
        return {**tasks, **(ai_review_tasks or {})}

    def require_benchmark_idle() -> None:
        if benchmark_active():
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail="Wait for the model benchmark to finish")

    @router.get("/api/settings")
    def get_settings():
        return public_settings()

    @router.put("/api/settings")
    def update_settings(req: SettingsUpdateRequest):
        require_benchmark_idle()
        return save_settings(req, all_tasks())

    @router.post("/api/settings/connection-test")
    def connection_test(req: SettingsConnectionTestRequest):
        require_benchmark_idle()
        return test_connection(req.provider, req.model, all_tasks(), test_kind=req.test_kind)

    @router.post("/api/settings/models/discover")
    def discover_models(req: SettingsModelDiscoveryRequest):
        require_benchmark_idle()
        return discover_provider_models(req.provider, all_tasks())

    @router.get("/api/settings/benchmark")
    def get_benchmark():
        return benchmark_status()

    @router.post("/api/settings/benchmark/start")
    def begin_benchmark(req: SettingsBenchmarkStartRequest):
        return start_benchmark(req.provider, req.models, req.mode, all_tasks())

    @router.post("/api/settings/benchmark/cancel")
    def stop_benchmark():
        return cancel_benchmark()

    @router.post("/api/settings/benchmark/apply")
    def apply_benchmark(req: SettingsBenchmarkApplyRequest):
        return apply_benchmark_strategy(req.strategy, all_tasks())

    return router


__all__ = ["create_router"]
