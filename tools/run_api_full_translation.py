from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation import checkpoint
import translation.usage as token_usage
from translation.config import (
    batch_translation_config,
    default_model,
    set_default_model,
    set_fallback_models,
    set_model_provider,
)
from translation.review import review_report_path, write_review_report
from translation.translate import TranslationRequest, translate


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    batch_cfg = batch_translation_config()
    parser = argparse.ArgumentParser(description="Run a full API-backed MTool JSON translation benchmark.")
    parser.add_argument("--file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--model", default=default_model())
    parser.add_argument("--concurrency", type=int, default=batch_cfg.get("api_concurrency", 5))
    parser.add_argument(
        "--event-driven",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("api_event_driven_enabled", False)),
        help="Dynamically overlap parent, primary, fallback, and repair jobs.",
    )
    parser.add_argument(
        "--adaptive-concurrency",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("api_adaptive_concurrency_enabled", False)),
        help="Use independent adaptive request windows for each routed model.",
    )
    parser.add_argument("--quality-concurrency", type=int, default=10)
    parser.add_argument("--quality-max-concurrency", type=int, default=32)
    parser.add_argument(
        "--quality-max-inflight-chars",
        type=int,
        default=48000,
        help="Maximum combined source/context characters concurrently sent to the quality model.",
    )
    parser.add_argument("--fast-concurrency", type=int, default=10)
    parser.add_argument("--fast-max-concurrency", type=int, default=24)
    parser.add_argument(
        "--fast-max-inflight-chars",
        type=int,
        default=32000,
        help="Maximum combined source/context characters concurrently sent to the fast model.",
    )
    parser.add_argument(
        "--concurrency-increase-every",
        type=int,
        default=int(batch_cfg.get("api_concurrency_increase_every", 8)),
    )
    parser.add_argument(
        "--concurrency-decrease-factor",
        type=float,
        default=float(batch_cfg.get("api_concurrency_decrease_factor", 0.5)),
    )
    parser.add_argument("--batch-size", type=int, default=batch_cfg.get("json_batch_size", 40))
    parser.add_argument("--max-batch-chars", type=int, default=batch_cfg.get("max_batch_chars", 8000))
    parser.add_argument("--protocol", choices=["json", "line", "auto"], default="line")
    parser.add_argument(
        "--compact-json",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("compact_json_protocol", False)),
        help="Use compact [id,text] JSON records for non-short batches.",
    )
    parser.add_argument("--num-predict", type=int, default=batch_cfg.get("num_predict", 3072))
    parser.add_argument("--quality-num-predict", type=int, default=batch_cfg.get("quality_num_predict", 4096))
    parser.add_argument("--timeout", type=int, default=batch_cfg.get("timeout", 180))
    parser.add_argument(
        "--content-split-max-depth",
        type=int,
        default=int(batch_cfg.get("api_content_split_max_depth", 3)),
        help="Maximum bisection depth used to isolate content-rejected quality batches.",
    )
    parser.add_argument("--model-routing", action="store_true")
    parser.add_argument("--fast-model", default="")
    parser.add_argument("--quality-model", default="")
    parser.add_argument(
        "--sensitive-routing",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("api_sensitive_routing_enabled", True)),
        help="Route high-confidence adult content to an adult-capable model before quality routing.",
    )
    parser.add_argument(
        "--sensitive-model",
        default=str(batch_cfg.get("api_sensitive_model") or "api:minimax-m3"),
    )
    parser.add_argument(
        "--sensitive-repair",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("api_sensitive_repair_enabled", True)),
        help="Repair severe sensitive-route quality failures with the same model.",
    )
    parser.add_argument(
        "--sensitive-repair-batch-size",
        type=int,
        default=int(batch_cfg.get("api_sensitive_repair_batch_size", 5)),
    )
    parser.add_argument(
        "--sensitive-repair-max-batch-chars",
        type=int,
        default=int(batch_cfg.get("api_sensitive_repair_max_batch_chars", 1000)),
    )
    parser.add_argument(
        "--sensitive-repair-single-retry",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("api_sensitive_repair_single_retry", True)),
        help="Retry remaining severe sensitive failures individually once.",
    )
    parser.add_argument(
        "--sensitive-cross-model-retry",
        action=argparse.BooleanOptionalAction,
        default=bool(
            batch_cfg.get("api_sensitive_cross_model_retry_enabled", True)
        ),
        help=(
            "Use the configured quality model for the final isolated "
            "sensitive retry."
        ),
    )
    parser.add_argument(
        "--sensitive-parent-repair",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("api_sensitive_parent_repair_enabled", True)),
        help="Repair a remaining failed composed child through its full multiline parent.",
    )
    parser.add_argument(
        "--sensitive-parent-repair-max-chars",
        type=int,
        default=int(batch_cfg.get("api_sensitive_parent_repair_max_chars", 2400)),
    )
    parser.add_argument(
        "--sensitive-repair-issue-types",
        default=",".join(
            batch_cfg.get("api_sensitive_repair_issue_types", [
                "empty_translation",
                "untranslated_japanese",
                "identical_japanese_source",
                "model_refusal",
                "suspicious_artifact",
            ])
        ),
        help="Comma-separated validation issues eligible for same-model repair.",
    )
    parser.add_argument(
        "--fast-categories",
        default=",".join(batch_cfg.get("api_fast_categories", ["short_label"])),
        help="Comma-separated homogeneous text categories routed to the fast model.",
    )
    parser.add_argument("--line-for-short-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--short-line-max-chars", type=int, default=batch_cfg.get("short_line_max_chars", 80))
    parser.add_argument(
        "--mtool-composition",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("mtool_composition_enabled", True)),
    )
    parser.add_argument(
        "--mtool-parent-first",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("mtool_parent_first_enabled", False)),
    )
    parser.add_argument(
        "--mtool-parent-first-max-chars",
        type=int,
        default=int(batch_cfg.get("mtool_parent_first_max_chars", 2400)),
    )
    parser.add_argument(
        "--mtool-context-max-chars",
        type=int,
        default=int(batch_cfg.get("mtool_context_max_chars", 1200)),
    )
    parser.add_argument(
        "--mtool-context-max-per-item",
        type=int,
        default=int(batch_cfg.get("mtool_context_max_per_item", 2)),
    )
    parser.add_argument(
        "--mtool-neighbor-context",
        action=argparse.BooleanOptionalAction,
        default=bool(batch_cfg.get("mtool_neighbor_context_enabled", True)),
    )
    parser.add_argument(
        "--mtool-neighbor-context-radius",
        type=int,
        default=int(batch_cfg.get("mtool_neighbor_context_radius", 2)),
    )
    parser.add_argument(
        "--mtool-neighbor-context-max-chars",
        type=int,
        default=int(batch_cfg.get("mtool_neighbor_context_max_chars", 120)),
    )
    parser.add_argument(
        "--mtool-neighbor-context-min-dialogue-items",
        type=int,
        default=int(batch_cfg.get("mtool_neighbor_context_min_dialogue_items", 3)),
    )
    parser.add_argument("--progress-seconds", type=float, default=30.0)
    args = parser.parse_args()

    model_concurrency_initial: dict[str, int] = {}
    model_concurrency_max: dict[str, int] = {}
    model_inflight_chars_max: dict[str, int] = {}

    def register_model_window(model: str, initial: int, maximum: int) -> None:
        rendered = str(model or "")
        if not rendered:
            return
        initial = max(1, int(initial))
        maximum = max(initial, int(maximum))
        model_concurrency_initial[rendered] = max(
            initial,
            model_concurrency_initial.get(rendered, 0),
        )
        model_concurrency_max[rendered] = max(
            maximum,
            model_concurrency_max.get(rendered, 0),
        )

    def register_model_character_budget(model: str, maximum: int) -> None:
        rendered = str(model or "")
        if not rendered:
            return
        model_inflight_chars_max[rendered] = max(
            max(1, int(maximum)),
            model_inflight_chars_max.get(rendered, 0),
        )

    register_model_window(
        args.quality_model or args.model,
        args.quality_concurrency,
        args.quality_max_concurrency,
    )
    register_model_character_budget(
        args.quality_model or args.model,
        args.quality_max_inflight_chars,
    )
    register_model_window(
        args.fast_model,
        args.fast_concurrency,
        args.fast_max_concurrency,
    )
    register_model_character_budget(
        args.fast_model,
        args.fast_max_inflight_chars,
    )
    register_model_window(
        args.sensitive_model,
        args.fast_concurrency,
        args.fast_max_concurrency,
    )
    register_model_character_budget(
        args.sensitive_model,
        args.fast_max_inflight_chars,
    )

    checkpoint.CHECKPOINT_DIR = args.checkpoint_dir
    batch_cfg.update({
        "enabled": True,
        "api_parallel_enabled": True,
        "api_event_driven_enabled": bool(args.event_driven),
        "api_concurrency": max(1, args.concurrency),
        "api_adaptive_concurrency_enabled": bool(args.adaptive_concurrency),
        "api_model_concurrency_initial": model_concurrency_initial,
        "api_model_concurrency_max": model_concurrency_max,
        "api_model_inflight_chars_max": model_inflight_chars_max,
        "api_adaptive_default_maximum": max(
            1,
            args.quality_max_concurrency,
            args.fast_max_concurrency,
        ),
        "api_default_inflight_chars_max": max(
            1,
            args.quality_max_inflight_chars,
            args.fast_max_inflight_chars,
        ),
        "api_concurrency_increase_every": max(
            1,
            args.concurrency_increase_every,
        ),
        "api_concurrency_decrease_factor": min(
            0.99,
            max(0.01, args.concurrency_decrease_factor),
        ),
        "api_max_retries": int(batch_cfg.get("api_max_retries", 2)),
        "json_batch_size": max(1, args.batch_size),
        "max_batch_chars": max(500, args.max_batch_chars),
        "protocol": args.protocol,
        "compact_json_protocol": bool(args.compact_json),
        "num_predict": max(64, args.num_predict),
        "quality_num_predict": max(64, args.quality_num_predict),
        "timeout": max(10, args.timeout),
        "api_content_split_max_depth": max(0, args.content_split_max_depth),
        "api_model_routing_enabled": bool(args.model_routing),
        "api_fast_model": args.fast_model,
        "api_quality_model": args.quality_model,
        "api_sensitive_routing_enabled": bool(args.sensitive_routing),
        "api_sensitive_model": args.sensitive_model,
        "api_sensitive_repair_enabled": bool(args.sensitive_repair),
        "api_sensitive_repair_batch_size": max(1, args.sensitive_repair_batch_size),
        "api_sensitive_repair_max_batch_chars": max(
            1,
            args.sensitive_repair_max_batch_chars,
        ),
        "api_sensitive_repair_single_retry": bool(args.sensitive_repair_single_retry),
        "api_sensitive_cross_model_retry_enabled": bool(
            args.sensitive_cross_model_retry
        ),
        "api_sensitive_parent_repair_enabled": bool(args.sensitive_parent_repair),
        "api_sensitive_parent_repair_max_chars": max(
            1,
            args.sensitive_parent_repair_max_chars,
        ),
        "api_sensitive_repair_issue_types": [
            item.strip()
            for item in args.sensitive_repair_issue_types.split(",")
            if item.strip()
        ],
        "api_fast_categories": [item.strip() for item in args.fast_categories.split(",") if item.strip()],
        "line_for_short_only": bool(args.line_for_short_only),
        "short_line_max_chars": max(1, args.short_line_max_chars),
        "mtool_composition_enabled": bool(args.mtool_composition),
        "mtool_parent_first_enabled": bool(args.mtool_parent_first),
        "mtool_parent_first_max_chars": max(
            1,
            args.mtool_parent_first_max_chars,
        ),
        "mtool_context_max_chars": max(0, args.mtool_context_max_chars),
        "mtool_context_max_per_item": max(0, args.mtool_context_max_per_item),
        "mtool_neighbor_context_enabled": bool(args.mtool_neighbor_context),
        "mtool_neighbor_context_radius": max(1, args.mtool_neighbor_context_radius),
        "mtool_neighbor_context_max_chars": max(0, args.mtool_neighbor_context_max_chars),
        "mtool_neighbor_context_min_dialogue_items": max(
            1,
            args.mtool_neighbor_context_min_dialogue_items,
        ),
    })
    set_model_provider("api")
    set_default_model(args.model)
    route_fallbacks = []
    if args.model_routing or args.sensitive_routing:
        route_fallbacks = list(dict.fromkeys([
            model
            for model in (args.fast_model, args.quality_model, args.sensitive_model)
            if model and model != args.model
        ]))
    set_fallback_models(route_fallbacks)

    source = Path(args.file)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    Path(args.checkpoint_dir).mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    token_usage.reset()
    last_emit = 0.0
    last_payload: dict[str, object] = {}

    def progress(payload: dict[str, object]) -> None:
        nonlocal last_emit, last_payload
        last_payload = payload
        now = time.perf_counter()
        if now - last_emit < args.progress_seconds and payload.get("processed") != payload.get("total"):
            return
        last_emit = now
        elapsed = now - started
        processed = int(payload.get("processed") or 0)
        total = int(payload.get("total") or 0)
        rate = processed / elapsed if elapsed > 0 else 0
        remaining = (total - processed) / rate if rate > 0 else 0
        print(json.dumps({
            "event": "progress",
            "processed": processed,
            "total": total,
            "percent": payload.get("percent"),
            "status": payload.get("status"),
            "elapsed_seconds": round(elapsed, 1),
            "items_per_second": round(rate, 3),
            "eta_seconds": round(remaining, 1),
        }, ensure_ascii=False), flush=True)

    try:
        translate(TranslationRequest(
            file_path=str(source),
            output_path=str(output),
            model=args.model,
            progress_callback=progress,
            glossary_path=args.glossary,
        ))
    finally:
        elapsed = time.perf_counter() - started
        cp = checkpoint.load_checkpoint(str(source))
        review_path = review_report_path(str(source), str(output))
        review_report_error = ""
        try:
            review_path = write_review_report(str(source), str(output))
        except Exception as exc:
            review_report_error = f"{type(exc).__name__}: {exc}"
        usage = token_usage.snapshot()
        entries = list(cp.get("entries", {}).values())
        statuses: dict[str, int] = {}
        issues: dict[str, int] = {}
        for entry in entries:
            statuses[str(entry.get("status", ""))] = statuses.get(str(entry.get("status", "")), 0) + 1
            for issue in entry.get("issues", []) or []:
                issue_type = str(issue.get("type", ""))
                issues[issue_type] = issues.get(issue_type, 0) + 1
        phase_seconds = float(usage.get("translation_phase_seconds", 0.0) or 0.0)
        eligible_completed = sum(statuses.get(status, 0) for status in (
            "translated",
            "translated_needs_review",
            "review_required",
        ))
        review_items = statuses.get("translated_needs_review", 0) + statuses.get("review_required", 0)
        try:
            input_payload = json.loads(source.read_text(encoding="utf-8-sig"))
            total_input_entries = len(input_payload) if isinstance(input_payload, (dict, list)) else 0
        except (OSError, json.JSONDecodeError):
            total_input_entries = 0
        print(json.dumps({
            "event": "summary",
            "file": str(source),
            "output": str(output),
            "checkpoint_dir": args.checkpoint_dir,
            "model": args.model,
            "protocol": args.protocol,
            "compact_json": args.compact_json,
            "concurrency": args.concurrency,
            "event_driven": args.event_driven,
            "adaptive_concurrency": args.adaptive_concurrency,
            "model_concurrency_initial": model_concurrency_initial,
            "model_concurrency_max": model_concurrency_max,
            "model_inflight_chars_max": model_inflight_chars_max,
            "quality_max_inflight_chars": args.quality_max_inflight_chars,
            "fast_max_inflight_chars": args.fast_max_inflight_chars,
            "concurrency_increase_every": args.concurrency_increase_every,
            "concurrency_decrease_factor": args.concurrency_decrease_factor,
            "batch_size": args.batch_size,
            "max_batch_chars": args.max_batch_chars,
            "num_predict": args.num_predict,
            "quality_num_predict": args.quality_num_predict,
            "timeout": args.timeout,
            "content_split_max_depth": args.content_split_max_depth,
            "model_routing": args.model_routing,
            "fast_model": args.fast_model,
            "quality_model": args.quality_model,
            "sensitive_routing": args.sensitive_routing,
            "sensitive_model": args.sensitive_model,
            "sensitive_repair": args.sensitive_repair,
            "sensitive_repair_batch_size": args.sensitive_repair_batch_size,
            "sensitive_repair_max_batch_chars": args.sensitive_repair_max_batch_chars,
            "sensitive_repair_single_retry": args.sensitive_repair_single_retry,
            "sensitive_cross_model_retry": args.sensitive_cross_model_retry,
            "sensitive_parent_repair": args.sensitive_parent_repair,
            "sensitive_parent_repair_max_chars": args.sensitive_parent_repair_max_chars,
            "sensitive_repair_issue_types": args.sensitive_repair_issue_types,
            "fallback_models": route_fallbacks,
            "fast_categories": args.fast_categories,
            "line_for_short_only": args.line_for_short_only,
            "mtool_composition": args.mtool_composition,
            "mtool_parent_first": args.mtool_parent_first,
            "mtool_parent_first_max_chars": args.mtool_parent_first_max_chars,
            "mtool_context_max_chars": args.mtool_context_max_chars,
            "mtool_context_max_per_item": args.mtool_context_max_per_item,
            "mtool_neighbor_context": args.mtool_neighbor_context,
            "mtool_neighbor_context_radius": args.mtool_neighbor_context_radius,
            "mtool_neighbor_context_max_chars": args.mtool_neighbor_context_max_chars,
            "mtool_neighbor_context_min_dialogue_items": args.mtool_neighbor_context_min_dialogue_items,
            "elapsed_seconds": round(elapsed, 3),
            "translation_phase_seconds": round(phase_seconds, 3),
            "entries_per_minute": round((eligible_completed / phase_seconds) * 60, 3) if phase_seconds else 0,
            "token_usage": usage,
            "total_input_entries": total_input_entries,
            "eligible_completed_entries": eligible_completed,
            "preserved_entries": statuses.get("preserved", 0),
            "review_required_entries": statuses.get("review_required", 0),
            "review_item_count": review_items,
            "review_report": review_path,
            "review_report_error": review_report_error,
            "validation_failure_count": sum(1 for entry in entries if entry.get("issues")),
            "prompt_version": cp.get("prompt_version", "default"),
            "glossary_version": cp.get("glossary_version", "0"),
            "model_configuration": cp.get("model_configuration", {}),
            "hardware": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
            "entries": len(entries),
            "statuses": statuses,
            "issue_counts": issues,
            "last_progress": last_payload,
        }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
