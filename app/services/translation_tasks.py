from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional

from translation.runtime import TranslationRuntime
from translation.translate import TranslationCancelled, TranslationRequest


class TranslationTask:
    """Manage one translation task with pause/resume/cancel support."""

    def __init__(
        self,
        task_id: str,
        file_path: str,
        model: str,
        prompt_style: str,
        translate_columns: List[int],
        *,
        execution_profile: str = "quality_first",
        profile_summary: dict[str, Any] | None = None,
        batch_config_override: dict[str, Any] | None = None,
    ):
        self.task_id = task_id
        self.file_path = file_path
        self.model = model
        self.prompt_style = prompt_style
        self.translate_columns = translate_columns
        self.execution_profile = execution_profile
        self.profile_summary = dict(profile_summary or {})
        self.batch_config_override = dict(batch_config_override or {})
        self.status = "idle"
        self.progress: Dict[str, Any] = {
            "current": 0,
            "total": 0,
            "percentage": 0.0,
            "status": "idle",
        }
        self.error: Optional[str] = None
        self.runtime: TranslationRuntime | None = None
        self.review_summary: dict[str, int] = {}
        self.has_unexported_result = False
        self.started_at = 0.0
        self.finished_at = 0.0
        self.updated_at = 0.0
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cancel_requested = False
        self._pause_requested = False
        self.completion_verified = False

    def _update_progress(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            self.updated_at = time.time()
            self.progress = {
                "current": payload.get("processed", 0),
                "total": payload.get("total", 0),
                # 100% is reserved for a structurally verified terminal result.
                "percentage": min(float(payload.get("percent", 0.0) or 0.0), 99.9),
                "status": payload.get("status", ""),
            }

    def get_progress(self) -> Dict[str, Any]:
        from translation.output import default_output_path

        with self._lock:
            progress = dict(self.progress)
            progress["cell_status"] = progress.get("status", "")
            task_status = self.status
            error = self.error
            review_summary = dict(self.review_summary)
            started_at = self.started_at
            finished_at = self.finished_at
            has_unexported_result = self.has_unexported_result
            completion_verified = self.completion_verified
            runtime = self.runtime

        # Keep observers outside the task lock. This in-memory usage snapshot
        # cannot write the checkpoint or block progress callbacks.
        token_usage = runtime.token_usage() if runtime else {}
        now = finished_at or time.time()
        elapsed = max(0.0, now - started_at) if started_at else 0.0
        completed = int(progress.get("current", 0) or 0)
        total = int(progress.get("total", 0) or 0)
        rate = completed / elapsed if elapsed > 0 else 0.0
        remaining = max(0, total - completed)
        progress["status"] = task_status
        progress["token_usage"] = token_usage
        progress["elapsed_seconds"] = round(elapsed, 1)
        progress["entries_per_minute"] = round(rate * 60, 1)
        progress["eta_seconds"] = round(remaining / rate, 1) if rate > 0 and task_status == "running" else None
        progress["phase"] = self._phase(progress)
        progress["profile"] = dict(self.profile_summary)
        progress["completion_verified"] = completion_verified
        progress["snapshot_ready"] = (
            task_status in ("completed", "cancelled", "error")
            and has_unexported_result
            and Path(default_output_path(self.file_path)).is_file()
        )
        progress["control_note"] = (
            "已暂停派发新请求；已发出的请求可能仍在返回。"
            if task_status == "paused"
            else "正在等待已发出的请求结束并写入检查点。"
            if task_status == "stopping"
            else "正在校验检查点、输出文件和复核报告。"
            if task_status == "finalizing"
            else ""
        )
        return {
            "task_id": self.task_id,
            "error": error,
            "review_summary": review_summary,
            **progress,
        }

    def _phase(self, progress: Dict[str, Any]) -> str:
        if self.status == "completed":
            return "completed"
        if self.status in ("cancelled", "error"):
            return "finalized"
        if self.status == "stopping":
            return "stopping"
        if self.status == "finalizing":
            return "validation"
        cell_status = str(progress.get("cell_status") or "")
        if cell_status in ("idle", "") and not progress.get("total"):
            return "analysis"
        if cell_status in ("queued", "translating"):
            return "translation"
        if int(progress.get("current", 0) or 0) >= int(progress.get("total", 0) or 0) > 0:
            return "validation"
        return "translation"

    def start(self) -> None:
        if self.status == "running":
            return
        self.status = "running"
        self.started_at = time.time()
        self.updated_at = self.started_at
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self.status == "running":
            self._pause_requested = True
            if self.runtime:
                self.runtime.pause()
            self.status = "paused"

    def resume(self) -> None:
        if self.status == "paused" and self.runtime:
            self.runtime.resume()
            self._pause_requested = False
            self.status = "running"

    def cancel(self) -> None:
        if self.status in ("running", "paused"):
            self._cancel_requested = True
            if self.runtime:
                self.runtime.cancel()
            self.status = "stopping"

    def flush(self) -> None:
        if self.runtime:
            self.runtime.flush_writer()

    def update_output_cell(self, row: int, col: int, text: str) -> bool:
        if self.runtime:
            return self.runtime.update_output_cell(row, col, text)
        return False

    def replace_glossary(self, glossary: Any) -> None:
        if self.runtime:
            self.runtime.replace_glossary(glossary)

    def _run(self) -> None:
        self.runtime = TranslationRuntime(TranslationRequest(
            file_path=self.file_path,
            model=self.model,
            prompt_style=self.prompt_style,
            task_id=self.task_id,
            batch_config_override=self.batch_config_override,
        ))
        if self._pause_requested:
            self.runtime.pause()
        if self._cancel_requested:
            self.runtime.cancel()

        try:
            result = self.runtime.translate_file(
                progress_callback=self._update_progress,
                translate_columns=self.translate_columns,
            )
            with self._lock:
                self.status = "finalizing"
                self.review_summary = dict(result.review_summary)
                self.has_unexported_result = True
            verified_total = self._validate_completion(result)
            with self._lock:
                self.status = "completed"
                self.finished_at = time.time()
                self.completion_verified = True
                self.progress["current"] = verified_total
                self.progress["total"] = verified_total
                self.progress["percentage"] = 100.0
                self.progress["status"] = "completed"
        except TranslationCancelled:
            self.status = "cancelled"
            self.finished_at = time.time()
            self.has_unexported_result = True
        except Exception as exc:
            self.status = "error"
            self.finished_at = time.time()
            self.error = str(exc)
            self.has_unexported_result = True

    def _validate_completion(self, result: Any) -> int:
        """Return the verified entry count or raise instead of faking 100%."""
        from translation import checkpoint
        from translation.input import load_json_items
        from translation.output import default_output_path

        output_path = str(result.output_path or default_output_path(self.file_path))
        if not Path(output_path).is_file():
            raise RuntimeError("翻译流程结束，但完整输出文件不存在")
        if not result.review_report_path or not Path(result.review_report_path).is_file():
            raise RuntimeError("翻译流程结束，但质量复核报告不存在")

        original_items = load_json_items(self.file_path)
        output_items = load_json_items(output_path)
        if len(output_items) != len(original_items):
            raise RuntimeError(f"输出条目数不完整：{len(output_items)} / {len(original_items)}")
        if [key for key, _ in output_items] != [key for key, _ in original_items]:
            raise RuntimeError("输出文件的原文键或键顺序发生变化")

        cp_data = checkpoint.load_checkpoint(self.file_path)
        cp_entries = cp_data.get("entries", {})
        if not isinstance(cp_entries, dict):
            raise RuntimeError("检查点条目结构无效")
        incomplete: list[int] = []
        for row in range(len(original_items)):
            entry = cp_entries.get(f"{row}_0")
            if not isinstance(entry, dict) or not checkpoint.is_completed_status(str(entry.get("status", ""))):
                incomplete.append(row)
                if len(incomplete) >= 5:
                    break
        if incomplete:
            display_rows = ", ".join(str(row + 1) for row in incomplete)
            raise RuntimeError(f"仍有未完成的检查点条目（示例行：{display_rows}）")

        pending = int((result.review_summary or {}).get("pending", 0) or 0)
        summary_total = int((result.review_summary or {}).get("total", 0) or 0)
        if pending or summary_total != len(original_items):
            raise RuntimeError(
                f"完成摘要未通过校验：总计 {summary_total} / {len(original_items)}，未完成 {pending}"
            )
        return len(original_items)


class BatchTranslationManager:
    """Manage a queue of files to translate sequentially."""

    def __init__(
        self,
        batch_id: str,
        file_paths: List[str],
        model: str,
        prompt_style: str,
        translate_columns: List[int],
        task_registry: MutableMapping[str, TranslationTask] | None = None,
    ):
        self.batch_id = batch_id
        self.file_paths = file_paths
        self.model = model
        self.prompt_style = prompt_style
        self.translate_columns = translate_columns
        self.task_registry = task_registry
        self.status = "idle"
        self.current_index = 0
        self.current_task: Optional[TranslationTask] = None
        self.file_results: List[Dict[str, Any]] = [
            {
                "file_path": fp,
                "file_name": Path(fp).name,
                "status": "pending",
                "progress": {"current": 0, "total": 0, "percentage": 0.0},
                "error": None,
            }
            for fp in file_paths
        ]
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_flag = False

    def start(self) -> None:
        if self.status == "running":
            return
        self.status = "running"
        self._thread = threading.Thread(target=self._run_queue, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self.status == "running" and self.current_task:
                self.current_task.pause()
                self.status = "paused"
                self._pause_event.clear()

    def resume(self) -> None:
        with self._lock:
            if self.status == "paused" and self.current_task:
                self.current_task.resume()
                self.status = "running"
                self._pause_event.set()

    def cancel(self) -> None:
        with self._lock:
            self._cancel_flag = True
            if self.current_task:
                self.current_task.cancel()
            self.status = "cancelled"
            self._pause_event.set()

    def get_progress(self) -> Dict[str, Any]:
        with self._lock:
            total_current = 0
            total_total = 0
            for file_result in self.file_results:
                total_current += file_result["progress"].get("current", 0)
                total_total += file_result["progress"].get("total", 0)

            overall_pct = 0.0
            if total_total > 0:
                overall_pct = round((total_current / total_total) * 100, 1)
            elif self.status == "completed":
                overall_pct = 100.0

            if self.current_task and self.current_index < len(self.file_results):
                task_progress = self.current_task.get_progress()
                self.file_results[self.current_index]["progress"] = {
                    "current": task_progress.get("current", 0),
                    "total": task_progress.get("total", 0),
                    "percentage": task_progress.get("percentage", 0.0),
                }

            return {
                "batch_id": self.batch_id,
                "status": self.status,
                "current_index": self.current_index,
                "total_files": len(self.file_paths),
                "overall_progress": {
                    "current": total_current,
                    "total": total_total,
                    "percentage": overall_pct,
                },
                "files": [dict(file_result) for file_result in self.file_results],
            }

    def _run_queue(self) -> None:
        for idx, file_path in enumerate(self.file_paths):
            if self._cancel_flag:
                with self._lock:
                    for remaining_idx in range(idx, len(self.file_results)):
                        self.file_results[remaining_idx]["status"] = "cancelled"
                break

            self.current_index = idx
            with self._lock:
                self.file_results[idx]["status"] = "running"

            task_id = f"{self.batch_id}_{idx}"
            task = TranslationTask(
                task_id=task_id,
                file_path=file_path,
                model=self.model,
                prompt_style=self.prompt_style,
                translate_columns=self.translate_columns,
            )
            self.current_task = task
            if self.task_registry is not None:
                self.task_registry[task_id] = task

            self._pause_event.wait()
            if self._cancel_flag:
                with self._lock:
                    self.file_results[idx]["status"] = "cancelled"
                break

            task.start()
            if task._thread:
                task._thread.join()

            with self._lock:
                task_progress = task.get_progress()
                self.file_results[idx]["progress"] = {
                    "current": task_progress.get("current", 0),
                    "total": task_progress.get("total", 0),
                    "percentage": task_progress.get("percentage", 0.0),
                }
                if task.status == "completed":
                    self.file_results[idx]["status"] = "completed"
                elif task.status == "cancelled":
                    self.file_results[idx]["status"] = "cancelled"
                elif task.status == "error":
                    self.file_results[idx]["status"] = "error"
                    self.file_results[idx]["error"] = task.error

            self.current_task = None

        if not self._cancel_flag:
            self.status = "completed"
