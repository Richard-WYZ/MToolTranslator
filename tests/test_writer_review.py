from __future__ import annotations

import json
import time
from threading import Event
from threading import Thread

import pytest


def test_small_json_batch_flushes_on_deadline(tmp_path):
    from translation.output import TranslationWriter

    output_path = tmp_path / "out.json"
    writer = TranslationWriter(
        "json",
        [("a", "old")],
        str(output_path),
        flush_interval=0.05,
        json_every=100,
    )
    writer.start()
    writer.update_cell(0, 0, "new")
    deadline = time.monotonic() + 2
    while not output_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert output_path.exists()
    writer.stop()

    assert json.loads(output_path.read_text(encoding="utf-8")) == {"a": "new"}
    assert not writer.is_alive()


def test_stop_propagates_background_write_failure(tmp_path, monkeypatch):
    from translation.output import TranslationWriter

    writer = TranslationWriter(
        "json",
        [("a", "old")],
        str(tmp_path / "out.json"),
        flush_interval=0.01,
        json_every=1,
    )
    failure_seen = Event()

    def fail(_snapshot=None):
        failure_seen.set()
        raise OSError("write failed")

    monkeypatch.setattr(writer, "_write_atomic", fail)
    writer.start()
    writer.mark_dirty()
    assert failure_seen.wait(2)
    with pytest.raises(OSError, match="write failed"):
        writer.stop()
    assert not writer.is_alive()


def test_stop_flushes_pending_update_without_waiting_for_deadline(tmp_path):
    from translation.output import TranslationWriter

    output_path = tmp_path / "out.json"
    writer = TranslationWriter(
        "json",
        [("a", "old")],
        str(output_path),
        flush_interval=60,
        json_every=100,
    )
    writer.start()
    writer.update_cell(0, 0, "new")

    started = time.monotonic()
    writer.stop()

    assert time.monotonic() - started < 2
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"a": "new"}
    assert not writer.is_alive()


def test_concurrent_flushes_serialize_snapshot_and_write(tmp_path, monkeypatch):
    from translation.output import TranslationWriter

    output_path = tmp_path / "out.json"
    data = [("a", "old")]
    writer = TranslationWriter(
        "json",
        data,
        str(output_path),
        periodic_enabled=False,
    )
    first_write_started = Event()
    release_first_write = Event()
    snapshots = []

    def controlled_write(snapshot=None):
        snapshots.append(list(snapshot or []))
        if len(snapshots) == 1:
            first_write_started.set()
            assert release_first_write.wait(2)
        writer._write_atomic_original(snapshot)

    writer._write_atomic_original = writer._write_atomic
    monkeypatch.setattr(writer, "_write_atomic", controlled_write)
    writer.mark_dirty()

    first = Thread(target=writer.flush)
    first.start()
    assert first_write_started.wait(2)

    writer.update_cell(0, 0, "new")
    second = Thread(target=writer.flush)
    second.start()
    release_first_write.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert snapshots == [[("a", "old")], [("a", "new")]]
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"a": "new"}
