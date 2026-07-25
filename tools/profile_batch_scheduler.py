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

from translation.batching import (
    api_job_is_short_text,
    api_job_uses_fast_model,
    build_batch_payload,
    build_batch_system_prompt,
    candidate_batch_category,
    candidate_template_key,
    pack_api_candidate_batches,
    prepare_model_candidate,
    reindex_candidates,
)
from translation.classification import deterministic_translation, looks_like_short_label
from translation.input import is_mtool_items, load_json_items, source_text
from translation.terminology import Glossary


def collect_candidates(file_path: str | Path) -> tuple[list[dict[str, Any]], int]:
    items = load_json_items(str(file_path))
    mtool = is_mtool_items(items)
    glossary = Glossary.in_memory()
    sources = [source_text(key, value, mtool=mtool) for key, value in items]
    glossary.preseed_from_sources(sources)
    candidates: list[dict[str, Any]] = []
    deterministic_count = 0
    for idx, (key, value) in enumerate(items):
        source = source_text(key, value, mtool=mtool)
        if not source.strip() or deterministic_translation(source, glossary=glossary):
            deterministic_count += 1
            continue
        candidates.append(prepare_model_candidate(
            batch_i=0,
            idx=idx,
            source=source,
            glossary=glossary,
            short_label=looks_like_short_label(source),
        ))
    return candidates, deterministic_count


def pack_contiguous_batches(
    candidates: list[dict[str, Any]],
    *,
    batch_size: int,
    max_batch_chars: int,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for candidate in candidates:
        candidate_chars = len(str(candidate.get("protected", candidate.get("text", ""))))
        if current and (len(current) >= batch_size or current_chars + candidate_chars > max_batch_chars):
            batches.append(reindex_candidates(current))
            current = []
            current_chars = 0
        current.append(candidate)
        current_chars += candidate_chars
    if current:
        batches.append(reindex_candidates(current))
    return batches


def profile_scheduler(
    file_path: str | Path,
    *,
    batch_size: int,
    max_batch_chars: int,
    short_line_max_chars: int,
    long_text_min_chars: int,
    fast_categories: list[str],
) -> dict[str, Any]:
    started = time.perf_counter()
    candidates, deterministic_count = collect_candidates(file_path)
    cfg = {
        "short_line_max_chars": short_line_max_chars,
        "long_text_min_chars": long_text_min_chars,
        "api_fast_categories": fast_categories,
    }
    contiguous = pack_contiguous_batches(
        candidates,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
    )
    homogeneous = pack_api_candidate_batches(
        candidates,
        batch_size=batch_size,
        max_batch_chars=max_batch_chars,
        batch_cfg=cfg,
    )

    def mixed(batch: list[dict[str, Any]]) -> bool:
        return len({candidate_batch_category(candidate, cfg) for candidate in batch}) > 1

    old_fast = [batch for batch in contiguous if api_job_is_short_text(batch, cfg)]
    new_fast = [batch for batch in homogeneous if api_job_uses_fast_model(batch, cfg)]
    category_items = Counter(candidate_batch_category(candidate, cfg) for candidate in candidates)
    template_items = Counter(candidate_template_key(candidate) for candidate in candidates)
    category_batches = Counter(candidate_batch_category(batch[0], cfg) for batch in homogeneous)
    quality_batches = [batch for batch in homogeneous if not api_job_uses_fast_model(batch, cfg)]

    def representative_items(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        representatives: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for candidate in batch:
            key = candidate_template_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            representatives.append(dict(candidate, i=len(representatives)))
        return representatives

    verbose_payload_chars = 0
    compact_payload_chars = 0
    for batch in quality_batches:
        representatives = representative_items(batch)
        verbose_payload_chars += len(build_batch_payload(representatives))
        compact_payload_chars += len(build_batch_payload(representatives, compact=True))
    verbose_protocol_chars = (
        verbose_payload_chars + len(quality_batches) * len(build_batch_system_prompt())
    )
    compact_protocol_chars = (
        compact_payload_chars
        + len(quality_batches) * len(build_batch_system_prompt(compact=True))
    )
    elapsed = time.perf_counter() - started
    return {
        "file": str(Path(file_path).resolve()),
        "batch_size": batch_size,
        "max_batch_chars": max_batch_chars,
        "fast_categories": fast_categories,
        "model_bound_items": len(candidates),
        "unique_model_templates": len(template_items),
        "reusable_template_items": len(candidates) - len(template_items),
        "deterministic_or_empty_items": deterministic_count,
        "category_items": dict(category_items),
        "legacy_contiguous": {
            "batches": len(contiguous),
            "fast_batches": len(old_fast),
            "quality_batches": len(contiguous) - len(old_fast),
            "mixed_category_batches": sum(mixed(batch) for batch in contiguous),
        },
        "homogeneous": {
            "batches": len(homogeneous),
            "fast_batches": len(new_fast),
            "quality_batches": len(homogeneous) - len(new_fast),
            "fast_items": sum(len(batch) for batch in new_fast),
            "quality_items": len(candidates) - sum(len(batch) for batch in new_fast),
            "mixed_category_batches": sum(mixed(batch) for batch in homogeneous),
            "category_batches": dict(category_batches),
        },
        "batch_count_delta": len(homogeneous) - len(contiguous),
        "quality_json_protocol_chars": {
            "verbose": verbose_protocol_chars,
            "compact": compact_protocol_chars,
            "saved": verbose_protocol_chars - compact_protocol_chars,
            "reduction_percent": round(
                (verbose_protocol_chars - compact_protocol_chars) / verbose_protocol_chars * 100,
                3,
            ) if verbose_protocol_chars else 0,
        },
        "analysis_seconds": round(elapsed, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile legacy and homogeneous translation batch scheduling offline.")
    parser.add_argument("--file", default=str(ROOT / "test_work" / "ManualTransFile.json"))
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--max-batch-chars", type=int, default=4000)
    parser.add_argument("--short-line-max-chars", type=int, default=80)
    parser.add_argument("--long-text-min-chars", type=int, default=120)
    parser.add_argument("--fast-categories", default="short_label")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    result = profile_scheduler(
        args.file,
        batch_size=max(1, args.batch_size),
        max_batch_chars=max(1, args.max_batch_chars),
        short_line_max_chars=max(1, args.short_line_max_chars),
        long_text_min_chars=max(1, args.long_text_min_chars),
        fast_categories=[item.strip() for item in args.fast_categories.split(",") if item.strip()],
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
