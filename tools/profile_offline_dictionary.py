from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation.analysis import collect_model_bound_texts as collect_mtool_model_bound_texts
from translation.classification import looks_like_short_label
from translation.diagnostics import build_diagnostic_pipeline, diagnostic_glossary
from translation.terminology import (
    SudachiProvider,
    YomitanDictionary,
    summarize_sudachi_candidates,
    summarize_yomitan_matches,
)


DEFAULT_FILE = ROOT / "test_work" / "ManualTransFile.json"


def collect_model_bound_texts(file_path: Path, limit: int = 0) -> list[str]:
    pipeline = build_diagnostic_pipeline()
    glossary = diagnostic_glossary(pipeline)
    return collect_mtool_model_bound_texts(file_path, glossary=glossary, limit=limit)


def profile_file(file_path: Path, yomitan_paths: list[Path], limit: int = 0) -> dict[str, Any]:
    texts = collect_model_bound_texts(file_path, limit=limit)
    short_texts = [text for text in texts if looks_like_short_label(text)]
    sudachi = SudachiProvider()
    yomitan = YomitanDictionary(yomitan_paths)

    sudachi_summary = summarize_sudachi_candidates(texts, sudachi)
    yomitan_summary = summarize_yomitan_matches(texts, yomitan)
    return {
        "file": str(file_path),
        "model_bound_texts": len(texts),
        "short_model_bound_texts": len(short_texts),
        "limit": limit,
        "sudachi": sudachi_summary,
        "yomitan": yomitan_summary,
        "estimated_model_call_reduction": {
            "safe_deterministic": 0,
            "exact_dictionary_candidates": int(yomitan_summary.get("exact_matches", 0)),
            "notes": (
                "Sudachi tokens are terminology evidence only. "
                "Exact dictionary matches are candidates, not safe deterministic translations, "
                "because dictionary glosses can be context-sensitive, explanatory, or not Simplified Chinese."
            ),
        },
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Profile optional offline dictionary coverage on translation inputs.")
    parser.add_argument("--file", default=str(DEFAULT_FILE))
    parser.add_argument("--limit", type=int, default=0, help="Limit model-bound texts for a quick sample; 0 means full file.")
    parser.add_argument(
        "--yomitan-zip",
        action="append",
        default=[],
        help="Path to a local Yomitan .zip dictionary. Can be passed multiple times.",
    )
    args = parser.parse_args()

    result = profile_file(
        Path(args.file),
        [Path(path) for path in args.yomitan_zip],
        limit=max(0, args.limit),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
