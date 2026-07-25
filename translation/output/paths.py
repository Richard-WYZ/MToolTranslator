from __future__ import annotations

import os


def default_output_path(file_path: str) -> str:
    """Return the default translated JSON output path for a source file."""
    root, ext = os.path.splitext(file_path)
    return f"{root}.translated{ext or '.json'}"


__all__ = ["default_output_path"]
