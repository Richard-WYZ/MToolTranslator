from __future__ import annotations

import copy

import pytest


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
