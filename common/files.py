from __future__ import annotations

import shutil
from pathlib import Path


def detect_encoding(file_path: str) -> str:
    """Auto-detect text encoding using the project's current fallback order."""
    for encoding in ("utf-8", "shift-jis", "latin-1"):
        try:
            with open(file_path, "r", encoding=encoding) as handle:
                handle.read(4096)
            return encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "latin-1"


def is_path_inside(path: str | Path, root: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def apply_translated_output(translated_path_value: str | Path, original_path: str | Path) -> None:
    translated = Path(translated_path_value)
    original = Path(original_path)
    if not translated.is_file():
        return
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(translated), str(original))
    try:
        translated.unlink()
    except OSError:
        pass


__all__ = ["apply_translated_output", "detect_encoding", "is_path_inside"]
