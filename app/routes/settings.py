from __future__ import annotations

from typing import Any, MutableMapping

from fastapi import APIRouter

from app.schemas import (
    SettingsConnectionTestRequest,
    SettingsModelDiscoveryRequest,
    SettingsUpdateRequest,
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

    @router.get("/api/settings")
    def get_settings():
        return public_settings()

    @router.put("/api/settings")
    def update_settings(req: SettingsUpdateRequest):
        return save_settings(req, all_tasks())

    @router.post("/api/settings/connection-test")
    def connection_test(req: SettingsConnectionTestRequest):
        return test_connection(req.provider, req.model, all_tasks(), test_kind=req.test_kind)

    @router.post("/api/settings/models/discover")
    def discover_models(req: SettingsModelDiscoveryRequest):
        return discover_provider_models(req.provider, all_tasks())

    return router


__all__ = ["create_router"]
