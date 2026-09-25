from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any

from translation.benchmark.suite import BenchmarkCase
from translation.quality.refusal import assess_model_output
from translation.quality.rules import translation_issues


ISSUE_PENALTIES = {
    "empty_translation": 100,
    "model_refusal": 100,
    "untranslated_japanese": 70,
    "identical_japanese_source": 60,
    "internal_placeholder_leak": 70,
    "term_placeholder_leak": 70,
    "resource_identifier_preservation": 55,
    "numeric_preservation": 45,
    "line_break_preservation": 35,
    "context_contamination": 35,
    "english_residue": 12,
    "length_expansion": 10,
    "short_label_expansion": 12,
    "honorific_rendering_review": 5,
}
CRITICAL_ISSUES = {
    "empty_translation",
    "model_refusal",
    "untranslated_japanese",
    "internal_placeholder_leak",
    "term_placeholder_leak",
    "resource_identifier_preservation",
    "numeric_preservation",
    "line_break_preservation",
}


def score_output(case: BenchmarkCase, output: str) -> dict[str, Any]:
    rendered = str(output or "").strip()
    issues = translation_issues(case.source, rendered, short_label=case.short_label)
    assessment = assess_model_output(rendered, original=case.source)
    if assessment.issue_type and not any(item.get("type") == assessment.issue_type for item in issues):
        issues.append(assessment.as_issue())

    missing_tokens = [token for token in case.protected_tokens if rendered.count(token) != case.source.count(token)]
    if missing_tokens:
        issues.append({
            "type": "runtime_token_preservation",
            "message": "Benchmark output changed protected runtime tokens.",
        })

    matched_groups = sum(
        any(term.lower() in rendered.lower() for term in group)
        for group in case.required_groups
    )
    concept_ratio = matched_groups / max(1, len(case.required_groups))
    penalty = sum(ISSUE_PENALTIES.get(str(issue.get("type") or ""), 8) for issue in issues)
    penalty += round((1.0 - concept_ratio) * 55)
    score = max(0.0, min(100.0, 100.0 - penalty))
    issue_types = [str(issue.get("type") or "") for issue in issues]
    critical = assessment.is_hard_failure or bool(CRITICAL_ISSUES.intersection(issue_types)) or bool(missing_tokens)
    return {
        "score": round(score, 2),
        "concept_coverage": round(concept_ratio, 4),
        "issues": issues,
        "critical": critical,
        "adult_supported": bool(rendered) and not assessment.is_hard_failure and concept_ratio >= 0.75,
    }


def aggregate_model(model: str, samples: list[dict[str, Any]], *, protocol: str) -> dict[str, Any]:
    total = len(samples)
    successful = [sample for sample in samples if not sample.get("error")]
    ordinary = [sample for sample in successful if not sample.get("adult")]
    adult = [sample for sample in successful if sample.get("adult")]
    elapsed = sum(float(sample.get("elapsed_seconds") or 0.0) for sample in samples)
    source_chars = sum(len(str(sample.get("source") or "")) for sample in samples)
    critical_failures = sum(bool(sample.get("critical")) for sample in samples)
    success_rate = len(successful) / max(1, total)
    quality_score = mean(float(sample.get("score") or 0.0) for sample in ordinary) if ordinary else 0.0
    nsfw_score = mean(float(sample.get("score") or 0.0) for sample in adult) if adult else 0.0
    nsfw_supported = bool(adult) and all(bool(sample.get("adult_supported")) for sample in adult)
    issue_counts = Counter(
        str(issue.get("type") or "unknown")
        for sample in samples
        for issue in sample.get("issues") or []
    )
    return {
        "model": model,
        "protocol": protocol,
        "samples": total,
        "successful_samples": len(successful),
        "success_rate": round(success_rate, 4),
        "critical_failures": critical_failures,
        "qualified": success_rate >= 0.95 and critical_failures == 0,
        "quality_score": round(quality_score, 2),
        "nsfw_score": round(nsfw_score, 2),
        "nsfw_supported": nsfw_supported,
        "elapsed_seconds": round(elapsed, 4),
        "effective_chars_per_second": round(source_chars / elapsed, 3) if elapsed > 0 else 0.0,
        "issue_counts": dict(sorted(issue_counts.items())),
        "errors": [str(sample.get("error"))[:300] for sample in samples if sample.get("error")],
        "sample_results": samples,
    }


def recommend_profiles(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"warning": "没有可用于推荐的模型。", "auto_applicable": False, "profiles": {}}
    qualified = [item for item in results if item.get("qualified")]
    pool = qualified or results
    warning = "" if qualified else "没有模型通过可靠性门槛，以下推荐仅供人工复核。"
    max_speed = max(float(item.get("effective_chars_per_second") or 0.0) for item in pool) or 1.0
    for item in results:
        speed_score = min(100.0, float(item.get("effective_chars_per_second") or 0.0) / max_speed * 100.0)
        item["speed_score"] = round(speed_score, 2)
        item["balanced_score"] = round(float(item.get("quality_score") or 0.0) * 0.65 + speed_score * 0.35, 2)

    quality = max(pool, key=lambda item: (item["quality_score"], item["speed_score"]))
    efficiency_pool = [
        item for item in pool
        if float(item.get("quality_score") or 0.0) >= max(70.0, float(quality["quality_score"]) - 15.0)
    ] or pool
    efficiency = max(efficiency_pool, key=lambda item: (item["speed_score"], item["quality_score"]))
    balanced = max(pool, key=lambda item: (item["balanced_score"], item["quality_score"]))

    adult_pool = [item for item in pool if item.get("nsfw_supported")]
    nsfw_primary = max(adult_pool, key=lambda item: (item["nsfw_score"], item["quality_score"])) if adult_pool else None
    nsfw_fallback = None
    if nsfw_primary:
        alternatives = [item for item in adult_pool if item["model"] != nsfw_primary["model"]]
        if alternatives:
            primary_family = _model_family(nsfw_primary["model"])
            nsfw_fallback = max(
                alternatives,
                key=lambda item: (
                    float(item["nsfw_score"])
                    + (5.0 if _model_family(item["model"]) != primary_family else 0.0)
                    + (3.0 if item.get("protocol") != nsfw_primary.get("protocol") else 0.0),
                    item["quality_score"],
                ),
            )

    adult_model = nsfw_primary["model"] if nsfw_primary else balanced["model"]
    fallback_model = nsfw_fallback["model"] if nsfw_fallback else adult_model
    profiles = {
        "quality": _profile(quality["model"], quality["model"], quality["model"], adult_model, fallback_model),
        "efficiency": _profile(efficiency["model"], efficiency["model"], quality["model"], adult_model, fallback_model),
        "balanced": _profile(balanced["model"], efficiency["model"], quality["model"], adult_model, fallback_model),
        "nsfw": _profile(balanced["model"], efficiency["model"], quality["model"], adult_model, fallback_model),
    }
    return {
        "warning": warning,
        "auto_applicable": bool(qualified),
        "quality_model": quality["model"],
        "efficiency_model": efficiency["model"],
        "balanced_model": balanced["model"],
        "nsfw_primary_model": nsfw_primary["model"] if nsfw_primary else "",
        "nsfw_fallback_model": nsfw_fallback["model"] if nsfw_fallback else "",
        "profiles": profiles,
    }


def _profile(primary: str, fast: str, quality: str, sensitive: str, fallback: str) -> dict[str, str]:
    return {
        "primary_model": primary,
        "fast_model": fast,
        "quality_model": quality,
        "sensitive_model": sensitive,
        "sensitive_fallback_model": fallback,
    }


def _model_family(model: str) -> str:
    name = str(model).split(":", 1)[-1].lower()
    return name.split("-", 1)[0].split(".", 1)[0]


__all__ = ["aggregate_model", "recommend_profiles", "score_output"]
