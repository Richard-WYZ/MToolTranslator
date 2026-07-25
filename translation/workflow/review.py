from __future__ import annotations

from translation.context import TranslationWorkflowContext
from translation.review import build_review_summary, write_review_report


class ReviewPreparationStage:
    """Prepare the explicit proofreading queue after translation execution."""

    name = "review_preparation"

    def run(self, context: TranslationWorkflowContext) -> TranslationWorkflowContext:
        if context.result is None:
            raise RuntimeError("Review preparation requires a translation result")
        summary = build_review_summary(context.request.file_path).as_dict()
        report_path = write_review_report(
            context.request.file_path,
            context.result.output_path,
        )
        context.analysis["review"] = summary
        context.analysis["review_report_path"] = report_path
        context.result.review_summary = summary
        context.result.review_report_path = report_path
        return context


__all__ = ["ReviewPreparationStage"]
