from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


def create_router(*, bundle_dir: str | Path) -> APIRouter:
    router = APIRouter()
    root_dir = Path(bundle_dir)

    @router.get("/", response_class=HTMLResponse)
    def read_root():
        html_path = root_dir / "ui" / "templates" / "index.html"
        return html_path.read_text(encoding="utf-8")

    @router.get("/health")
    async def health_check():
        return {"status": "ok", "app": "MTool 汉化工具"}

    return router


__all__ = ["create_router"]
