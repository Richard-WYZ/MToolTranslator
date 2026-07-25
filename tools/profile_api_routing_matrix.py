from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_manual_trans_file import DEFAULT_FILE
from tools.profile_batch_throughput import run_profile
from translation.config import batch_translation_config


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _compact_parallel_result(result: dict[str, Any]) -> dict[str, Any]:
    parallel = result.get("parallel") or {}
    issue_counts = Counter(parallel.get("issue_counts", {}) or {})
    batch_errors = [
        str(batch.get("error"))
        for batch in parallel.get("batches", []) or []
        if batch.get("error")
    ]
    return {
        "model": result.get("model"),
        "candidate_filter": result.get("candidate_filter"),
        "protocol": result.get("protocol"),
        "workers": result.get("workers"),
        "batch_size": result.get("batch_size"),
        "max_batch_chars": result.get("max_batch_chars"),
        "items": sum(int(batch.get("items", 0) or 0) for batch in parallel.get("batches", []) or []),
        "elapsed_seconds": parallel.get("elapsed_seconds"),
        "items_per_second": parallel.get("items_per_second"),
        "estimated_full_minutes": parallel.get("estimated_full_minutes"),
        "error_batches": parallel.get("error_batches"),
        "error_types": dict(Counter(error.split(":", 1)[0] for error in batch_errors)),
        "refusal_count": parallel.get("refusal_count"),
        "issue_counts": dict(issue_counts),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    batch_cfg = batch_translation_config()
    parser = argparse.ArgumentParser(description="Run a small API routing matrix over model/protocol/text-class combinations.")
    parser.add_argument("--file", default=str(DEFAULT_FILE))
    parser.add_argument("--models", default="api:minimax-m3,api:qwen3.7-plus")
    parser.add_argument("--candidate-filters", default="short,non-short")
    parser.add_argument("--protocols", default="line,json")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--max-batch-chars", type=int, default=4000)
    parser.add_argument("--num-predict", type=int, default=int(batch_cfg.get("num_predict", 3072)))
    parser.add_argument("--timeout", type=int, default=int(batch_cfg.get("timeout", 180)))
    parser.add_argument("--issue-examples", type=int, default=0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    file_path = Path(args.file)
    results: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for candidate_filter in _split_csv(args.candidate_filters):
        for protocol in _split_csv(args.protocols):
            for model in _split_csv(args.models):
                result = run_profile(
                    file_path,
                    model,
                    offset=max(0, args.offset),
                    workers=max(1, args.workers),
                    batch_size=max(1, args.batch_size),
                    max_batch_chars=max(500, args.max_batch_chars),
                    protocol=protocol,
                    mode="parallel",
                    candidate_filter=candidate_filter,
                    issue_examples=max(0, args.issue_examples),
                    num_predict=max(64, args.num_predict),
                    timeout=max(10, args.timeout),
                )
                results.append(result)
                summary = _compact_parallel_result(result)
                summaries.append(summary)
                print(json.dumps({"event": "matrix_result", **summary}, ensure_ascii=False), flush=True)

    payload = {
        "file": str(file_path),
        "models": _split_csv(args.models),
        "candidate_filters": _split_csv(args.candidate_filters),
        "protocols": _split_csv(args.protocols),
        "offset": max(0, args.offset),
        "workers": max(1, args.workers),
        "batch_size": max(1, args.batch_size),
        "max_batch_chars": max(500, args.max_batch_chars),
        "num_predict": max(64, args.num_predict),
        "timeout": max(10, args.timeout),
        "summary": summaries,
        "results": results,
    }
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"event": "matrix_summary", "summary": summaries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
