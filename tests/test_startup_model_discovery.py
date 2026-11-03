from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import time
import threading

import pytest
import requests

from app.routes import models as routes
from app.schemas import PreflightRequest
from app.services.models import available_models
from translation import settings
from translation.models import api_client, ollama_client, router
from translation.models.catalog_cache import CatalogCache


@pytest.fixture
def isolated_catalog(monkeypatch):
    monkeypatch.setattr(ollama_client, "_MODEL_CATALOG", CatalogCache())
    monkeypatch.setattr(ollama_client, "ollama_host", lambda: "http://discovery-test.invalid:11434")
    monkeypatch.setattr(api_client, "list_models", lambda: [{"name": "remote-model", "provider": "api"}])
    monkeypatch.setitem(settings.DEFAULT_CONFIG, "model_provider", "api")
    monkeypatch.setitem(settings.DEFAULT_CONFIG, "model", "api:remote-model")
    monkeypatch.setitem(settings.DEFAULT_CONFIG, "third_party_api", {
        "style": "openai", "preset": "openai", "profile_id": "legacy",
        "base_url": "https://api.invalid/v1", "api_key": "offline-test-key",
        "models": ["remote-model"],
    })


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {"models": [{"name": "local-model"}, {"name": "some-embed"}]}


def test_api_catalog_and_preflight_skip_blocked_ollama(isolated_catalog, monkeypatch, tmp_path):
    calls = []

    def delayed_offline(*args, **kwargs):
        calls.append(kwargs)
        time.sleep(0.18)
        raise requests.exceptions.Timeout("controlled offline discovery")

    monkeypatch.setattr(ollama_client, "transport_request", delayed_offline)
    started = time.perf_counter()
    payload = routes.get_models()
    fast_elapsed = time.perf_counter() - started
    assert calls == []
    assert [model["id"] for model in payload["models"]] == ["api:remote-model"]
    assert payload["runtime"]["providers"]["ollama"]["discovery_checked"] is False
    path = tmp_path / "source.json"
    path.write_text(json.dumps({"こんにちは": "こんにちは"}, ensure_ascii=False), encoding="utf-8")
    assert routes.preflight(PreflightRequest(file_path=str(path), execution_profile="single_model", model="api:remote-model"))["ok"]
    assert calls == []
    started = time.perf_counter()
    assert router.list_models(refresh_local=True)[0]["name"] == "api:remote-model"
    synchronous_elapsed = time.perf_counter() - started
    assert synchronous_elapsed >= 0.17
    assert fast_elapsed < synchronous_elapsed / 2
    assert calls[0]["timeout"] == (0.5, 1.5)
    measurement = {"controlled_discovery_delay_seconds": 0.18, "synchronous_catalog_seconds": synchronous_elapsed, "configured_api_catalog_seconds": fast_elapsed}
    (tmp_path / "startup-measurement.json").write_text(json.dumps(measurement, indent=2), encoding="utf-8")
    print("Controlled startup latency:", json.dumps(measurement))
    assert not routes.get_models()["runtime"]["providers"]["ollama"]["configured"]


def test_ollama_success_cache_explicit_refresh_and_failure_status(isolated_catalog, monkeypatch):
    calls = []
    monkeypatch.setattr(ollama_client, "transport_request", lambda *a, **kw: calls.append(kw) or Response())
    models = ollama_client.list_models()
    assert models == [{"name": "local-model"}]
    models[0]["name"] = "changed-by-caller"
    assert ollama_client.list_models() == [{"name": "local-model"}]
    assert len(calls) == 1
    assert any(model["name"] == "ollama:local-model" for model in available_models())
    assert routes.get_models()["runtime"]["providers"]["ollama"]["configured"] is True

    def offline(*a, **kw):
        calls.append(kw)
        raise requests.exceptions.ConnectionError("stopped")

    monkeypatch.setattr(ollama_client, "transport_request", offline)
    with pytest.raises(ConnectionError):
        ollama_client.list_models(refresh=True)
    with pytest.raises(ConnectionError):
        ollama_client.list_models()
    assert len(calls) == 2, "A negative TTL must avoid repeatedly probing an offline service"
    snapshot = ollama_client.model_catalog_snapshot()
    assert snapshot["checked"] and snapshot["stale"] and not snapshot["available"]
    assert snapshot["models"] == [{"name": "local-model"}], "Keep the last known catalog as stale information"
    failed = routes.get_models()
    assert failed["runtime"]["providers"]["ollama"]["configured"] is False
    assert failed["runtime"]["providers"]["ollama"]["cached_model_count"] == 1
    assert all(model["provider"] != "ollama" for model in failed["models"])
    monkeypatch.setattr(ollama_client, "transport_request", lambda *a, **kw: Response())
    refreshed = routes.get_models(discover_local=True, refresh=True)
    assert refreshed["runtime"]["providers"]["ollama"]["configured"] is True


def test_local_discovery_is_singleflight_and_ttl_expiry_requires_recheck(monkeypatch):
    cache = CatalogCache(ttl=10, failure_ttl=2)
    calls = []

    def loader():
        calls.append(1)
        time.sleep(0.06)
        return [{"name": "local"}]

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: cache.get("host-one", loader, refresh=True), range(6)))
    assert results == [[{"name": "local"}]] * 6
    assert len(calls) == 1
    assert cache.snapshot("host-two")["checked"] is False
    snapshot = cache.snapshot("host-one")
    assert snapshot["available"] and not snapshot["pending"]
    monkeypatch.setattr("translation.models.catalog_cache.time.monotonic", lambda: 10**12)
    assert not cache.snapshot("host-one")["available"]
    assert cache.snapshot("host-one")["stale"]
    assert cache.get("host-one", lambda: [{"name": "new-local"}]) == [{"name": "new-local"}]


def test_pending_discovery_cannot_report_old_host_models_after_settings_change(isolated_catalog, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setitem(settings.DEFAULT_CONFIG, "ollama_host", "http://old-host.invalid:11434")
    monkeypatch.setattr(ollama_client, "ollama_host", lambda: settings.DEFAULT_CONFIG["ollama_host"])

    def delayed_request(*args, **kwargs):
        assert args[2] == "http://old-host.invalid:11434/api/tags"
        entered.set()
        assert release.wait(2)
        return Response()

    monkeypatch.setattr(ollama_client, "transport_request", delayed_request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(routes.get_models, True, False)
        assert entered.wait(1)
        settings.DEFAULT_CONFIG["ollama_host"] = "http://new-host.invalid:11434"
        release.set()
        payload = future.result(timeout=2)
    assert all(model["provider"] != "ollama" for model in payload["models"])
    assert payload["runtime"]["providers"]["ollama"]["configured"] is False
    assert payload["discovery"]["ollama"]["checked"] is False


def test_failed_old_host_probe_does_not_hide_successful_new_host_catalog(isolated_catalog, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setitem(settings.DEFAULT_CONFIG, "ollama_host", "http://old-host.invalid:11434")
    monkeypatch.setattr(ollama_client, "ollama_host", lambda: settings.DEFAULT_CONFIG["ollama_host"])

    def delayed_request(*args, **kwargs):
        if "new-host.invalid" in args[2]:
            return Response()
        entered.set()
        assert release.wait(2)
        raise requests.exceptions.ConnectionError("old server unavailable")

    monkeypatch.setattr(ollama_client, "transport_request", delayed_request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(routes.get_models, True, False)
        assert entered.wait(1)
        settings.DEFAULT_CONFIG["ollama_host"] = "http://new-host.invalid:11434"
        assert ollama_client.list_models() == [{"name": "local-model"}]
        release.set()
        payload = future.result(timeout=2)
    assert any(model["id"] == "ollama:local-model" for model in payload["models"])
    assert payload["runtime"]["providers"]["ollama"]["configured"] is True
    assert payload["discovery"]["ollama"]["available"] is True
