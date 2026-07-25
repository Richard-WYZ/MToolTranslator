from __future__ import annotations

import sys

import uvicorn

from app.main import app


def main() -> None:
    """Run the application using the same CLI behavior as the legacy entry."""
    if "--desktop" in sys.argv or getattr(sys, "frozen", False):
        from app.desktop import run_desktop

        run_desktop(app)
        return
    uvicorn.run(app, host="127.0.0.1", port=8000)


__all__ = ["app", "main"]
