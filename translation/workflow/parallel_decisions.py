"""Provider-neutral decisions shared by phased and event-driven execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from translation.batching import BatchJob, candidate_needs_quality_model_retry, candidate_needs_sensitive_repair


_STRUCTURAL_QUALITY_ISSUE_TYPES = frozenset({
    "line_break_preservation",
    "length_expansion",
    "short_label_expansion",
    "symbol_preservation",
    "runtime_token_preservation",
    "numeric_preservation",
    "marker_lost",
    "version_marker_lost",
    "term_placeholder_leak",
})

Candidate = dict[str, Any]
Payload = tuple[str, str, list[dict[str, Any]]]


@dataclass
class PrimaryDecision:
    accepted: list[Candidate]
    payloads: dict[int, Payload]
    quality: list[Candidate]
    sensitive: list[Candidate]


def partition_primary_result(
    job: BatchJob, payloads: dict[int, Payload], *,
    batch_cfg: dict[str, Any], model: str, attempts: int,
) -> PrimaryDecision:
    """Decide each candidate's next stage once, independently of scheduling."""
    from translation.workflow.parallel_support import _sensitive_repair_candidate

    fast = str(batch_cfg.get("api_fast_model") or "")
    quality_model = str(batch_cfg.get("api_quality_model") or "")
    can_retry_quality = bool(
        batch_cfg.get("api_model_routing_enabled", False)
        and fast and quality_model and fast != quality_model
        and job.model in {fast, quality_model}
    )
    decision = PrimaryDecision([], {}, [], [])
    for candidate in job.candidates:
        index = int(candidate["idx"])
        translated, status, issues = payloads[index]
        if can_retry_quality and candidate_needs_quality_model_retry(candidate, status, issues, batch_cfg):
            decision.quality.append(dict(candidate, quality_retry={
                "previous": translated,
                "issues": [str(issue.get("type", "")) for issue in issues if str(issue.get("type", ""))],
            }))
        elif candidate_needs_sensitive_repair(candidate, status, issues, batch_cfg, repair_round=1):
            decision.sensitive.append(_sensitive_repair_candidate(
                candidate, translated, issues, model=model,
                repair_round=1, prior_retry_count=max(0, int(attempts) - 1),
            ))
        else:
            decision.accepted.append(candidate)
            decision.payloads[index] = payloads[index]
    return decision
