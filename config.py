"""Compatibility alias for translation settings."""

from __future__ import annotations

import sys

from translation import settings as _settings


sys.modules[__name__] = _settings
