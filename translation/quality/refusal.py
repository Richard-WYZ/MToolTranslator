from __future__ import annotations

import re
from dataclasses import dataclass


EXPLICIT_ENGLISH_REFUSAL_MARKERS = (
    "i can't assist",
    "i cannot assist",
    "i can’t assist",
    "i cannot comply",
    "i'm sorry",
    "i am sorry",
    "as an ai",
    "cannot translate",
    "can't translate",
    "unable to translate",
    "refuse",
    "violation",
    "inappropriate",
    "apologize",
    "i cannot",
    "i can't",
    "i can not",
    "i'm unable",
    "i am unable",
    "not able",
)

# Small tsu and kana voicing marks are often retained as non-lexical sound
# notation inside otherwise translated game dialogue.  They are not evidence
# of a Japanese refusal or untranslated lexical content by themselves.
JAPANESE_KANA_RE = re.compile(
    r"[\u3041-\u3062\u3064-\u3098\u309d-\u309f"
    r"\u30a1-\u30c2\u30c4-\u30fa\u30fd-\u30ff]"
)
PRESERVED_NAME_READING_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]{1,16}[（(]"
    r"[\u3041-\u309f\u30a1-\u30fa\u30fd-\u30ff]{1,24}[)）]"
)
CHINESE_EXPLICIT_REFUSAL_RE = re.compile(
    r"(?:\u4f5c\u4e3a|\u8eab\u4e3a).{0,8}(?:AI|ai|\u4eba\u5de5\u667a\u80fd).{0,24}"
    r"(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u4fbf|\u62d2\u7edd)"
    r"|(?:AI|ai|\u4eba\u5de5\u667a\u80fd).{0,8}(?:\u52a9\u624b|\u6a21\u578b).{0,24}"
    r"(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u4fbf|\u62d2\u7edd)"
    r"|(?:\u62b1\u6b49|\u5bf9\u4e0d\u8d77|\u5f88\u9057\u61be)[\uff0c,\u3002\s]*"
    r"(?:\u6211|\u6211\u4eec|\u672c\u52a9\u624b|\u672c\u6a21\u578b)?.{0,8}"
    r"(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u4fbf).{0,12}"
    r"(?:\u534f\u52a9|\u5e2e\u52a9|\u7ffb\u8bd1|\u63d0\u4f9b|\u5904\u7406|\u56de\u7b54)"
    r"|(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u4fbf).{0,8}"
    r"(?:\u534f\u52a9|\u5e2e\u52a9).{0,8}(?:\u7ffb\u8bd1|\u5904\u7406|\u5b8c\u6210|\u63d0\u4f9b)"
    r"|(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u4fbf).{0,8}\u7ffb\u8bd1"
    r"|(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u4fbf).{0,8}\u63d0\u4f9b.{0,6}"
    r"(?:\u7ffb\u8bd1|\u5e2e\u52a9|\u56de\u7b54)"
    r"|(?:\u65e0\u6cd5|\u4e0d\u80fd).{0,4}\u6ee1\u8db3"
    r"(?:\u8be5|\u6b64|\u8fd9\u4e2a|\u60a8\u7684|\u4f60\u7684)\u8bf7\u6c42"
    r"|(?:\u8be5|\u6b64|\u8fd9\u4e2a|\u60a8\u7684|\u4f60\u7684)\u8bf7\u6c42.{0,12}"
    r"(?:\u8fdd\u53cd\u653f\u7b56|\u8fdd\u89c4|\u65e0\u6cd5\u5904\u7406|\u4e0d\u80fd\u5904\u7406)"
    r"|\u8fdd\u53cd\u653f\u7b56|\u8fdd\u89c4\u5185\u5bb9"
    r"|(?:\u4e0d\u9002\u5408|\u4e0d\u9002\u5f53|\u4e0d\u9069\u5207).{0,12}"
    r"(?:\u7ffb\u8bd1|\u534f\u52a9|\u5904\u7406|\u56de\u7b54)"
)
CHINESE_SUSPECTED_META_RE = re.compile(
    r"(?:\u6211|\u6211\u4eec|\u672c\u52a9\u624b|\u672c\u6a21\u578b).{0,8}"
    r"(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u4fbf|\u62d2\u7edd).{0,12}"
    r"(?:\u8bf7\u6c42|\u5185\u5bb9)"
    r"|(?:\u8be5|\u6b64|\u8fd9\u4e2a|\u60a8\u7684|\u4f60\u7684)\u8bf7\u6c42.{0,8}"
    r"(?:\u4e0d\u9002\u5408|\u4e0d\u9002\u5f53|\u4e0d\u9069\u5207)"
    r"|(?:\u62b1\u6b49|\u5bf9\u4e0d\u8d77|\u5f88\u9057\u61be).{0,8}"
    r"(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u4fbf).{0,8}(?:\u5b8c\u6210|\u6ee1\u8db3|\u5904\u7406)?"
)


@dataclass(frozen=True, slots=True)
class ModelOutputAssessment:
    issue_type: str = ""
    severity: str = "none"
    message: str = ""
    evidence: str = ""

    @property
    def is_hard_failure(self) -> bool:
        return self.severity == "hard"

    @property
    def is_advisory(self) -> bool:
        return self.severity == "advisory"

    def as_issue(self) -> dict[str, str]:
        issue = {"type": self.issue_type, "message": self.message}
        if self.evidence:
            issue["evidence"] = self.evidence
        return issue


def has_japanese(text: str, original: str = "") -> bool:
    candidate = str(text or "")
    if original:
        source_readings = set(PRESERVED_NAME_READING_RE.findall(str(original)))
        for reading in source_readings:
            if reading in candidate:
                candidate = candidate.replace(reading, "", str(original).count(reading))
    return bool(JAPANESE_KANA_RE.search(candidate))


def assess_model_output(text: str, original: str = "") -> ModelOutputAssessment:
    """Classify unusable output without confusing narrative wording with model refusals."""
    if not text or not text.strip():
        return ModelOutputAssessment(
            issue_type="empty_translation",
            severity="hard",
            message="Model returned an empty translation.",
        )

    stripped = text.strip()
    if _is_only_punctuation(stripped):
        return ModelOutputAssessment(
            issue_type="empty_translation",
            severity="hard",
            message="Model returned punctuation without translatable content.",
            evidence=stripped[:40],
        )

    if has_japanese(stripped, original=original):
        return ModelOutputAssessment(
            issue_type="untranslated_japanese",
            severity="hard",
            message="Japanese kana remain in the model output.",
        )

    lowered = stripped.lower()
    english_marker = next((marker for marker in EXPLICIT_ENGLISH_REFUSAL_MARKERS if marker in lowered), "")
    if english_marker:
        return ModelOutputAssessment(
            issue_type="model_refusal",
            severity="hard",
            message="Model returned an explicit refusal instead of a translation.",
            evidence=english_marker,
        )

    explicit_match = CHINESE_EXPLICIT_REFUSAL_RE.search(stripped)
    if explicit_match:
        return ModelOutputAssessment(
            issue_type="model_refusal",
            severity="hard",
            message="Model returned explicit refusal language instead of a translation.",
            evidence=explicit_match.group(0),
        )

    suspected_match = CHINESE_SUSPECTED_META_RE.search(stripped)
    if suspected_match:
        return ModelOutputAssessment(
            issue_type="suspected_meta_response",
            severity="advisory",
            message="Output resembles a model meta-response; verify the translation before export.",
            evidence=suspected_match.group(0),
        )

    return ModelOutputAssessment()


def is_refusal(text: str, original: str = "") -> bool:
    """Return True only for explicit model refusal language."""
    assessment = assess_model_output(text, original=original)
    return assessment.issue_type == "model_refusal" and assessment.is_hard_failure


def is_unusable_model_output(text: str, original: str = "") -> bool:
    """Return True for output that should trigger a retry or hard review."""
    return assess_model_output(text, original=original).is_hard_failure


def _is_only_punctuation(text: str) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    return not bool(re.search(r"[\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf]", stripped))


__all__ = [
    "ModelOutputAssessment",
    "assess_model_output",
    "has_japanese",
    "is_refusal",
    "is_unusable_model_output",
]
