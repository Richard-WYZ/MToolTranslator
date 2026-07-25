from __future__ import annotations

from typing import Any

from translation.batching.protocol import api_job_is_short_text, resolve_candidate_batch_protocol
from translation.classification import (
    candidate_has_explicit_adult_content,
    looks_like_dialogue_boundary,
)

_TERMINAL_RETRY_ISSUE_TYPES = {
    "api_quota_exhausted",
    "api_batch_transport_error",
    "api_content_filter_fallback",
    "api_request_fallback",
}


def default_batch_options(batch_cfg: dict[str, Any]) -> dict[str, Any]:
    """Return default model options for one batch translation request."""
    options = {
        "temperature": batch_cfg.get("temperature", 0),
        "num_predict": batch_cfg.get("num_predict", 2048),
    }
    if batch_cfg.get("compact_json_protocol", False):
        options["compact_json_protocol"] = True
    return options


def uses_api_parallel_batches(batch_cfg: dict[str, Any], *, model: str, provider: str) -> bool:
    """Return whether a run should use API parallel batch scheduling."""
    if not batch_cfg.get("api_parallel_enabled", False):
        return False
    if int(batch_cfg.get("api_concurrency", 1) or 1) <= 1:
        return False
    return model.startswith("api:") or provider == "api"


def resolve_parallel_candidate_protocol(
    configured_protocol: str,
    default_protocol: str,
    candidates: list[dict[str, Any]],
    batch_cfg: dict[str, Any],
) -> str:
    """Resolve protocol for an API-parallel job without mixing long text into line mode."""
    if any(candidate.get("contexts") for candidate in candidates):
        return "json"
    if not batch_cfg.get("line_for_short_only", True):
        return resolve_candidate_batch_protocol(configured_protocol, default_protocol, candidates)
    if api_job_is_short_text(candidates, batch_cfg):
        if configured_protocol == "line":
            return "line"
        return resolve_candidate_batch_protocol(configured_protocol, default_protocol, candidates)
    if configured_protocol == "line":
        return "json"
    return resolve_candidate_batch_protocol(configured_protocol, default_protocol, candidates)


def select_api_job_model(candidates: list[dict[str, Any]], batch_cfg: dict[str, Any], *, default_model: str) -> str:
    """Select a routed API model for a batch job."""
    if api_job_is_sensitive_adult(candidates, batch_cfg):
        return str(batch_cfg.get("api_sensitive_model") or default_model)
    if not batch_cfg.get("api_model_routing_enabled", False):
        return default_model
    if api_job_uses_fast_model(candidates, batch_cfg):
        return str(batch_cfg.get("api_fast_model") or default_model)
    return str(batch_cfg.get("api_quality_model") or default_model)


def select_api_job_options(
    candidates: list[dict[str, Any]],
    batch_options: dict[str, Any],
    batch_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Return per-job options, expanding output budget for quality-routed jobs."""
    options = dict(batch_options)
    if api_job_is_sensitive_adult(candidates, batch_cfg):
        return options
    if batch_cfg.get("api_model_routing_enabled", False) and not api_job_uses_fast_model(candidates, batch_cfg):
        if batch_cfg.get("quality_num_predict"):
            options["num_predict"] = int(batch_cfg["quality_num_predict"])
    return options


def candidate_batch_category(candidate: dict[str, Any], batch_cfg: dict[str, Any]) -> str:
    """Classify a candidate for homogeneous API batching."""
    if api_job_is_short_text([candidate], batch_cfg):
        return "short_label"

    source = str(candidate.get("source", ""))
    if "\n" in source or "\r" in source:
        return "multiline"
    if _candidate_is_dialogue(candidate):
        return "dialogue"
    long_text_min_chars = max(1, int(batch_cfg.get("long_text_min_chars", 120)))
    if len(source) >= long_text_min_chars:
        return "long_narrative"
    return "prose"


def _candidate_is_dialogue(candidate: dict[str, Any]) -> bool:
    """Recognize dialogue boundaries without treating quoted UI terms as speech."""
    source = str(candidate.get("source", "")).strip()
    if looks_like_dialogue_boundary(source):
        return True

    for context in candidate.get("contexts", []) or []:
        if not isinstance(context, dict):
            continue
        context_text = str(context.get("text", "")).strip()
        if not context_text:
            continue
        if looks_like_dialogue_boundary(context_text):
            return True
        lines = context_text.splitlines()
        line_number = max(1, int(context.get("line", 1) or 1))
        if lines:
            target_line = lines[min(line_number - 1, len(lines) - 1)].strip()
            if looks_like_dialogue_boundary(target_line):
                return True
    return False


def api_job_is_sensitive_adult(
    candidates: list[dict[str, Any]],
    batch_cfg: dict[str, Any],
) -> bool:
    """Return whether a batch must use the configured adult-capable route."""
    if not candidates or not batch_cfg.get("api_sensitive_routing_enabled", False):
        return False
    return any(
        bool(candidate.get("sensitive_adult"))
        or candidate_has_explicit_adult_content(candidate)
        for candidate in candidates
    )


def api_job_uses_fast_model(candidates: list[dict[str, Any]], batch_cfg: dict[str, Any]) -> bool:
    """Return whether every candidate belongs to a configured fast-model category."""
    if not candidates:
        return False
    configured = batch_cfg.get("api_fast_categories", ["short_label"])
    if isinstance(configured, str):
        fast_categories = {item.strip() for item in configured.split(",") if item.strip()}
    else:
        fast_categories = {str(item).strip() for item in configured or [] if str(item).strip()}
    if not fast_categories:
        fast_categories = {"short_label"}
    return all(candidate_batch_category(candidate, batch_cfg) in fast_categories for candidate in candidates)


def needs_quality_model_retry(
    status: str,
    issues: list[dict[str, Any]],
    batch_cfg: dict[str, Any],
) -> bool:
    """Return whether a fast-model result should be regenerated by the quality model."""
    if any(str(issue.get("type", "")) in _TERMINAL_RETRY_ISSUE_TYPES for issue in issues):
        return False
    if status == "review_required":
        return True
    configured = batch_cfg.get("api_quality_retry_issue_types", ())
    if isinstance(configured, str):
        issue_types = {item.strip() for item in configured.split(",") if item.strip()}
    else:
        issue_types = {str(item).strip() for item in configured or [] if str(item).strip()}
    return any(str(issue.get("type", "")) in issue_types for issue in issues)


def candidate_needs_quality_model_retry(
    candidate: dict[str, Any],
    status: str,
    issues: list[dict[str, Any]],
    batch_cfg: dict[str, Any],
) -> bool:
    """Keep adult-sensitive entries away from a restrictive quality route."""
    if api_job_is_sensitive_adult([candidate], batch_cfg):
        return False
    return needs_quality_model_retry(status, issues, batch_cfg)


def candidate_needs_sensitive_repair(
    candidate: dict[str, Any],
    status: str,
    issues: list[dict[str, Any]],
    batch_cfg: dict[str, Any],
    *,
    repair_round: int,
) -> bool:
    """Select validation failures for bounded repair with the same adult-capable model."""
    if not batch_cfg.get("api_sensitive_repair_enabled", False):
        return False
    if not api_job_is_sensitive_adult([candidate], batch_cfg):
        return False
    issue_types = {
        str(issue.get("type", ""))
        for issue in issues
        if isinstance(issue, dict) and str(issue.get("type", ""))
    }
    if issue_types & _TERMINAL_RETRY_ISSUE_TYPES:
        return False

    configured = batch_cfg.get("api_sensitive_repair_issue_types", ())
    if isinstance(configured, str):
        repair_issue_types = {
            item.strip()
            for item in configured.split(",")
            if item.strip()
        }
    else:
        repair_issue_types = {
            str(item).strip()
            for item in configured or ()
            if str(item).strip()
        }
    if not issue_types.intersection(repair_issue_types):
        return False

    # The second and final round is intentionally narrower: soft review warnings
    # remain review items instead of triggering one API call per entry.
    if repair_round >= 2 and status != "review_required":
        return False
    return True


def pack_api_candidate_batches(
    candidates: list[dict[str, Any]],
    *,
    batch_size: int,
    max_batch_chars: int,
    batch_cfg: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    """Pack candidates by text category so fast and quality routes never mix."""
    from translation.batching.candidates import candidate_template_key, reindex_candidates

    buckets: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for candidate in candidates:
        category = candidate_batch_category(candidate, batch_cfg)
        sensitive_adult = (
            bool(candidate.get("sensitive_adult"))
            or candidate_has_explicit_adult_content(candidate)
        ) if batch_cfg.get("api_sensitive_routing_enabled", False) else False
        buckets.setdefault((category, sensitive_adult), []).append(dict(
            candidate,
            entry_classification=category,
            sensitive_adult=sensitive_adult,
        ))

    packed: list[list[dict[str, Any]]] = []
    max_items = max(1, int(batch_size))
    max_chars = max(1, int(max_batch_chars))
    for bucket in buckets.values():
        template_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for candidate in bucket:
            template_groups.setdefault(candidate_template_key(candidate), []).append(candidate)
        current: list[dict[str, Any]] = []
        current_chars = 0
        current_templates = 0
        current_contexts: set[str] = set()
        for group in template_groups.values():
            representative = group[0]
            candidate_chars = len(str(representative.get("protected", representative.get("text", ""))))
            candidate_contexts = {
                str(context.get("text", ""))
                for context in representative.get("contexts", []) or []
                if isinstance(context, dict) and str(context.get("text", ""))
            }
            added_context_chars = sum(
                len(context)
                for context in candidate_contexts
                if context not in current_contexts
            )
            if current and (
                current_templates >= max_items
                or current_chars + candidate_chars + added_context_chars > max_chars
            ):
                packed.append(reindex_candidates(current))
                current = []
                current_chars = 0
                current_templates = 0
                current_contexts = set()
                added_context_chars = sum(len(context) for context in candidate_contexts)
            current.extend(group)
            current_chars += candidate_chars + added_context_chars
            current_templates += 1
            current_contexts.update(candidate_contexts)
        if current:
            packed.append(reindex_candidates(current))
    return packed


__all__ = [
    "default_batch_options",
    "api_job_is_sensitive_adult",
    "api_job_uses_fast_model",
    "candidate_needs_quality_model_retry",
    "candidate_needs_sensitive_repair",
    "candidate_batch_category",
    "needs_quality_model_retry",
    "pack_api_candidate_batches",
    "resolve_parallel_candidate_protocol",
    "select_api_job_model",
    "select_api_job_options",
    "uses_api_parallel_batches",
]
