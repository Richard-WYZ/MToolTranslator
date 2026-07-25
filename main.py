"""Compatibility entrypoint.

The application implementation now lives under ``app``. This module keeps
existing imports such as ``import main`` working while providing a thin runtime
entrypoint.
"""

from app.main import *  # noqa: F401,F403
from app.main import _apply_term_edit_to_outputs  # noqa: F401
from app.entrypoint import main as _run_app


if __name__ == "__main__":
    _run_app()
