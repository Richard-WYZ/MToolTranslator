from __future__ import annotations

import time
from typing import Callable


def check_control_flags(
    *,
    is_cancelled: Callable[[], bool],
    is_paused: Callable[[], bool],
    cancelled_factory: Callable[[], Exception],
    sleep_seconds: float = 0.2,
) -> None:
    """Raise on cancellation and wait while paused."""
    if is_cancelled():
        raise cancelled_factory()
    while is_paused():
        if is_cancelled():
            raise cancelled_factory()
        time.sleep(sleep_seconds)


__all__ = ["check_control_flags"]
