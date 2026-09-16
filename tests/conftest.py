from __future__ import annotations

import copy
import os
import tempfile
import time
import uuid
from pathlib import Path

import pytest


# Keep implicit runtime state away from the repository and the developer's
# credentials. This runs during pytest collection, before most application
# modules are imported.
_TEST_BASE_ROOT = Path(__file__).resolve().parents[1] / "test_work" / "pytest"
_TEST_BASE_ROOT.mkdir(parents=True, exist_ok=True)

# Every pytest process gets its own runtime tree. pytest is allowed to clear
# its basetemp, so sharing a fixed directory would erase previous diagnostics
# and test artifacts from another run.
_requested_runtime_root = os.environ.get("LOCAL_GAME_TRANSLATOR_TEST_ROOT")
if _requested_runtime_root:
    _TEST_RUNTIME_ROOT = Path(_requested_runtime_root).resolve()
    try:
        _TEST_RUNTIME_ROOT.relative_to(_TEST_BASE_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeError(
            "LOCAL_GAME_TRANSLATOR_TEST_ROOT must stay under test_work/pytest"
        ) from exc
    _TEST_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
else:
    _run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    _TEST_RUNTIME_ROOT = _TEST_BASE_ROOT / _run_id
    _TEST_RUNTIME_ROOT.mkdir()

_TEST_TEMP_ROOT = _TEST_RUNTIME_ROOT / "tempfiles"
_TEST_PYTEST_TEMP_ROOT = _TEST_RUNTIME_ROOT / "pytest-temp"
_TEST_CACHE_ROOT = _TEST_RUNTIME_ROOT / "cache"
_TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
_TEST_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["LOCAL_GAME_TRANSLATOR_TEST_MODE"] = "1"
for _name in ("TMP", "TEMP", "TMPDIR"):
    os.environ[_name] = str(_TEST_TEMP_ROOT)
tempfile.tempdir = str(_TEST_TEMP_ROOT)

_ORIGINAL_CWD = Path.cwd()

from translation import checkpoint as _checkpoint

_checkpoint.CHECKPOINT_DIR = str(_TEST_RUNTIME_ROOT / "checkpoints")


def pytest_sessionfinish(session, exitstatus):
    os.chdir(_ORIGINAL_CWD)


def pytest_sessionstart(session):
    # Keep testpaths resolution rooted at the project, then isolate relative
    # application files (for example the default glossary) for test execution.
    os.chdir(_TEST_RUNTIME_ROOT)


def pytest_configure(config):
    # run_tests.ps1 supplies these options for reproducible collection. Direct
    # pytest invocations receive the same unique paths here.
    if not config.option.basetemp:
        config.option.basetemp = str(_TEST_PYTEST_TEMP_ROOT)
    # cache_dir is an ini value, not a command-line option. The cache plugin
    # has already constructed its object by this hook, so repoint that object
    # for direct pytest invocations whose ini still has the fixed default.
    if config.getini("cache_dir") == "test_work/pytest/cache" and config.cache is not None:
        config.cache._cachedir = _TEST_CACHE_ROOT


@pytest.fixture(autouse=True)
def block_network_by_default(monkeypatch):
    """Keep test runs offline while allowing tests to replace HTTP clients."""
    if os.environ.get("LOCAL_GAME_TRANSLATOR_ALLOW_NETWORK") == "1":
        return

    import socket
    import requests

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def is_loopback(address):
        try:
            host = str(address[0]).strip().lower()
        except (IndexError, TypeError):
            return False
        return host in {"localhost", "127.0.0.1", "::1"}

    def deny_connect(sock, address):
        if is_loopback(address):
            return original_connect(sock, address)
        raise RuntimeError("network access is disabled during tests")

    def deny_connect_ex(sock, address):
        if is_loopback(address):
            return original_connect_ex(sock, address)
        raise RuntimeError("network access is disabled during tests")

    def deny_create_connection(address, *args, **kwargs):
        if is_loopback(address):
            return original_create_connection(address, *args, **kwargs)
        raise RuntimeError("network access is disabled during tests")

    monkeypatch.setattr(socket.socket, "connect", deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connect_ex)
    monkeypatch.setattr(socket, "create_connection", deny_create_connection)

    def deny_http_request(*args, **kwargs):
        raise RuntimeError("HTTP requests are disabled during tests")

    monkeypatch.setattr(requests.sessions.Session, "request", deny_http_request)


@pytest.fixture(autouse=True)
def stable_test_batch_config():
    import config

    old_batch = copy.deepcopy(config.DEFAULT_CONFIG.get("batch_translation", {}))
    config.DEFAULT_CONFIG["batch_translation"].update({
        "enabled": True,
        "protocol": "json",
        "json_batch_size": 40,
        "max_batch_chars": 4000,
        "num_predict": 2048,
        "response_format": None,
        "temperature": 0,
        "timeout": 300,
        "api_parallel_enabled": False,
        "api_concurrency": 10,
        "api_max_retries": 2,
        "api_retry_backoff_seconds": [2, 5, 15],
    })
    try:
        yield
    finally:
        config.DEFAULT_CONFIG["batch_translation"].clear()
        config.DEFAULT_CONFIG["batch_translation"].update(old_batch)
