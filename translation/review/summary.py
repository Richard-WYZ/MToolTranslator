from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import translation.checkpoint as checkpoint


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    total: int
    translated: int
    preserved: int
    translated_needs_review: int
    review_required: int
    pending: int
    issue_entries: int

    @property
    def review_queue_size(self) -> int:
        return self.translated_needs_review + self.review_required

    def as_dict(self) -> dict[str, int]:
        return {**asdict(self), "review_queue_size": self.review_queue_size}


def build_review_summary(file_path: str) -> ReviewSummary:
    data = checkpoint.load_checkpoint(file_path)
    raw_entries = data.get("entries", {})
    entries = raw_entries.values() if isinstance(raw_entries, dict) else []
    counts = {
        "translated": 0,
        "preserved": 0,
        "translated_needs_review": 0,
        "review_required": 0,
    }
    issue_entries = 0
    final_entries = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        status = checkpoint.normalize_status(str(entry.get("status", "")))
        if status in counts:
            counts[status] += 1
            final_entries += 1
        if entry.get("issues"):
            issue_entries += 1

    total = max(int(data.get("total", 0) or 0), final_entries)
    return ReviewSummary(
        total=total,
        translated=counts["translated"],
        preserved=counts["preserved"],
        translated_needs_review=counts["translated_needs_review"],
        review_required=counts["review_required"],
        pending=max(total - final_entries, 0),
        issue_entries=issue_entries,
    )


__all__ = ["ReviewSummary", "build_review_summary"]
