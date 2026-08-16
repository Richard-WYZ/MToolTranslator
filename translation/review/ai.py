from __future__ import annotations

import json
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from translation import checkpoint
from translation.analysis import apply_mtool_compositions, build_mtool_composition_plan
from translation.batching import prepare_model_candidate
from translation.classification import (
    deterministic_translation,
    has_explicit_adult_content,
    has_source_japanese,
    looks_like_short_label,
)
from translation.config import output_constraints
from translation.input import load_json_items
from translation.models import model_configuration, translate as model_translate
from translation.output import serialize_json_items
from translation.pollution import translation_pollution_issues
from translation.protection import restore_protected_translation
from translation.quality import (
    apply_source_conditioned_fixes,
    assess_model_output,
    get_violations,
    new_issues,
    status_for_output,
    translation_issues,
)
from translation.quality.status import HARD_REVIEW_ISSUE_TYPES
from translation.review import write_review_report
from translation.terminology import Glossary


AI_REVIEW_VERSION = "ai-review-v11-auto-retry"
AI_REVIEW_ACTIVE_STATUSES = {"preparing", "reviewing", "verifying", "applying", "finalizing"}
AI_REVIEW_TERMINAL_STATUSES = {"completed", "cancelled", "error"}
NON_WAIVABLE_ISSUE_TYPES = set(HARD_REVIEW_ISSUE_TYPES) | {
    "line_break_preservation",
    "numeric_value_preservation",
    "symbol_preservation",
    "term_preservation",
    "marker_loss",
    "unsupported_context",
}
PRIMARY_RETRY_ISSUE_TYPES = NON_WAIVABLE_ISSUE_TYPES | {"english_residue"}

TranslateFunc = Callable[[str, str, str, dict[str, Any] | None], str]
ProgressFunc = Callable[[dict[str, Any]], None]
CancelFunc = Callable[[], bool]


@dataclass(slots=True)
class AIReviewModels:
    review: str
    verifier: str
    sensitive: str
    sensitive_verifier: str

    def as_dict(self) -> dict[str, str]:
        return {
            "review": self.review,
            "verifier": self.verifier,
            "sensitive": self.sensitive,
            "sensitive_verifier": self.sensitive_verifier,
        }


class AIReviewCancelled(RuntimeError):
    pass


def ai_review_store_path(file_path: str) -> str:
    return checkpoint.get_checkpoint_path(file_path) + ".ai-review.json"


def load_ai_review_store(file_path: str) -> dict[str, Any]:
    path = ai_review_store_path(file_path)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "file_path": os.path.abspath(file_path), "sessions": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
        return {"version": 1, "file_path": os.path.abspath(file_path), "sessions": []}
    return payload


def save_ai_review_store(file_path: str, payload: dict[str, Any]) -> None:
    path = Path(ai_review_store_path(file_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(rendered)
            temp_path = Path(stream.name)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def latest_ai_review_by_row(file_path: str) -> dict[int, dict[str, Any]]:
    store = load_ai_review_store(file_path)
    latest: dict[int, dict[str, Any]] = {}
    for session in store.get("sessions", []):
        if not isinstance(session, dict):
            continue
        for record in session.get("records", []) or []:
            if not isinstance(record, dict):
                continue
            try:
                row = int(record.get("row"))
            except (TypeError, ValueError):
                continue
            row_status = "" if session.get("rolled_back_at") else record.get("result", "")
            latest[row] = {
                "task_id": session.get("task_id", ""),
                "status": row_status,
                "decision": record.get("decision", ""),
                "review_model": record.get("review_model", session.get("models", {}).get("review", "")),
                "verifier_model": record.get("verifier_model", session.get("models", {}).get("verifier", "")),
                "translation": record.get("after", ""),
                "updated_at": session.get("finished_at") or session.get("updated_at") or session.get("started_at", ""),
            }
    return latest


def get_ai_review_session(file_path: str, task_id: str = "") -> dict[str, Any] | None:
    sessions = load_ai_review_store(file_path).get("sessions", [])
    matches = [session for session in sessions if isinstance(session, dict) and (not task_id or session.get("task_id") == task_id)]
    return dict(matches[-1]) if matches else None


def begin_ai_review_session(
    *,
    task_id: str,
    file_path: str,
    models: AIReviewModels,
    items: list[dict[str, Any]],
    auto_apply: bool,
    auto_retry: bool = True,
) -> None:
    store = load_ai_review_store(file_path)
    sessions = store.setdefault("sessions", [])
    session = next((value for value in sessions if isinstance(value, dict) and value.get("task_id") == task_id), None)
    if session is None:
        session = {"task_id": task_id, "started_at": _now(), "work": {}}
        sessions.append(session)
    session.update({
        "version": AI_REVIEW_VERSION,
        "status": "preparing",
        "models": models.as_dict(),
        "request": {
            "items": items,
            "auto_apply": bool(auto_apply),
            "auto_retry": bool(auto_retry),
        },
        "retry": {
            "enabled": bool(auto_retry),
            "rounds": 0,
            "no_progress_rounds": 0,
            "remaining": len(items),
        },
        "updated_at": _now(),
        "finished_at": "",
    })
    save_ai_review_store(file_path, store)


def update_ai_review_session_status(
    file_path: str,
    task_id: str,
    status: str,
    *,
    error: str = "",
    token_usage: dict[str, Any] | None = None,
) -> None:
    store = load_ai_review_store(file_path)
    session = next((value for value in store.get("sessions", []) if isinstance(value, dict) and value.get("task_id") == task_id), None)
    if session is None:
        return
    session["status"] = status
    session["updated_at"] = _now()
    if error:
        session["error"] = error
    if token_usage is not None:
        session["token_usage"] = dict(token_usage)
    if status in {"completed", "cancelled", "error", "candidate_ready"}:
        session["finished_at"] = _now()
    save_ai_review_store(file_path, store)


def estimate_review_usage(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    rows = list(items)
    total_chars = sum(
        len(str(item.get("source", "")))
        + len(str(item.get("current", "")))
        + sum(len(str(value)) for value in item.get("issue_types", []) or [])
        + sum(len(str(context.get("original", ""))) for context in item.get("neighbors", []) or [])
        for item in rows
    )
    primary_batches = _batch_count(rows, max_items=20, max_chars=3000)
    verifier_batches = _batch_count(rows, max_items=16, max_chars=3600)
    # Review prompts include the source/current pair twice and may have one bounded repair.
    estimated_tokens = max(0, int(total_chars * 2.4) + len(rows) * 90)
    return {
        "entries": len(rows),
        "estimated_requests": primary_batches + verifier_batches,
        "estimated_tokens": estimated_tokens,
    }


def build_deterministic_reclassification_records(
    source_texts: list[str],
    current_texts: list[str],
    checkpoint_entries: dict[Any, Any],
    glossary: Glossary | None = None,
) -> list[dict[str, Any]]:
    """Build rollback-safe repairs for outputs made stale by newer deterministic rules."""
    records: list[dict[str, Any]] = []
    for row, source in enumerate(source_texts):
        current = current_texts[row] if row < len(current_texts) else source
        deterministic = deterministic_translation(source, glossary=glossary)
        deterministic_quality_repair = False
        if not deterministic:
            repaired = apply_source_conditioned_fixes(source, current)
            repair_issues = translation_issues(
                source,
                repaired,
                short_label=looks_like_short_label(source),
            )
            if repaired != current and not repair_issues:
                deterministic = repaired
                deterministic_quality_repair = True
        if not deterministic:
            continue
        cp_entry = checkpoint_entries.get(f"{row}_0", checkpoint_entries.get((row, 0), {}))
        if not isinstance(cp_entry, dict):
            cp_entry = {}
        expected_status = status_for_output(source, deterministic)
        before_issues = list(cp_entry.get("issues", []) or [])
        already_current = (
            current == deterministic
            and str(cp_entry.get("translated", current)) == deterministic
            and str(cp_entry.get("status", "")) == expected_status
            and str(cp_entry.get("entry_classification", "")) == "deterministic"
            and not before_issues
        )
        if already_current:
            continue
        records.append({
            "row": row,
            "source": source,
            "before": current,
            "after": deterministic,
            "before_status": str(cp_entry.get("status", "pending")),
            "before_issues": before_issues,
            "before_model": str(cp_entry.get("model_identifier", "")),
            "before_entry_classification": str(cp_entry.get("entry_classification", "")),
            "entry_classification": (
                "deterministic_quality_repair"
                if deterministic_quality_repair
                else "deterministic"
            ),
            "decision": "deterministic",
            "primary_decision": "deterministic",
            "result": "reclassified",
            "final_status": expected_status,
            "remaining_issues": [],
            "resolved_issue_types": sorted({
                str(issue.get("type", ""))
                for issue in before_issues
                if isinstance(issue, dict) and str(issue.get("type", ""))
            }),
            "review_model": "deterministic:classification",
            "verifier_model": "",
            "attempts": 0,
            "applied": False,
            "message": "Reclassified by a newer deterministic preservation rule.",
            "deterministic_reclassification": True,
        })
    return records


def run_ai_review(
    *,
    task_id: str,
    file_path: str,
    items: list[dict[str, Any]],
    models: AIReviewModels,
    output_path: str,
    progress: ProgressFunc,
    cancelled: CancelFunc,
    translator: TranslateFunc | None = None,
    auto_apply: bool = True,
    auto_retry: bool = False,
    reclassification_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    translator = translator or _translate
    if not items and not reclassification_records:
        return _empty_result(task_id, file_path, models)

    glossary = Glossary(file_path=checkpoint.get_glossary_path(file_path))
    deterministic_records = [dict(record) for record in reclassification_records or []]
    deterministic_rows = {int(record["row"]) for record in deterministic_records}
    prepared = [
        _prepare_item(item, glossary)
        for item in items
        if int(item.get("row", -1)) not in deterministic_rows
    ]
    deterministic_count = len(deterministic_records)
    total = deterministic_count + len(prepared)
    progress({
        "status": "reviewing",
        "phase": "reviewing",
        "current": deterministic_count,
        "total": total,
    })

    work = _load_session_work(file_path, task_id)
    primary_results = _decode_result_map(work.get("primary", {})) if work.get("primary_complete") else {}
    if not work.get("primary_complete"):
        primary_results = _run_primary_batches(
            prepared,
            models=models,
            translator=translator,
            cancelled=cancelled,
            progress=progress,
            progress_offset=deterministic_count,
            progress_total=total,
        )
        _raise_if_cancelled(cancelled)

        retry_items: list[dict[str, Any]] = []
        for item in prepared:
            result = primary_results.get(item["row"], {})
            candidate = str(result.get("translation", ""))
            if result.get("decision") == "unable" or _has_retry_issue(result.get("issues", [])) or not candidate:
                retry_item = dict(item)
                retry_issue_types = sorted({
                    *item["issue_types"],
                    *[str(issue.get("type", "")) for issue in result.get("issues", []) if isinstance(issue, dict)],
                })
                retry_item["issue_types_for_prompt"] = retry_issue_types
                retry_item["current_for_prompt"] = (
                    "" if any(issue in NON_WAIVABLE_ISSUE_TYPES for issue in retry_issue_types)
                    else candidate or item["current"]
                )
                retry_items.append(retry_item)
        if retry_items:
            retry_results = _run_primary_batches(
                retry_items,
                models=models,
                translator=translator,
                cancelled=cancelled,
                progress=progress,
                progress_offset=deterministic_count + len(prepared),
                progress_total=total + len(retry_items),
                retry=True,
            )
            primary_results.update(retry_results)
        for item in prepared:
            result = primary_results.get(item["row"], {})
            if (
                ("\n" in item["source"] or "\r" in item["source"])
                and _has_non_waivable_issue(result.get("issues", []))
            ):
                isolated = _repair_multiline_by_lines(
                    item,
                    model=models.sensitive if item["sensitive"] else models.review,
                    translator=translator,
                )
                if isolated and not _has_non_waivable_issue(isolated.get("issues", [])):
                    primary_results[item["row"]] = isolated
        _save_session_work(file_path, task_id, primary=primary_results, primary_complete=True)

    _raise_if_cancelled(cancelled)
    progress({
        "status": "verifying",
        "phase": "verifying",
        "current": deterministic_count,
        "total": total,
    })
    work = _load_session_work(file_path, task_id)
    verifier_results = _decode_result_map(work.get("verifier", {})) if work.get("verifier_complete") else {}
    if not work.get("verifier_complete"):
        verifier_results = _run_verifier_batches(
            prepared,
            primary_results,
            models=models,
            translator=translator,
            cancelled=cancelled,
            progress=progress,
            progress_total=total,
            progress_offset=deterministic_count,
        )
        _save_session_work(file_path, task_id, verifier=verifier_results, verifier_complete=True)
    _raise_if_cancelled(cancelled)

    model_records = [
        _resolve_record(item, primary_results.get(item["row"], {}), verifier_results.get(item["row"], {}))
        for item in prepared
    ]
    records_by_row = {int(record["row"]): record for record in model_records}
    latest_primary = dict(primary_results)
    retry_rounds = 1 if prepared else 0
    initial_successes = deterministic_count + sum(
        str(record.get("result", "")) in {"fixed", "confirmed"}
        for record in model_records
    )
    no_progress_rounds = 0 if initial_successes else (1 if prepared else 0)
    pending = [
        item for item in prepared
        if str(records_by_row[item["row"]].get("result", "")) not in {"fixed", "confirmed"}
    ]

    while auto_retry and pending and no_progress_rounds < 3:
        retry_rounds += 1
        resolved_before_round = len(prepared) - len(pending)

        def retry_progress(payload: dict[str, Any]) -> None:
            progress({
                **payload,
                "retry_rounds": retry_rounds,
                "no_progress_rounds": no_progress_rounds,
            })

        retry_items: list[dict[str, Any]] = []
        for item in pending:
            retry_item = dict(item)
            previous = latest_primary.get(item["row"], {})
            previous_candidate = str(previous.get("translation", ""))
            retry_issue_types = sorted({
                *item["issue_types"],
                *[
                    str(issue.get("type", ""))
                    for issue in previous.get("issues", [])
                    if isinstance(issue, dict) and str(issue.get("type", ""))
                ],
            })
            retry_item["issue_types_for_prompt"] = retry_issue_types
            retry_item["current_for_prompt"] = (
                ""
                if any(issue in NON_WAIVABLE_ISSUE_TYPES for issue in retry_issue_types)
                else previous_candidate or item["current"]
            )
            retry_items.append(retry_item)

        round_primary = _run_primary_batches(
            retry_items,
            models=models,
            translator=translator,
            cancelled=cancelled,
            progress=retry_progress,
            progress_offset=deterministic_count + resolved_before_round,
            progress_total=total,
            retry=True,
        )
        _raise_if_cancelled(cancelled)
        round_verifier = _run_verifier_batches(
            retry_items,
            round_primary,
            models=models,
            translator=translator,
            cancelled=cancelled,
            progress=retry_progress,
            progress_total=total,
            progress_offset=deterministic_count + resolved_before_round,
        )
        _raise_if_cancelled(cancelled)

        round_records = [
            _resolve_record(item, round_primary.get(item["row"], {}), round_verifier.get(item["row"], {}))
            for item in retry_items
        ]
        round_successes = sum(
            str(record.get("result", "")) in {"fixed", "confirmed"}
            for record in round_records
        )
        for record in round_records:
            records_by_row[int(record["row"])] = record
        latest_primary.update(round_primary)
        pending = [
            item for item in pending
            if str(records_by_row[item["row"]].get("result", "")) not in {"fixed", "confirmed"}
        ]
        no_progress_rounds = 0 if round_successes else no_progress_rounds + 1
        progress({
            "status": "reviewing",
            "phase": "reviewing",
            "current": deterministic_count + len(prepared) - len(pending),
            "total": total,
            "retry_rounds": retry_rounds,
            "no_progress_rounds": no_progress_rounds,
        })

    model_records = [records_by_row[item["row"]] for item in prepared]
    records = sorted(
        [*deterministic_records, *model_records],
        key=lambda record: int(record.get("row", -1)),
    )
    counts = _record_counts(records)
    applied = 0
    if auto_apply:
        progress({"status": "applying", "phase": "applying", "current": total, "total": total})
        applied = apply_ai_review_records(
            task_id=task_id,
            file_path=file_path,
            output_path=output_path,
            records=records,
            models=models,
        )
    else:
        _persist_session(
            task_id=task_id,
            file_path=file_path,
            models=models,
            records=records,
            status="candidate_ready",
            applied=0,
        )
    counts["applied"] = applied
    _persist_retry_metadata(
        file_path=file_path,
        task_id=task_id,
        rounds=retry_rounds,
        no_progress_rounds=no_progress_rounds,
        remaining=len(pending),
        enabled=auto_retry,
    )
    progress({"status": "finalizing", "phase": "finalizing", "current": total, "total": total})
    return {
        "task_id": task_id,
        "file_path": os.path.abspath(file_path),
        "models": models.as_dict(),
        "total": total,
        "counts": counts,
        "records": records,
        "auto_retry": bool(auto_retry),
        "retry_rounds": retry_rounds,
        "no_progress_rounds": no_progress_rounds,
        "remaining": len(pending),
        "store_path": ai_review_store_path(file_path),
    }


def _persist_retry_metadata(
    *,
    file_path: str,
    task_id: str,
    rounds: int,
    no_progress_rounds: int,
    remaining: int,
    enabled: bool,
) -> None:
    store = load_ai_review_store(file_path)
    session = next(
        (value for value in store.get("sessions", []) if isinstance(value, dict) and value.get("task_id") == task_id),
        None,
    )
    if session is None:
        return
    session["retry"] = {
        "enabled": bool(enabled),
        "rounds": max(0, int(rounds)),
        "no_progress_rounds": max(0, int(no_progress_rounds)),
        "remaining": max(0, int(remaining)),
    }
    session["updated_at"] = _now()
    save_ai_review_store(file_path, store)


def apply_ai_review_records(
    *,
    task_id: str,
    file_path: str,
    output_path: str,
    records: list[dict[str, Any]],
    models: AIReviewModels,
) -> int:
    source_items = load_json_items(file_path)
    output_items = load_json_items(output_path)
    if len(source_items) != len(output_items):
        raise RuntimeError("Cannot apply AI review because source/output entry counts differ")
    if [key for key, _ in source_items] != [key for key, _ in output_items]:
        raise RuntimeError("Cannot apply AI review because source/output keys or key order changed")

    progress_records: list[dict[str, Any]] = []
    applied = 0
    for record in records:
        if record.get("result") not in {"fixed", "confirmed", "reclassified"}:
            continue
        row = int(record["row"])
        current_output = str(output_items[row][1])
        after = str(record.get("after", ""))
        if current_output == after:
            # Idempotent resume after a process stopped between output replacement
            # and checkpoint/session persistence.
            pass
        elif current_output != str(record.get("before", "")):
            record["result"] = "conflict"
            record["message"] = "Translation changed after AI review started; automatic apply was skipped."
            continue
        key, _ = output_items[row]
        output_items[row] = (key, after)
        deterministic_record = bool(record.get("deterministic_reclassification"))
        recorded_model = "deterministic:classification" if deterministic_record else str(record.get("review_model", models.review))
        recorded_configuration = (
            {
                "review": {"provider": "deterministic", "model": "classification"},
                "verifier": {"provider": "deterministic", "model": "classification"},
                "ai_review_version": AI_REVIEW_VERSION,
            }
            if deterministic_record
            else {
                "review": model_configuration(str(record.get("review_model", models.review))),
                "verifier": model_configuration(str(record.get("verifier_model", models.verifier))),
                "ai_review_version": AI_REVIEW_VERSION,
            }
        )
        progress_records.append({
            "row": row,
            "col": 0,
            "original": str(record.get("source", "")),
            "translated": after,
            "status": str(record.get("final_status", "translated")),
            "issues": list(record.get("remaining_issues", []) or []),
            "json_key": str(key),
            "entry_classification": str(record.get("entry_classification", "")),
            "batch_id": f"ai_review_{task_id}",
            "model_identifier": recorded_model,
            "model_configuration": recorded_configuration,
            "retry_count": 0 if deterministic_record else int(record.get("attempts", 1) or 1),
            "ai_review_task_id": task_id,
            "ai_review_decision": str(record.get("decision", "")),
            "ai_review_resolved_issues": list(record.get("resolved_issue_types", []) or []),
        })
        record["applied"] = True
        applied += 1

    if applied:
        _append_recomposed_parent_records(
            file_path=file_path,
            source_items=source_items,
            output_items=output_items,
            progress_records=progress_records,
        )
        _atomic_write_json_items(output_path, output_items)
        checkpoint.save_progress_many(file_path, progress_records)
        write_review_report(file_path, output_path)
    _persist_session(
        task_id=task_id,
        file_path=file_path,
        models=models,
        records=records,
        status="completed",
        applied=applied,
    )
    return applied


def rollback_ai_review_session(file_path: str, task_id: str, output_path: str) -> dict[str, Any]:
    store = load_ai_review_store(file_path)
    session = next(
        (item for item in reversed(store.get("sessions", [])) if isinstance(item, dict) and item.get("task_id") == task_id),
        None,
    )
    if not session:
        raise KeyError(f"AI review session not found: {task_id}")
    if session.get("rolled_back_at"):
        return {"ok": True, "restored": 0, "skipped": 0, "already_rolled_back": True}

    output_items = load_json_items(output_path)
    source_items = load_json_items(file_path)
    restored_records: list[dict[str, Any]] = []
    restored = 0
    skipped = 0
    for record in session.get("records", []) or []:
        if not isinstance(record, dict) or not record.get("applied"):
            continue
        row = int(record.get("row", -1))
        if row < 0 or row >= len(output_items):
            skipped += 1
            continue
        if str(output_items[row][1]) != str(record.get("after", "")):
            skipped += 1
            continue
        key, _ = output_items[row]
        before = str(record.get("before", ""))
        output_items[row] = (key, before)
        restored_records.append({
            "row": row,
            "col": 0,
            "original": str(record.get("source", "")),
            "translated": before,
            "status": str(record.get("before_status", "translated_needs_review")),
            "issues": list(record.get("before_issues", []) or []),
            "json_key": str(source_items[row][0]),
            "entry_classification": str(record.get("before_entry_classification", record.get("entry_classification", ""))),
            "batch_id": f"ai_review_rollback_{task_id}",
            "model_identifier": str(record.get("before_model", "")),
            "ai_review_rollback_task_id": task_id,
        })
        restored += 1
    if restored:
        _append_recomposed_parent_records(
            file_path=file_path,
            source_items=source_items,
            output_items=output_items,
            progress_records=restored_records,
        )
        _atomic_write_json_items(output_path, output_items)
        checkpoint.save_progress_many(file_path, restored_records)
        write_review_report(file_path, output_path)
    session["rolled_back_at"] = _now()
    session["rollback_restored"] = restored
    session["rollback_skipped"] = skipped
    save_ai_review_store(file_path, store)
    return {"ok": True, "restored": restored, "skipped": skipped, "already_rolled_back": False}


def _append_recomposed_parent_records(
    *,
    file_path: str,
    source_items: list[tuple[Any, Any]],
    output_items: list[tuple[Any, Any]],
    progress_records: list[dict[str, Any]],
) -> None:
    """Rebuild derived multiline parents after reviewed leaf values change."""
    plan = build_mtool_composition_plan(source_items)
    if not plan.entries:
        return

    checkpoint_entries = checkpoint.load_progress(file_path)
    for record in progress_records:
        checkpoint_entries[(int(record["row"]), int(record.get("col", 0)))] = {
            "status": str(record.get("status", "translated")),
        }

    composition_records: list[dict[str, Any]] = []

    def buffer_record(_file_path: str, records: list[dict[str, Any]], **record: Any) -> None:
        records.append(record)

    apply_mtool_compositions(
        plan,
        translated_items=output_items,
        checkpoint_entries=checkpoint_entries,
        file_path=file_path,
        progress_records=composition_records,
        processed_targets=0,
        total_targets=0,
        progress_callback=None,
        save_record=buffer_record,
        mark_dirty=lambda: None,
        emit_progress=lambda *args, **kwargs: None,
        progress_status=lambda status: status,
    )
    progress_records.extend(composition_records)


def _prepare_item(item: dict[str, Any], glossary: Glossary) -> dict[str, Any]:
    source = str(item.get("source", ""))
    candidate = prepare_model_candidate(
        batch_i=int(item.get("row", 0)),
        idx=int(item.get("row", 0)),
        source=source,
        glossary=glossary,
        short_label=looks_like_short_label(source),
    )
    candidate.update({
        "row": int(item.get("row", 0)),
        "current": str(item.get("current", "")),
        "current_for_prompt": str(item.get("current", "")),
        "before_status": str(item.get("status", "translated_needs_review")),
        "before_issues": list(item.get("issues", []) or []),
        "before_model": str(item.get("model_identifier", "")),
        "issue_types": [str(value) for value in item.get("issue_types", []) or [] if str(value)],
        "issue_types_for_prompt": [str(value) for value in item.get("issue_types", []) or [] if str(value)],
        "neighbors": list(item.get("neighbors", []) or []),
        "related": list(item.get("related", []) or []),
        "entry_classification": str(item.get("entry_classification", candidate["entry_classification"])),
        "sensitive": bool(item.get("sensitive")) or has_explicit_adult_content(source) or any(
            has_explicit_adult_content(str(neighbor.get("original", "")))
            for neighbor in item.get("neighbors", []) or []
            if isinstance(neighbor, dict)
        ),
        "glossary": glossary,
    })
    return candidate


def _run_primary_batches(
    items: list[dict[str, Any]],
    *,
    models: AIReviewModels,
    translator: TranslateFunc,
    cancelled: CancelFunc,
    progress: ProgressFunc,
    progress_offset: int,
    progress_total: int,
    retry: bool = False,
) -> dict[int, dict[str, Any]]:
    batches = _make_batches(items, max_items=5 if retry else 20, max_chars=1800 if retry else 3000)
    results: dict[int, dict[str, Any]] = {}
    done = 0
    workers = min(4, max(1, len(batches)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai-review") as executor:
        futures = {
            executor.submit(
                _primary_batch_with_fallback,
                batch,
                models=models,
                translator=translator,
                retry=retry,
            ): batch
            for batch in batches
        }
        for future in as_completed(futures):
            _raise_if_cancelled(cancelled)
            batch = futures[future]
            try:
                batch_results = future.result()
            except Exception as exc:
                batch_results = {
                    item["row"]: {
                        "decision": "unable",
                        "translation": "",
                        "issues": [{"type": "ai_review_error", "message": str(exc)}],
                        "attempts": 2 if retry else 1,
                        "review_model": models.sensitive if item["sensitive"] else models.review,
                    }
                    for item in batch
                }
            results.update(batch_results)
            done += len(batch)
            progress({
                "status": "reviewing",
                "phase": "reviewing",
                "current": min(progress_total, progress_offset + done),
                "total": progress_total,
            })
    return results


def _primary_batch_with_fallback(
    items: list[dict[str, Any]],
    *,
    models: AIReviewModels,
    translator: TranslateFunc,
    retry: bool,
) -> dict[int, dict[str, Any]]:
    """Retry a primary batch on configured alternate models after request/protocol failure."""
    sensitive = bool(items[0].get("sensitive"))
    candidates = (
        [models.sensitive, models.sensitive_verifier, models.verifier, models.review]
        if sensitive
        else [models.review, models.verifier, models.sensitive, models.sensitive_verifier]
    )
    attempted: list[str] = []
    last_error: Exception | None = None
    for model in candidates:
        if not model or model in attempted:
            continue
        attempted.append(model)
        try:
            return _primary_batch(items, model=model, translator=translator, retry=retry)
        except Exception as exc:
            last_error = exc
    attempted_text = ", ".join(attempted)
    raise RuntimeError(f"AI primary models failed ({attempted_text}): {last_error}") from last_error


def _primary_batch(
    items: list[dict[str, Any]],
    *,
    model: str,
    translator: TranslateFunc,
    retry: bool,
) -> dict[int, dict[str, Any]]:
    payload_items = []
    for item in items:
        context_rows = list(item.get("related", []) or [])[:2]
        context_rows.extend(list(item.get("neighbors", []) or [])[: 4 - len(context_rows)])
        contexts = [
            {
                "position": context.get("position", ""),
                "source": str(context.get("original", "")),
                "translation": str(context.get("translated", "")),
            }
            for context in context_rows
        ]
        payload_items.append({
            "i": item["row"],
            "source": item["protected"],
            "current": item.get("current_for_prompt", item["current"]),
            "severity": "required" if item["before_status"] == "review_required" else "advisory",
            "issues": item.get("issue_types_for_prompt", item["issue_types"]),
            "terms": [{"source": hit["source"], "target": hit["target"]} for hit in item.get("term_hits", [])],
            "context": contexts,
        })
    response = translator(
        model,
        json.dumps({"items": payload_items}, ensure_ascii=False),
        _primary_prompt(retry=retry),
        {"temperature": 0, "num_predict": 4096},
    )
    parsed = _parse_items_response(response, expected={item["row"] for item in items}, kind="primary")
    results: dict[int, dict[str, Any]] = {}
    for item in items:
        raw = parsed[item["row"]]
        decision = str(raw.get("decision", "unable"))
        translated = str(raw.get("t", ""))
        restored = ""
        issues: list[dict[str, Any]] = []
        if decision == "keep":
            restored = item["current"]
        elif decision == "revise" and translated.strip():
            restored, issues = _restore_candidate(item, translated)
        else:
            decision = "unable"
        issues = _validate_translation(item["source"], restored, issues, short_label=item["short_label"])
        results[item["row"]] = {
            "decision": decision,
            "translation": restored,
            "issues": issues,
            "attempts": 2 if retry else 1,
            "review_model": model,
        }
    return results


def _run_verifier_batches(
    items: list[dict[str, Any]],
    primary: dict[int, dict[str, Any]],
    *,
    models: AIReviewModels,
    translator: TranslateFunc,
    cancelled: CancelFunc,
    progress: ProgressFunc,
    progress_total: int,
    progress_offset: int = 0,
) -> dict[int, dict[str, Any]]:
    batches = _make_batches(items, max_items=16, max_chars=3600, verifier_payload=primary)
    results: dict[int, dict[str, Any]] = {}
    done = 0
    workers = min(4, max(1, len(batches)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai-verify") as executor:
        futures = {
            executor.submit(
                _verifier_batch_with_fallback,
                batch,
                primary=primary,
                models=models,
                translator=translator,
            ): batch
            for batch in batches
        }
        for future in as_completed(futures):
            _raise_if_cancelled(cancelled)
            batch = futures[future]
            try:
                batch_results = future.result()
            except Exception as exc:
                batch_results = {
                    item["row"]: {
                        "choice": "unresolved",
                        "resolved": [],
                        "verifier_model": models.sensitive_verifier if item["sensitive"] else models.verifier,
                        "error": str(exc),
                    }
                    for item in batch
                }
            results.update(batch_results)
            done += len(batch)
            progress({
                "status": "verifying",
                "phase": "verifying",
                "current": min(progress_total, progress_offset + done),
                "total": progress_total,
            })
    return results


def _repair_multiline_by_lines(
    item: dict[str, Any],
    *,
    model: str,
    translator: TranslateFunc,
) -> dict[str, Any] | None:
    """Repair a failed multiline review without allowing line or context leakage."""
    fragments = str(item["source"]).splitlines(keepends=True)
    if len(fragments) < 2:
        return None
    rendered: list[str] = [""] * len(fragments)
    model_lines: list[dict[str, Any]] = []
    line_indexes: dict[int, int] = {}
    for line_index, fragment in enumerate(fragments):
        ending_match = re.search(r"(?:\r\n|\r|\n)$", fragment)
        ending = ending_match.group(0) if ending_match else ""
        body = fragment[:-len(ending)] if ending else fragment
        if not body or not has_source_japanese(body):
            rendered[line_index] = body + ending
            continue
        deterministic = deterministic_translation(body)
        if deterministic:
            rendered[line_index] = deterministic + ending
            continue
        synthetic_row = -((int(item["row"]) + 1) * 10000 + line_index + 1)
        line_item = _prepare_item({
            "row": synthetic_row,
            "source": body,
            "current": "",
            "status": "review_required",
            "issues": [{"type": "untranslated_japanese"}],
            "issue_types": ["untranslated_japanese"],
            "neighbors": [],
            "entry_classification": "multiline_line",
            "model_identifier": item.get("before_model", ""),
            "sensitive": bool(item.get("sensitive")),
        }, item["glossary"])
        line_item["current_for_prompt"] = ""
        line_item["issue_types_for_prompt"] = sorted({
            "untranslated_japanese",
            *[str(value) for value in item.get("issue_types_for_prompt", []) if str(value)],
        })
        model_lines.append(line_item)
        line_indexes[synthetic_row] = line_index
        rendered[line_index] = ending
    if not model_lines:
        return None
    line_results = _primary_batch(model_lines, model=model, translator=translator, retry=True)
    for line_item in model_lines:
        result = line_results.get(line_item["row"], {})
        translated = str(result.get("translation", ""))
        if not translated or _has_non_waivable_issue(result.get("issues", [])):
            return None
        line_index = line_indexes[line_item["row"]]
        ending_match = re.search(r"(?:\r\n|\r|\n)$", fragments[line_index])
        ending = ending_match.group(0) if ending_match else ""
        body = fragments[line_index][:-len(ending)] if ending else fragments[line_index]
        leading = re.match(r"^[ \t]*", body).group(0)
        rendered[line_index] = leading + translated.lstrip(" \t") + ending
    translation = "".join(rendered)
    issues = _validate_translation(
        item["source"],
        translation,
        [],
        short_label=False,
    )
    return {
        "decision": "revise",
        "translation": translation,
        "issues": issues,
        "attempts": 3,
        "review_model": model,
    }


def _verifier_batch_with_fallback(
    items: list[dict[str, Any]],
    *,
    primary: dict[int, dict[str, Any]],
    models: AIReviewModels,
    translator: TranslateFunc,
) -> dict[int, dict[str, Any]]:
    """Run verification with bounded model fallbacks on transport failure."""
    sensitive = bool(items[0]["sensitive"])
    preferred = models.sensitive_verifier if sensitive else models.verifier
    primary_model = models.sensitive if sensitive else models.review
    candidates = [
        preferred,
        models.sensitive if sensitive else models.sensitive_verifier,
        models.verifier if sensitive else models.sensitive,
        primary_model,
    ]
    attempted: list[str] = []
    last_error: Exception | None = None
    for model in candidates:
        if not model or model in attempted:
            continue
        attempted.append(model)
        try:
            results = _verifier_batch(items, primary=primary, model=model, translator=translator)
            adjudication_items = [
                item
                for item in items
                if _needs_candidate_adjudication(
                    item,
                    primary.get(item["row"], {}),
                    results.get(item["row"], {}),
                )
            ]
            if adjudication_items:
                results.update(_verifier_batch(
                    adjudication_items,
                    primary=primary,
                    model=model,
                    translator=translator,
                    adjudicate=True,
                ))
            return results
        except Exception as exc:
            last_error = exc
    attempted_text = ", ".join(attempted)
    raise RuntimeError(f"AI verifier models failed ({attempted_text}): {last_error}") from last_error


def _verifier_batch(
    items: list[dict[str, Any]],
    *,
    primary: dict[int, dict[str, Any]],
    model: str,
    translator: TranslateFunc,
    adjudicate: bool = False,
) -> dict[int, dict[str, Any]]:
    def issue_types(row: int) -> list[str]:
        return [
            str(issue.get("type", ""))
            for issue in primary.get(row, {}).get("issues", [])
            if isinstance(issue, dict) and str(issue.get("type", ""))
        ]

    payload = {
        "items": [
            {
                "i": item["row"],
                "source": item["source"],
                "current": item["current"],
                "candidate": str(primary.get(item["row"], {}).get("translation", "")),
                "issues": item["issue_types"],
                "candidate_issues": issue_types(item["row"]),
                "candidate_blocking_issues": [
                    value for value in issue_types(item["row"]) if value in NON_WAIVABLE_ISSUE_TYPES
                ],
                "candidate_advisory_issues": [
                    value for value in issue_types(item["row"]) if value not in NON_WAIVABLE_ISSUE_TYPES
                ],
            }
            for item in items
        ]
    }
    response = translator(
        model,
        json.dumps(payload, ensure_ascii=False),
        _verifier_prompt(adjudicate=adjudicate),
        {"temperature": 0, "num_predict": 3072},
    )
    parsed = _parse_items_response(response, expected={item["row"] for item in items}, kind="verifier")
    return {
        item["row"]: {
            "choice": str(parsed[item["row"]].get("choice", "unresolved")),
            "resolved": [str(value) for value in parsed[item["row"]].get("resolved", []) if str(value)],
            "verifier_model": model,
        }
        for item in items
    }


def _needs_candidate_adjudication(
    item: dict[str, Any],
    primary: dict[str, Any],
    verifier: dict[str, Any],
) -> bool:
    candidate = str(primary.get("translation", ""))
    candidate_issues = list(primary.get("issues", []) or [])
    if not candidate or any(
        str(issue.get("type", "")) in NON_WAIVABLE_ISSUE_TYPES
        for issue in candidate_issues
        if isinstance(issue, dict)
    ):
        return False
    current_issues = _validate_translation(
        item["source"],
        item["current"],
        [],
        short_label=item["short_label"],
    )
    current_has_hard_issue = any(
        str(issue.get("type", "")) in NON_WAIVABLE_ISSUE_TYPES
        for issue in current_issues
        if isinstance(issue, dict)
    )
    return current_has_hard_issue and str(verifier.get("choice", "unresolved")) != "candidate"


def _resolve_record(item: dict[str, Any], primary: dict[str, Any], verifier: dict[str, Any]) -> dict[str, Any]:
    choice = str(verifier.get("choice", "unresolved"))
    candidate = str(primary.get("translation", ""))
    selected = item["current"] if choice == "current" else candidate if choice == "candidate" else ""
    selected_issues = (
        _validate_translation(item["source"], item["current"], [], short_label=item["short_label"])
        if choice == "current"
        else list(primary.get("issues", []) or [])
        if choice == "candidate"
        else []
    )
    allowed_resolutions = set(item["issue_types"]) | {
        str(issue.get("type", "")) for issue in selected_issues if isinstance(issue, dict)
    }
    resolved = {
        str(value)
        for value in verifier.get("resolved", []) or []
        if str(value) in allowed_resolutions and str(value) not in NON_WAIVABLE_ISSUE_TYPES
    }
    hard_issues = [issue for issue in selected_issues if str(issue.get("type", "")) in NON_WAIVABLE_ISSUE_TYPES]
    remaining = [
        issue
        for issue in selected_issues
        if str(issue.get("type", "")) in NON_WAIVABLE_ISSUE_TYPES
        or str(issue.get("type", "")) not in resolved
    ]
    accepted = bool(selected) and not hard_issues and choice in {"current", "candidate"}
    final_status = status_for_output(item["source"], selected, remaining) if accepted else item["before_status"]
    if accepted and final_status == "preserved" and selected != item["source"]:
        final_status = "translated"
    result = (
        "confirmed" if accepted and choice == "current"
        else "fixed" if accepted and choice == "candidate"
        else "unresolved"
    )
    return {
        "row": item["row"],
        "source": item["source"],
        "before": item["current"],
        "after": selected if accepted else item["current"],
        "before_status": item["before_status"],
        "before_issues": item["before_issues"],
        "before_model": item["before_model"],
        "entry_classification": item["entry_classification"],
        "decision": choice,
        "primary_decision": primary.get("decision", "unable"),
        "result": result,
        "final_status": final_status,
        "remaining_issues": remaining if accepted else item["before_issues"],
        "resolved_issue_types": sorted(resolved),
        "review_model": primary.get("review_model", ""),
        "verifier_model": verifier.get("verifier_model", ""),
        "attempts": int(primary.get("attempts", 1) or 1),
        "applied": False,
        "message": str(verifier.get("error", "")),
    }


def _restore_candidate(item: dict[str, Any], translated: str) -> tuple[str, list[dict[str, Any]]]:
    restored, symbol_issues, missing_terms = restore_protected_translation(
        glossary=item["glossary"],
        original_text=item["source"],
        prepared_text=item["prepared"],
        protected_text=item["protected"],
        translated=translated,
        symbol_tokens=item["symbol_tokens"],
        term_tokens=item.get("term_tokens", []),
        runtime_tokens=item["runtime_tokens"],
        term_hits=item.get("term_hits", []),
    )
    issues = list(symbol_issues)
    if missing_terms:
        issues.append({
            "type": "term_preservation",
            "message": "Confirmed terminology was not preserved: "
            + ", ".join(f"{term['source']}=>{term['target']}" for term in missing_terms),
        })
    restored = apply_source_conditioned_fixes(item["source"], restored)
    return restored, issues


def _validate_translation(
    source: str,
    translated: str,
    issues: list[dict[str, Any]],
    *,
    short_label: bool,
) -> list[dict[str, Any]]:
    result = list(issues)
    assessment = assess_model_output(translated, original=source)
    if assessment.issue_type:
        result.append(assessment.as_issue())
    result.extend(new_issues(result, translation_issues(source, translated, short_label=short_label)))
    result.extend(new_issues(result, translation_pollution_issues(source, translated)))
    max_chars, max_lines = output_constraints()
    source_lines = source.split("\n")
    effective_max_chars = max(max_chars, *(len(line) for line in source_lines))
    effective_max_lines = max(max_lines, len(source_lines))
    result.extend(new_issues(
        result,
        get_violations(
            translated,
            max_chars=effective_max_chars,
            max_lines=effective_max_lines,
        ),
    ))
    return _dedupe_issues(result)


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        key = (str(issue.get("type", "")), str(issue.get("message", "")))
        if key not in seen:
            seen.add(key)
            result.append(dict(issue))
    return result


def _primary_prompt(*, retry: bool) -> str:
    retry_rule = (
        "This is the final bounded repair attempt. Do not repeat the defective draft. "
        if retry else ""
    )
    return (
        "/no_think\n"
        "Review Japanese-to-Simplified-Chinese game translations. "
        + retry_rule
        + "For advisory records, keep a correct current translation and revise only when an issue is real. "
        "For required records, repair every listed issue from the Japanese source. Context is read-only. "
        "Never add neighboring meaning, names, relationships, symbols, or explanations. Preserve every __KEEP_*, "
        "__SYM_*, __TERM_*, and __PERSON_* placeholder exactly once. Replace meaningful Japanese kana and vocalizations "
        "with natural Chinese sounds; small っ/ッ may be omitted or localized, but do not copy lexical kana. "
        "Translate adult content faithfully without refusal or sanitization. "
        "A common-noun role or occupation followed by a Japanese honorific is not automatically a person name; "
        "translate the role and render its respect naturally in Chinese instead of retaining Japanese kana. "
        "When read-only context includes a translated standalone occurrence of that role, use it as terminology evidence. "
        "Return JSON only: {\"items\":[{\"i\":0,\"decision\":\"keep|revise|unable\",\"t\":\"translation\"}]}. "
        "Use t=current translation when keeping, the new translation when revising, and an empty t only when unable.\n/no_think"
    )


def _verifier_prompt(*, adjudicate: bool = False) -> str:
    adjudication_rule = (
        "This is an isolated adjudication because current has a non-waivable defect while candidate passed hard checks. "
        "Do not choose current. Choose candidate only if it faithfully covers the source; otherwise choose unresolved. "
        if adjudicate else ""
    )
    return (
        "/no_think\n"
        "Act as an independent Japanese-to-Simplified-Chinese localization verifier. Compare current and candidate "
        + adjudication_rule
        + "against the Japanese source. Select current, candidate, or unresolved. Do not prefer a changed wording merely "
        "because it is new. resolved may contain only listed advisory issue types that the selected translation clearly "
        "handles. candidate_blocking_issues are non-waivable; candidate_advisory_issues such as layout length, line count, "
        "or harmless English residue do not by themselves make a faithful candidate unusable. Choose candidate when it "
        "faithfully covers the source and candidate_blocking_issues is empty; "
        "adult or explicit wording is not a reason to choose unresolved. Choose unresolved only when neither version is "
        "usable or the meaning cannot be verified. Never resolve missing Japanese meaning, refusal, runtime/control tokens, numbers, line breaks, symbols, "
        "terminology loss, or unsupported names/context. Translate adult content faithfully when judging it. "
        "Return JSON only: {\"items\":[{\"i\":0,\"choice\":\"current|candidate|unresolved\",\"resolved\":[\"issue_type\"]}]}.\n/no_think"
    )


def _parse_items_response(response: str, *, expected: set[int], kind: str) -> dict[int, dict[str, Any]]:
    data = _load_json(response)
    if not isinstance(data, dict) or set(data) != {"items"} or not isinstance(data["items"], list):
        raise ValueError(f"Invalid {kind} response root")
    parsed: dict[int, dict[str, Any]] = {}
    for raw in data["items"]:
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid {kind} response item")
        allowed = {"i", "decision", "t"} if kind == "primary" else {"i", "choice", "resolved"}
        if set(raw) - allowed:
            raise ValueError(f"Unexpected {kind} response fields")
        idx = int(raw.get("i", -1))
        if idx not in expected or idx in parsed:
            raise ValueError(f"Unexpected or duplicate {kind} response id: {idx}")
        if kind == "primary":
            if raw.get("decision") not in {"keep", "revise", "unable"} or not isinstance(raw.get("t", ""), str):
                raise ValueError("Invalid primary decision")
        else:
            if raw.get("choice") not in {"current", "candidate", "unresolved"} or not isinstance(raw.get("resolved", []), list):
                raise ValueError("Invalid verifier choice")
        parsed[idx] = raw
    if set(parsed) != expected:
        raise ValueError(f"Missing {kind} response ids")
    return parsed


def _load_json(response: str) -> Any:
    text = str(response or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            return json.loads(fenced.group(1).strip())
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _make_batches(
    items: list[dict[str, Any]],
    *,
    max_items: int,
    max_chars: int,
    verifier_payload: dict[int, dict[str, Any]] | None = None,
) -> list[list[dict[str, Any]]]:
    groups: dict[bool, list[dict[str, Any]]] = {False: [], True: []}
    for item in items:
        groups[bool(item.get("sensitive"))].append(item)
    batches: list[list[dict[str, Any]]] = []
    for group in groups.values():
        current: list[dict[str, Any]] = []
        chars = 0
        for item in group:
            item_chars = len(str(item.get("source", ""))) + len(str(item.get("current", "")))
            if verifier_payload:
                item_chars += len(str(verifier_payload.get(item["row"], {}).get("translation", "")))
            if current and (len(current) >= max_items or chars + item_chars > max_chars):
                batches.append(current)
                current = []
                chars = 0
            current.append(item)
            chars += item_chars
        if current:
            batches.append(current)
    return batches


def _batch_count(items: list[dict[str, Any]], *, max_items: int, max_chars: int) -> int:
    normalized = [dict(item, sensitive=bool(item.get("sensitive"))) for item in items]
    return len(_make_batches(normalized, max_items=max_items, max_chars=max_chars))


def _record_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"fixed": 0, "confirmed": 0, "reclassified": 0, "unresolved": 0, "conflict": 0, "applied": 0}
    for record in records:
        result = str(record.get("result", "unresolved"))
        counts[result if result in counts else "unresolved"] += 1
    return counts


def _persist_session(
    *,
    task_id: str,
    file_path: str,
    models: AIReviewModels,
    records: list[dict[str, Any]],
    status: str,
    applied: int,
) -> None:
    store = load_ai_review_store(file_path)
    sessions = store.setdefault("sessions", [])
    existing = next((item for item in sessions if isinstance(item, dict) and item.get("task_id") == task_id), None)
    session = existing if existing is not None else {"task_id": task_id, "started_at": _now()}
    session.update({
        "version": AI_REVIEW_VERSION,
        "status": status,
        "models": models.as_dict(),
        "records": records,
        "applied": applied,
        "updated_at": _now(),
        "finished_at": _now() if status in {"completed", "candidate_ready"} else "",
    })
    if existing is None:
        sessions.append(session)
    save_ai_review_store(file_path, store)


def _load_session_work(file_path: str, task_id: str) -> dict[str, Any]:
    session = get_ai_review_session(file_path, task_id) or {}
    return dict(session.get("work", {}) or {})


def _save_session_work(file_path: str, task_id: str, **values: Any) -> None:
    store = load_ai_review_store(file_path)
    session = next((value for value in store.get("sessions", []) if isinstance(value, dict) and value.get("task_id") == task_id), None)
    if session is None:
        return
    work = session.setdefault("work", {})
    for key, value in values.items():
        if key in {"primary", "verifier"} and isinstance(value, dict):
            work[key] = {str(row): result for row, result in value.items()}
        else:
            work[key] = value
    session["updated_at"] = _now()
    save_ai_review_store(file_path, store)


def _decode_result_map(value: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for row, item in value.items():
        try:
            result[int(row)] = dict(item)
        except (TypeError, ValueError):
            continue
    return result


def _atomic_write_json_items(path: str, items: list[tuple[Any, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(serialize_json_items(items))
            temp_path = Path(stream.name)
        os.replace(temp_path, target)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _translate(model: str, text: str, system_prompt: str, options: dict[str, Any] | None) -> str:
    return model_translate(model, text, system_prompt=system_prompt, options=options, think=False)


def _has_retry_issue(issues: list[dict[str, Any]]) -> bool:
    return any(str(issue.get("type", "")) in PRIMARY_RETRY_ISSUE_TYPES for issue in issues if isinstance(issue, dict))


def _has_non_waivable_issue(issues: list[dict[str, Any]]) -> bool:
    return any(
        str(issue.get("type", "")) in NON_WAIVABLE_ISSUE_TYPES
        for issue in issues
        if isinstance(issue, dict)
    )


def _raise_if_cancelled(cancelled: CancelFunc) -> None:
    if cancelled():
        raise AIReviewCancelled("AI review was cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_result(task_id: str, file_path: str, models: AIReviewModels) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "file_path": os.path.abspath(file_path),
        "models": models.as_dict(),
        "total": 0,
        "counts": _record_counts([]),
        "records": [],
        "store_path": ai_review_store_path(file_path),
    }


__all__ = [
    "AI_REVIEW_ACTIVE_STATUSES",
    "AI_REVIEW_TERMINAL_STATUSES",
    "AI_REVIEW_VERSION",
    "AIReviewCancelled",
    "AIReviewModels",
    "ai_review_store_path",
    "begin_ai_review_session",
    "build_deterministic_reclassification_records",
    "estimate_review_usage",
    "latest_ai_review_by_row",
    "get_ai_review_session",
    "load_ai_review_store",
    "rollback_ai_review_session",
    "run_ai_review",
    "update_ai_review_session_status",
]
