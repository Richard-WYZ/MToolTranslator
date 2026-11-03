from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from app.desktop import Api
from app.services import workspace_drafts as store


@pytest.fixture
def draft_root(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "runtime_base_dir", lambda: tmp_path)
    return tmp_path


def review(text="修改中的译文", row=35):
    return {"path": "E:\\项目 空格\\源文件.json", "row": row, "text": text, "original": "アリス\n帰ります。"}


def glossary(target="艾莉丝"):
    return {"file": "E:\\项目 空格\\源文件.json", "source": "アリス", "target": target, "initialTarget": "爱丽丝"}


def test_bridge_roundtrip_keeps_editor_kinds_separate_and_does_not_apply_text(draft_root):
    bridge = Api()
    assert bridge.get_workspace_drafts("review") == []
    assert bridge.get_workspace_drafts("glossary") == []
    assert not (draft_root / ".checkpoints").exists()
    assert bridge.set_workspace_drafts("review", [review("")]) == {"ok": True, "count": 1}
    assert bridge.set_workspace_drafts("glossary", [glossary()])["count"] == 1
    assert Api().get_workspace_drafts("review") == [review("")]
    assert Api().get_workspace_drafts("glossary") == [glossary()]
    assert bridge.pending_review_draft is False
    assert bridge.set_pending_review_draft(True) is True
    review_path = draft_root / ".checkpoints" / "workspace-drafts-review.json"
    glossary_path = draft_root / ".checkpoints" / "workspace-drafts-glossary.json"
    glossary_bytes = glossary_path.read_bytes()
    bridge.set_workspace_drafts("review", [])
    assert review_path.exists() and bridge.get_workspace_drafts("review") == []
    assert glossary_path.read_bytes() == glossary_bytes
    assert sorted(path.name for path in (draft_root / ".checkpoints").iterdir()) == ["workspace-drafts-glossary.json", "workspace-drafts-review.json"]


@pytest.mark.parametrize("kind,drafts", [
    ("../other", []), ("review", {}), ("review", [review(row=-1)]),
    ("review", [review(row=True)]), ("review", [{**review(), "api_key": "must-not-be-stored"}]),
    ("review", [{**review(), "text": {"nested": "bad"}}]),
    ("glossary", [{**glossary(), "source": ""}]),
    ("glossary", [{**glossary(), "file": "\x00"}]),
])
def test_invalid_snapshots_never_replace_last_saved_drafts(draft_root, kind, drafts):
    store.set_workspace_drafts("review", [review()])
    store.set_workspace_drafts("glossary", [glossary()])
    with pytest.raises(ValueError):
        store.set_workspace_drafts(kind, drafts)
    assert store.get_workspace_drafts("review") == [review()]
    assert store.get_workspace_drafts("glossary") == [glossary()]


def test_deduplication_and_failed_atomic_replace_keep_recovery_data(draft_root, monkeypatch):
    assert store.set_workspace_drafts("review", [review("旧稿"), review("新稿")])["count"] == 1
    assert store.get_workspace_drafts("review") == [review("新稿")]

    def failed_replace(*args):
        raise OSError("controlled replacement failure")

    monkeypatch.setattr(store.os, "replace", failed_replace)
    with pytest.raises(OSError):
        store.set_workspace_drafts("review", [review("不完整写入")])
    assert store.get_workspace_drafts("review") == [review("新稿")]
    assert len(list((draft_root / ".checkpoints").iterdir())) == 1


def test_large_multi_entry_drafts_are_retained_and_limits_fail_without_truncation(draft_root, monkeypatch):
    drafts = [review("草稿" * 100, row=row) for row in range(1000)]
    store.set_workspace_drafts("review", drafts)
    assert store.get_workspace_drafts("review") == drafts
    monkeypatch.setattr(store, "MAX_DRAFTS", 2)
    with pytest.raises(ValueError):
        store.set_workspace_drafts("review", [review(row=row) for row in range(3)])
    monkeypatch.setattr(store, "MAX_DRAFTS", 200_000)
    assert store.get_workspace_drafts("review") == drafts
    monkeypatch.setattr(store, "MAX_STORE_BYTES", 64)
    with pytest.raises(ValueError):
        store.set_workspace_drafts("review", [review("超长" * 20)])
    monkeypatch.setattr(store, "MAX_STORE_BYTES", 64 * 1024 * 1024)
    assert store.get_workspace_drafts("review") == drafts


def test_concurrent_updates_are_complete_atomic_snapshots(draft_root):
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda row: store.set_workspace_drafts("review", [review(str(row), row=row)]), range(24)))
    drafts = store.get_workspace_drafts("review")
    assert len(drafts) == 1 and drafts[0]["text"] == str(drafts[0]["row"])
    payload = json.loads((draft_root / ".checkpoints" / "workspace-drafts-review.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1 and payload["drafts"] == drafts
