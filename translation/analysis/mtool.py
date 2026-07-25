from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from translation.batching import prepare_model_candidate
from translation.classification import deterministic_translation, label_variant_groups, looks_like_short_label
from translation.input import is_mtool_items, load_json_items, source_text
from translation.terminology import Glossary


def classify_mtool_file(file_path: str | Path, glossary: Glossary | None = None) -> dict[str, Any]:
    """Classify MTool JSON entries before model translation."""
    items = load_json_items(str(file_path))
    mtool = is_mtool_items(items)
    texts = [source_text(key, value, mtool=mtool) for key, value in items]
    if glossary is not None:
        glossary.preseed_from_sources(texts)
    if glossary is not None:
        glossary.preseed_from_sources(texts)
    nonempty = [text for text in texts if text.strip()]
    classes = Counter()
    lengths: list[int] = []
    model_bound_texts: list[str] = []

    for text in nonempty:
        lengths.append(len(text))
        if deterministic_translation(text, glossary=glossary):
            classes["deterministic"] += 1
            continue
        model_bound_texts.append(text)
        if looks_like_short_label(text):
            classes["short_model"] += 1
        elif len(text) <= 120:
            classes["medium_model"] += 1
        else:
            classes["long_model"] += 1

    model_bound_lengths = [len(text) for text in model_bound_texts]
    return {
        "file": str(file_path),
        "mtool": mtool,
        "total_items": len(items),
        "nonempty": len(nonempty),
        "unique": len(set(nonempty)),
        "classes": dict(classes),
        "label_variants": label_variant_summary(label_variant_groups(model_bound_texts)),
        "model_bound_avg_length": round(sum(model_bound_lengths) / len(model_bound_lengths), 2) if model_bound_lengths else 0,
        "avg_length": round(sum(lengths) / len(lengths), 2) if lengths else 0,
        "max_length": max(lengths) if lengths else 0,
    }


def collect_model_bound_texts(file_path: str | Path, glossary: Glossary | None = None, limit: int = 0) -> list[str]:
    """Return source texts that require model translation."""
    items = load_json_items(str(file_path))
    mtool = is_mtool_items(items)
    if glossary is not None:
        glossary.preseed_from_sources(
            [source_text(key, value, mtool=mtool) for key, value in items]
        )
    if glossary is not None:
        glossary.preseed_from_sources(
            [source_text(key, value, mtool=mtool) for key, value in items]
        )
    texts: list[str] = []
    for key, value in items:
        source = source_text(key, value, mtool=mtool)
        if not source.strip():
            continue
        if deterministic_translation(source, glossary=glossary):
            continue
        texts.append(source)
        if limit and len(texts) >= limit:
            break
    return texts


def collect_model_candidates(
    file_path: str | Path,
    *,
    glossary: Glossary | None = None,
    limit: int,
    batch_size: int,
    max_batch_chars: int,
    offset: int = 0,
    candidate_filter: str = "all",
) -> list[dict[str, Any]]:
    """Collect protected model-bound candidates for batch profiling."""
    items = load_json_items(str(file_path))
    mtool = is_mtool_items(items)
    if glossary is not None:
        glossary.preseed_from_sources(
            [source_text(key, value, mtool=mtool) for key, value in items]
        )
    if glossary is not None:
        glossary.preseed_from_sources(
            [source_text(key, value, mtool=mtool) for key, value in items]
        )
    candidates: list[dict[str, Any]] = []
    total_chars = 0
    skipped_model_candidates = 0

    for idx, (key, value) in enumerate(items):
        source = source_text(key, value, mtool=mtool)
        if not source.strip() or deterministic_translation(source, glossary=glossary):
            continue
        short_label = looks_like_short_label(source)
        if not candidate_filter_matches(short_label, candidate_filter):
            continue
        if skipped_model_candidates < offset:
            skipped_model_candidates += 1
            continue

        candidate = prepare_model_candidate(
            batch_i=len(candidates),
            idx=idx,
            source=source,
            glossary=glossary,
            short_label=short_label,
        )
        protected = candidate["protected"]
        projected = total_chars + len(protected)
        if candidates and (len(candidates) >= batch_size or projected > max_batch_chars):
            break

        candidates.append(candidate)
        total_chars = projected
        if limit and len(candidates) >= limit:
            break
    return candidates


def label_variant_summary(groups: dict[str, list[Any]]) -> dict[str, Any]:
    """Summarize label-variant groups for benchmark output."""
    top_groups = []
    for base, variants in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)[:10]:
        kinds = Counter(variant.kind for variant in variants)
        top_groups.append({
            "base": base,
            "count": len(variants),
            "kinds": dict(kinds),
            "samples": [variant.source for variant in variants[:5]],
        })
    return {
        "groups": len(groups),
        "items": sum(len(variants) for variants in groups.values()),
        "top_groups": top_groups,
    }


def candidate_filter_matches(short_label: bool, candidate_filter: str) -> bool:
    if candidate_filter == "short":
        return short_label
    if candidate_filter == "non-short":
        return not short_label
    return True


__all__ = [
    "candidate_filter_matches",
    "classify_mtool_file",
    "collect_model_bound_texts",
    "collect_model_candidates",
    "label_variant_summary",
]
