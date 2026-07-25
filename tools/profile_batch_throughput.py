from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_manual_trans_file import BENCHMARK_GLOSSARY_PATH, DEFAULT_FILE, classify_texts, collect_model_candidates
from translation.batching import BatchTranslationError, translate_batch, translate_line_batch
from translation.config import batch_translation_config, default_model
from translation.diagnostics import build_diagnostic_pipeline, diagnostic_batch_translator, finish_diagnostic_batch_translation
from translation.quality import is_refusal


def translate_candidate_batch(
    file_path: Path,
    model: str,
    offset: int,
    batch_size: int,
    max_batch_chars: int,
    options: dict[str, Any],
    protocol: str,
    candidate_filter: str = "all",
    issue_examples: int = 0,
) -> dict[str, Any]:
    candidates = collect_model_candidates(
        file_path,
        limit=batch_size,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        offset=offset,
        candidate_filter=candidate_filter,
    )
    pipeline = build_diagnostic_pipeline(model=model, glossary_path=str(BENCHMARK_GLOSSARY_PATH))
    started = time.perf_counter()
    try:
        batch_translator = translate_line_batch if protocol == "line" else translate_batch
        parsed = batch_translator(model, candidates, translator=diagnostic_batch_translator(pipeline), options=options)
    except (BatchTranslationError, Exception) as exc:
        elapsed = time.perf_counter() - started
        return {
            "offset": offset,
            "items": len(candidates),
            "elapsed_seconds": round(elapsed, 3),
            "items_per_second": round(len(candidates) / elapsed, 3) if elapsed else 0,
            "error": type(exc).__name__ + ": " + str(exc),
        }
    elapsed = time.perf_counter() - started

    issue_counts = Counter()
    examples: list[dict[str, Any]] = []
    refusal_count = 0
    for candidate in candidates:
        raw = parsed[candidate["i"]]
        if is_refusal(raw, original=candidate["protected"]):
            refusal_count += 1
        translated, _status, issues = finish_diagnostic_batch_translation(pipeline, candidate, raw)
        for issue in issues:
            issue_counts[issue["type"]] += 1
        if issues and len(examples) < issue_examples:
            examples.append({
                "idx": candidate["idx"],
                "source": candidate["source"],
                "raw_translated": raw,
                "translated": translated,
                "issues": issues,
            })
    return {
        "offset": offset,
        "items": len(candidates),
        "elapsed_seconds": round(elapsed, 3),
        "items_per_second": round(len(candidates) / elapsed, 3) if elapsed else 0,
        "refusal_count": refusal_count,
        "issue_counts": dict(issue_counts),
        "issue_examples": examples,
    }


def run_profile(
    file_path: Path,
    model: str,
    offset: int,
    workers: int,
    batch_size: int,
    max_batch_chars: int,
    protocol: str,
    mode: str = "parallel",
    candidate_filter: str = "all",
    issue_examples: int = 0,
    num_predict: int | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    batch_cfg = batch_translation_config()
    if timeout is not None:
        batch_cfg["timeout"] = int(timeout)
    options = {
        "temperature": batch_cfg.get("temperature", 0),
        "num_predict": int(num_predict or batch_cfg.get("num_predict", 4096)),
    }
    if protocol == "auto":
        protocol = _resolve_classification_protocol(classify_texts(file_path))
    offsets = [offset + i * batch_size for i in range(workers)]
    classes = classify_texts(file_path).get("classes", {})
    model_bound_total = _filtered_model_bound_total(classes, candidate_filter)
    result: dict[str, Any] = {
        "file": str(file_path),
        "model": model,
        "workers": workers,
        "batch_size": batch_size,
        "max_batch_chars": max_batch_chars,
        "protocol": protocol,
        "mode": mode,
        "candidate_filter": candidate_filter,
        "offsets": offsets,
        "model_bound_total": model_bound_total,
    }

    if mode in ("sequential", "both"):
        sequential_started = time.perf_counter()
        sequential = [
            translate_candidate_batch(file_path, model, item_offset, batch_size, max_batch_chars, options, protocol, candidate_filter, issue_examples)
            for item_offset in offsets
        ]
        sequential_elapsed = time.perf_counter() - sequential_started
        total_items = sum(item["items"] for item in sequential)
        result["sequential"] = {
            "elapsed_seconds": round(sequential_elapsed, 3),
            "items_per_second": round(total_items / sequential_elapsed, 3) if sequential_elapsed else 0,
            "estimated_full_minutes": round((model_bound_total / max(0.001, total_items / sequential_elapsed)) / 60, 2) if total_items else 0,
            "batches": sequential,
        }

    if mode in ("parallel", "both"):
        parallel_started = time.perf_counter()
        parallel: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    translate_candidate_batch,
                    file_path,
                    model,
                    item_offset,
                    batch_size,
                    max_batch_chars,
                    options,
                    protocol,
                    candidate_filter,
                    issue_examples,
                )
                for item_offset in offsets
            ]
            for future in as_completed(futures):
                parallel.append(future.result())
        parallel_elapsed = time.perf_counter() - parallel_started
        parallel.sort(key=lambda item: item["offset"])

        total_items = sum(item["items"] for item in parallel)
        result["parallel"] = {
            "elapsed_seconds": round(parallel_elapsed, 3),
            "items_per_second": round(total_items / parallel_elapsed, 3) if parallel_elapsed else 0,
            "estimated_full_minutes": round((model_bound_total / max(0.001, total_items / parallel_elapsed)) / 60, 2) if total_items else 0,
            "error_batches": sum(1 for item in parallel if item.get("error")),
            "issue_counts": dict(sum((Counter(item.get("issue_counts", {})) for item in parallel), Counter())),
            "refusal_count": sum(int(item.get("refusal_count", 0)) for item in parallel),
            "batches": parallel,
        }
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Profile sequential vs parallel batch translation throughput.")
    batch_cfg = batch_translation_config()
    parser.add_argument("--file", default=str(DEFAULT_FILE))
    parser.add_argument("--model", default=default_model())
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=batch_cfg.get("json_batch_size", 40))
    parser.add_argument("--max-batch-chars", type=int, default=batch_cfg.get("max_batch_chars", 4000))
    parser.add_argument("--protocol", choices=["auto", "json", "line"], default=batch_cfg.get("protocol", "json"))
    parser.add_argument("--mode", choices=["parallel", "sequential", "both"], default="parallel")
    parser.add_argument("--candidate-filter", choices=["all", "short", "non-short"], default="all")
    parser.add_argument("--issue-examples", type=int, default=0)
    parser.add_argument("--num-predict", type=int, default=batch_cfg.get("num_predict", 4096))
    parser.add_argument("--timeout", type=int, default=batch_cfg.get("timeout", 300))
    args = parser.parse_args()

    result = run_profile(
        Path(args.file),
        args.model,
        max(0, args.offset),
        max(1, args.workers),
        max(1, args.batch_size),
        max(500, args.max_batch_chars),
        args.protocol,
        mode=args.mode,
        candidate_filter=args.candidate_filter,
        issue_examples=max(0, args.issue_examples),
        num_predict=max(64, args.num_predict),
        timeout=max(10, args.timeout),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _resolve_classification_protocol(classification: dict[str, Any]) -> str:
    classes = classification.get("classes", {})
    model_bound_total = int(classes.get("short_model", 0)) + int(classes.get("medium_model", 0)) + int(classes.get("long_model", 0))
    if model_bound_total < 20:
        return "json"
    short_ratio = int(classes.get("short_model", 0)) / max(1, model_bound_total)
    avg_chars = float(classification.get("model_bound_avg_length") or classification.get("avg_length") or 0)
    if avg_chars <= 20 and short_ratio >= 0.5:
        return "line"
    return "json"


def _filtered_model_bound_total(classes: dict[str, Any], candidate_filter: str) -> int:
    short_total = int(classes.get("short_model", 0))
    non_short_total = int(classes.get("medium_model", 0)) + int(classes.get("long_model", 0))
    if candidate_filter == "short":
        return short_total
    if candidate_filter == "non-short":
        return non_short_total
    return short_total + non_short_total


if __name__ == "__main__":
    raise SystemExit(main())
