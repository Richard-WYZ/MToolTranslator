from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation.analysis import (
    classify_mtool_file,
    collect_model_candidates as collect_mtool_model_candidates,
)
from translation.batching import (
    build_batch_payload,
    build_batch_system_prompt,
    build_line_batch_payload,
    build_line_batch_system_prompt,
    parse_batch_response,
    parse_line_batch_response,
)
from translation.diagnostics import build_diagnostic_pipeline, diagnostic_glossary, finish_diagnostic_batch_translation
from translation.config import batch_translation_config, default_model, set_model_provider, think_setting
from translation.models import translate_once
from translation.quality import is_refusal


DEFAULT_FILE = ROOT / "test_work" / "ManualTransFile.json"
BENCHMARK_GLOSSARY_PATH = ROOT / ".checkpoints" / "_benchmark_empty.glossary.json"


def _benchmark_pipeline():
    return build_diagnostic_pipeline(glossary_path=str(BENCHMARK_GLOSSARY_PATH))


def classify_texts(file_path: Path) -> dict[str, Any]:
    pipeline = _benchmark_pipeline()
    glossary = diagnostic_glossary(pipeline)
    return classify_mtool_file(file_path, glossary=glossary)


def collect_model_candidates(
    file_path: Path,
    limit: int,
    batch_size: int,
    max_batch_chars: int,
    offset: int = 0,
    candidate_filter: str = "all",
) -> list[dict[str, Any]]:
    pipeline = _benchmark_pipeline()
    glossary = diagnostic_glossary(pipeline)
    return collect_mtool_model_candidates(
        file_path,
        glossary=glossary,
        limit=limit,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        offset=offset,
        candidate_filter=candidate_filter,
    )


def run_sample(
    file_path: Path,
    model: str,
    sample_size: int,
    batch_size: int,
    max_batch_chars: int,
    timeout: int,
    model_bound_total: int,
    offset: int = 0,
    issue_examples: int = 0,
    protocol: str = "json",
    candidate_filter: str = "all",
) -> dict[str, Any]:
    candidates = collect_model_candidates(
        file_path,
        sample_size,
        batch_size,
        max_batch_chars,
        offset=offset,
        candidate_filter=candidate_filter,
    )
    if not candidates:
        return {"sample_size": 0, "error": "no candidates"}
    payload_items = [{"i": c["i"], "text": c["text"], "terms": c["terms"]} for c in candidates]
    if protocol == "auto":
        protocol = _resolve_sample_protocol(candidates)
    use_line_protocol = protocol == "line"
    batch_cfg = batch_translation_config()
    started = time.perf_counter()
    response = ""
    try:
        response = translate_once(
            model,
            build_line_batch_payload(payload_items) if use_line_protocol else build_batch_payload(payload_items),
            system_prompt=build_line_batch_system_prompt() if use_line_protocol else build_batch_system_prompt(),
            timeout=timeout,
            think=think_setting(),
            response_format=batch_cfg.get("response_format"),
            options={
                "temperature": batch_cfg.get("temperature", 0),
                "num_predict": batch_cfg.get("num_predict", 4096),
            },
        )
        elapsed = time.perf_counter() - started
        expected = {c["i"] for c in candidates}
        parsed = parse_line_batch_response(response, expected) if use_line_protocol else parse_batch_response(response, expected)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "sample_size": len(candidates),
            "batch_size": batch_size,
            "max_batch_chars": max_batch_chars,
            "elapsed_seconds": round(elapsed, 3),
            "error": type(exc).__name__ + ": " + str(exc),
            "response_preview": response[:1000],
        }
    issue_counts = Counter()
    examples: list[dict[str, Any]] = []
    refusal_count = 0
    pipeline = _benchmark_pipeline()
    for candidate in candidates:
        raw_translated = parsed[candidate["i"]]
        if is_refusal(raw_translated, original=candidate["text"]):
            refusal_count += 1
        translated, _status, issues = finish_diagnostic_batch_translation(pipeline, candidate, raw_translated)
        for issue in issues:
            issue_counts[issue["type"]] += 1
        if issues and len(examples) < issue_examples:
            examples.append({
                "idx": candidate["idx"],
                "source": candidate["source"],
                "protected": candidate["protected"],
                "raw_translated": raw_translated,
                "translated": translated,
                "issues": issues,
            })
    return {
        "sample_size": len(candidates),
        "offset": offset,
        "candidate_filter": candidate_filter,
        "protocol": protocol,
        "batch_size": batch_size,
        "max_batch_chars": max_batch_chars,
        "elapsed_seconds": round(elapsed, 3),
        "items_per_second": round(len(candidates) / elapsed, 3) if elapsed else 0,
        "estimated_full_minutes": round((model_bound_total / max(0.001, len(candidates) / elapsed)) / 60, 2),
        "refusal_count": refusal_count,
        "issue_counts": dict(issue_counts),
        "issue_examples": examples,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Benchmark ManualTransFile batch translation behavior.")
    batch_cfg = batch_translation_config()
    parser.add_argument("--file", default=str(DEFAULT_FILE))
    parser.add_argument("--model", default=default_model())
    parser.add_argument(
        "--provider",
        choices=["configured", "ollama", "api"],
        default="configured",
        help="Override MODEL_PROVIDER for this diagnostic run.",
    )
    parser.add_argument("--sample-size", type=int, default=0, help="If 0, only print dry-run classification.")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many model-bound candidates before sampling.")
    parser.add_argument("--issue-examples", type=int, default=0, help="Include up to this many sample issue examples.")
    parser.add_argument("--candidate-filter", choices=["all", "short", "non-short"], default="all")
    parser.add_argument("--protocol", choices=["auto", "json", "line"], default=batch_cfg.get("protocol", "json"))
    parser.add_argument("--batch-size", type=int, default=batch_cfg.get("json_batch_size", 160))
    parser.add_argument("--max-batch-chars", type=int, default=batch_cfg.get("max_batch_chars", 12000))
    parser.add_argument("--timeout", type=int, default=batch_cfg.get("timeout", 300))
    args = parser.parse_args()
    if args.provider != "configured":
        set_model_provider(args.provider)

    file_path = Path(args.file)
    classification = classify_texts(file_path)
    result = {"classification": classification}
    if args.sample_size > 0:
        classes = classification.get("classes", {})
        model_bound_total = _filtered_model_bound_total(classes, args.candidate_filter)
        protocol = args.protocol
        if protocol == "auto":
            protocol = _resolve_classification_protocol(classification)
        result["sample"] = run_sample(
            file_path,
            args.model,
            args.sample_size,
            args.batch_size,
            args.max_batch_chars,
            args.timeout,
            model_bound_total,
            offset=max(0, args.offset),
            issue_examples=max(0, args.issue_examples),
            protocol=protocol,
            candidate_filter=args.candidate_filter,
        )
    try:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


def _resolve_sample_protocol(candidates: list[dict[str, Any]]) -> str:
    if len(candidates) < 20:
        return "json"
    avg_chars = sum(len(candidate["source"]) for candidate in candidates) / len(candidates)
    short_ratio = sum(1 for candidate in candidates if candidate["short_label"]) / len(candidates)
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


if __name__ == "__main__":
    raise SystemExit(main())
