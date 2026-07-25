"""Compatibility wrapper for older imports.

New code should import application objects from ``app.main``.
"""

from app.main import *  # noqa: F401,F403
from app.main import _apply_term_edit_to_outputs  # noqa: F401


if __name__ == "__main__":
    from app.entrypoint import main

    main()
