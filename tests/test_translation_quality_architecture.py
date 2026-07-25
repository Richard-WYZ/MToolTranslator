import json
import os
import ast
from pathlib import Path
import tempfile


def test_application_layer_uses_translation_facades_not_translator_internals():
    root = Path(__file__).resolve().parents[1]
    checked_paths = [
        root / "app",
        root / "main.py",
        root / "desktop.py",
        root / "tools" / "run_api_full_translation.py",
    ]
    violations: list[str] = []

    def iter_python_files(path: Path):
        if path.is_file():
            yield path
            return
        for child in path.rglob("*.py"):
            if "__pycache__" not in child.parts:
                yield child

    for base in checked_paths:
        for path in iter_python_files(base):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module == "translator" or (node.module or "").startswith("translator.")):
                    violations.append(f"{path.relative_to(root)}:{node.lineno} imports {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "translator" or alias.name.startswith("translator."):
                            violations.append(f"{path.relative_to(root)}:{node.lineno} imports {alias.name}")

    assert violations == []


def test_canonical_layers_do_not_depend_on_legacy_translator_package():
    root = Path(__file__).resolve().parents[1]
    checked_paths = [
        root / "translation",
        root / "tools",
        root / "app",
        root / "ui",
        root / "common",
    ]
    violations: list[str] = []

    for base in checked_paths:
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (
                    node.module == "translator" or (node.module or "").startswith("translator.")
                ):
                    violations.append(f"{path.relative_to(root)}:{node.lineno} imports {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "translator" or alias.name.startswith("translator."):
                            violations.append(f"{path.relative_to(root)}:{node.lineno} imports {alias.name}")

    assert violations == []


def test_canonical_layers_do_not_depend_on_root_compatibility_modules():
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for package in ("translation", "app", "common"):
        for path in (root / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (
                    node.module == "config" or (node.module or "").startswith("parser.")
                ):
                    violations.append(f"{path.relative_to(root)}:{node.lineno} imports {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "config" or alias.name.startswith("parser."):
                            violations.append(f"{path.relative_to(root)}:{node.lineno} imports {alias.name}")

    assert violations == []


def test_root_config_and_parser_modules_are_thin_compatibility_facades():
    import config
    import parser.json_parser as legacy_json
    from translation import settings
    from translation.input import load_json_items
    from translation.output import serialize_json_items

    root = Path(__file__).resolve().parents[1]
    assert config is settings
    assert legacy_json.parse_json is load_json_items
    assert legacy_json.serialize_json_items is serialize_json_items
    assert len((root / "config.py").read_text(encoding="utf-8")) < 400
    assert len((root / "parser" / "json_parser.py").read_text(encoding="utf-8")) < 800
    assert len((root / "parser" / "csv_parser.py").read_text(encoding="utf-8")) < 600


def test_build_configuration_packages_canonical_layers_not_compatibility_packages():
    root = Path(__file__).resolve().parents[1]
    text = (root / "build.spec").read_text(encoding="utf-8")

    assert "translation.workflow.pipeline" in text
    assert "translation.settings" in text
    assert "app.desktop" in text
    assert "'translator" not in text
    assert "'parser" not in text
    assert "'config.py'" not in text


def test_test_and_build_commands_use_dedicated_workspaces():
    root = Path(__file__).resolve().parents[1]
    pytest_config = (root / "pytest.ini").read_text(encoding="utf-8")
    test_script = (root / "tools" / "run_tests.ps1").read_text(encoding="utf-8")
    build_script = (root / "tools" / "build.ps1").read_text(encoding="utf-8")

    assert "cache_dir = test_work/pytest/cache" in pytest_config
    assert "--basetemp=test_work/pytest/tmp" in pytest_config
    assert 'PYTHONDONTWRITEBYTECODE = "1"' in test_script
    assert '"test_work\\pytest"' in test_script
    assert 'Join-Path $BuildRoot "work"' in build_script
    assert 'Join-Path $BuildRoot "dist"' in build_script
    assert "--workpath $WorkPath --distpath $DistPath" in build_script


def test_glossary_delegates_candidate_policy_and_storage_to_focused_modules():
    root = Path(__file__).resolve().parents[1]
    glossary_text = (root / "translation" / "terminology" / "glossary.py").read_text(encoding="utf-8")
    policy_text = (root / "translation" / "terminology" / "candidate_policy.py").read_text(encoding="utf-8")
    store_text = (root / "translation" / "terminology" / "store.py").read_text(encoding="utf-8")

    assert "from translation.terminology.candidate_policy import" in glossary_text
    assert "from translation.terminology.store import" in glossary_text
    assert "def extract_terms(" in policy_text
    assert "def score_term(" in policy_text
    assert "def read_glossary(" in store_text
    assert "def write_glossary(" in store_text
    assert "json.dump(" not in glossary_text
    assert "open(" not in glossary_text


def test_application_layer_uses_configuration_facades_not_root_config():
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []

    for path in (root / "app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "config":
                        violations.append(f"{path.relative_to(root)}:{node.lineno} imports root config")
            elif isinstance(node, ast.ImportFrom) and node.module == "config":
                violations.append(f"{path.relative_to(root)}:{node.lineno} imports root config")

    assert violations == []


def test_production_tools_use_translation_domain_facades():
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "tools" / "run_api_full_translation.py",
        root / "tools" / "profile_api_routing_matrix.py",
        root / "tools" / "profile_offline_dictionary.py",
        root / "tools" / "benchmark_manual_trans_file.py",
        root / "tools" / "profile_batch_throughput.py",
    ]
    forbidden_modules = {
        "translator.batch",
        "translator.glossary",
        "translator.label_patterns",
        "translator.label_rules",
        "translator.model_router",
        "translator.offline_dictionary",
        "translator.ollama_client",
        "translator.pipeline",
        "translator.quality",
        "translator.refusal_detector",
        "translator.symbols",
    }
    violations: list[str] = []
    for path in checked_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                violations.append(f"{path.relative_to(root)}:{node.lineno} imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        violations.append(f"{path.relative_to(root)}:{node.lineno} imports {alias.name}")

    assert violations == []


def test_production_tools_use_configuration_facades_not_root_config():
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "tools" / "run_api_full_translation.py",
        root / "tools" / "profile_api_routing_matrix.py",
        root / "tools" / "benchmark_manual_trans_file.py",
        root / "tools" / "profile_batch_throughput.py",
    ]
    violations: list[str] = []

    for path in checked_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "config":
                        violations.append(f"{path.relative_to(root)}:{node.lineno} imports root config")
            elif isinstance(node, ast.ImportFrom) and node.module == "config":
                violations.append(f"{path.relative_to(root)}:{node.lineno} imports root config")

    assert violations == []


def test_application_and_pipeline_use_translation_json_io_facades():
    root = Path(__file__).resolve().parents[1]
    checked_paths = [
        root / "app",
        root / "tools",
        root / "translator" / "pipeline.py",
        root / "translator" / "writer.py",
        root / "translation" / "analysis",
    ]
    allowed = {
        root / "translation" / "input" / "json_io.py",
        root / "translation" / "output" / "json_io.py",
    }
    violations: list[str] = []

    def iter_python_files(path: Path):
        if path.is_file():
            yield path
            return
        for child in path.rglob("*.py"):
            if "__pycache__" not in child.parts:
                yield child

    for base in checked_paths:
        for path in iter_python_files(base):
            if path in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "parser.json_parser":
                    violations.append(f"{path.relative_to(root)}:{node.lineno} imports parser.json_parser")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "parser.json_parser":
                            violations.append(f"{path.relative_to(root)}:{node.lineno} imports parser.json_parser")

    assert violations == []


def test_diagnostics_pipeline_is_the_only_tool_legacy_pipeline_adapter():
    from translation.diagnostics import (
        build_diagnostic_pipeline,
        diagnostic_batch_translator,
        diagnostic_glossary,
        finish_diagnostic_batch_translation,
    )
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    translation_sources = [
        path
        for path in (root / "translation").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert all("translator.pipeline" not in path.read_text(encoding="utf-8") for path in translation_sources)

    pipeline = build_diagnostic_pipeline()

    assert isinstance(pipeline, TranslationPipeline)
    assert diagnostic_glossary(pipeline) is pipeline.glossary
    assert diagnostic_batch_translator(pipeline).__self__ is pipeline
    assert diagnostic_batch_translator(pipeline).__func__ is pipeline._batch_translate_call.__func__
    assert callable(finish_diagnostic_batch_translation)


def test_legacy_pipeline_module_aliases_workflow_pipeline():
    import sys
    import translator.pipeline as legacy_pipeline
    import translation.workflow.pipeline as workflow_pipeline

    assert legacy_pipeline is workflow_pipeline
    assert sys.modules["translator.pipeline"] is workflow_pipeline
    assert legacy_pipeline.TranslationPipeline is workflow_pipeline.TranslationPipeline


def test_legacy_translator_modules_are_thin_compatibility_shims():
    root = Path(__file__).resolve().parents[1]
    shim_files = [
        "api_client.py",
        "batch.py",
        "checkpoint.py",
        "constraints.py",
        "glossary.py",
        "label_patterns.py",
        "label_rules.py",
        "model_router.py",
        "offline_dictionary.py",
        "ollama_client.py",
        "pipeline.py",
        "pollution.py",
        "quality.py",
        "refusal_detector.py",
        "scheduler.py",
        "symbols.py",
        "usage.py",
        "writer.py",
    ]
    violations: list[str] = []

    for name in shim_files:
        path = root / "translator" / name
        text = path.read_text(encoding="utf-8")
        if "Legacy implementation body retained" in text:
            violations.append(f"{name} retains legacy body")
        if len(text) > 1200:
            violations.append(f"{name} is too large for a compatibility shim")

    assert violations == []


def test_production_tools_do_not_call_legacy_pipeline_private_members():
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "tools" / "profile_offline_dictionary.py",
        root / "tools" / "benchmark_manual_trans_file.py",
        root / "tools" / "profile_batch_throughput.py",
    ]
    forbidden_attrs = {"_batch_translate_call", "_finish_batch_translation", "glossary"}
    violations: list[str] = []

    for path in checked_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                violations.append(f"{path.relative_to(root)}:{node.lineno} uses .{node.attr}")

    assert violations == []


def test_production_tools_use_analysis_for_mtool_pretranslation_flow():
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "tools" / "profile_offline_dictionary.py",
        root / "tools" / "benchmark_manual_trans_file.py",
        root / "tools" / "profile_batch_throughput.py",
    ]
    forbidden_modules = {
        "parser.json_parser",
        "translation.input",
        "translation.protection",
    }
    violations: list[str] = []

    for path in checked_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                violations.append(f"{path.relative_to(root)}:{node.lineno} imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        violations.append(f"{path.relative_to(root)}:{node.lineno} imports {alias.name}")

    assert violations == []


def test_legacy_pipeline_uses_domain_facades_for_migrated_dependencies():
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "translator" / "pipeline.py",
        root / "translation" / "workflow" / "legacy_pipeline.py",
    ]
    forbidden_modules = {
        "translator.batch",
        "translator.constraints",
        "translator.model_router",
        "translator.glossary",
        "translator.label_rules",
        "translator.pollution",
        "translator.quality",
        "translator.refusal_detector",
        "translator.scheduler",
        "translator.symbols",
        "translator.writer",
    }
    forbidden_from_translator = {"checkpoint", "usage"}
    violations: list[str] = []

    for path in checked_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module in forbidden_modules:
                    violations.append(f"{path.relative_to(root)}:{node.lineno} imports {node.module}")
                if node.module == "translator":
                    for alias in node.names:
                        if alias.name in forbidden_from_translator:
                            violations.append(f"{path.relative_to(root)}:{node.lineno} imports translator.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        violations.append(f"{path.relative_to(root)}:{node.lineno} imports {alias.name}")

    assert violations == []


def test_translation_workflow_runs_stages_in_order():
    from translation.context import TranslationRequest, TranslationWorkflowContext
    from translation.workflow.runner import TranslationWorkflow

    calls: list[str] = []

    class FirstStage:
        name = "first"

        def run(self, context: TranslationWorkflowContext) -> TranslationWorkflowContext:
            calls.append(self.name)
            context.request.task_id = "first-completed"
            return context

    class SecondStage:
        name = "second"

        def run(self, context: TranslationWorkflowContext) -> TranslationWorkflowContext:
            calls.append(f"{self.name}:{context.request.task_id}")
            return context

    context = TranslationWorkflow([FirstStage(), SecondStage()]).run(
        TranslationWorkflowContext(request=TranslationRequest(file_path="fixture.json"))
    )

    assert calls == ["first", "second:first-completed"]
    assert context.request.task_id == "first-completed"


def test_default_translation_workflow_runs_named_modules_in_order():
    from translation.translate import build_workflow
    from translation.workflow import MToolAnalysisStage, PipelineBuildStage, ReviewPreparationStage, TranslationStage

    workflow = build_workflow()

    assert [stage.name for stage in workflow.stages] == [
        "mtool_analysis",
        "pipeline_build",
        "translation",
        "review_preparation",
    ]
    assert isinstance(workflow.stages[0], MToolAnalysisStage)
    assert isinstance(workflow.stages[1], PipelineBuildStage)
    assert isinstance(workflow.stages[2], TranslationStage)
    assert isinstance(workflow.stages[3], ReviewPreparationStage)


def test_translation_entrypoint_uses_canonical_execution_stages():
    root = Path(__file__).resolve().parents[1]
    entrypoint_text = (root / "translation" / "translate.py").read_text(encoding="utf-8")
    legacy_text = (root / "translation" / "workflow" / "legacy_pipeline.py").read_text(encoding="utf-8")

    assert "translation.workflow.legacy_pipeline" not in entrypoint_text
    assert "LegacyPipeline" not in entrypoint_text
    assert "from translation.workflow.execution import" in entrypoint_text
    assert len(legacy_text.splitlines()) <= 25
    assert "class LegacyPipeline" not in legacy_text


def test_legacy_workflow_names_alias_canonical_execution_stages():
    from translation.workflow.execution import PIPELINE_RESOURCE, PipelineBuildStage, TranslationStage, build_pipeline
    from translation.workflow.legacy_pipeline import (
        LEGACY_PIPELINE_RESOURCE,
        LegacyPipelineBuildStage,
        LegacyPipelineStage,
        build_legacy_pipeline,
    )

    assert LEGACY_PIPELINE_RESOURCE == PIPELINE_RESOURCE
    assert LegacyPipelineBuildStage is PipelineBuildStage
    assert LegacyPipelineStage is TranslationStage
    assert build_legacy_pipeline is build_pipeline


def test_mtool_analysis_stage_populates_workflow_context(tmp_path):
    from translation.context import TranslationRequest, TranslationWorkflowContext
    from translation.workflow import MToolAnalysisStage

    path = tmp_path / "sample.json"
    glossary_path = tmp_path / "glossary.json"
    path.write_text(
        json.dumps(
            {
                "EV001": "EV001",
                "\u30d5\u30a3\u30fc\u30cd": "\u30d5\u30a3\u30fc\u30cd",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    context = MToolAnalysisStage().run(
        TranslationWorkflowContext(
            request=TranslationRequest(file_path=str(path), glossary_path=str(glossary_path))
        )
    )

    assert context.analysis["glossary_path"] == str(glossary_path)
    assert context.analysis["mtool"]["total_items"] == 2
    assert context.analysis["mtool"]["classes"]["deterministic"] == 1
    assert context.analysis["mtool"]["classes"]["short_model"] == 1


def test_pipeline_build_stage_stores_pipeline_resource(tmp_path):
    from translation.context import TranslationRequest, TranslationWorkflowContext
    from translation.workflow import PIPELINE_RESOURCE, PipelineBuildStage
    from translation.workflow.pipeline import TranslationPipeline

    glossary_path = tmp_path / "glossary.json"
    context = TranslationWorkflowContext(
        request=TranslationRequest(file_path="fixture.json"),
        analysis={"glossary_path": str(glossary_path)},
    )

    result = PipelineBuildStage().run(context)

    assert isinstance(result.resources[PIPELINE_RESOURCE], TranslationPipeline)
    assert result.resources[PIPELINE_RESOURCE].glossary.file_path == str(glossary_path)


def test_translation_stage_uses_built_pipeline_resource():
    from translation.context import TranslationRequest, TranslationWorkflowContext
    from translation.workflow import PIPELINE_RESOURCE, TranslationStage

    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None, list[int] | None]] = []

        def translate_file(self, file_path, output_path=None, progress_callback=None, translate_columns=None):
            self.calls.append((file_path, output_path, translate_columns))
            return [("k", "v")]

    class NoBuildStage(TranslationStage):
        def build_pipeline(self, request):
            raise AssertionError("pipeline should come from workflow context resources")

    fake = FakePipeline()
    context = TranslationWorkflowContext(
        request=TranslationRequest(file_path="fixture.json", output_path="out.json", translate_columns=[0]),
        resources={PIPELINE_RESOURCE: fake},
    )

    result = NoBuildStage().run(context)

    assert result.result is not None
    assert result.result.items == [("k", "v")]
    assert fake.calls == [("fixture.json", "out.json", [0])]


def test_review_preparation_stage_attaches_explicit_proofreading_summary(monkeypatch):
    import translation.workflow.review as review_stage
    from translation.context import TranslationRequest, TranslationResult, TranslationWorkflowContext
    from translation.review import ReviewSummary
    from translation.workflow import ReviewPreparationStage

    summary = ReviewSummary(
        total=4,
        translated=1,
        preserved=1,
        translated_needs_review=1,
        review_required=1,
        pending=0,
        issue_entries=2,
    )
    monkeypatch.setattr(review_stage, "build_review_summary", lambda file_path: summary)
    monkeypatch.setattr(review_stage, "write_review_report", lambda file_path, output_path: "out.review.json")
    context = TranslationWorkflowContext(
        request=TranslationRequest(file_path="fixture.json"),
        result=TranslationResult(file_path="fixture.json", output_path="out.json"),
    )

    result = ReviewPreparationStage().run(context)

    assert result.analysis["review"]["review_queue_size"] == 2
    assert result.result is not None
    assert result.result.review_summary == result.analysis["review"]
    assert result.result.review_report_path == "out.review.json"
    assert result.analysis["review_report_path"] == "out.review.json"


def test_review_summary_counts_final_and_pending_checkpoint_entries(monkeypatch):
    import translation.review.summary as review_summary

    monkeypatch.setattr(
        review_summary.checkpoint,
        "load_checkpoint",
        lambda file_path: {
            "total": 5,
            "entries": {
                "0_0": {"status": "translated", "issues": []},
                "1_0": {"status": "preserved", "issues": []},
                "2_0": {"status": "translated_needs_review", "issues": [{"type": "term"}]},
                "3_0": {"status": "failed_refusal", "issues": [{"type": "model_refusal"}]},
            },
        },
    )

    summary = review_summary.build_review_summary("fixture.json")

    assert summary.as_dict() == {
        "total": 5,
        "translated": 1,
        "preserved": 1,
        "translated_needs_review": 1,
        "review_required": 1,
        "pending": 1,
        "issue_entries": 2,
        "review_queue_size": 2,
    }


def test_progress_emission_is_shared_by_progress_module_and_pipeline():
    from translation.progress import build_progress_payload, emit_progress
    from translator.pipeline import TranslationPipeline

    payloads: list[dict] = []
    long_text = "x" * 250

    emit_progress(
        payloads.append,
        file_path="game.json",
        row_idx=2,
        col_idx=0,
        status="translated",
        processed=1,
        total=4,
        original_text=long_text,
        translated_text="ok",
    )

    expected = build_progress_payload(
        file_path="game.json",
        row_idx=2,
        col_idx=0,
        status="translated",
        processed=1,
        total=4,
        original_text=long_text,
        translated_text="ok",
    )
    assert payloads == [expected]
    assert payloads[0]["percent"] == 25
    assert len(payloads[0]["original_text"]) == 200

    legacy_calls: list[tuple[int, int, str]] = []

    def legacy_callback(row, col, status):
        legacy_calls.append((row, col, status))

    TranslationPipeline._emit_progress(legacy_callback, "game.json", 3, 1, "resumed", 2, 0)
    assert legacy_calls == [(3, 1, "resumed")]


def test_control_flags_are_shared_by_control_module_and_pipeline():
    from translation.control import check_control_flags
    from translator.pipeline import TranslationCancelled, TranslationPipeline

    class Cancelled(Exception):
        pass

    try:
        check_control_flags(
            is_cancelled=lambda: True,
            is_paused=lambda: False,
            cancelled_factory=lambda: Cancelled("cancelled"),
            sleep_seconds=0,
        )
    except Cancelled as exc:
        assert str(exc) == "cancelled"
    else:
        raise AssertionError("expected cancellation")

    pause_checks = {"count": 0}

    def is_paused():
        pause_checks["count"] += 1
        return pause_checks["count"] == 1

    check_control_flags(
        is_cancelled=lambda: False,
        is_paused=is_paused,
        cancelled_factory=lambda: Cancelled("cancelled"),
        sleep_seconds=0,
    )
    assert pause_checks["count"] == 2

    pipeline = TranslationPipeline()
    pipeline.cancel()
    try:
        pipeline._check_control_flags()
    except TranslationCancelled as exc:
        assert str(exc) == "Translation task cancelled"
    else:
        raise AssertionError("expected pipeline cancellation")


def test_checkpoint_progress_save_or_buffer_is_shared_by_checkpoint_and_pipeline(tmp_path):
    import translation.checkpoint as checkpoint
    import translator.checkpoint as legacy_checkpoint
    from translation.checkpoint import store as checkpoint_store
    from translator.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    checkpoint_init = (root / "translation" / "checkpoint" / "__init__.py").read_text(encoding="utf-8")
    checkpoint_store_text = (root / "translation" / "checkpoint" / "store.py").read_text(encoding="utf-8")
    pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    runtime_adapter_text = (root / "translation" / "workflow" / "runtime_adapter.py").read_text(encoding="utf-8")

    assert "from translator.checkpoint import *" not in checkpoint_init
    assert "translator.checkpoint" not in checkpoint_store_text
    assert "import translation.checkpoint as checkpoint" not in pipeline_text
    assert "runtime_adapter.save_or_buffer_progress" in pipeline_text
    assert "checkpoint.save_or_buffer_progress" in runtime_adapter_text
    assert checkpoint.normalize_status is checkpoint_store.normalize_status

    records: list[dict] = []
    record = {
        "row": 1,
        "col": 0,
        "original": "\u30c6\u30b9\u30c8",
        "translated": "\u6d4b\u8bd5",
        "status": "translated",
        "issues": [],
        "json_key": "\u30c6\u30b9\u30c8",
        "mtool": True,
    }

    checkpoint.save_or_buffer_progress("unused.json", records, **record)
    assert records == [record]

    old_dir = checkpoint.CHECKPOINT_DIR
    old_legacy_dir = legacy_checkpoint.CHECKPOINT_DIR
    try:
        checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
        file_path = str(tmp_path / "sample.json")
        checkpoint.save_or_buffer_progress(file_path, None, **record)
        entry = checkpoint.get_entry(file_path, 1, 0)
        assert entry["translated"] == "\u6d4b\u8bd5"
        assert entry["status"] == "translated"

        checkpoint.save_progress(file_path, 2, 0, "\u8ffd\u52a0", "\u8ffd\u52a0\u8bd1\u6587", status="translated")
        direct_entry = checkpoint.get_entry(file_path, 2, 0)
        assert direct_entry["translated"] == "\u8ffd\u52a0\u8bd1\u6587"

        records.clear()
        TranslationPipeline._save_or_buffer_progress(file_path, records, **record)
        assert records == [record]
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        legacy_checkpoint.CHECKPOINT_DIR = old_legacy_dir


def test_runtime_uses_public_pipeline_token_usage_adapter(monkeypatch):
    import translation.runtime as runtime_mod
    import translation.usage as usage
    from translation.context import TranslationRequest
    from translation.runtime import TranslationRuntime
    from translator.pipeline import TranslationPipeline

    assert "_update_token_usage" not in Path(runtime_mod.__file__).read_text(encoding="utf-8")

    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[str | None] = []

        def token_usage(self, file_path: str | None = None) -> dict:
            self.calls.append(file_path)
            return {"total_tokens": 7}

    fake = FakePipeline()
    monkeypatch.setattr(runtime_mod, "build_pipeline", lambda request: fake)
    runtime = TranslationRuntime(TranslationRequest(file_path="game.json"))

    assert runtime.token_usage() == {"total_tokens": 7}
    assert fake.calls == ["game.json"]

    usage.reset()
    usage.record("api", "model", {"total_tokens": 4})
    assert TranslationPipeline().token_usage()["total_tokens"] == 4
    usage.reset()


def test_runtime_executes_the_canonical_workflow_with_its_controllable_pipeline(monkeypatch):
    import translation.runtime as runtime_mod
    from translation.context import TranslationRequest, TranslationResult
    from translation.runtime import TranslationRuntime
    from translation.workflow import PIPELINE_RESOURCE

    pipeline = object()
    callback = lambda payload: None
    observed: list[object] = []

    class FakeWorkflow:
        def run(self, context):
            observed.append(context)
            assert context.resources[PIPELINE_RESOURCE] is pipeline
            context.result = TranslationResult(
                file_path=context.request.file_path,
                output_path=context.request.output_path,
                items=[("key", "value")],
            )
            return context

    monkeypatch.setattr(runtime_mod, "build_pipeline", lambda request: pipeline)
    monkeypatch.setattr(runtime_mod, "build_workflow", lambda: FakeWorkflow())

    runtime = TranslationRuntime(TranslationRequest(file_path="game.json", output_path="out.json"))
    result = runtime.translate_file(progress_callback=callback, translate_columns=[1])

    assert result.items == [("key", "value")]
    assert observed[0].request.progress_callback is callback
    assert observed[0].request.translate_columns == [1]


def test_pipeline_build_stage_reuses_runtime_pipeline_resource():
    from translation.context import TranslationRequest, TranslationWorkflowContext
    from translation.workflow import PIPELINE_RESOURCE, PipelineBuildStage

    pipeline = object()

    class NoBuildStage(PipelineBuildStage):
        def build_pipeline(self, context):
            raise AssertionError("existing runtime pipeline must be reused")

    context = TranslationWorkflowContext(
        request=TranslationRequest(file_path="game.json"),
        resources={PIPELINE_RESOURCE: pipeline},
    )

    assert NoBuildStage().run(context).resources[PIPELINE_RESOURCE] is pipeline


def test_common_paths_resolve_runtime_and_bundle_dirs(monkeypatch, tmp_path):
    import common.paths as paths

    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)
    assert paths.runtime_base_dir() == paths.PROJECT_ROOT
    assert paths.bundled_base_dir() == paths.PROJECT_ROOT
    assert paths.upload_dir() == paths.PROJECT_ROOT / "tmp_uploads"

    exe = tmp_path / "LocalGameTranslator.exe"
    bundle = tmp_path / "_MEIPASS"
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(exe))
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(bundle), raising=False)

    assert paths.runtime_base_dir() == tmp_path
    assert paths.bundled_base_dir() == bundle
    assert paths.upload_dir() == tmp_path / "tmp_uploads"


def test_common_file_helpers_back_app_file_service(tmp_path):
    from app.services.files import detect_encoding as app_detect_encoding
    from app.services.files import is_path_inside as app_is_path_inside
    from common.files import detect_encoding, is_path_inside

    sample = tmp_path / "sample.txt"
    sample.write_text("hello", encoding="utf-8")
    assert detect_encoding(str(sample)) == "utf-8"
    assert app_detect_encoding(str(sample)) == detect_encoding(str(sample))

    child = tmp_path / "child" / "file.txt"
    assert is_path_inside(child, tmp_path)
    assert app_is_path_inside(child, tmp_path)
    assert not is_path_inside(tmp_path.parent, child)


def test_mtool_input_contract_is_shared_by_app_and_pipeline():
    from app.services.files import is_mtool_items as app_is_mtool_items
    from app.services.files import json_original_text
    from translation.input import is_mtool_items, original_text, source_text
    from translator.pipeline import TranslationPipeline

    items = [("こんにちは", "translated"), ("", "")]
    assert is_mtool_items(items)
    assert app_is_mtool_items(items)
    assert TranslationPipeline._is_mtool_json(items)

    assert source_text("こんにちは", "translated", mtool=True) == "こんにちは"
    assert TranslationPipeline._json_source_text("こんにちは", "translated", True) == "こんにちは"
    assert original_text("こんにちは", "translated", {"original": "checkpoint"}, mtool=True) == "こんにちは"
    assert json_original_text("こんにちは", "translated", {"original": "checkpoint"}, mtool=True) == "こんにちは"

    assert not is_mtool_items([])
    assert source_text("k", 123, mtool=False) == ""
    assert original_text("k", "", {"original": "checkpoint"}, mtool=False) == "checkpoint"


def test_translation_json_io_facades_preserve_order(tmp_path):
    from translation.input import load_json_items
    from translation.output import write_json_items

    path = tmp_path / "ordered.json"
    write_json_items([("b", "2"), ("a", "1"), ("", "")], str(path))

    assert load_json_items(str(path)) == [("b", "2"), ("a", "1"), ("", "")]


def test_translation_writer_lives_in_output_layer():
    import inspect
    from translation.output import TranslationWriter
    from translator.writer import TranslationWriter as LegacyTranslationWriter

    assert TranslationWriter is LegacyTranslationWriter
    assert inspect.getmodule(TranslationWriter).__name__ == "translation.output.writer"


def test_translation_writer_updates_output_cells(tmp_path):
    from translation.output import TranslationWriter

    output_path = tmp_path / "out.json"
    items = [("a", "old"), ("b", "keep")]
    writer = TranslationWriter("json", items, str(output_path))

    assert writer.update_cell(0, 0, "new")
    assert not writer.update_cell(5, 0, "missing")
    writer.flush()

    assert items == [("a", "new"), ("b", "keep")]
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"a": "new", "b": "keep"}


def test_translation_writer_can_defer_full_json_until_stop(tmp_path, monkeypatch):
    from translation.output import TranslationWriter

    output_path = tmp_path / "out.json"
    items = [("a", "old"), ("b", "keep")]
    writer = TranslationWriter(
        "json",
        items,
        str(output_path),
        periodic_enabled=False,
    )
    writes = []
    original_write = writer._write_atomic

    def tracked_write(snapshot=None):
        writes.append(list(snapshot or []))
        return original_write(snapshot)

    monkeypatch.setattr(writer, "_write_atomic", tracked_write)
    writer.start()
    for _ in range(20):
        writer.mark_dirty()

    assert writes == []
    assert not output_path.exists()

    writer.stop()
    assert len(writes) == 1
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "a": "old",
        "b": "keep",
    }


def test_batch_checkpoint_journal_replays_and_compacts(tmp_path):
    import translation.checkpoint as checkpoint

    old_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        file_path = str(tmp_path / "source.json")
        checkpoint.init_checkpoint(
            file_path,
            total=2,
            model="api:qwen",
            model_configuration={"model": "api:qwen"},
            prompt_version="prompt-test",
            glossary_version="glossary-test",
        )
        checkpoint.save_progress_many(file_path, [
            {
                "row": 0,
                "col": 0,
                "original": "\u30c6\u30b9\u30c8",
                "translated": "\u6d4b\u8bd5",
                "status": "translated",
                "issues": [],
            },
            {
                "row": 1,
                "col": 0,
                "original": "EV001",
                "translated": "EV001",
                "status": "preserved",
                "issues": [],
            },
        ])

        checkpoint_path = checkpoint.get_checkpoint_path(file_path)
        journal_path = checkpoint.get_checkpoint_journal_path(file_path)
        assert os.path.exists(journal_path)
        with open(checkpoint_path, encoding="utf-8") as stream:
            assert json.load(stream)["entries"] == {}

        with open(journal_path, "a", encoding="utf-8") as stream:
            stream.write('{"truncated":')

        replayed = checkpoint.load_checkpoint(file_path)
        assert replayed["stats"]["completed"] == 2
        assert replayed["entries"]["0_0"]["translated"] == "\u6d4b\u8bd5"
        assert replayed["entries"]["1_0"]["status"] == "preserved"

        checkpoint.save_checkpoint(file_path, replayed)
        assert not os.path.exists(journal_path)
        with open(checkpoint_path, encoding="utf-8") as stream:
            compacted = json.load(stream)
        assert len(compacted["entries"]) == 2
        assert compacted["stats"]["completed"] == 2
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir


def test_pipeline_output_cell_update_delegates_to_writer():
    from translator.pipeline import TranslationPipeline

    calls = []

    class FakeWriter:
        def update_cell(self, row_idx, col_idx, text):
            calls.append((row_idx, col_idx, text))
            return True

    pipeline = TranslationPipeline()
    pipeline._writer = FakeWriter()

    assert pipeline.update_output_cell(3, 0, "edited")
    assert calls == [(3, 0, "edited")]


def test_analysis_classifies_and_collects_mtool_model_candidates(tmp_path):
    from translation.analysis import classify_mtool_file, collect_model_bound_texts, collect_model_candidates
    from translation.terminology import Glossary

    path = tmp_path / "sample.json"
    path.write_text(
        json.dumps(
            {
                "EV001": "EV001",
                "\u30d5\u30a3\u30fc\u30cd": "\u30d5\u30a3\u30fc\u30cd",
                "\u9577\u6587\u3002\u3053\u308c\u306f\u30e2\u30c7\u30eb\u3067\u7ffb\u8a33\u3059\u308b\u6587\u7ae0\u3067\u3059\u3002": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    glossary = Glossary(file_path=str(tmp_path / "glossary.json"))

    classification = classify_mtool_file(path, glossary=glossary)
    texts = collect_model_bound_texts(path, glossary=glossary)
    candidates = collect_model_candidates(
        path,
        glossary=glossary,
        limit=1,
        batch_size=10,
        max_batch_chars=1000,
    )

    assert classification["total_items"] == 3
    assert classification["classes"]["deterministic"] == 1
    assert classification["classes"]["short_model"] == 1
    assert texts[0] == "\u30d5\u30a3\u30fc\u30cd"
    assert candidates[0]["source"] == "\u30d5\u30a3\u30fc\u30cd"
    assert candidates[0]["protected"]
    assert candidates[0]["short_label"] is True


def test_batch_candidate_preparation_is_shared_by_analysis_and_pipeline(tmp_path):
    from translation.batching import prepare_model_candidate
    from translation.terminology import Glossary

    glossary = Glossary(file_path=str(tmp_path / "glossary.json"))
    source = r"\\V[1]" + "\u30d5\u30a3\u30fc\u30cd"

    candidate = prepare_model_candidate(
        batch_i=3,
        idx=7,
        source=source,
        glossary=glossary,
    )

    assert candidate["i"] == 3
    assert candidate["idx"] == 7
    assert candidate["source"] == source
    assert candidate["text"] == candidate["protected"]
    assert candidate["runtime_tokens"]
    assert "term_hits" in candidate
    assert candidate["terms"] is candidate["term_hits"]
    assert candidate["short_label"] is True


def test_default_output_path_contract_is_shared_by_app_and_pipeline():
    from app.services.files import translated_path
    from translation.output import default_output_path
    from translator.pipeline import TranslationPipeline

    assert default_output_path("/some/path/foo.json") == "/some/path/foo.translated.json"
    assert translated_path("/some/path/foo.json") == default_output_path("/some/path/foo.json")
    assert TranslationPipeline._default_output_path("/some/path/foo.json") == default_output_path("/some/path/foo.json")
    assert default_output_path("/some/path/foo") == "/some/path/foo.translated.json"


def test_translation_status_contract_is_shared_by_quality_and_pipeline():
    from translation.quality import progress_status, status_for_output
    from translator.pipeline import TranslationPipeline

    issues = [{"type": "term_preservation", "message": "review"}]
    soft_issues = [{"type": "honorific_ambiguity", "message": "review"}]
    assert status_for_output("", "", None) == "preserved"
    assert status_for_output("白奴奈生", "白奴奈生") == "preserved"
    assert status_for_output("こんにちは", "你好") == "translated"
    assert status_for_output("こんにちは", "你好", issues) == "translated_needs_review"
    assert status_for_output("こんにちは", "你好", soft_issues) == "translated_needs_review"

    assert TranslationPipeline._status_for_output("こんにちは", "你好", issues) == status_for_output("こんにちは", "你好", issues)
    assert progress_status("translated_needs_review") == "translated"
    assert TranslationPipeline._progress_status("translated_needs_review") == "translated"
    assert progress_status("review_required") == "review_required"


def test_constraint_contract_is_shared_by_quality_and_pipeline():
    import translator.pipeline as pipeline_mod
    from translation.quality import apply_output_constraints, auto_wrap, get_violations, validate
    from translator.pipeline import auto_wrap as pipeline_auto_wrap
    from translator.pipeline import validate as pipeline_validate

    text = "\u8fd9\u662f\u4e00\u6bb5\u8d85\u8fc7\u9650\u5236\u7684\u957f\u6587\u672c"

    assert not validate(text, max_chars=5, max_lines=4)
    assert get_violations(text, max_chars=5, max_lines=4)[0]["type"] == "line_too_long"
    assert auto_wrap(text, max_chars=5, max_lines=4) == pipeline_auto_wrap(text, max_chars=5, max_lines=4)
    assert apply_output_constraints(text, max_chars=5, max_lines=4) == auto_wrap(text, max_chars=5, max_lines=4)
    assert apply_output_constraints("\u77ed\u53e5", max_chars=5, max_lines=4) == "\u77ed\u53e5"
    assert pipeline_mod.apply_output_constraints is apply_output_constraints
    assert pipeline_validate("\u77ed\u53e5", max_chars=5, max_lines=4)


def test_output_constraint_config_is_shared_by_app_and_pipeline(monkeypatch):
    import config
    from translation.config import output_constraints

    monkeypatch.setitem(config.DEFAULT_CONFIG, "max_chars_per_line", 12)
    monkeypatch.setitem(config.DEFAULT_CONFIG, "max_lines_per_cell", 3)

    assert output_constraints() == (12, 3)


def test_output_constraint_config_reads_are_centralized():
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "app" / "services" / "review.py",
        root / "app" / "routes" / "review.py",
        root / "translator" / "pipeline.py",
    ]
    violations: list[str] = []
    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        for key in ("max_chars_per_line", "max_lines_per_cell"):
            if key in text:
                violations.append(f"{path.relative_to(root)} reads {key}")

    assert violations == []


def test_translation_runtime_config_facade_reads_root_config(monkeypatch):
    import config
    from translation.config import (
        batch_translation_config,
        default_model,
        default_system_prompt,
        fallback_chunk_strategy,
        fallback_models,
        fallback_prompt_names,
        model_provider,
        ollama_host,
        set_default_model,
        set_model_provider,
        system_prompts,
        third_party_api_config,
        think_setting,
    )

    monkeypatch.setitem(config.DEFAULT_CONFIG, "model", "api:test-model")
    monkeypatch.setitem(config.DEFAULT_CONFIG, "model_provider", "api")
    monkeypatch.setitem(config.DEFAULT_CONFIG, "think", "disabled")
    monkeypatch.setitem(config.DEFAULT_CONFIG, "system_prompts", {"professional": "base", "uncensored": "label"})
    monkeypatch.setitem(config.DEFAULT_CONFIG, "batch_translation", {"enabled": True, "json_batch_size": 7})
    monkeypatch.setitem(config.DEFAULT_CONFIG, "fallback_models", ["api:fallback"])
    monkeypatch.setitem(config.DEFAULT_CONFIG, "third_party_api", {"base_url": "https://api.example.test"})
    monkeypatch.setitem(config.DEFAULT_CONFIG, "ollama_host", "http://ollama.example.test")
    monkeypatch.setitem(config.FALLBACK_CONFIG, "chunk_strategy", {"max_chars": 123})
    monkeypatch.setitem(config.FALLBACK_CONFIG, "prompts", ["uncensored"])

    assert default_model() == "api:test-model"
    assert model_provider() == "api"
    assert think_setting() == "disabled"
    assert system_prompts() == {"professional": "base", "uncensored": "label"}
    assert default_system_prompt("missing") == "base"
    assert batch_translation_config() == {"enabled": True, "json_batch_size": 7}
    assert fallback_models() == ["api:fallback"]
    assert third_party_api_config() == {"base_url": "https://api.example.test"}
    assert ollama_host() == "http://ollama.example.test"
    assert fallback_chunk_strategy() == {"max_chars": 123}
    assert fallback_prompt_names() == ["uncensored"]

    set_default_model("api:updated")
    set_model_provider("ollama")
    assert config.DEFAULT_CONFIG["model"] == "api:updated"
    assert config.DEFAULT_CONFIG["model_provider"] == "ollama"
    assert default_model() == "api:updated"
    assert model_provider() == "ollama"


def test_legacy_pipeline_runtime_config_reads_are_centralized():
    root = Path(__file__).resolve().parents[1]
    text = (root / "translator" / "pipeline.py").read_text(encoding="utf-8")

    forbidden = [
        "import config",
        "config.",
        "DEFAULT_CONFIG",
        "FALLBACK_CONFIG",
    ]

    violations = [item for item in forbidden if item in text]
    assert violations == []


def test_legacy_model_runtime_config_reads_are_centralized():
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "translator" / "api_client.py",
        root / "translator" / "model_router.py",
        root / "translator" / "ollama_client.py",
        root / "translator" / "refusal_detector.py",
    ]
    violations: list[str] = []

    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        for item in ("import config", "config.", "DEFAULT_CONFIG", "FALLBACK_CONFIG"):
            if item in text:
                violations.append(f"{path.relative_to(root)} contains {item}")

    assert violations == []


def test_quality_issue_dedup_contract_is_shared_by_quality_and_pipeline():
    from translation.quality import new_issues
    from translator.pipeline import TranslationPipeline

    existing = [{"type": "term_preservation", "message": "review"}]
    incoming = [
        {"type": "term_preservation", "message": "review"},
        {"type": "english_residue", "message": "English remains"},
    ]

    assert new_issues(existing, incoming) == [{"type": "english_residue", "message": "English remains"}]
    assert TranslationPipeline._new_issues(existing, incoming) == new_issues(existing, incoming)


def test_source_echo_repair_contract_is_shared_by_repair_and_pipeline():
    from translation.repair import strip_source_echo
    from translator.pipeline import TranslationPipeline

    source = "払わない"
    echoed = "払わない -> 不支付"
    assert strip_source_echo(source, echoed) == "不支付"
    assert TranslationPipeline._strip_source_echo(source, echoed) == strip_source_echo(source, echoed)
    assert strip_source_echo(source, "不支付") == "不支付"
    assert strip_source_echo("", echoed) == echoed


def test_batch_protocol_contract_is_shared_by_batching_and_pipeline():
    from translation.batching import (
        api_job_is_short_text,
        default_batch_options,
        resolve_candidate_batch_protocol,
        resolve_json_batch_protocol_for_items,
        resolve_parallel_candidate_protocol,
        resolve_scanned_batch_protocol,
        select_api_job_model,
        select_api_job_options,
        uses_api_parallel_batches,
    )
    from translator.pipeline import TranslationPipeline

    pipeline = TranslationPipeline(model="api:quality")
    short_candidates = [{"short_label": True, "source": "短い"} for _ in range(20)]
    mixed_candidates = short_candidates[:19] + [{"short_label": False, "source": "これは長い台詞です。"}]

    assert resolve_scanned_batch_protocol("auto", scanned=20, short_labels=20, total_chars=80) == "line"
    assert resolve_scanned_batch_protocol("auto", scanned=20, short_labels=5, total_chars=80) == "json"
    assert resolve_scanned_batch_protocol("auto", scanned=19, short_labels=19, total_chars=76) == "json"
    assert resolve_scanned_batch_protocol("line", scanned=0, short_labels=0, total_chars=0) == "line"
    assert resolve_json_batch_protocol_for_items(
        "auto",
        translated_items=[("短い", "短い") for _ in range(20)],
        mtool=True,
        completed={},
        source_text=lambda key, value, mtool: str(key if mtool else value),
        is_completed_entry=lambda entry, source: False,
        deterministic_translation=lambda text: "",
        looks_like_short_label=lambda text: True,
    ) == "line"

    assert resolve_candidate_batch_protocol("auto", "json", short_candidates) == "line"
    assert TranslationPipeline._resolve_candidate_batch_protocol("auto", "json", short_candidates) == "line"
    assert resolve_candidate_batch_protocol("auto", "json", mixed_candidates) == "json"
    assert resolve_candidate_batch_protocol("json", "line", short_candidates) == "json"

    cfg = {"short_line_max_chars": 5}
    assert api_job_is_short_text(short_candidates, cfg)
    assert TranslationPipeline._api_job_is_short_text(short_candidates, cfg)
    assert not api_job_is_short_text([{"short_label": True, "source": "長すぎるテキスト"}], cfg)

    routing_cfg = {
        "temperature": 0,
        "num_predict": 256,
        "api_parallel_enabled": True,
        "api_concurrency": 2,
        "api_model_routing_enabled": True,
        "api_fast_model": "api:fast",
        "api_quality_model": "api:quality",
        "quality_num_predict": 1024,
        "line_for_short_only": True,
        "short_line_max_chars": 5,
    }
    assert default_batch_options(routing_cfg) == {"temperature": 0, "num_predict": 256}
    assert uses_api_parallel_batches(routing_cfg, model="api:quality", provider="ollama")
    assert pipeline._uses_api_parallel_batches(routing_cfg)
    assert resolve_parallel_candidate_protocol("line", "json", short_candidates, routing_cfg) == "line"
    assert pipeline._resolve_parallel_candidate_protocol("line", "json", short_candidates, routing_cfg) == "line"
    assert resolve_parallel_candidate_protocol("line", "line", mixed_candidates, routing_cfg) == "json"
    assert select_api_job_model(short_candidates, routing_cfg, default_model="api:quality") == "api:fast"
    assert pipeline._select_api_job_model(short_candidates, routing_cfg) == "api:fast"
    assert select_api_job_model(mixed_candidates, routing_cfg, default_model="api:quality") == "api:quality"
    assert select_api_job_options(mixed_candidates, {"num_predict": 256}, routing_cfg) == {"num_predict": 1024}
    assert pipeline._select_api_job_options(mixed_candidates, {"num_predict": 256}, routing_cfg) == {"num_predict": 1024}


def test_legacy_pipeline_api_batch_routing_policy_lives_in_batching_layer():
    root = Path(__file__).resolve().parents[1]
    text = (root / "translator" / "pipeline.py").read_text(encoding="utf-8")
    forbidden = [
        "api_model_routing_enabled",
        "api_fast_model",
        "api_quality_model",
        "quality_num_predict",
        "line_for_short_only",
        "short_line_max_chars",
    ]

    violations = [item for item in forbidden if item in text]
    assert violations == []


def test_legacy_pipeline_file_protocol_scan_lives_in_batching_layer():
    root = Path(__file__).resolve().parents[1]
    text = (root / "translator" / "pipeline.py").read_text(encoding="utf-8")

    assert "scanned = 0" not in text
    assert "short_labels = 0" not in text
    assert "total_chars = 0" not in text


def test_batch_execution_split_retry_contract_is_shared_by_batching_and_pipeline():
    from translation.batching import translate_candidates_with_split
    from translator.pipeline import TranslationPipeline

    candidates = [
        {"idx": 10, "i": 0, "source": "一"},
        {"idx": 11, "i": 1, "source": "二"},
    ]
    calls: list[list[int]] = []

    def translate_raw(items, batch_options, batch_protocol, model):
        calls.append([item["idx"] for item in items])
        if len(items) > 1:
            raise ValueError("bad batch")
        item = items[0]
        return {item["i"]: f"译{item['idx']}"}

    def finish(candidate, translated):
        return translated, "translated", []

    def fallback(candidate, exc):
        return {candidate["idx"]: ("fallback", "review_required", [{"type": "batch_fallback", "message": str(exc)}])}

    assert translate_candidates_with_split(
        candidates,
        batch_options={},
        batch_protocol="json",
        model="api:test",
        translate_raw=translate_raw,
        finish_candidate=finish,
        fallback_candidate=fallback,
    ) == {
        10: ("译10", "translated", []),
        11: ("译11", "translated", []),
    }
    assert calls == [[10, 11], [10], [11]]
    assert callable(TranslationPipeline._translate_json_candidates)


def test_batch_window_collection_contract_is_shared_by_batching_and_pipeline():
    from translation.batching import collect_json_batch_candidates, collect_json_batch_window
    from translator.pipeline import TranslationPipeline

    items = [("", ""), ("PluginCommonBase", "PluginCommonBase"), ("短い", "短い")]
    records: list[dict] = []
    progress: list[str] = []
    dirty: list[int] = []

    def source_text(key, value, mtool):
        return str(key if mtool else value)

    def deterministic(text):
        return text if text == "PluginCommonBase" else ""

    def prepare_candidate(**kwargs):
        return {
            "idx": kwargs["idx"],
            "i": kwargs["batch_i"],
            "source": kwargs["source"],
            "protected": kwargs["source"],
        }

    candidates, next_idx, processed = collect_json_batch_window(
        translated_items=items,
        start_idx=0,
        mtool=True,
        completed={},
        batch_size=10,
        max_batch_chars=100,
        file_path="sample.json",
        total_targets=3,
        processed_targets=0,
        progress_callback=None,
        progress_records=records,
        glossary=None,
        check_control_flags=lambda: None,
        source_text=source_text,
        is_completed_entry=lambda entry, source: bool(entry and entry.get("status") == "translated"),
        deterministic_translation=deterministic,
        status_for_output=lambda source, translated: "preserved" if source == translated else "translated",
        progress_status=lambda status: status,
        save_or_buffer_progress=lambda file_path, progress_records, **record: progress_records.append(record),
        mark_dirty=lambda: dirty.append(1),
        emit_progress=lambda callback, file_path, row, col, status, processed, total, **kwargs: progress.append(status),
        prepare_candidate=prepare_candidate,
        looks_like_short_label=lambda text: True,
    )

    assert [candidate["idx"] for candidate in candidates] == [2]
    assert next_idx == 3
    assert processed == 2
    assert [record["status"] for record in records] == ["preserved", "preserved"]
    assert progress == ["preserved", "preserved"]
    assert len(dirty) == 2

    simple_candidates = collect_json_batch_candidates(
        translated_items=[("短い", "短い"), ("長すぎる", "長すぎる")],
        start_idx=0,
        mtool=True,
        completed={},
        batch_size=10,
        max_batch_chars=len("短い") + 1,
        glossary=None,
        source_text=source_text,
        is_completed_entry=lambda entry, source: False,
        deterministic_translation=lambda text: "",
        prepare_candidate=prepare_candidate,
        looks_like_short_label=lambda text: True,
    )
    assert [candidate["idx"] for candidate in simple_candidates] == [0]
    assert callable(TranslationPipeline._collect_json_batch_window)
    assert callable(TranslationPipeline._collect_json_batch)


def test_legacy_pipeline_batch_window_policy_lives_in_batching_layer():
    root = Path(__file__).resolve().parents[1]
    text = (root / "translator" / "pipeline.py").read_text(encoding="utf-8")

    assert "projected_chars = total_chars + len(protected_text)" not in text


def test_batch_finish_contract_is_shared_by_batching_and_pipeline():
    from translation.batching import finish_batch_translation
    from translator.pipeline import TranslationPipeline

    class FakeGlossary:
        def apply_post_translation(self, source, translated):
            return translated

    candidate = {
        "idx": 0,
        "i": 0,
        "source": "短い",
        "prepared": "短い",
        "protected": "短い",
        "symbol_tokens": [],
        "runtime_tokens": [],
        "term_hits": [],
        "short_label": True,
    }

    restored = finish_batch_translation(
        candidate,
        "短译",
        glossary=FakeGlossary(),
        restore_func=lambda *args: ("短译", [], []),
        pollution_issues_func=lambda source, translated: [],
        status_for_output_func=lambda source, translated, issues=None: "translated",
    )

    assert restored == ("短译", "translated", [])
    assert callable(TranslationPipeline._finish_batch_translation)


def test_batch_result_application_contract_is_shared_by_batching_and_pipeline():
    from translation.batching import apply_batch_translation_results
    import translator.pipeline as pipeline_mod

    class FakeGlossary:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def auto_extract(self, source, translated):
            self.calls.append((source, translated))
            return [{"source": source, "target": translated}] if source == "短い" else []

    translated_items = [("短い", "短い")]
    records: list[dict] = []
    progress: list[dict] = []
    backfilled: list[tuple[str, list[dict]]] = []

    processed, glossary_changed = apply_batch_translation_results(
        candidates=[{"idx": 0, "source": "短い"}],
        translated_payloads={0: ("短译", "translated", [])},
        translated_items=translated_items,
        processed_targets=0,
        total_targets=1,
        progress_callback=None,
        file_path="sample.json",
        mtool=True,
        progress_records=records,
        glossary=FakeGlossary(),
        mark_dirty=lambda: None,
        emit_progress=lambda callback, file_path, row, col, status, processed, total, **kwargs: progress.append({
            "row": row,
            "status": status,
            "processed": processed,
        }),
        progress_status=lambda status: status,
        apply_confirmed_terms_to_outputs=lambda file_path, terms: backfilled.append((file_path, terms)),
        batch_id="batch-1",
    )

    assert translated_items == [("短い", "短译")]
    assert processed == 1
    assert glossary_changed is True
    assert records == [{
        "row": 0,
        "col": 0,
        "original": "短い",
        "translated": "短译",
        "status": "translated",
        "issues": [],
        "json_key": "短い",
        "mtool": True,
        "entry_classification": "model_text",
        "batch_id": "batch-1",
    }]
    assert progress == [{"row": 0, "status": "translated", "processed": 1}]
    assert backfilled == [("sample.json", [{"source": "短い", "target": "短译"}])]
    assert pipeline_mod.apply_batch_translation_results is apply_batch_translation_results


def test_legacy_pipeline_batch_result_application_lives_in_batching_layer():
    root = Path(__file__).resolve().parents[1]
    text = (root / "translator" / "pipeline.py").read_text(encoding="utf-8")

    assert '"json_key": str(key)' not in text
    assert 'confirmed_terms = self.glossary.auto_extract(candidate["source"], translated)' not in text


def test_legacy_pipeline_batch_finish_policy_lives_in_batching_layer():
    root = Path(__file__).resolve().parents[1]
    text = (root / "translator" / "pipeline.py").read_text(encoding="utf-8")

    assert "Batch model returned Japanese text" not in text
    assert "Batch model refused or failed" not in text


def test_legacy_pipeline_batch_split_policy_lives_in_batching_layer():
    root = Path(__file__).resolve().parents[1]
    text = (root / "translator" / "pipeline.py").read_text(encoding="utf-8")

    assert "BatchTranslationError" not in text
    assert "mid = len(candidates) // 2" not in text


def test_runtime_model_and_scheduler_facades_back_pipeline_imports():
    import translation.usage as usage
    import translator.usage as legacy_usage
    import translator.pipeline as pipeline_mod
    from translation.batching import BatchJob, run_concurrent_batches
    from translation.models import chunk_translate, fallback_translate, retry_short_label_translation, retry_with_fallback
    from translation.models.router import translate as routed_translate
    from translation.quality import has_japanese, is_refusal
    from translator import model_router as legacy_model_router

    assert legacy_model_router.translate is routed_translate
    assert pipeline_mod.BatchJob is BatchJob
    assert pipeline_mod.run_concurrent_batches is run_concurrent_batches
    assert pipeline_mod.chunk_translate is chunk_translate
    assert pipeline_mod.fallback_translate is fallback_translate
    assert pipeline_mod.retry_short_label_translation is retry_short_label_translation
    assert pipeline_mod.retry_with_fallback is retry_with_fallback
    assert pipeline_mod.has_japanese is has_japanese
    assert pipeline_mod.is_refusal is is_refusal
    assert pipeline_mod.token_usage is usage
    assert legacy_usage.record is usage.record
    assert legacy_usage.snapshot is usage.snapshot

    usage.reset()
    usage.record("api", "model", {"prompt_tokens": 2, "completion_tokens": 3})
    snapshot = usage.snapshot()
    assert snapshot["total_tokens"] == 5
    assert snapshot["calls"] == 1
    usage.reset()


def test_translation_model_router_owns_model_routing_implementation():
    root = Path(__file__).resolve().parents[1]
    legacy_text = (root / "translator" / "model_router.py").read_text(encoding="utf-8")
    models_init = (root / "translation" / "models" / "__init__.py").read_text(encoding="utf-8")
    refusal_text = (root / "translator" / "refusal_detector.py").read_text(encoding="utf-8")

    assert "from translation.models.router import" in legacy_text
    assert "from translator.model_router import" not in models_init
    assert "from translator.model_router import" not in refusal_text


def test_translation_models_own_fallback_retry_implementation():
    import translator.refusal_detector as legacy_refusal
    from translation.models import chunk_translate, retry_with_fallback
    from translation.models import retry

    root = Path(__file__).resolve().parents[1]
    models_init = (root / "translation" / "models" / "__init__.py").read_text(encoding="utf-8")

    assert "translator.refusal_detector" not in models_init
    assert chunk_translate is retry.chunk_translate
    assert retry_with_fallback is retry.retry_with_fallback
    assert legacy_refusal.log_retry_stats is retry.log_retry_stats

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append((model, text, system_prompt, terminology))
        return f"{text}-ok"

    assert chunk_translate("m", "abc", "prompt", max_chars=50, translator=fake_translate) == "abc-ok"
    assert calls == [("m", "abc", "prompt", None)]

    legacy_refusal.translate = fake_translate
    try:
        assert legacy_refusal.chunk_translate("m", "abc", "prompt", max_chars=50) == "abc-ok"
    finally:
        legacy_refusal.translate = retry.translate


def test_api_client_records_usage_through_translation_usage_facade():
    import translator.api_client as legacy_api_client
    import translation.models.api_client as api_client

    root = Path(__file__).resolve().parents[1]
    text = (root / "translation" / "models" / "api_client.py").read_text(encoding="utf-8")

    assert "import translation.usage as token_usage" in text
    assert "from translator import usage as token_usage" not in text
    assert legacy_api_client is api_client


def test_model_transport_clients_live_in_translation_models():
    import translator.api_client as legacy_api_client
    import translator.ollama_client as legacy_ollama_client
    import translation.models.api_client as api_client
    import translation.models.ollama_client as ollama_client

    root = Path(__file__).resolve().parents[1]
    router_text = (root / "translation" / "models" / "router.py").read_text(encoding="utf-8")

    assert "from translator import api_client, ollama_client" not in router_text
    assert "from translation.models import api_client, ollama_client" in router_text
    assert legacy_api_client is api_client
    assert legacy_ollama_client is ollama_client


def test_translation_pollution_owns_pollution_policy_implementation():
    import translator.pollution as legacy_pollution
    import translation.pollution as pollution

    root = Path(__file__).resolve().parents[1]
    text = (root / "translation" / "pollution" / "__init__.py").read_text(encoding="utf-8")

    assert "translator.pollution" not in text
    assert legacy_pollution.translation_pollution_issues is pollution.translation_pollution_issues
    assert legacy_pollution.glossary_term_pollution_issues is pollution.glossary_term_pollution_issues


def test_translation_batching_owns_scheduler_implementation():
    import translator.scheduler as legacy_scheduler
    from translation.batching import BatchJob, BatchResult, run_concurrent_batches
    from translation.batching import scheduler as batching_scheduler

    root = Path(__file__).resolve().parents[1]
    batching_init = (root / "translation" / "batching" / "__init__.py").read_text(encoding="utf-8")

    assert "translator.scheduler" not in batching_init
    assert legacy_scheduler.BatchJob is BatchJob is batching_scheduler.BatchJob
    assert legacy_scheduler.BatchResult is BatchResult is batching_scheduler.BatchResult
    assert legacy_scheduler.run_concurrent_batches is run_concurrent_batches is batching_scheduler.run_concurrent_batches


def test_translation_batching_owns_batch_payload_and_parser_implementation():
    import translator.batch as legacy_batch
    from translation.batching import parse_batch_response, parse_line_batch_response, translate_batch
    from translation.batching import payloads

    root = Path(__file__).resolve().parents[1]
    batching_init = (root / "translation" / "batching" / "__init__.py").read_text(encoding="utf-8")
    execution_text = (root / "translation" / "batching" / "execution.py").read_text(encoding="utf-8")

    assert "translator.batch" not in batching_init
    assert "translator.batch" not in execution_text
    assert legacy_batch.parse_batch_response is parse_batch_response is payloads.parse_batch_response
    assert legacy_batch.parse_line_batch_response is parse_line_batch_response is payloads.parse_line_batch_response
    assert legacy_batch.translate_batch is translate_batch is payloads.translate_batch


def test_translation_protection_owns_symbol_protection_implementation():
    import translator.symbols as legacy_symbols
    from translation.protection import protect_symbols, restore_symbols
    from translation.protection import symbols

    root = Path(__file__).resolve().parents[1]
    protection_init = (root / "translation" / "protection" / "__init__.py").read_text(encoding="utf-8")
    restore_text = (root / "translation" / "protection" / "restore.py").read_text(encoding="utf-8")

    assert "translator.symbols" not in protection_init
    assert "translator.symbols" not in restore_text
    assert legacy_symbols.protect_symbols is protect_symbols is symbols.protect_symbols
    assert legacy_symbols.restore_symbols is restore_symbols is symbols.restore_symbols


def test_translation_protection_owns_runtime_token_implementation():
    import translator.quality as legacy_quality
    from translation.protection import protect_runtime_tokens, restore_runtime_tokens
    from translation.protection import runtime

    root = Path(__file__).resolve().parents[1]
    protection_init = (root / "translation" / "protection" / "__init__.py").read_text(encoding="utf-8")
    restore_text = (root / "translation" / "protection" / "restore.py").read_text(encoding="utf-8")

    assert "protect_runtime_tokens" in runtime.__all__
    assert "translator.quality import protect_runtime_tokens" not in protection_init
    assert "translator.quality import restore_runtime_tokens" not in restore_text
    assert legacy_quality.protect_runtime_tokens is protect_runtime_tokens is runtime.protect_runtime_tokens
    assert legacy_quality.restore_runtime_tokens is restore_runtime_tokens is runtime.restore_runtime_tokens

    protected, tokens = protect_runtime_tokens("HENTAI_progressがリセット")
    assert protected.startswith("__KEEP_0__")
    assert restore_runtime_tokens(protected, tokens) == "HENTAI_progressがリセット"


def test_translation_quality_owns_output_constraint_implementation():
    import translator.constraints as legacy_constraints
    from translation.quality import auto_wrap, get_violations, validate
    from translation.quality import constraints_core

    root = Path(__file__).resolve().parents[1]
    quality_init = (root / "translation" / "quality" / "__init__.py").read_text(encoding="utf-8")
    constraints_facade = (root / "translation" / "quality" / "constraints.py").read_text(encoding="utf-8")

    assert "translator.constraints" not in quality_init
    assert "translator.constraints" not in constraints_facade
    assert legacy_constraints.auto_wrap is auto_wrap is constraints_core.auto_wrap
    assert legacy_constraints.get_violations is get_violations is constraints_core.get_violations
    assert legacy_constraints.validate is validate is constraints_core.validate


def test_translation_quality_owns_refusal_detection_implementation():
    import translator.refusal_detector as legacy_refusal
    from translation.quality import has_japanese, is_refusal
    from translation.quality import refusal

    root = Path(__file__).resolve().parents[1]
    quality_init = (root / "translation" / "quality" / "__init__.py").read_text(encoding="utf-8")
    classification_rules = (root / "translation" / "classification" / "rules.py").read_text(encoding="utf-8")

    assert "translator.refusal_detector import has_japanese" not in quality_init
    assert "translator.refusal_detector import has_japanese" not in classification_rules
    assert legacy_refusal.has_japanese is has_japanese is refusal.has_japanese
    assert legacy_refusal.is_refusal is is_refusal is refusal.is_refusal
    assert has_japanese("テスト")
    assert not is_refusal("「抱歉，那做不到。」", original="「悪いがそれは出来ない」")
    assert is_refusal("抱歉，我无法协助翻译该内容", original="悪い")


def test_translation_quality_owns_prompt_rule_implementation():
    import translator.quality as legacy_quality
    from translation.quality import prompts, quality_prompt_rules

    root = Path(__file__).resolve().parents[1]
    quality_init = (root / "translation" / "quality" / "__init__.py").read_text(encoding="utf-8")

    assert "from translator.quality import" not in quality_init
    assert legacy_quality.quality_prompt_rules is quality_prompt_rules is prompts.quality_prompt_rules
    assert "Simplified Chinese" in quality_prompt_rules()


def test_translation_quality_owns_fixed_rule_and_issue_implementation():
    import translator.quality as legacy_quality
    from translation.quality import (
        apply_fixed_translations,
        apply_source_conditioned_fixes,
        english_residue,
        translation_issues,
    )
    from translation.quality import rules

    root = Path(__file__).resolve().parents[1]
    quality_init = (root / "translation" / "quality" / "__init__.py").read_text(encoding="utf-8")
    classification_rules = (root / "translation" / "classification" / "rules.py").read_text(encoding="utf-8")
    protection_restore = (root / "translation" / "protection" / "restore.py").read_text(encoding="utf-8")

    assert "from translator.quality import" not in quality_init
    assert "from translator.quality import" not in classification_rules
    assert "from translator.quality import" not in protection_restore
    assert legacy_quality.apply_fixed_translations is apply_fixed_translations is rules.apply_fixed_translations
    assert legacy_quality.apply_source_conditioned_fixes is apply_source_conditioned_fixes is rules.apply_source_conditioned_fixes
    assert legacy_quality.english_residue is english_residue is rules.english_residue
    assert legacy_quality.translation_issues is translation_issues is rules.translation_issues
    assert apply_fixed_translations("Save") == "保存"
    assert apply_source_conditioned_fixes("オーク", "橡树") == "兽人"
    assert english_residue("Press A", original="") == ["Press"]
    assert english_residue(
        "\u300c\u54c8\u54c8ww\u300d",
        original="\u300c\u304a\u3082\u3057\u308d\u3044\uff57\uff57\uff57\u300d",
    ) == []
    assert english_residue(
        "\u8bd1\u6587ww",
        original="\u300c\u304a\u3082\u3057\u308d\u3044\u300d",
    ) == ["ww"]
    assert any(issue["type"] == "untranslated_japanese" for issue in translation_issues("テスト", "テスト"))


def test_translation_classification_owns_label_rule_implementation():
    import translator.label_rules as legacy_rules
    from translation.classification import deterministic_translation, has_source_japanese, looks_like_short_label
    from translation.classification import rules

    root = Path(__file__).resolve().parents[1]
    classification_init = (root / "translation" / "classification" / "__init__.py").read_text(encoding="utf-8")
    label_patterns = (root / "translator" / "label_patterns.py").read_text(encoding="utf-8")

    assert "translator.label_rules" not in classification_init
    assert "from translator.label_rules import" not in label_patterns
    assert legacy_rules.deterministic_translation is deterministic_translation is rules.deterministic_translation
    assert legacy_rules.has_source_japanese is has_source_japanese is rules.has_source_japanese
    assert legacy_rules.looks_like_short_label is looks_like_short_label is rules.looks_like_short_label


def test_translation_classification_owns_label_pattern_implementation():
    import translator.label_patterns as legacy_patterns
    from translation.classification import LabelVariant, label_variant_groups, parse_label_variant
    from translation.classification import patterns

    root = Path(__file__).resolve().parents[1]
    classification_init = (root / "translation" / "classification" / "__init__.py").read_text(encoding="utf-8")

    assert "translator.label_patterns" not in classification_init
    assert legacy_patterns.LabelVariant is LabelVariant is patterns.LabelVariant
    assert legacy_patterns.label_variant_groups is label_variant_groups is patterns.label_variant_groups
    assert legacy_patterns.parse_label_variant is parse_label_variant is patterns.parse_label_variant


def test_fallback_translate_tries_prompt_styles_before_fallback_model():
    from translation.models import fallback_translate

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append((model, system_prompt))
        return "成功" if model == "api:fallback" else "REFUSAL"

    def fake_retry(*args, **kwargs):
        raise AssertionError("fallback model should succeed before generic retry")

    def fake_chunk(*args, **kwargs):
        raise AssertionError("fallback model should succeed before chunk translation")

    result = fallback_translate(
        "protected",
        model="api:primary",
        system_prompt="primary prompt",
        prompt_style="professional",
        system_prompts={"professional": "pro", "uncensored": "uncensored"},
        fallback_models=["api:primary", "api:fallback"],
        chunk_strategy={"max_chars": 12, "overlap": 2},
        file_path="game.json",
        row_idx=1,
        col_idx=0,
        compose_prompt=lambda base: f"composed:{base}",
        translate_func=fake_translate,
        retry_with_fallback_func=fake_retry,
        chunk_translate_func=fake_chunk,
        is_refusal_func=lambda text, original=None: text == "REFUSAL",
    )

    assert result == "成功"
    assert calls == [
        ("api:primary", "composed:pro"),
        ("api:primary", "composed:uncensored"),
        ("api:fallback", "composed:primary prompt"),
    ]


def test_fallback_translate_skips_remaining_primary_prompts_after_permanent_error():
    from translation.models import fallback_translate

    calls = []

    class PermanentError(RuntimeError):
        retryable = False

    def fake_translate(model, text, system_prompt=None, terminology=None):
        calls.append((model, system_prompt))
        if model == "api:primary":
            raise PermanentError("content rejected")
        return "成功"

    result = fallback_translate(
        "protected",
        model="api:primary",
        system_prompt="primary prompt",
        prompt_style="professional",
        system_prompts={"professional": "pro", "uncensored": "uncensored"},
        fallback_models=["api:fallback"],
        chunk_strategy={"max_chars": 12, "overlap": 2},
        file_path="game.json",
        row_idx=1,
        col_idx=0,
        compose_prompt=lambda base: f"composed:{base}",
        translate_func=fake_translate,
        retry_with_fallback_func=lambda *args, **kwargs: {"status": "FAILED"},
        chunk_translate_func=lambda *args, **kwargs: "",
        is_refusal_func=lambda text, original=None: False,
    )

    assert result == "成功"
    assert calls == [
        ("api:primary", "composed:pro"),
        ("api:fallback", "composed:primary prompt"),
    ]


def test_pipeline_transport_error_uses_configured_fallback(monkeypatch):
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    class PermanentError(RuntimeError):
        retryable = False

    monkeypatch.setattr(pipeline_mod, "translate", lambda *args, **kwargs: (_ for _ in ()).throw(PermanentError("blocked")))
    monkeypatch.setattr(pipeline_mod, "fallback_translate", lambda **kwargs: "使用谜箱")

    translated, status, issues = TranslationPipeline(model="api:quality")._translate_cell_with_meta(
        "謎箱を使う",
        0,
        0,
        "",
    )

    assert translated == "使用谜箱"
    assert status == "translated_needs_review"
    assert {issue["type"] for issue in issues} == {"translation_error"}


def test_call_translate_with_options_tolerates_transports_without_options():
    from translation.models import call_translate_with_options

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None, options=None):
        calls.append(options)
        if options is not None:
            raise TypeError("transport does not support options")
        return "翻译"

    result = call_translate_with_options(
        model="api:model",
        text="source",
        system_prompt="prompt",
        options={"temperature": 0},
        translate_func=fake_translate,
    )

    assert result == "翻译"
    assert calls == [{"temperature": 0}, None]


def test_retry_short_label_translation_uses_strict_prompt_and_options():
    from translation.models import retry_short_label_translation

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None, options=None):
        calls.append((model, text, system_prompt, options))
        return "保存"

    result = retry_short_label_translation(
        model="api:model",
        protected_text="セーブ",
        retry_prompt="strict label prompt",
        options={"temperature": 0, "num_predict": 32},
        translate_func=fake_translate,
        is_refusal_func=lambda text, original=None: False,
    )

    assert result == "保存"
    assert calls == [("api:model", "セーブ", "strict label prompt", {"temperature": 0, "num_predict": 32})]


def test_retry_short_label_translation_returns_empty_on_refusal_or_error():
    from translation.models import retry_short_label_translation

    assert retry_short_label_translation(
        model="api:model",
        protected_text="セーブ",
        retry_prompt="strict",
        options={},
        translate_func=lambda *args, **kwargs: "REFUSAL",
        is_refusal_func=lambda text, original=None: True,
    ) == ""

    def broken_translate(*args, **kwargs):
        raise RuntimeError("transport failed")

    assert retry_short_label_translation(
        model="api:model",
        protected_text="セーブ",
        retry_prompt="strict",
        options={},
        translate_func=broken_translate,
        is_refusal_func=lambda text, original=None: False,
    ) == ""


def test_restore_protected_translation_restores_runtime_tokens_and_reports_terms():
    from translation.protection import protect_runtime_tokens, restore_protected_translation

    class FakeGlossary:
        def restore_terms(self, text, tokens):
            return text

        def apply_post_translation(self, original, translated):
            return translated.replace("固定", "修正")

        def missing_restored_terms(self, original, translated, tokens):
            return []

        def missing_hits(self, original, translated, hits):
            return hits

    prepared, runtime_tokens = protect_runtime_tokens("HENTAI_progressがリセット")
    restored, symbol_issues, missing_terms = restore_protected_translation(
        glossary=FakeGlossary(),
        original_text="HENTAI_progressがリセット",
        prepared_text=prepared,
        protected_text=prepared,
        translated="__KEEP_0__已固定",
        symbol_tokens=[],
        term_tokens=[],
        runtime_tokens=runtime_tokens,
        term_hits=[{"source": "リセット", "target": "重置"}],
    )

    assert restored == "HENTAI_progress已修正"
    assert symbol_issues == []
    assert missing_terms == [{"source": "リセット", "target": "重置"}]


def test_restore_protected_translation_strips_foreign_runtime_placeholder():
    from translation.protection import restore_protected_translation

    class FakeGlossary:
        @staticmethod
        def restore_terms(text, tokens):
            return text

        @staticmethod
        def apply_post_translation(original, translated):
            return translated

        @staticmethod
        def missing_hits(original, translated, hits):
            return []

    restored, issues, missing_terms = restore_protected_translation(
        glossary=FakeGlossary(),
        original_text="モーリ様",
        prepared_text="モーリ様",
        protected_text="モーリ様",
        translated="莫莉大人__KEEP_0____KEEP_0__",
        symbol_tokens=[],
        term_tokens=[],
        runtime_tokens=[],
        term_hits=[],
    )

    assert restored == "莫莉大人"
    assert issues == []
    assert missing_terms == []


def test_quality_retry_accepts_english_residue_improvement():
    from translation.quality import retry_english_residue_translation

    restored, missing_terms, issues = retry_english_residue_translation(
        original_text="続ける",
        protected_text="続ける",
        current_restored="Continue",
        current_missing_terms=[{"source": "続ける", "target": "继续"}],
        residue=["Continue"],
        retry_prompt="retry prompt",
        model="api:model",
        translate_func=lambda *args, **kwargs: "继续",
        restore_func=lambda text: (text, [{"type": "symbol_preservation", "message": "ok"}], []),
        is_refusal_func=lambda text, original=None: False,
        english_residue_func=lambda text, original=None: [] if text == "继续" else ["Continue"],
    )

    assert restored == "继续"
    assert missing_terms == []
    assert issues == [{"type": "symbol_preservation", "message": "ok"}]


def test_quality_retry_reports_term_retry_errors():
    from translation.quality import retry_missing_terms_translation

    def broken_translate(*args, **kwargs):
        raise RuntimeError("transport failed")

    restored, missing_terms, issues = retry_missing_terms_translation(
        protected_text="__TERM_0__",
        retry_prompt="strict",
        model="api:model",
        current_restored="旧译文",
        current_missing_terms=[{"source": "フィーネ", "target": "菲妮"}],
        translate_func=broken_translate,
        restore_func=lambda text: (text, [], []),
        is_refusal_func=lambda text, original=None: False,
    )

    assert restored == "旧译文"
    assert missing_terms == [{"source": "フィーネ", "target": "菲妮"}]
    assert issues == [{"type": "term_retry_error", "message": "transport failed"}]


def test_pipeline_english_residue_retry_delegates_to_quality(monkeypatch):
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    calls = []

    def fake_translate(*args, **kwargs):
        return "Use box"

    def fake_english_residue(text, original=""):
        return ["Use"] if text == "Use box" else []

    def fake_retry_english_residue_translation(**kwargs):
        calls.append(kwargs)
        return "使用箱子", kwargs["current_missing_terms"], []

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    monkeypatch.setattr(pipeline_mod, "english_residue", fake_english_residue)
    monkeypatch.setattr(pipeline_mod, "is_refusal", lambda text, original=None: False)
    monkeypatch.setattr(pipeline_mod, "retry_english_residue_translation", fake_retry_english_residue_translation)

    pipeline = TranslationPipeline()
    translated, status, issues = pipeline._translate_cell_with_meta("謎箱を使う", 0, 0, "")

    assert translated == "使用箱子"
    assert status == "translated"
    assert issues == []
    assert calls
    assert calls[0]["current_restored"] == "Use box"
    assert calls[0]["residue"] == ["Use"]


def test_pipeline_fallback_translation_delegates_to_models_module(monkeypatch):
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    calls = []

    def fake_fallback_translate(**kwargs):
        calls.append(kwargs)
        return "委托结果"

    monkeypatch.setattr(pipeline_mod, "fallback_translate", fake_fallback_translate)
    pipeline = TranslationPipeline(model="api:primary", system_prompt="base prompt", prompt_style="academic")

    result = pipeline._fallback_translate("protected", "game.json", 3, 0, [])

    assert result == "委托结果"
    call = calls[0]
    assert call["model"] == "api:primary"
    assert call["protected_text"] == "protected"
    assert call["system_prompt"] == "base prompt"
    assert call["prompt_style"] == "academic"
    assert call["file_path"] == "game.json"
    assert call["row_idx"] == 3
    assert call["col_idx"] == 0
    assert call["translate_func"] is pipeline_mod.translate
    assert call["retry_with_fallback_func"] is pipeline_mod.retry_with_fallback
    assert call["chunk_translate_func"] is pipeline_mod.chunk_translate
    assert call["is_refusal_func"] is pipeline_mod.is_refusal


def test_pipeline_translate_call_delegates_to_models_module(monkeypatch):
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    calls = []

    def fake_call_translate_with_options(**kwargs):
        calls.append(kwargs)
        return "委托翻译"

    monkeypatch.setattr(pipeline_mod, "call_translate_with_options", fake_call_translate_with_options)
    pipeline = TranslationPipeline(model="api:model")

    assert pipeline._call_translate("source", "prompt", options={"temperature": 0}) == "委托翻译"
    assert calls == [{
        "model": "api:model",
        "text": "source",
        "system_prompt": "prompt",
        "options": {"temperature": 0},
        "translate_func": pipeline_mod.translate,
    }]


def test_pipeline_short_label_retry_delegates_to_models_module(monkeypatch):
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    calls = []

    def fake_retry_short_label_translation(**kwargs):
        calls.append(kwargs)
        return "严格重试"

    monkeypatch.setattr(pipeline_mod, "retry_short_label_translation", fake_retry_short_label_translation)
    pipeline = TranslationPipeline(model="api:model")

    assert pipeline._retry_short_label("__SYM_0__", []) == "严格重试"
    call = calls[0]
    assert call["model"] == "api:model"
    assert call["protected_text"] == "__SYM_0__"
    assert "Strict retry" in call["retry_prompt"]
    assert call["options"] == {"temperature": 0, "num_predict": 32}
    assert call["translate_func"] is pipeline_mod.translate
    assert call["is_refusal_func"] is pipeline_mod.is_refusal


def test_pipeline_restore_protected_translation_delegates_to_protection(monkeypatch):
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    calls = []

    def fake_restore_protected_translation(**kwargs):
        calls.append(kwargs)
        return "restored", [], []

    monkeypatch.setattr(pipeline_mod, "restore_protected_translation", fake_restore_protected_translation)
    pipeline = TranslationPipeline()

    assert pipeline._restore_protected_translation(
        "source",
        "prepared",
        "protected",
        "translated",
        [],
        [],
        [],
        [],
    ) == ("restored", [], [])
    assert calls[0]["glossary"] is pipeline.glossary
    assert calls[0]["original_text"] == "source"
    assert calls[0]["prepared_text"] == "prepared"
    assert calls[0]["protected_text"] == "protected"
    assert calls[0]["translated"] == "translated"


def test_legacy_cell_translation_path_is_explicitly_removed():
    from translator.pipeline import TranslationPipeline

    pipeline = TranslationPipeline()

    try:
        pipeline._legacy_translate_cell_with_meta("text", 0, 0, "file.json", [])
    except NotImplementedError as exc:
        assert "_translate_cell_with_meta" in str(exc)
    else:
        raise AssertionError("legacy cell translation path should not be callable")


def test_workflow_pipeline_delegates_file_entry_to_entry_module(monkeypatch):
    import translation.workflow.pipeline as workflow_pipeline
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    entry_text = (root / "translation" / "workflow" / "file_entry.py").read_text(encoding="utf-8")

    assert "os.path.splitext" not in pipeline_text
    assert "load_json_items(" not in pipeline_text
    assert "translate_file_for_pipeline(self, file_path, output_path, progress_callback, translate_columns)" in pipeline_text
    assert "os.path.splitext" in entry_text
    assert "load_json_items(file_path)" in entry_text

    calls = []

    def fake_translate_file_for_pipeline(pipeline, file_path, output_path, progress_callback, translate_columns):
        calls.append((pipeline, file_path, output_path, progress_callback, translate_columns))
        return [("k", "v")]

    monkeypatch.setattr(workflow_pipeline, "translate_file_for_pipeline", fake_translate_file_for_pipeline)
    pipeline = TranslationPipeline()

    assert pipeline.translate_file("sample.json", "out.json", None, [0]) == [("k", "v")]
    assert calls == [(pipeline, "sample.json", "out.json", None, [0])]


def test_workflow_pipeline_delegates_cell_translation_to_cell_module(monkeypatch):
    import translation.workflow.pipeline as workflow_pipeline
    from translation.workflow import cell as cell_module
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    cell_text = (root / "translation" / "workflow" / "cell.py").read_text(encoding="utf-8")
    cell_services_text = (root / "translation" / "workflow" / "cell_services.py").read_text(encoding="utf-8")

    assert "Quality retry: translate ordinary English words into Chinese" not in pipeline_text
    assert "Quality retry: translate ordinary English words into Chinese" in cell_text
    assert "translate_cell_with_meta(" in pipeline_text
    assert "CellTranslationServices(" not in pipeline_text
    assert "build_cell_translation_services(self, globals())" in pipeline_text
    assert "CellTranslationServices(" in cell_services_text
    assert "class CellTranslationServices" in cell_text

    calls = []

    def fake_translate_cell_with_meta(**kwargs):
        calls.append(kwargs)
        return "delegated", "translated", []

    monkeypatch.setattr(workflow_pipeline, "translate_cell_with_meta", fake_translate_cell_with_meta)
    pipeline = TranslationPipeline(model="api:model")

    assert pipeline._translate_cell_with_meta("テスト", 2, 0, "sample.json") == ("delegated", "translated", [])
    assert calls[0]["text"] == "テスト"
    assert calls[0]["row_idx"] == 2
    assert isinstance(calls[0]["services"], cell_module.CellTranslationServices)
    assert calls[0]["services"].model == "api:model"


def test_workflow_pipeline_delegates_json_flow_to_json_module(monkeypatch):
    import translation.workflow.pipeline as workflow_pipeline
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    workflow_pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    json_flow_text = (root / "translation" / "workflow" / "json_flow.py").read_text(encoding="utf-8")

    assert "preseed_from_sources" not in workflow_pipeline_text
    assert "preseed_from_sources" in json_flow_text
    assert "translate_json_workflow(self, file_path, output_path, progress_callback)" in workflow_pipeline_text

    calls = []

    def fake_translate_json_workflow(pipeline, file_path, output_path, progress_callback):
        calls.append((pipeline, file_path, output_path, progress_callback))
        return [("k", "v")]

    monkeypatch.setattr(workflow_pipeline, "translate_json_workflow", fake_translate_json_workflow)
    pipeline = TranslationPipeline()

    assert pipeline._translate_json("sample.json", "out.json", None) == [("k", "v")]
    assert calls == [(pipeline, "sample.json", "out.json", None)]


def test_workflow_pipeline_delegates_nonparallel_batch_flow_to_batch_module(monkeypatch):
    import translation.workflow.pipeline as workflow_pipeline
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    workflow_pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    json_batch_text = (root / "translation" / "workflow" / "json_batch.py").read_text(encoding="utf-8")
    workflow_nonparallel = workflow_pipeline_text.split("def _translate_json_batched(", 1)[1].split("def _translate_json_batched_parallel(", 1)[0]

    assert "glossary_changed = apply_batch_translation_results" not in workflow_nonparallel
    assert "glossary_changed = apply_batch_translation_results" in json_batch_text
    assert "translate_json_batched_workflow(" in workflow_pipeline_text

    calls = []

    def fake_translate_json_batched_workflow(*args):
        calls.append(args)
        return [("k", "v")]

    monkeypatch.setattr(workflow_pipeline, "translate_json_batched_workflow", fake_translate_json_batched_workflow)
    pipeline = TranslationPipeline()

    assert pipeline._translate_json_batched("file.json", [], True, {}, "out.json", 0, None) == [("k", "v")]
    assert calls[0][0] is pipeline
    assert calls[0][1:] == ("file.json", [], True, {}, "out.json", 0, None)


def test_workflow_pipeline_delegates_api_parallel_batch_flow_to_parallel_module(monkeypatch):
    import translation.workflow.pipeline as workflow_pipeline
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    workflow_pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    json_parallel_text = (root / "translation" / "workflow" / "json_parallel.py").read_text(encoding="utf-8")

    assert "api_parallel_batch_retry_failed" not in workflow_pipeline_text
    assert "api_parallel_batch_retry_failed" in json_parallel_text
    assert "translate_json_batched_parallel_workflow(" in workflow_pipeline_text
    assert "def _run_concurrent_batches" in workflow_pipeline_text

    calls = []

    def fake_translate_json_batched_parallel_workflow(*args):
        calls.append(args)
        return [("k", "v")]

    monkeypatch.setattr(workflow_pipeline, "translate_json_batched_parallel_workflow", fake_translate_json_batched_parallel_workflow)
    pipeline = TranslationPipeline()

    assert pipeline._translate_json_batched_parallel(
        "file.json",
        [],
        True,
        {},
        "out.json",
        0,
        None,
        2,
        4000,
        {"temperature": 0},
        "json",
        "json",
        {"api_concurrency": 1},
    ) == [("k", "v")]
    assert calls[0][0] is pipeline
    assert calls[0][1:7] == ("file.json", [], True, {}, "out.json", 0)


def test_workflow_pipeline_batch_helpers_delegate_to_batch_adapter():
    from translation.workflow import batch_adapter
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    workflow_pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    adapter_text = (root / "translation" / "workflow" / "batch_adapter.py").read_text(encoding="utf-8")

    assert "collect_json_batch_window(\n            translated_items=" not in workflow_pipeline_text
    assert "translate_candidates_with_split(\n        candidates," not in workflow_pipeline_text
    assert "finish_batch_translation(\n        candidate," not in workflow_pipeline_text
    assert "translate_candidate_batch_raw(\n            model or" not in workflow_pipeline_text
    assert "batch_adapter.collect_batch_window" in workflow_pipeline_text
    assert "batch_adapter.translate_candidates_for_pipeline" in workflow_pipeline_text
    assert "batch_adapter.finish_batch_candidate" in workflow_pipeline_text

    assert "collect_json_batch_window(" in adapter_text
    assert "translate_candidates_with_split(" in adapter_text
    assert "finish_batch_translation(" in adapter_text
    assert "translate_candidate_batch_raw(" in adapter_text
    assert callable(batch_adapter.translate_candidates_for_pipeline)
    assert TranslationPipeline._strip_source_echo("A", "A -> B") == "B"


def test_workflow_pipeline_translation_helpers_delegate_to_translation_adapter():
    from translation.workflow import translation_adapter
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    workflow_pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    adapter_text = (root / "translation" / "workflow" / "translation_adapter.py").read_text(encoding="utf-8")

    assert "fallback_translate(\n            model=self.model" not in workflow_pipeline_text
    assert "restore_protected_translation(\n            glossary=self.glossary" not in workflow_pipeline_text
    assert "compose_translation_prompt(\n            base_prompt" not in workflow_pipeline_text
    assert "retry_short_label_translation(\n            model=self.model" not in workflow_pipeline_text
    assert "translation_adapter.fallback_translate_for_pipeline" in workflow_pipeline_text
    assert "translation_adapter.restore_protected_for_pipeline" in workflow_pipeline_text
    assert "translation_adapter.compose_system_prompt_for_pipeline" in workflow_pipeline_text

    assert "fallback_translate_func(" in adapter_text
    assert "restore_protected_translation_func(" in adapter_text
    assert "compose_translation_prompt(" in adapter_text
    assert callable(translation_adapter.call_translate_for_pipeline)

    pipeline = TranslationPipeline()
    assert pipeline._glossary_mappings_for_quality() == translation_adapter.glossary_mappings_for_quality(pipeline)


def test_workflow_pipeline_runtime_helpers_delegate_to_runtime_adapter():
    from translation.workflow import runtime_adapter
    from translation.workflow.pipeline import TranslationPipeline

    root = Path(__file__).resolve().parents[1]
    workflow_pipeline_text = (root / "translation" / "workflow" / "pipeline.py").read_text(encoding="utf-8")
    adapter_text = (root / "translation" / "workflow" / "runtime_adapter.py").read_text(encoding="utf-8")

    assert "from translation.control import check_control_flags" not in workflow_pipeline_text
    assert "from translation.progress import emit_progress" not in workflow_pipeline_text
    assert "from translation.output import default_output_path" not in workflow_pipeline_text
    assert "backfill_confirmed_terms_to_outputs" not in workflow_pipeline_text
    assert "check_control_flags(\n            is_cancelled=" not in workflow_pipeline_text
    assert "emit_progress(\n            progress_callback" not in workflow_pipeline_text
    assert "runtime_adapter.update_token_usage" in workflow_pipeline_text
    assert "runtime_adapter.apply_confirmed_terms_to_outputs" in workflow_pipeline_text
    assert "runtime_adapter.check_pipeline_control_flags" in workflow_pipeline_text
    assert "runtime_adapter.emit_pipeline_progress" in workflow_pipeline_text

    assert "backfill_confirmed_terms_to_outputs(" in adapter_text
    assert "check_control_flags(" in adapter_text
    assert "emit_progress(" in adapter_text
    assert callable(runtime_adapter.update_token_usage)

    pipeline = TranslationPipeline()
    assert runtime_adapter.update_output_cell(pipeline, 0, 0, "x") is False


def test_term_alias_contract_is_shared_by_terminology_and_pipeline():
    from translation.terminology import apply_term_aliases
    from translator.pipeline import TranslationPipeline

    confirmed = [{
        "source": "触手姦",
        "target": "触手奸",
        "aliases": ["触", "手奸", "触手奸", "触手姦"],
    }]

    assert apply_term_aliases("触手姦", "触手奸", confirmed) == "触手奸"
    assert TranslationPipeline._apply_term_aliases("触手姦", "触手奸", confirmed) == "触手奸"
    assert apply_term_aliases("触手姦", "触手姦を使う", confirmed) == "触手奸を使う"
    assert apply_term_aliases("別の語", "触手姦", confirmed) == "触手姦"


def test_translation_terminology_owns_glossary_implementation(tmp_path):
    import translator.glossary as legacy_glossary
    from translation.terminology import Glossary
    from translation.terminology import glossary as terminology_glossary

    root = Path(__file__).resolve().parents[1]
    terminology_init = (root / "translation" / "terminology" / "__init__.py").read_text(encoding="utf-8")
    glossary_text = (root / "translation" / "terminology" / "glossary.py").read_text(encoding="utf-8")

    assert "translator.glossary" not in terminology_init
    assert "translator." not in glossary_text
    assert legacy_glossary.Glossary is Glossary is terminology_glossary.Glossary

    glossary = Glossary(file_path=str(tmp_path / "glossary.json"))
    glossary.add("\u30d5\u30a3\u30fc\u30cd", "\u83f2\u59ae", term_type="person")
    assert glossary.find_hits("\u30d5\u30a3\u30fc\u30cd\u306f\u6765\u305f")[0]["target"] == "\u83f2\u59ae"


def test_confirmed_term_backfill_updates_checkpoint_and_output(tmp_path):
    import translation.checkpoint as checkpoint
    import translator.checkpoint as legacy_checkpoint
    from translation.terminology import backfill_confirmed_terms_to_outputs

    file_path = str(tmp_path / "sample.json")
    old_dir = checkpoint.CHECKPOINT_DIR
    old_legacy_dir = legacy_checkpoint.CHECKPOINT_DIR
    updates = []
    try:
        checkpoint.CHECKPOINT_DIR = str(tmp_path / ".checkpoints")
        legacy_checkpoint.CHECKPOINT_DIR = checkpoint.CHECKPOINT_DIR
        checkpoint.init_checkpoint(file_path, total=1, file_type="json")
        checkpoint.save_progress(
            file_path,
            2,
            0,
            "フィーネが来た",
            "菲内来了",
            status="translated_needs_review",
            issues=[{"type": "term_preservation", "message": "stale"}],
        )

        changed = backfill_confirmed_terms_to_outputs(
            file_path,
            [{"source": "フィーネ", "target": "菲妮", "aliases": ["菲内"]}],
            update_output_cell=lambda row, col, text: updates.append((row, col, text)) or True,
        )

        entry = checkpoint.get_entry(file_path, 2, 0)
        assert changed == 1
        assert entry["translated"] == "菲妮来了"
        assert entry["output_translation"] == "菲妮来了"
        assert entry["status"] == "translated"
        assert entry["issues"] == []
        assert updates == [(2, 0, "菲妮来了")]
    finally:
        checkpoint.CHECKPOINT_DIR = old_dir
        legacy_checkpoint.CHECKPOINT_DIR = old_legacy_dir


def test_pipeline_confirmed_term_backfill_delegates_to_terminology(monkeypatch):
    from translation.workflow import runtime_adapter
    from translator.pipeline import TranslationPipeline

    calls = []

    def fake_backfill(file_path, confirmed_terms, *, update_output_cell=None, glossary_mappings=None):
        calls.append({
            "file_path": file_path,
            "confirmed_terms": confirmed_terms,
            "update_output_cell": update_output_cell,
            "glossary_mappings": glossary_mappings,
        })
        return 0

    monkeypatch.setattr(runtime_adapter, "backfill_confirmed_terms_to_outputs", fake_backfill)
    pipeline = TranslationPipeline()
    confirmed = [{"source": "フィーネ", "target": "菲妮"}]

    pipeline._apply_confirmed_terms_to_outputs("sample.json", confirmed)

    assert calls[0]["file_path"] == "sample.json"
    assert calls[0]["confirmed_terms"] == confirmed
    assert calls[0]["update_output_cell"] == pipeline.update_output_cell
    assert calls[0]["glossary_mappings"] == pipeline._glossary_mappings_for_quality()


def test_prompt_composition_is_shared_by_prompts_and_pipeline():
    import config
    from translation.prompts import compose_label_prompt, compose_translation_prompt
    from translation.quality import quality_prompt_rules
    from translator.pipeline import TranslationPipeline

    pipeline = TranslationPipeline()
    rules = quality_prompt_rules()
    base = "Base prompt"

    assert pipeline._compose_system_prompt(base) == compose_translation_prompt(base, quality_rules=rules)
    assert "Preserve placeholders like __SYM_0__" in pipeline._compose_system_prompt(base)
    assert "Strict terminology retry" in pipeline._compose_system_prompt(base, strict=True)

    label_base = config.DEFAULT_CONFIG.get("system_prompts", {}).get("uncensored") or pipeline.system_prompt
    assert pipeline._compose_label_prompt() == compose_label_prompt(label_base, quality_rules=rules)
    assert "short Japanese game UI label" in pipeline._compose_label_prompt()
    assert "Strict retry" in pipeline._compose_label_prompt(strict=True)


def test_dotenv_loader_reads_current_working_directory(monkeypatch):
    import config

    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="dotenv_cwd_") as tmpdir:
        env_path = os.path.join(tmpdir, ".env")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("THIRD_PARTY_API_KEY=cwd-key\n")

        monkeypatch.chdir(tmpdir)

        assert config._load_dotenv()["THIRD_PARTY_API_KEY"] == "cwd-key"
        monkeypatch.chdir(old_cwd)


def test_dotenv_loader_prefers_frozen_executable_directory(monkeypatch):
    import config

    with tempfile.TemporaryDirectory(prefix="dotenv_exe_") as tmpdir:
        exe_path = os.path.join(tmpdir, "LocalGameTranslator.exe")
        env_path = os.path.join(tmpdir, ".env")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("THIRD_PARTY_API_KEY=exe-key\n")

        monkeypatch.setattr(config.sys, "frozen", True, raising=False)
        monkeypatch.setattr(config.sys, "executable", exe_path)

        assert config._load_dotenv()["THIRD_PARTY_API_KEY"] == "exe-key"


def test_short_label_first_call_uses_low_cost_options(monkeypatch):
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    calls = []

    def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
        calls.append({"text": text, "options": options})
        return "\u6551\u51fa\uff01"

    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
    pipeline = TranslationPipeline()
    translated, status, issues = pipeline._translate_cell_with_meta("\u6551\u51fa\u3059\u308b\uff01", 0, 0, "")

    assert translated == "\u6551\u51fa\uff01"
    assert status == "translated"
    assert issues == []
    assert calls[0]["options"] == {"temperature": 0, "num_predict": 32}


def test_single_cell_translation_uses_shared_candidate_preparation(monkeypatch):
    from translator.pipeline import TranslationPipeline
    import translator.pipeline as pipeline_mod

    original_prepare = pipeline_mod.prepare_model_candidate
    prepared_calls = []

    def spy_prepare_model_candidate(**kwargs):
        prepared_calls.append(kwargs)
        return original_prepare(**kwargs)

    def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
        return "\u6551\u51fa\uff01"

    monkeypatch.setattr(pipeline_mod, "prepare_model_candidate", spy_prepare_model_candidate)
    monkeypatch.setattr(pipeline_mod, "translate", fake_translate)

    pipeline = TranslationPipeline()
    translated, status, issues = pipeline._translate_cell_with_meta("\u6551\u51fa\u3059\u308b\uff01", 4, 0, "")

    assert translated == "\u6551\u51fa\uff01"
    assert status == "translated"
    assert issues == []
    assert prepared_calls == [{
        "batch_i": 0,
        "idx": 4,
        "source": "\u6551\u51fa\u3059\u308b\uff01",
        "glossary": pipeline.glossary,
        "short_label": True,
    }]


def test_pipeline_rejects_non_mtool_json_files():
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="mtool_only_") as tmpdir:
        csv_path = os.path.join(tmpdir, "sample.csv")
        asar_path = os.path.join(tmpdir, "app.asar")
        for path in (csv_path, asar_path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
            try:
                TranslationPipeline().translate_file(path)
            except ValueError as exc:
                assert "Only MTool-style JSON files are supported" in str(exc)
            else:
                raise AssertionError(f"Expected unsupported file to be rejected: {path}")


def test_third_party_api_client_records_token_usage(monkeypatch):
    import config
    from translator import api_client, usage

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "你好"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://api.example.test/v1/chat/completions"
        assert headers["Authorization"] == "Bearer test-key"
        assert json["model"] == "third-model"
        return FakeResponse()

    old_cfg = dict(config.DEFAULT_CONFIG.get("third_party_api", {}))
    config.DEFAULT_CONFIG["third_party_api"] = {
        "base_url": "https://api.example.test/v1",
        "api_key_env": "THIRD_PARTY_API_KEY",
        "api_key": "",
        "models": ["third-model"],
    }
    monkeypatch.setenv("THIRD_PARTY_API_KEY", "test-key")
    monkeypatch.setattr(api_client.requests, "post", fake_post)
    usage.reset()
    try:
        assert api_client.translate_once("third-model", "こんにちは", system_prompt="Translate") == "你好"
        stats = usage.snapshot()
        assert stats["prompt_tokens"] == 7
        assert stats["completion_tokens"] == 3
        assert stats["total_tokens"] == 10
        assert stats["by_provider"]["api"]["models"]["third-model"]["calls"] == 1
    finally:
        config.DEFAULT_CONFIG["third_party_api"] = old_cfg
        usage.reset()


def test_opencode_go_openai_model_uses_chat_completions(monkeypatch):
    import config
    from translator import api_client

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "译文"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://opencode.ai/zen/go/v1/chat/completions"
        assert headers["Authorization"] == "Bearer test-key"
        assert json["model"] == "kimi-k2.7-code"
        assert json["reasoning_effort"] == "none"
        return FakeResponse()

    old_cfg = dict(config.DEFAULT_CONFIG.get("third_party_api", {}))
    config.DEFAULT_CONFIG["third_party_api"] = {
        "base_url": "https://opencode.ai/zen/go/v1/chat/completions",
        "api_key_env": "THIRD_PARTY_API_KEY",
        "api_key": "test-key",
        "style": "opencode_go",
        "models": [],
    }
    monkeypatch.delenv("THIRD_PARTY_API_BASE_URL", raising=False)
    monkeypatch.delenv("THIRD_PARTY_API_KEY", raising=False)
    monkeypatch.setattr(api_client.requests, "post", fake_post)
    try:
        assert api_client.translate_once("opencode-go/kimi-k2.7-code", "テスト") == "译文"
    finally:
        config.DEFAULT_CONFIG["third_party_api"] = old_cfg


def test_opencode_go_messages_model_uses_anthropic_endpoint(monkeypatch):
    import config
    from translator import api_client

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "content": [{"type": "text", "text": "译文"}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://opencode.ai/zen/go/v1/messages"
        assert headers["x-api-key"] == "test-key"
        assert headers["anthropic-version"] == "2023-06-01"
        assert json["model"] == "qwen3.7-plus"
        assert json["system"] == "Translate"
        assert json["max_tokens"] == 12
        assert json["thinking"] == {"type": "disabled"}
        return FakeResponse()

    old_cfg = dict(config.DEFAULT_CONFIG.get("third_party_api", {}))
    config.DEFAULT_CONFIG["third_party_api"] = {
        "base_url": "",
        "api_key_env": "THIRD_PARTY_API_KEY",
        "api_key": "test-key",
        "style": "opencode_go",
        "anthropic_version": "2023-06-01",
        "models": [],
    }
    monkeypatch.delenv("THIRD_PARTY_API_BASE_URL", raising=False)
    monkeypatch.delenv("THIRD_PARTY_API_KEY", raising=False)
    monkeypatch.setattr(api_client.requests, "post", fake_post)
    try:
        assert api_client.translate_once("qwen3.7-plus", "テスト", system_prompt="Translate", options={"num_predict": 12}) == "译文"
    finally:
        config.DEFAULT_CONFIG["third_party_api"] = old_cfg


def test_only_evidence_backed_kanji_names_are_preserved_deterministically():
    from translation.classification import deterministic_translation
    from translator.glossary import Glossary
    from translator.pipeline import TranslationPipeline

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    glossary.preseed_from_sources([
        "白奴奈生",
        "白奴奈生\uff1a\u6765\u305f",
        "白奴奈生\uff1a\u7b11\u3063\u305f",
    ])

    assert deterministic_translation("白奴奈生") == ""
    assert deterministic_translation("白奴奈生", glossary=glossary) == "白奴奈生"
    assert deterministic_translation("\u5974\u96b7\u5e02\u5834") == ""
    assert deterministic_translation("\u653b\u6483\u6280") == ""
    assert TranslationPipeline._status_for_output("白奴奈生", "白奴奈生") == "preserved"


def test_source_conditioned_fixes_correct_common_bad_terms():
    from translation.quality import apply_source_conditioned_fixes

    assert apply_source_conditioned_fixes("オーク", "橡树") == "兽人"
    assert apply_source_conditioned_fixes("メスガキVer", "女童版") == "小恶女版"
    assert apply_source_conditioned_fixes("ショーツ", "短裤") == "内裤"
    assert apply_source_conditioned_fixes("手マン", "手按摩") == "指交"
    assert apply_source_conditioned_fixes("ショタレイプ", "幼女性侵") == "正太强奸"


def test_unified_quality_issues_report_japanese_english_and_artifacts():
    from translation.quality import translation_issues

    issues = translation_issues("\u30c6\u30b9\u30c8", '\u30c6\u30b9\u30c8 accent},"')
    issue_types = {issue["type"] for issue in issues}

    assert "untranslated_japanese" in issue_types
    assert "english_residue" in issue_types
    assert "suspicious_artifact" in issue_types
    assert "term_placeholder_leak" in {
        issue["type"]
        for issue in translation_issues(
            "モーリ様",
            "莫莉大人__KEEP_0__",
        )
    }


def test_quality_allows_source_code_identifiers_but_not_plain_english():
    from translation.protection import protect_runtime_tokens
    from translation.quality import is_refusal, translation_issues

    protected, tokens = protect_runtime_tokens("HENTAI_progress\u304c\u30ea\u30bb\u30c3\u30c8\u72b6\u614b\u306e\u3068\u304d")
    assert protected.startswith("__KEEP_0__")
    assert tokens[0].value == "HENTAI_progress"
    assert protect_runtime_tokens("__KEEP_0__")[1] == []

    assert translation_issues("EV003\u306e\u5185\u5bb9\u3092\u8ffd\u52a0", "\u6dfb\u52a0EV003\u5185\u5bb9") == []
    assert translation_issues(
        "this.character()\u5185\u306e\u6570\u3092\u5186\u30a8\u30ea\u30a2\u30a4\u30d9\u30f3\u30c8ID\u306b\u66f8\u304d\u63db\u3048",
        "\u5c06 this.character() \u4e2d\u7684\u6570\u503c\u66ff\u6362\u4e3a\u5706\u533a\u57df\u4e8b\u4ef6ID",
    ) == []
    assert any(
        issue["type"] == "english_residue"
        for issue in translation_issues("Press A to Continue", "Press A to Continue")
    )
    assert not is_refusal(
        "\u5f53HENTAI_progress\u5904\u4e8e\u91cd\u7f6e\u72b6\u6001\u65f6",
        original="HENTAI_progress\u304c\u30ea\u30bb\u30c3\u30c8\u72b6\u614b\u306e\u3068\u304d",
    )


def test_quality_allows_source_version_markers():
    from translation.quality import translation_issues

    issues = translation_issues("\u30e1\u30b9\u30ac\u30adVer", "\u5c0f\u6076\u5973Ver", short_label=True)
    assert not any(issue["type"] == "english_residue" for issue in issues)


def test_pollution_validator_rejects_contextual_common_term_glossary():
    from translator.glossary import Glossary
    from translation.pollution import glossary_term_pollution_issues

    issues = glossary_term_pollution_issues("\u5974\u96b7", "\u6210\u4e3a\u83ab\u91cc\u5927\u4eba\u7684\u5974\u96b7")
    assert {issue["type"] for issue in issues} >= {
        "glossary_contextual_expansion",
        "glossary_proper_name_pollution",
    }

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    glossary.candidates["\u5974\u96b7"] = {
        "count": 3,
        "targets": {"\u6210\u4e3a\u83ab\u91cc\u5927\u4eba\u7684\u5974\u96b7": 3},
        "target": "\u6210\u4e3a\u83ab\u91cc\u5927\u4eba\u7684\u5974\u96b7",
        "status": "candidate",
        "type": "proper_noun",
        "score": 0.9,
        "evidence": ["standalone_line"],
    }
    assert not glossary.promote("\u5974\u96b7")
    assert "\u5974\u96b7" not in glossary.terms


def test_pollution_validator_flags_unsupported_proper_name_in_translation():
    from translator.glossary import Glossary
    from translator.pipeline import TranslationPipeline

    glossary = Glossary(file_path=os.path.join(tempfile.mkdtemp(), "g.json"))
    glossary.add("\u30e2\u30fc\u30ea", "\u83ab\u91cc", term_type="person")
    pipeline = TranslationPipeline(glossary=glossary)

    translated, status, issues = pipeline._finish_batch_translation(
        {
            "idx": 0,
            "i": 0,
            "source": "\u5974\u96b7\u5e02\u5834",
            "prepared": "\u5974\u96b7\u5e02\u5834",
            "protected": "\u5974\u96b7\u5e02\u5834",
            "symbol_tokens": [],
            "runtime_tokens": [],
            "term_hits": [],
            "short_label": True,
        },
        "\u6210\u4e3a\u83ab\u91cc\u5927\u4eba\u7684\u5974\u96b7\u5e02\u573a",
    )

    assert translated == "\u6210\u4e3a\u83ab\u91cc\u5927\u4eba\u7684\u5974\u96b7\u5e02\u573a"
    assert status == "review_required"
    assert {issue["type"] for issue in issues} >= {
        "unsupported_glossary_name",
        "unsupported_proper_name",
        "contextual_term_pollution",
    }


def test_pollution_validator_accepts_honorific_names_supported_by_source():
    from translation.pollution import translation_pollution_issues

    supported_pairs = [
        ("すらいむさん、お待たせ", "史莱姆先生，让您久等了"),
        ("お姉ちゃん", "姐姐大人"),
        ("その方をお通しして", "请让那位大人通过"),
    ]

    for source, translated in supported_pairs:
        issue_types = {
            issue["type"]
            for issue in translation_pollution_issues(source, translated)
        }
        assert "unsupported_proper_name" not in issue_types


def test_pollution_validator_keeps_clear_context_contamination_actionable():
    from translation.pollution import translation_pollution_issues
    from translation.quality import status_for_output

    source = "そ、それは……それだけは許して……！"
    translated = "咦！？中、中出！？请、请等一下王子大人！"
    issues = translation_pollution_issues(source, translated)

    assert "unsupported_proper_name" in {issue["type"] for issue in issues}
    assert status_for_output(source, translated, issues) == "review_required"


def test_quality_accepts_fullwidth_parentheses_for_source_parentheses():
    from translation.quality import translation_issues

    issues = translation_issues("\u4f11\u3080(\u5168\u56de\u5fa9)", "\u4f11\u606f\uff08\u5b8c\u5168\u6062\u590d\uff09", short_label=True)
    assert not any(issue["type"] == "marker_lost" for issue in issues)


def test_version_marker_detection_does_not_match_ordinary_words():
    from translation.quality import translation_issues

    assert not translation_issues("Coordinate converter", "\u5750\u6807\u8f6c\u6362\u5668")
    issues = translation_issues("Patch Ver1.2", "\u8865\u4e01")
    assert any(issue["type"] == "version_marker_lost" for issue in issues)


def test_done_checkpoint_deterministic_resume_clears_stale_issues(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="resume_issue_fix_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"EV002": "EV002"}, f, ensure_ascii=False)

            checkpoint.init_checkpoint(path, total=1, file_type="json")
            checkpoint.save_progress(
                path,
                0,
                0,
                "EV002",
                "EV002",
                status="done",
                issues=[{"type": "english_residue", "message": "stale"}],
                json_key="EV002",
                mtool=True,
            )

            def fail_translate(*args, **kwargs):
                raise AssertionError("deterministic resume should not call model")

            monkeypatch.setattr("translator.pipeline.translate", fail_translate)
            pipeline = TranslationPipeline()
            pipeline.translate_file(path)

            entry = checkpoint.get_entry(path, 0, 0)
            assert entry["translated"] == "EV002"
            assert entry["issues"] == []
            assert json.load(open(path.replace(".json", ".translated.json"), encoding="utf-8"))["EV002"] == "EV002"
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir


def test_classification_rules_are_shared_by_pipeline():
    from translation.classification import deterministic_translation, looks_like_short_label
    from translator.pipeline import TranslationPipeline

    assert looks_like_short_label("\u30d5\u30a3\u30fc\u30cd\uff1f")
    assert TranslationPipeline._looks_like_short_label("\u30d5\u30a3\u30fc\u30cd\uff1f")
    assert not looks_like_short_label("\u3053\u308c\u306f\u9577\u3044\u53f0\u8a5e\u3067\u3059\u3002")
    assert TranslationPipeline()._deterministic_translation("EV001") == deterministic_translation("EV001")


def test_non_japanese_resources_are_deterministic():
    from translation.classification import deterministic_translation

    assert deterministic_translation("\u30fc\u30fc\u30fc\u30fc\u30fc\u30fc\u30fc\u30fc") == "\u30fc\u30fc\u30fc\u30fc\u30fc\u30fc\u30fc\u30fc"
    assert deterministic_translation("PluginCommonBase") == "PluginCommonBase"
    assert deterministic_translation("$gameParty.members();") == "$gameParty.members();"
    assert deterministic_translation("blog.livedoor.jp/hanabipapa0910") == "blog.livedoor.jp/hanabipapa0910"
    assert deterministic_translation("TMAnimeLight3 80") == "TMAnimeLight3 80"
    assert deterministic_translation("OriginMenuStatus SetParamVariable param1 111") == "OriginMenuStatus SetParamVariable param1 111"
    assert deterministic_translation("(C) Kokoro Reflections") == "(C) Kokoro Reflections"
    assert deterministic_translation("A1\uff08http") == "A1\uff08http"
    assert deterministic_translation("shadow, 0, 0, 255, 1,0") == "shadow, 0, 0, 255, 1,0"
    assert deterministic_translation("3EhAyAKIAJBAXRqIRkgGSADOwEAIAJBAWohAiASKAIAIQMgAiADSA0AIAMhAgsLIAQsAAAh") != ""
    assert deterministic_translation("\u3010ED\u3011") == "\u3010ED\u3011"
    assert deterministic_translation("Press A to Continue") == "Press A to Continue"
    assert deterministic_translation("strip panty") == "strip panty"
    assert deterministic_translation("\u753b\u50cf-fi-ne_muneA.png") == "\u753b\u50cf-fi-ne_muneA.png"
    assert (
        deterministic_translation("<\u88c5\u5099\u6761\u4ef6\u30a2\u30af\u30bf\u30fc:1>")
        == "<\u88c5\u5099\u6761\u4ef6\u30a2\u30af\u30bf\u30fc:1>"
    )
    assert deterministic_translation("\u884c\u6570 = 0") == "\u884c\u6570 = 0"
    assert deterministic_translation("\u30d5\u30a3\u30fc\u30cd") == ""


def test_decorative_katakana_marks_do_not_count_as_japanese_residue():
    from translation.quality import translation_issues
    from translation.quality import has_japanese, is_refusal

    translated = "\u30fc\u30fc\u30fc\u30fc\u7cbe\u7075\u7528\u30fb\u6218\u6597\u8868\u60c5\u30fc\u30fc\u30fc\u30fc"
    assert not has_japanese(translated)
    assert not is_refusal(translated, original="\u30fc\u30fc\u30fc\u30fc\u30a8\u30eb\u30d5\u7528\u30fb\u6226\u95d8\u8868\u60c5\u30fc\u30fc\u30fc\u30fc")
    assert not any(issue["type"] == "untranslated_japanese" for issue in translation_issues("", translated))


def test_refusal_detection_allows_dialogue_apologies():
    from translation.quality import is_refusal

    assert not is_refusal("\u300c\u62b1\u6b49\uff0c\u90a3\u505a\u4e0d\u5230\u3002\u300d", original="\u300c\u60aa\u3044\u304c\u305d\u308c\u306f\u51fa\u6765\u306a\u3044\u300d")
    assert is_refusal("\u62b1\u6b49\uff0c\u6211\u65e0\u6cd5\u534f\u52a9\u7ffb\u8bd1\u8be5\u5185\u5bb9", original="\u60aa\u3044")


def test_label_variant_patterns_are_generic_and_conservative():
    from translation.classification import label_variant_groups, parse_label_variant

    audience_a = parse_label_variant("\u89b3\u5ba2A")
    assert audience_a is not None
    assert audience_a.base == "\u89b3\u5ba2"
    assert audience_a.suffix == "A"
    assert audience_a.kind == "letter_suffix"

    training_day = parse_label_variant("\u8abf\u65591\u65e5\u76ee")
    assert training_day is not None
    assert training_day.base == "\u8abf\u6559"
    assert training_day.suffix == "1\u65e5\u76ee"
    assert training_day.kind == "count_suffix"

    assert parse_label_variant("\u3053\u306e\u59ff\u3001\u53ef\u611b\u3044\uff1f") is None
    assert parse_label_variant("\u5f7c\u306e\u90e8\u5c4bA") is None

    groups = label_variant_groups([
        "\u5175\u58ebA",
        "\u5175\u58ebB",
        "\u5175\u58ebC",
        "\u30d5\u30a3\u30fc\u30cdA",
    ])
    assert set(groups) == {"\u5175\u58eb"}
    assert [item.suffix for item in groups["\u5175\u58eb"]] == ["A", "B", "C"]


def test_batch_response_parser_accepts_fenced_json():
    from translator.batch import parse_batch_response

    response = '```json\n[{"i": 0, "t": "\u95e8\u536b"}, {"i": 1, "t": "\u652f\u4ed8"}]\n```'
    assert parse_batch_response(response, {0, 1}) == {0: "\u95e8\u536b", 1: "\u652f\u4ed8"}


def test_batch_response_parser_accepts_text_field_alias():
    from translator.batch import parse_batch_response

    response = '{"items":[{"i":0,"t":"\u95e8\u536b"},{"i":1,"text":"\u652f\u4ed8"}]}'
    assert parse_batch_response(response, {0, 1}) == {0: "\u95e8\u536b", 1: "\u652f\u4ed8"}


def test_line_batch_response_parser_keeps_continuation_lines():
    from translator.batch import parse_line_batch_response

    response = "0\t\u95e8\u536b\n1\t\u7b2c\u4e00\u884c\n\u7b2c\u4e8c\u884c\n2: \u5148\u7b97\u4e86"
    assert parse_line_batch_response(response, {0, 1, 2}) == {
        0: "\u95e8\u536b",
        1: "\u7b2c\u4e00\u884c\n\u7b2c\u4e8c\u884c",
        2: "\u5148\u7b97\u4e86",
    }


def test_line_batch_response_parser_repairs_position_indexes():
    from translator.batch import parse_line_batch_response

    response = "0\t\u96f6\n1\t\u4e00\n1\u3001\u5e94\u8be5\u662f\u4e8c\n3\t\u4e09"
    assert parse_line_batch_response(response, {0, 1, 2, 3}) == {
        0: "\u96f6",
        1: "\u4e00",
        2: "\u5e94\u8be5\u662f\u4e8c",
        3: "\u4e09",
    }


def test_line_batch_response_parser_accepts_plain_translation_lines():
    from translator.batch import parse_line_batch_response

    response = "\u95e8\u536b\n\u652f\u4ed8\n\u5148\u653e\u7740"
    assert parse_line_batch_response(response, {0, 1, 2}) == {
        0: "\u95e8\u536b",
        1: "\u652f\u4ed8",
        2: "\u5148\u653e\u7740",
    }


def test_line_batch_response_parser_accepts_literal_tab_marker():
    from translator.batch import parse_line_batch_response

    response = "0<TAB>\u95e8\u536b\n1<TAB>\u652f\u4ed8"
    assert parse_line_batch_response(response, {0, 1}) == {0: "\u95e8\u536b", 1: "\u652f\u4ed8"}


def test_line_batch_response_parser_retries_partial_neighboring_results():
    import pytest
    from translator.batch import BatchTranslationError, parse_line_batch_response

    with pytest.raises(BatchTranslationError) as exc_info:
        parse_line_batch_response("0\t\u95e8\u536b\n2\t\u5148\u653e\u7740", {0, 1, 2})

    assert exc_info.value.partial_results == {}
    assert exc_info.value.retry_indexes == {0, 1, 2}


def test_batch_finish_strips_source_arrow_echo():
    from translator.pipeline import TranslationPipeline

    assert TranslationPipeline._strip_source_echo(
        "\u6255\u308f\u306a\u3044",
        "\u6255\u308f\u306a\u3044 -> \u4e0d\u652f\u4ed8",
    ) == "\u4e0d\u652f\u4ed8"


def test_batch_response_parser_repairs_local_position_indexes():
    from translator.batch import parse_batch_response

    response = '{"items":[{"i":0,"t":"\u95e8\u536b"},{"i":-1,"t":"\u652f\u4ed8"},{"i":_2,"t":"\u5148\u653e\u7740"}]}'
    assert parse_batch_response(response, {0, 1, 2}) == {0: "\u95e8\u536b", 1: "\u652f\u4ed8", 2: "\u5148\u653e\u7740"}


def test_mtool_json_uses_batch_translation(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="batch_json_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u9580\u756a": "\u9580\u756a",
                    "\u6255\u3046": "\u6255\u3046",
                    "\u3084\u3081\u3066\u304a\u304f": "\u3084\u3081\u3066\u304a\u304f",
                }, f, ensure_ascii=False)

            calls = []

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                calls.append(text)
                payload = json.loads(text)
                translations = {
                    "\u6255\u3046": "\u652f\u4ed8",
                    "\u3084\u3081\u3066\u304a\u304f": "\u5148\u7b97\u4e86",
                }
                return json.dumps(
                    [
                        [item[0], translations[item[1]]]
                        if isinstance(item, list)
                        else {"i": item["i"], "t": translations[item["text"]]}
                        for item in payload
                    ],
                    ensure_ascii=False,
                )

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            from translator.glossary import Glossary
            pipeline = TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json")))
            result = pipeline.translate_file(path)

            assert len(calls) == 1
            assert dict(parse_json(path.replace(".json", ".translated.json"))) == {
                "\u9580\u756a": "\u95e8\u536b",
                "\u6255\u3046": "\u652f\u4ed8",
                "\u3084\u3081\u3066\u304a\u304f": "\u5148\u7b97\u4e86",
            }
            assert dict(result)["\u9580\u756a"] == "\u95e8\u536b"
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir


def test_mtool_json_uses_key_as_source_and_records_final_statuses(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="mtool_key_source_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "\u6328\u62f6": "\u65e7\u8bd1\u6587",
                        "\u6255\u3046": "\u6255\u3046",
                        "PluginCommonBase": "\u65e7\u503c",
                        "": "",
                    },
                    f,
                    ensure_ascii=False,
                )

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                assert "\u6255\u3046" in text
                assert "\u65e7\u8bd1\u6587" not in text
                assert "PluginCommonBase" not in text
                payload = json.loads(text)
                translations = {"\u6255\u3046": "\u652f\u4ed8"}
                return json.dumps(
                    [
                        [item[0], translations[item[1]]]
                        if isinstance(item, list)
                        else {"i": item["i"], "t": translations[item["text"]]}
                        for item in payload
                    ],
                    ensure_ascii=False,
                )

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json"))).translate_file(path)

            out = dict(parse_json(path.replace(".json", ".translated.json")))
            assert list(out) == ["\u6328\u62f6", "\u6255\u3046", "PluginCommonBase", ""]
            assert out["\u6328\u62f6"] == "\u95ee\u5019"
            assert out["\u6255\u3046"] == "\u652f\u4ed8"
            assert out["PluginCommonBase"] == "PluginCommonBase"
            assert out[""] == ""

            cp = checkpoint.load_checkpoint(path)
            entries = cp["entries"]
            assert entries["0_0"]["status"] == "translated"
            assert entries["0_0"]["source_key"] == "\u6328\u62f6"
            assert entries["0_0"]["source_hash"]
            assert entries["0_0"]["normalized_source"] == "\u6328\u62f6"
            assert entries["0_0"]["output_translation"] == "\u95ee\u5019"
            assert entries["0_0"]["translation_direction"] == "ja-Hans"
            assert entries["2_0"]["status"] == "preserved"
            assert entries["3_0"]["status"] == "preserved"
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir


def test_mtool_json_can_use_line_batch_protocol(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import config
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="line_batch_json_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        old_protocol = config.DEFAULT_CONFIG["batch_translation"].get("protocol", "json")
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        config.DEFAULT_CONFIG["batch_translation"]["protocol"] = "line"
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u9580\u756a": "\u9580\u756a",
                    "\u6255\u3046": "\u6255\u3046",
                    "\u3084\u3081\u3066\u304a\u304f": "\u3084\u3081\u3066\u304a\u304f",
                }, f, ensure_ascii=False)

            calls = []

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                calls.append(text)
                assert "\t" in text
                translations = {
                    "\u6255\u3046": "\u652f\u4ed8",
                    "\u3084\u3081\u3066\u304a\u304f": "\u5148\u7b97\u4e86",
                }
                rows = []
                for line in text.splitlines():
                    idx, source = line.split("\t", 1)
                    rows.append(f"{idx}\t{translations[source]}")
                return "\n".join(rows)

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            result = TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json"))).translate_file(path)

            assert len(calls) == 1
            assert dict(parse_json(path.replace(".json", ".translated.json"))) == {
                "\u9580\u756a": "\u95e8\u536b",
                "\u6255\u3046": "\u652f\u4ed8",
                "\u3084\u3081\u3066\u304a\u304f": "\u5148\u7b97\u4e86",
            }
            assert dict(result)["\u9580\u756a"] == "\u95e8\u536b"
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir
            config.DEFAULT_CONFIG["batch_translation"]["protocol"] = old_protocol


def test_auto_batch_protocol_uses_file_shape_not_content():
    from translator.pipeline import TranslationPipeline

    pipeline = TranslationPipeline()
    short_items = [(f"\u30e9\u30d9\u30eb{i}", f"\u30e9\u30d9\u30eb{i}") for i in range(30)]
    long_items = [
        (f"\u9577\u6587{i}\u3002\u3053\u308c\u306f\u9577\u3044\u8aac\u660e\u6587\u3067\u3001\u30e2\u30c7\u30eb\u3067\u81ea\u7136\u306b\u7ffb\u8a33\u3059\u308b\u5fc5\u8981\u304c\u3042\u308a\u307e\u3059\u3002", "")
        for i in range(30)
    ]

    assert pipeline._resolve_batch_protocol("auto", short_items, True, {}) == "line"
    assert pipeline._resolve_batch_protocol("auto", long_items, True, {}) == "json"

    short_candidates = [{"short_label": True} for _ in range(20)]
    mixed_candidates = [{"short_label": True} for _ in range(19)] + [{"short_label": False}]
    assert pipeline._resolve_candidate_batch_protocol("auto", "json", short_candidates) == "line"
    assert pipeline._resolve_candidate_batch_protocol("auto", "json", mixed_candidates) == "json"
    assert pipeline._resolve_candidate_batch_protocol("json", "line", short_candidates) == "json"


def test_json_batch_saves_frozen_glossary_candidate_evidence(monkeypatch):
    from translator import checkpoint
    from translator.glossary import Glossary
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="batch_glossary_save_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u9580\u756a": "\u9580\u756a",
                    "\u6255\u3046": "\u6255\u3046",
                    "\u3084\u3081\u3066\u304a\u304f": "\u3084\u3081\u3066\u304a\u304f",
                }, f, ensure_ascii=False)

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                return '[{"i":0,"t":"\u95e8\u536b"},{"i":1,"t":"\u652f\u4ed8"},{"i":2,"t":"\u5148\u7b97\u4e86"}]'

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            glossary = Glossary(file_path=os.path.join(tmpdir, "glossary.json"))
            saves = []
            monkeypatch.setattr(glossary, "save", lambda: saves.append(1))

            TranslationPipeline(glossary=glossary).translate_file(path)

            assert len(saves) == 1
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir


def test_batch_failure_falls_back_to_single_cell(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import config
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="batch_fallback_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        old_parallel = config.DEFAULT_CONFIG["batch_translation"].get("api_parallel_enabled", False)
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        config.DEFAULT_CONFIG["batch_translation"]["api_parallel_enabled"] = False
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"\u30d5\u30a3\u30fc\u30cd": "\u30d5\u30a3\u30fc\u30cd"}, f, ensure_ascii=False)

            calls = []

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                calls.append(text)
                if len(calls) == 1:
                    return "not json"
                return "\u83f2\u59ae"

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
            TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json"))).translate_file(path)

            assert len(calls) == 2
            assert dict(parse_json(path.replace(".json", ".translated.json")))["\u30d5\u30a3\u30fc\u30cd"] == "\u83f2\u59ae"
            entry = checkpoint.get_entry(path, 0, 0)
            assert any(issue["type"] == "batch_fallback" for issue in entry["issues"])
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir
            config.DEFAULT_CONFIG["batch_translation"]["api_parallel_enabled"] = old_parallel


def test_batch_parse_failure_splits_before_single_fallback(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import config
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="batch_split_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        old_parallel = config.DEFAULT_CONFIG["batch_translation"].get("api_parallel_enabled", False)
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        config.DEFAULT_CONFIG["batch_translation"]["api_parallel_enabled"] = False
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u30d5\u30a3\u30fc\u30cd": "\u30d5\u30a3\u30fc\u30cd",
                    "\u30b8\u30fc\u30af": "\u30b8\u30fc\u30af",
                }, f, ensure_ascii=False)

            calls = []

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                calls.append(text)
                if len(calls) == 1:
                    return "not json"
                if "\u30d5\u30a3\u30fc\u30cd" in text:
                    return '[{"i":0,"t":"\u83f2\u59ae"}]'
                return '[{"i":1,"t":"\u5409\u514b"}]'

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
            TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json"))).translate_file(path)

            out = dict(parse_json(path.replace(".json", ".translated.json")))
            assert out["\u30d5\u30a3\u30fc\u30cd"] == "\u83f2\u59ae"
            assert out["\u30b8\u30fc\u30af"] == "\u5409\u514b"
            assert len(calls) == 3
            assert not checkpoint.get_entry(path, 0, 0)["issues"]
            assert not checkpoint.get_entry(path, 1, 0)["issues"]
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir
            config.DEFAULT_CONFIG["batch_translation"]["api_parallel_enabled"] = old_parallel


def test_api_parallel_batches_write_results_by_original_rows(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import config
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="api_parallel_batch_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        old_provider = config.DEFAULT_CONFIG.get("model_provider")
        old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        config.DEFAULT_CONFIG["model_provider"] = "api"
        config.DEFAULT_CONFIG["batch_translation"].update({
            "api_parallel_enabled": True,
            "api_concurrency": 2,
            "api_max_retries": 0,
            "json_batch_size": 2,
            "max_batch_chars": 4000,
            "protocol": "json",
            "compact_json_protocol": False,
        })
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u30d5\u30a3\u30fc\u30cd": "\u30d5\u30a3\u30fc\u30cd",
                    "\u30b8\u30fc\u30af": "\u30b8\u30fc\u30af",
                    "\u30ea\u30ea\u30a2": "\u30ea\u30ea\u30a2",
                    "\u30ab\u30a4": "\u30ab\u30a4",
                }, f, ensure_ascii=False)

            calls = []
            translations = {
                "\u30d5\u30a3\u30fc\u30cd": "\u83f2\u59ae",
                "\u30b8\u30fc\u30af": "\u5409\u514b",
                "\u30ea\u30ea\u30a2": "\u8389\u8389\u5a05",
                "\u30ab\u30a4": "\u51ef",
            }

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                payload = json.loads(text)
                calls.append([item["text"] for item in payload])
                return json.dumps(
                    [{"i": item["i"], "t": translations[item["text"]]} for item in payload],
                    ensure_ascii=False,
                )

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            TranslationPipeline(
                model="api:test-model",
                glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json")),
            ).translate_file(path)

            assert len(calls) == 2
            assert dict(parse_json(path.replace(".json", ".translated.json"))) == {
                "\u30d5\u30a3\u30fc\u30cd": "\u83f2\u59ae",
                "\u30b8\u30fc\u30af": "\u5409\u514b",
                "\u30ea\u30ea\u30a2": "\u8389\u8389\u5a05",
                "\u30ab\u30a4": "\u51ef",
            }
            cp = checkpoint.load_checkpoint(path)
            assert {entry["batch_id"] for entry in cp["entries"].values()} == {"api_batch_000000", "api_batch_000001"}
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir
            config.DEFAULT_CONFIG["model_provider"] = old_provider
            config.DEFAULT_CONFIG["batch_translation"].clear()
            config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_api_parallel_failed_batch_does_not_mark_successful_fallback_as_issue(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    from translator.scheduler import BatchResult
    import config
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="api_parallel_recovered_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        old_provider = config.DEFAULT_CONFIG.get("model_provider")
        old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        config.DEFAULT_CONFIG["model_provider"] = "api"
        config.DEFAULT_CONFIG["batch_translation"].update({
            "api_parallel_enabled": True,
            "api_concurrency": 1,
            "api_max_retries": 0,
            "json_batch_size": 2,
            "max_batch_chars": 4000,
            "protocol": "json",
            "compact_json_protocol": False,
        })
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u30d5\u30a3\u30fc\u30cd": "\u30d5\u30a3\u30fc\u30cd",
                    "\u30b8\u30fc\u30af": "\u30b8\u30fc\u30af",
                }, f, ensure_ascii=False)

            def fake_run_concurrent_batches(jobs, worker_count, translate_job, **kwargs):
                assert len(jobs) == 1
                yield BatchResult(jobs[0].batch_id, {}, RuntimeError("temporary API failure"), 1, 0.01)

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                payload = json.loads(text)
                translations = {
                    "\u30d5\u30a3\u30fc\u30cd": "\u83f2\u59ae",
                    "\u30b8\u30fc\u30af": "\u5409\u514b",
                }
                return json.dumps(
                    [{"i": item["i"], "t": translations[item["text"]]} for item in payload],
                    ensure_ascii=False,
                )

            monkeypatch.setattr(pipeline_mod, "run_concurrent_batches", fake_run_concurrent_batches)
            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            TranslationPipeline(
                model="api:test-model",
                glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json")),
            ).translate_file(path)

            assert dict(parse_json(path.replace(".json", ".translated.json"))) == {
                "\u30d5\u30a3\u30fc\u30cd": "\u83f2\u59ae",
                "\u30b8\u30fc\u30af": "\u5409\u514b",
            }
            cp = checkpoint.load_checkpoint(path)
            assert {entry["status"] for entry in cp["entries"].values()} == {"translated"}
            assert all(not entry["validation_issues"] for entry in cp["entries"].values())
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir
            config.DEFAULT_CONFIG["model_provider"] = old_provider
            config.DEFAULT_CONFIG["batch_translation"].clear()
            config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_api_parallel_routing_uses_fast_line_for_short_and_quality_json_for_long(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import config
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="api_routing_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        old_provider = config.DEFAULT_CONFIG.get("model_provider")
        old_batch = dict(config.DEFAULT_CONFIG["batch_translation"])
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        config.DEFAULT_CONFIG["model_provider"] = "api"
        config.DEFAULT_CONFIG["batch_translation"].update({
            "api_parallel_enabled": True,
            "api_concurrency": 2,
            "api_max_retries": 0,
            "api_model_routing_enabled": True,
            "api_fast_model": "api:minimax-m3",
            "api_quality_model": "api:qwen3.7-plus",
            "json_batch_size": 2,
            "max_batch_chars": 4000,
            "protocol": "line",
            "compact_json_protocol": False,
            "line_for_short_only": True,
            "short_line_max_chars": 12,
        })
        try:
            path = os.path.join(tmpdir, "sample.json")
            long_source = "\u3053\u308c\u306f\u9577\u3044\u53f0\u8a5e\u3067\u3059\u3002\u81ea\u7136\u306a\u4e2d\u56fd\u8a9e\u306b\u7ffb\u8a33\u3059\u308b\u5fc5\u8981\u304c\u3042\u308a\u307e\u3059\u3002"
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u30d5\u30a3\u30fc\u30cd": "\u30d5\u30a3\u30fc\u30cd",
                    "\u30b8\u30fc\u30af": "\u30b8\u30fc\u30af",
                    long_source: long_source,
                }, f, ensure_ascii=False)

            calls = []

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                calls.append({"model": model, "text": text, "system_prompt": system_prompt})
                if model == "api:minimax-m3":
                    return "0\t\u83f2\u59ae\n1\t\u5409\u514b"
                payload = json.loads(text)
                return json.dumps([{"i": item["i"], "t": "\u8fd9\u662f\u4e00\u53e5\u957f\u53f0\u8bcd\u3002"} for item in payload], ensure_ascii=False)

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            TranslationPipeline(
                model="api:qwen3.7-plus",
                glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json")),
            ).translate_file(path)

            assert [(call["model"], call["text"].lstrip().startswith("[")) for call in calls] == [
                ("api:minimax-m3", False),
                ("api:qwen3.7-plus", True),
            ]
            out = dict(parse_json(path.replace(".json", ".translated.json")))
            assert out["\u30d5\u30a3\u30fc\u30cd"] == "\u83f2\u59ae"
            assert out["\u30b8\u30fc\u30af"] == "\u5409\u514b"
            assert out[long_source] == "\u8fd9\u662f\u4e00\u53e5\u957f\u53f0\u8bcd\u3002"
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir
            config.DEFAULT_CONFIG["model_provider"] = old_provider
            config.DEFAULT_CONFIG["batch_translation"].clear()
            config.DEFAULT_CONFIG["batch_translation"].update(old_batch)


def test_json_batch_collection_crosses_deterministic_gaps(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import config
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="batch_gap_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        old_batch_size = config.DEFAULT_CONFIG["batch_translation"]["json_batch_size"]
        old_batch_chars = config.DEFAULT_CONFIG["batch_translation"]["max_batch_chars"]
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        config.DEFAULT_CONFIG["batch_translation"]["json_batch_size"] = 10
        config.DEFAULT_CONFIG["batch_translation"]["max_batch_chars"] = 2000
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u30d5\u30a3\u30fc\u30cd": "\u30d5\u30a3\u30fc\u30cd",
                    "PluginCommonBase": "PluginCommonBase",
                    "\u30b8\u30fc\u30af": "\u30b8\u30fc\u30af",
                }, f, ensure_ascii=False)

            calls = []

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                calls.append(text)
                assert "PluginCommonBase" not in text
                return '[{"i":0,"t":"\u83f2\u59ae"},{"i":1,"t":"\u5409\u514b"}]'

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json"))).translate_file(path)

            out = dict(parse_json(path.replace(".json", ".translated.json")))
            assert out["\u30d5\u30a3\u30fc\u30cd"] == "\u83f2\u59ae"
            assert out["PluginCommonBase"] == "PluginCommonBase"
            assert out["\u30b8\u30fc\u30af"] == "\u5409\u514b"
            assert len(calls) == 1
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir
            config.DEFAULT_CONFIG["batch_translation"]["json_batch_size"] = old_batch_size
            config.DEFAULT_CONFIG["batch_translation"]["max_batch_chars"] = old_batch_chars


def test_json_batch_collection_respects_max_batch_chars():
    from translator.glossary import Glossary
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="batch_chars_") as tmpdir:
        pipeline = TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json")))
        items = [
            ("\u30d5\u30a3\u30fc\u30cd", "\u30d5\u30a3\u30fc\u30cd"),
            ("\u30b8\u30fc\u30af", "\u30b8\u30fc\u30af"),
            ("\u30e2\u30fc\u30ea", "\u30e2\u30fc\u30ea"),
        ]
        candidates = pipeline._collect_json_batch(
            items,
            start_idx=0,
            mtool=True,
            completed={},
            batch_size=10,
            max_batch_chars=len("\u30d5\u30a3\u30fc\u30cd") + 1,
        )

        assert [candidate["idx"] for candidate in candidates] == [0]


def test_glossary_preseed_enables_high_confidence_name_confirmation():
    from translator.glossary import Glossary

    with tempfile.TemporaryDirectory(prefix="preseed_glossary_") as tmpdir:
        glossary = Glossary(file_path=os.path.join(tmpdir, "glossary.json"))
        added = glossary.preseed_from_sources([
            "\u30d5\u30a3\u30fc\u30cd\uff1a\u6765\u305f",
            "\u30d5\u30a3\u30fc\u30cd\uff1a\u7b11\u3063\u305f",
            "\u6255\u3046",
            "\u6255\u3046",
        ])

        assert added == 1
        assert "\u30d5\u30a3\u30fc\u30cd" in glossary.candidates
        assert "\u6255\u3046" not in glossary.candidates
        assert glossary.auto_extract("\u30d5\u30a3\u30fc\u30cd\uff1a\u6765\u305f", "\u83f2\u59ae\uff1a\u6765\u4e86") == []
        assert glossary.auto_extract("\u30d5\u30a3\u30fc\u30cd\uff1a\u7b11\u3063\u305f", "\u83f2\u59ae\uff1a\u7b11\u4e86")
        assert glossary.terms["\u30d5\u30a3\u30fc\u30cd"] == "\u83f2\u59ae"


def test_offline_dictionary_yomitan_exact_chinese_matches_are_optional():
    import zipfile
    from translation.terminology import YomitanDictionary, summarize_yomitan_matches
    from translation.terminology import dictionary as terminology_dictionary
    from translator import offline_dictionary as legacy_dictionary

    root = Path(__file__).resolve().parents[1]
    terminology_init = (root / "translation" / "terminology" / "__init__.py").read_text(encoding="utf-8")
    dictionary_text = (root / "translation" / "terminology" / "dictionary.py").read_text(encoding="utf-8")

    assert "translator.offline_dictionary" not in terminology_init
    assert "translator.offline_dictionary" not in dictionary_text
    assert legacy_dictionary.YomitanDictionary is YomitanDictionary is terminology_dictionary.YomitanDictionary
    assert legacy_dictionary.summarize_yomitan_matches is summarize_yomitan_matches is terminology_dictionary.summarize_yomitan_matches

    with tempfile.TemporaryDirectory(prefix="yomitan_dict_") as tmpdir:
        path = os.path.join(tmpdir, "dict.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "term_bank_1.json",
                json.dumps([
                    ["\u9580\u756a", "\u3082\u3093\u3070\u3093", "", "", 0, ["\u95e8\u536b"], 1, ""],
                    ["\u30c6\u30b9\u30c8", "\u30c6\u30b9\u30c8", "", "", 0, ["test"], 2, ""],
                    ["\u30aa\u30fc\u30af", "\u30aa\u30fc\u30af", "", "", 0, ["\u8bcd\u6e90"], 3, ""],
                ], ensure_ascii=False),
            )

        dictionary = YomitanDictionary([path])
        assert dictionary.lookup("\u9580\u756a")[0].target == "\u95e8\u536b"
        assert dictionary.lookup("\u30c6\u30b9\u30c8") == []
        assert dictionary.lookup("\u30aa\u30fc\u30af") == []

        summary = summarize_yomitan_matches(["\u9580\u756a", "\u30c6\u30b9\u30c8"], dictionary)
        assert summary["exact_matches"] == 1
        assert summary["exact_short_matches"] == 1


def test_offline_dictionary_sudachi_unavailable_is_nonfatal(monkeypatch):
    from translation.terminology import SudachiProvider, summarize_sudachi_candidates

    provider = SudachiProvider()
    provider.available = False
    provider.error = "missing"

    summary = summarize_sudachi_candidates(["\u9580\u756a"], provider)
    assert summary["available"] is False
    assert summary["error"] == "missing"
    assert summary["texts_with_terms"] == 0


def test_frozen_glossary_does_not_change_later_json_batch_prompts(monkeypatch):
    from parser.json_parser import parse_json
    from translator import checkpoint
    from translator.glossary import Glossary
    import config
    import translator.pipeline as pipeline_mod
    from translator.pipeline import TranslationPipeline

    with tempfile.TemporaryDirectory(prefix="preseed_batches_") as tmpdir:
        old_dir = checkpoint.CHECKPOINT_DIR
        old_batch_size = config.DEFAULT_CONFIG["batch_translation"]["json_batch_size"]
        old_batch_chars = config.DEFAULT_CONFIG["batch_translation"]["max_batch_chars"]
        old_parallel = config.DEFAULT_CONFIG["batch_translation"].get("api_parallel_enabled", False)
        checkpoint.CHECKPOINT_DIR = os.path.join(tmpdir, ".checkpoints")
        config.DEFAULT_CONFIG["batch_translation"]["json_batch_size"] = 1
        config.DEFAULT_CONFIG["batch_translation"]["max_batch_chars"] = 2000
        config.DEFAULT_CONFIG["batch_translation"]["api_parallel_enabled"] = False
        try:
            path = os.path.join(tmpdir, "sample.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "\u30d5\u30a3\u30fc\u30cd\uff1a\u6765\u305f": "\u30d5\u30a3\u30fc\u30cd\uff1a\u6765\u305f",
                    "\u30d5\u30a3\u30fc\u30cd\uff1a\u7b11\u3063\u305f": "\u30d5\u30a3\u30fc\u30cd\uff1a\u7b11\u3063\u305f",
                    "\u30d5\u30a3\u30fc\u30cd\uff1a\u5f85\u3063\u305f": "\u30d5\u30a3\u30fc\u30cd\uff1a\u5f85\u3063\u305f",
                }, f, ensure_ascii=False)

            calls = []

            def fake_translate(model, text, system_prompt=None, terminology=None, options=None, **kwargs):
                calls.append(text)
                if len(calls) == 1:
                    return '[{"i":0,"t":"\u83f2\u59ae\uff1a\u6765\u4e86"}]'
                if len(calls) == 2:
                    assert "\u83f2\u59ae" not in text
                    return '[{"i":0,"t":"\u83f2\u59ae\uff1a\u7b11\u4e86"}]'
                assert "\u83f2\u59ae" not in text
                return '[{"i":0,"t":"\u83f2\u59ae\uff1a\u7b49\u4e86\u4e00\u4f1a"}]'

            monkeypatch.setattr(pipeline_mod, "translate_once", fake_translate)
            monkeypatch.setattr(pipeline_mod, "translate", fake_translate)
            TranslationPipeline(glossary=Glossary(file_path=os.path.join(tmpdir, "glossary.json"))).translate_file(path)

            out = dict(parse_json(path.replace(".json", ".translated.json")))
            assert out["\u30d5\u30a3\u30fc\u30cd\uff1a\u6765\u305f"] == "\u83f2\u59ae\uff1a\u6765\u4e86"
            assert out["\u30d5\u30a3\u30fc\u30cd\uff1a\u7b11\u3063\u305f"] == "\u83f2\u59ae\uff1a\u7b11\u4e86"
            assert out["\u30d5\u30a3\u30fc\u30cd\uff1a\u5f85\u3063\u305f"] == "\u83f2\u59ae\uff1a\u7b49\u4e86\u4e00\u4f1a"
        finally:
            checkpoint.CHECKPOINT_DIR = old_dir
            config.DEFAULT_CONFIG["batch_translation"]["json_batch_size"] = old_batch_size
            config.DEFAULT_CONFIG["batch_translation"]["max_batch_chars"] = old_batch_chars
            config.DEFAULT_CONFIG["batch_translation"]["api_parallel_enabled"] = old_parallel
