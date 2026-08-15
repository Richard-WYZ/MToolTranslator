from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.schemas import AIReviewRequest
from app.services.runtime_profiles import (
    QUALITY_FAST_MODEL,
    QUALITY_PRIMARY_MODEL,
    canonical_model_id,
    resolve_execution_profile,
)
from translation.models import router as model_router
from translation.workflow.pipeline import TranslationPipeline


def test_model_ids_preserve_provider_identity(monkeypatch):
    monkeypatch.setattr(
        model_router.ollama_client,
        "list_models",
        lambda: [{"name": "qwen-local", "size": 123}],
    )
    monkeypatch.setattr(
        model_router.api_client,
        "list_models",
        lambda: [{"name": "qwen-api", "provider": "api"}],
    )

    models = model_router.list_models()

    assert [item["name"] for item in models] == ["ollama:qwen-local", "api:qwen-api"]
    assert models[0]["provider"] == "ollama"
    assert model_router.model_configuration("ollama:qwen-local")["provider"] == "ollama"
    assert canonical_model_id("qwen-api", "api") == "api:qwen-api"


def test_ai_review_defaults_to_required_entries():
    assert AIReviewRequest(file_path="fixture.json").scope == "required"


def test_quality_profile_matches_validated_route():
    model, config, summary = resolve_execution_profile("quality_first", None)

    assert model == QUALITY_PRIMARY_MODEL
    assert config["api_fast_model"] == QUALITY_FAST_MODEL
    assert config["api_sensitive_model"] == QUALITY_FAST_MODEL
    assert config["api_quality_model"] == QUALITY_PRIMARY_MODEL
    assert config["api_concurrency"] == 10
    assert config["json_batch_size"] == 40
    assert config["max_batch_chars"] == 4000
    assert config["protocol"] == "json"
    assert config["compact_json_protocol"] is True
    assert config["line_for_short_only"] is True
    assert {route["role"] for route in summary["routes"]} == {
        "短标签",
        "普通文本",
        "敏感文本",
        "质量修复",
    }


def test_single_model_profile_disables_cross_model_routes():
    model, config, summary = resolve_execution_profile("single_model", "api:test-model")

    assert model == "api:test-model"
    assert config["api_model_routing_enabled"] is False
    assert config["api_sensitive_routing_enabled"] is False
    assert config["api_fast_model"] == ""
    assert config["api_quality_model"] == ""
    assert config["protocol"] == "json"
    assert summary["routes"] == [{"role": "普通文本", "model": "api:test-model"}]


def test_execution_profile_rejects_models_disabled_in_settings(monkeypatch):
    import app.services.runtime_profiles as runtime_profiles

    monkeypatch.setattr(
        runtime_profiles,
        "disabled_models",
        lambda provider: ["minimax-m3"] if provider == "api" else [],
    )

    with pytest.raises(Exception) as exc:
        resolve_execution_profile("quality_first", None)

    assert getattr(exc.value, "status_code", None) == 409
    assert "api:minimax-m3" in str(getattr(exc.value, "detail", exc.value))


def test_pipeline_uses_task_scoped_batch_configuration():
    pipeline = TranslationPipeline(
        model="api:test-model",
        batch_config_override={"json_batch_size": 7, "max_batch_chars": 999},
    )

    config = pipeline._batch_translation_config()
    config["json_batch_size"] = 100

    assert pipeline._batch_translation_config()["json_batch_size"] == 7
    assert pipeline._batch_translation_config()["max_batch_chars"] == 999


def test_preflight_reports_real_file_counts(monkeypatch, tmp_path: Path):
    import app.routes.models as models_route
    import main

    source = tmp_path / "sample.json"
    source.write_text(
        json.dumps(
            {
                "こんにちは": "こんにちは",
                "EV001": "EV001",
                "": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        models_route,
        "available_models",
        lambda: [
            {"name": QUALITY_PRIMARY_MODEL, "provider": "api"},
            {"name": QUALITY_FAST_MODEL, "provider": "api"},
        ],
    )
    client = TestClient(main.app)

    response = client.post(
        "/api/preflight",
        json={
            "file_path": str(source),
            "execution_profile": "quality_first",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["file"]["total_entries"] == 3
    assert payload["file"]["model_bound_entries"] >= 1
    assert payload["profile"]["primary_model"] == QUALITY_PRIMARY_MODEL
    assert payload["effective_batch"]["concurrency"] == 10


def test_preflight_marks_different_checkpoint_model_for_revalidation(monkeypatch, tmp_path: Path):
    import app.routes.models as models_route
    import main
    from translation import checkpoint

    source = tmp_path / "resume.json"
    source.write_text(json.dumps({"こんにちは": "こんにちは"}, ensure_ascii=False), encoding="utf-8")
    original_checkpoint_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        checkpoint.init_checkpoint(
            str(source),
            total=1,
            model="ollama:old-model",
            prompt_style="professional",
            file_type="json",
        )
        checkpoint.save_progress(
            str(source),
            0,
            0,
            "こんにちは",
            "你好",
            status="translated",
        )
        monkeypatch.setattr(
            models_route,
            "available_models",
            lambda: [
                {"name": QUALITY_PRIMARY_MODEL, "provider": "api"},
                {"name": QUALITY_FAST_MODEL, "provider": "api"},
            ],
        )
        response = TestClient(main.app).post(
            "/api/preflight",
            json={"file_path": str(source), "execution_profile": "quality_first"},
        )
    finally:
        checkpoint.CHECKPOINT_DIR = original_checkpoint_dir

    assert response.status_code == 200
    payload = response.json()
    assert payload["checkpoint"]["found"] is True
    assert payload["checkpoint"]["model_match"] is False
    assert payload["checkpoint"]["reuse_status"] == "model_mismatch"
    assert any("重新校验" in warning for warning in payload["warnings"])


def test_review_actions_keep_draft_and_preserve_states(tmp_path: Path):
    import main
    from translation import checkpoint
    from translation.output import default_output_path

    source = tmp_path / "review-actions.json"
    source.write_text(json.dumps({"こんにちは": "こんにちは"}, ensure_ascii=False), encoding="utf-8")
    Path(default_output_path(str(source))).write_text(
        json.dumps({"こんにちは": "你好"}, ensure_ascii=False),
        encoding="utf-8",
    )
    original_checkpoint_dir = checkpoint.CHECKPOINT_DIR
    checkpoint.CHECKPOINT_DIR = str(tmp_path / "checkpoints")
    try:
        checkpoint.save_progress(str(source), 0, 0, "こんにちは", "你好", status="translated")
        client = TestClient(main.app)
        draft = client.post(
            "/api/review/save",
            json={"file_path": str(source), "row": 0, "col": 0, "text": "您好", "action": "draft"},
        )
        assert draft.status_code == 200
        assert checkpoint.get_entry(str(source), 0, 0)["status"] == "translated_needs_review"

        preserve = client.post(
            "/api/review/save",
            json={"file_path": str(source), "row": 0, "col": 0, "text": "不会使用", "action": "preserve"},
        )
        assert preserve.status_code == 200
        assert checkpoint.get_entry(str(source), 0, 0)["status"] == "preserved"
        output = json.loads(Path(default_output_path(str(source))).read_text(encoding="utf-8"))
        assert output["こんにちは"] == "こんにちは"
    finally:
        checkpoint.CHECKPOINT_DIR = original_checkpoint_dir


def test_ui_v2_removes_misleading_controls_and_exposes_workspaces():
    root = Path(__file__).resolve().parents[1]
    html = (root / "ui" / "templates" / "index.html").read_text(encoding="utf-8")
    scripts = sorted((root / "ui" / "static" / "js").glob("*.js"))
    styles = sorted((root / "ui" / "static" / "css").glob("*.css"))
    script = "\n".join(path.read_text(encoding="utf-8") for path in scripts)

    assert 'id="prompt-select"' not in html
    assert "暂停新请求" in html
    assert "停止并写盘" in html
    assert "导出完整结果" in html
    for page in ("translate", "review", "glossary", "history", "settings"):
        assert f'id="page-{page}"' in html
    assert "api:qwen3.7-plus" in script
    assert "api:minimax-m3" in script
    assert 'id="settings-api-key"' in html
    assert 'id="settings-form"' in html
    assert "<title>MTool 汉化工具</title>" in html
    assert 'id="settings-advanced"' in html
    assert 'id="settings-model-list"' in html
    assert 'id="btn-test-enabled-models"' in html
    assert 'id="btn-test-enabled-nsfw"' in html
    assert 'id="settings-api-models"' not in html
    assert "/settings/connection-test" in script
    assert "/settings/models/discover" in script
    assert "api_key_action" in script
    assert "data-model-enabled" in script
    assert "data-test-model-nsfw" in script
    assert "availability-badge" in script
    assert "NSFW 可用" in script
    assert "NSFW 上次测试" in script
    assert "model_test_statuses" in script
    assert 'id="ai-review-model"' in html
    assert 'id="ai-verifier-model"' in html
    assert 'id="btn-ai-review-start"' in html
    assert 'id="btn-ai-review-resume"' in html
    assert '<option value="required" selected>仅必须复核</option>' in html
    assert 'data-filter="required" class="active"' in html
    assert 'data-filter="advisory"' in html
    assert 'data-filter="preserved"' in html
    assert 'id="review-select-page"' in html
    assert 'id="review-selected-count"' in html
    assert 'id="btn-review-clear-selection"' in html
    assert 'id="btn-review-export"' in html
    assert 'id="review-busy-overlay"' in html
    assert 'id="review-busy-label"' in html
    assert "/review/ai/preflight" in script
    assert "/review/ai/start" in script
    assert "system_corrections" in script
    assert "counts.reclassified" in script
    assert "/rollback" in script
    assert "/export/status" in script
    assert "/history/session?file_path=" in script
    assert "data-delete-history-path" in script
    assert "window.pywebview && window.pywebview.api" in script
    assert "save_file_dialog" in script
    assert "output_path: destination.path" in script
    assert "overwrite_original" in script
    assert "state.originalFilePath" in script
    assert "consume_dropped_file_path" in script
    assert "/import-local" in script
    assert "setReviewRowSelected" in script
    assert "toggleCurrentReviewPage" in script
    assert "setReviewActionBusy" in script
    assert "reviewActionIsBusy" in script
    assert 'setReviewActionBusy("save"' in script
    assert 'setReviewActionBusy("batch_save"' in script
    assert 'setReviewActionBusy("ai_start"' in script
    assert "warning is-working" in script
    main_script = (root / "ui" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    filter_handler = main_script.split('el("review-filters")', 1)[1].split('el("review-queue")', 1)[0]
    assert "selectedRows.clear" not in filter_handler
    assert 'test_kind: testKind' in script
    assert "state.settingsConnectionDirty" in script
    assert len(scripts) == 11
    assert len(styles) == 4
    assert max(len(path.read_text(encoding="utf-8").splitlines()) for path in scripts) < 400
    assert "/static/app.js" not in html
    assert "/static/style.css" not in html
    desktop_script = (root / "app" / "desktop.py").read_text(encoding="utf-8")
    assert "create_confirmation_dialog" in desktop_script
    assert "window.evaluate_js" not in desktop_script
    assert "capture_dropped_files" in desktop_script
    assert "DOMEventHandler" in desktop_script
