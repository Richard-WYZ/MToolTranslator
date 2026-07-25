from __future__ import annotations

import translation.checkpoint as checkpoint
from translation.analysis import classify_mtool_file
from translation.context import TranslationWorkflowContext
from translation.terminology import Glossary


class MToolAnalysisStage:
    """Analyze MTool input before translation execution."""

    name = "mtool_analysis"

    def run(self, context: TranslationWorkflowContext) -> TranslationWorkflowContext:
        request = context.request
        glossary_path = request.glossary_path or checkpoint.get_glossary_path(request.file_path)
        glossary = Glossary(file_path=glossary_path)
        context.analysis["mtool"] = classify_mtool_file(request.file_path, glossary=glossary)
        context.analysis["glossary_path"] = glossary_path
        return context


__all__ = ["MToolAnalysisStage"]
