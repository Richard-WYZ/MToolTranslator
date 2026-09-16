from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from translation import usage
from translation.batching import BatchJob, run_concurrent_batches, run_dynamic_batches
from translation.terminology import Glossary
from translation.workflow.pipeline import TranslationPipeline


@pytest.mark.parametrize("dynamic", [False, True])
def test_parallel_workers_charge_only_their_own_task(dynamic):
    barrier = Barrier(2)
    trackers = [usage.UsageTracker(), usage.UsageTracker()]

    def run(index):
        def translate(job):
            barrier.wait(timeout=5)
            started = usage.record_request_start("fake", job.model)
            usage.record("fake", job.model, {"total_tokens": index + 1})
            usage.record_response_received("fake", job.model, started)
            return {0: "translated"}

        jobs = [BatchJob(str(index), [], "json", model=f"model-{index}")]
        with usage.use_tracker(trackers[index]):
            if dynamic:
                results = run_dynamic_batches(jobs, 1, translate, lambda *args: (), max_retries=0)
            else:
                results = list(run_concurrent_batches(jobs, 1, translate, max_retries=0))
            assert all(result.error is None for result in results)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run, range(2)))
    for index, tracker in enumerate(trackers):
        snapshot = tracker.snapshot()
        assert snapshot["total_tokens"] == index + 1
        assert snapshot["request_calls"] == 1
        assert set(snapshot["by_provider"]["fake"]["models"]) == {f"model-{index}"}


def test_completed_pipeline_keeps_its_usage_after_another_run(tmp_path):
    class RecordingPipeline(TranslationPipeline):
        def _translate_json(self, *args):
            usage.record("fake", self.model, {"total_tokens": 17 if self.model == "A" else 3})
            usage.set_runtime_metadata("task", self.model)
            return []

    source = tmp_path / "game.json"
    source.write_text('{"物語": "物語"}', encoding="utf-8")
    first = RecordingPipeline(model="A", glossary=Glossary.in_memory())
    second = RecordingPipeline(model="B", glossary=Glossary.in_memory())
    first.translate_file(str(source))
    first_snapshot = first.token_usage()
    second.translate_file(str(source))
    assert first.token_usage() == first_snapshot
    assert first_snapshot["total_tokens"] == 17
    assert second.token_usage()["total_tokens"] == 3
    # Callers cannot mutate a stored snapshot through the public response.
    first_snapshot["runtime"]["task"] = "changed"
    assert first.token_usage()["runtime"]["task"] == "A"


def test_usage_scope_restores_outer_collector_on_failure():
    outer, inner = usage.UsageTracker(), usage.UsageTracker()
    with usage.use_tracker(outer):
        with pytest.raises(ValueError), usage.use_tracker(inner):
            usage.record("fake", "inner", {"total_tokens": 9})
            raise ValueError("failed")
        usage.record("fake", "outer", {"total_tokens": 4})
    assert outer.snapshot()["total_tokens"] == 4
    assert inner.snapshot()["total_tokens"] == 9


def test_parallel_finalization_always_stops_writer_on_checkpoint_error(monkeypatch):
    from translation.workflow import parallel_progress

    stopped = []
    pipeline = SimpleNamespace(_writer=SimpleNamespace(stop=lambda: stopped.append(True)))
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(parallel_progress.checkpoint, "save_progress_many", fail)
    with pytest.raises(OSError, match="disk full"):
        parallel_progress.finalize_parallel_run(
            pipeline, file_path="unused.json", translated_items=[], processed_targets=0,
            total_targets=0, progress_callback=None, collection_records=[], result_records=[],
            deferred_confirmed_terms={},
        )
    assert stopped == [True]


def test_cancel_before_file_entry_is_not_cleared(tmp_path, monkeypatch):
    from translation.workflow.pipeline import TranslationCancelled

    source = tmp_path / "game.json"
    source.write_text('{"物語": "物語"}', encoding="utf-8")
    pipeline = TranslationPipeline(glossary=Glossary.in_memory())
    calls = []
    monkeypatch.setattr(pipeline, "_translate_json", lambda *args: calls.append(args))
    pipeline.cancel()
    with pytest.raises(TranslationCancelled):
        pipeline.translate_file(str(source))
    assert calls == []


def test_batch_setup_failure_stops_writer_and_thaws_glossary(tmp_path, monkeypatch):
    source = tmp_path / "game.json"
    source.write_text('{"猫が歩いている": "猫が歩いている"}', encoding="utf-8")
    pipeline = TranslationPipeline(glossary=Glossary(file_path=str(tmp_path / "glossary.json")))

    def fail(*args):
        raise ValueError("invalid batch setup")

    monkeypatch.setattr(pipeline, "_resolve_batch_protocol", fail)
    with pytest.raises(ValueError, match="invalid batch setup"):
        pipeline.translate_file(str(source))
    assert pipeline._writer is not None
    assert not pipeline._writer.is_alive()
    assert not pipeline.glossary.frozen


def test_pause_before_file_entry_waits_for_resume(tmp_path, monkeypatch):
    from threading import Event, Thread

    source = tmp_path / "game.json"
    source.write_text('{"物語": "物語"}', encoding="utf-8")
    pipeline = TranslationPipeline(glossary=Glossary.in_memory())
    entered = Event()
    monkeypatch.setattr(pipeline, "_translate_json", lambda *args: entered.set())
    pipeline.pause()
    thread = Thread(target=pipeline.translate_file, args=(str(source),))
    thread.start()
    try:
        assert not entered.wait(0.1)
        pipeline.resume()
        assert entered.wait(3)
    finally:
        pipeline.resume()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_resume_invalidates_outputs_when_retry_policy_changes(tmp_path, monkeypatch):
    import json
    from translation.workflow import json_flow, pipeline as pipeline_module

    source = tmp_path / "game.json"
    source.write_text(json.dumps({"\u98df\u3079\u308b": "\u98df\u3079\u308b"}), encoding="utf-8")
    calls = []

    def translate(model, text, **kwargs):
        calls.append(text)
        return json.dumps([{"i": item["i"], "t": "\u5403"} for item in json.loads(text)])

    monkeypatch.setattr(pipeline_module, "translate_once", translate)

    def run():
        TranslationPipeline(
            model="api:test",
            glossary=Glossary(file_path=str(tmp_path / "glossary.json")),
        ).translate_file(str(source))

    run()
    run()
    assert len(calls) == 1
    monkeypatch.setattr(json_flow, "RETRY_POLICY_VERSION", "changed-policy")
    run()
    assert len(calls) == 2
