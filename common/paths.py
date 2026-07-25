from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_path(*parts: str) -> Path:
    """Return an absolute path inside the project root."""
    return PROJECT_ROOT.joinpath(*parts)


def runtime_base_dir() -> Path:
    """Return the persistent runtime data directory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def bundled_base_dir() -> Path:
    """Return the directory that contains bundled application assets."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return PROJECT_ROOT


def upload_dir() -> Path:
    """Return the directory used for imported/uploaded files."""
    return runtime_base_dir() / "tmp_uploads"


__all__ = ["PROJECT_ROOT", "bundled_base_dir", "project_path", "runtime_base_dir", "upload_dir"]
