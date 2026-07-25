from __future__ import annotations

import sys
from translation.models import ollama_client as _ollama_client

sys.modules[__name__] = _ollama_client