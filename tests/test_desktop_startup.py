from types import SimpleNamespace

import pytest
import requests

import app.desktop as desktop


def test_ready_desktop_server_opens_without_a_fixed_delay(monkeypatch):
    sleeps = []
    monkeypatch.setattr(desktop, "time", SimpleNamespace(monotonic=lambda: 0, sleep=sleeps.append))
    client = SimpleNamespace(get=lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: {"status": "ok"}))
    desktop.wait_for_server_ready(client)
    assert sleeps == []


def test_desktop_waits_for_readiness_and_times_out_without_busy_looping(monkeypatch):
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(desktop, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise requests.ConnectionError("not listening yet")

    with pytest.raises(TimeoutError):
        desktop.wait_for_server_ready(SimpleNamespace(get=request), timeout=0.09)
    assert calls and all(timeout == (0.2, 0.3) for timeout in calls)
    assert len(sleeps) == 3 and now[0] == pytest.approx(0.09)
