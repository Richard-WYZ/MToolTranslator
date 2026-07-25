from __future__ import annotations

import sys
from translation.pollution import detector as _detector

sys.modules[__name__] = _detector