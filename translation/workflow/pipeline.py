# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any, Callable

import translation.usage as token_usage
from translation.batching import (
    BatchJob,
    api_job_is_short_text,
    apply_batch_translation_results,
    collect_json_batch_candidates,
    collect_json_batch_window,
    default_batch_options,
    finish_batch_translation,
    prepare_model_candidate,
    resolve_candidate_batch_protocol,
    resolve_json_batch_protocol_for_items,
    resolve_parallel_candidate_protocol,
    resolve_scanned_batch_protocol,
    run_concurrent_batches,
    run_dynamic_batches,
    select_api_job_model,
    select_api_job_options,
    translate_candidate_batch_raw,
    translate_candidates_with_split,
    uses_api_parallel_batches,
)
from translation.classification import deterministic_translation, has_source_japanese, looks_like_short_label
from translation.config import (
    batch_translation_config,
    default_model,
    default_system_prompt,
    fallback_chunk_strategy,
    fallback_models,
    model_provider,
    output_constraints,
    system_prompts,
    think_setting,
)
from translation.input import is_mtool_items, source_text
from translation.models import (
    call_translate_with_options,
    chunk_translate,
    fallback_translate,
    list_models,
    model_configuration,
    retry_short_label_translation,
    retry_with_fallback,
    translate,
    translate_once,
)
from translation.output import write_json_items
from translation.output import TranslationWriter
from translation.pollution import translation_pollution_issues
from translation.prompts import compose_label_prompt, compose_translation_prompt
from translation.protection import (
    protect_runtime_tokens,
    protect_symbols,
    restore_protected_translation,
    restore_runtime_tokens,
    restore_symbols,
)
from translation.quality import (
    apply_fixed_translations,
    apply_output_constraints,
    apply_source_conditioned_fixes,
    auto_wrap,
    english_residue,
    has_japanese,
    is_refusal,
    new_issues,
    progress_status,
    quality_prompt_rules,
    retry_english_residue_translation,
    retry_missing_terms_translation,
    status_for_output,
    translation_issues,
    validate,
)
from translation.repair import strip_source_echo
from translation.terminology import Glossary, apply_term_aliases
from translation.workflow import batch_adapter, cell_services, runtime_adapter
from translation.workflow.cell import CellTranslationServices, translate_cell_with_meta
from translation.workflow.file_entry import translate_file_for_pipeline
from translation.workflow.json_batch import translate_json_batched_workflow
from translation.workflow.json_flow import translate_json_workflow
from translation.workflow.json_parallel import translate_json_batched_parallel_workflow
from translation.workflow import translation_adapter


ProgressCallback = Callable[[dict[str, Any]], None]


class TranslationCancelled(Exception):
    pass


class TranslationPaused(Exception):
    pass


class TranslationPipeline:
    def __init__(
        self,
        model: str | None = None,
        system_prompt: str | None = None,
        glossary: Glossary | None = None,
        prompt_style: str = "professional",
        task_id: str = "",
        batch_config_override: dict[str, Any] | None = None,
    ):
        self.model = model or default_model()
        self.prompt_style = prompt_style or "professional"
        self.system_prompt = system_prompt or default_system_prompt("professional")
        self.glossary = glossary or Glossary()
        self.task_id = task_id
        self._batch_config_override = deepcopy(batch_config_override) if batch_config_override else None
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._last_progress: dict[str, Any] = {}
        self._writer: TranslationWriter | None = None
        self._short_label_options = {"temperature": 0, "num_predict": 32}
        self._token_usage: dict[str, Any] = token_usage.snapshot()
        self._resume_context: dict[str, Any] = {
            "translation_direction": "ja-Hans",
            "prompt_version": "default",
            "glossary_version": "0",
            "model_configuration": {},
        }
        self._mtool_composition_plan = None
        self._mtool_neighbor_context_plan = None
        self._api_admission_snapshot: dict[str, Any] = {}

    def _batch_translation_config(self) -> dict[str, Any]:
        if self._batch_config_override is not None:
            return deepcopy(self._batch_config_override)
        return batch_translation_config()

    def translate_file(
        self,
        file_path: str,
        output_path: str | None = None,
        progress_callback: ProgressCallback | None = None,
        translate_columns: list[int] | None = None,
    ):
        return translate_file_for_pipeline(self, file_path, output_path, progress_callback, translate_columns)

    def _translate_json(self, file_path: str, output_path: str | None, progress_callback: ProgressCallback | None):
        return translate_json_workflow(self, file_path, output_path, progress_callback)

    def _translate_json_batched(
        self,
        file_path: str,
        translated_items: list[tuple[Any, Any]],
        mtool: bool,
        completed: dict[tuple[int, int], dict[str, Any]],
        target_path: str,
        total_targets: int,
        progress_callback: ProgressCallback | None,
    ):
        return translate_json_batched_workflow(
            self,
            file_path,
            translated_items,
            mtool,
            completed,
            target_path,
            total_targets,
            progress_callback,
        )

    def _translate_json_batched_parallel(
        self,
        file_path: str,
        translated_items: list[tuple[Any, Any]],
        mtool: bool,
        completed: dict[tuple[int, int], dict[str, Any]],
        target_path: str,
        total_targets: int,
        progress_callback: ProgressCallback | None,
        batch_size: int,
        max_batch_chars: int,
        batch_options: dict[str, Any],
        configured_protocol: str,
        batch_protocol: str,
        batch_cfg: dict[str, Any],
    ):
        return translate_json_batched_parallel_workflow(
            self,
            file_path,
            translated_items,
            mtool,
            completed,
            target_path,
            total_targets,
            progress_callback,
            batch_size,
            max_batch_chars,
            batch_options,
            configured_protocol,
            batch_protocol,
            batch_cfg,
        )

    def _resolve_batch_protocol(
        self,
        configured_protocol: str,
        translated_items: list[tuple[Any, Any]],
        mtool: bool,
        completed: dict[tuple[int, int], dict[str, Any]],
    ) -> str:
        return batch_adapter.resolve_batch_protocol(self, configured_protocol, translated_items, mtool, completed)

    @staticmethod
    def _resolve_candidate_batch_protocol(
        configured_protocol: str,
        default_protocol: str,
        candidates: list[dict[str, Any]],
    ) -> str:
        return resolve_candidate_batch_protocol(configured_protocol, default_protocol, candidates)

    def _collect_json_batch_window(
        self,
        translated_items: list[tuple[Any, Any]],
        start_idx: int,
        mtool: bool,
        completed: dict[tuple[int, int], dict[str, Any]],
        batch_size: int,
        max_batch_chars: int,
        file_path: str,
        total_targets: int,
        processed_targets: int,
        progress_callback: ProgressCallback | None,
        progress_records: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        return batch_adapter.collect_batch_window(
            self,
            translated_items,
            start_idx,
            mtool,
            completed,
            batch_size,
            max_batch_chars,
            file_path,
            total_targets,
            processed_targets,
            progress_callback,
            progress_records=progress_records,
        )

    @staticmethod
    def _save_or_buffer_progress(
        file_path: str,
        progress_records: list[dict[str, Any]] | None,
        **record: Any,
    ) -> None:
        runtime_adapter.save_or_buffer_progress(file_path, progress_records, **record)

    def _collect_json_batch(
        self,
        translated_items: list[tuple[Any, Any]],
        start_idx: int,
        mtool: bool,
        completed: dict[tuple[int, int], dict[str, Any]],
        batch_size: int,
        max_batch_chars: int,
    ) -> list[dict[str, Any]]:
        return batch_adapter.collect_batch_candidates(
            self,
            translated_items,
            start_idx,
            mtool,
            completed,
            batch_size,
            max_batch_chars,
        )

    def _batch_translate_call(self, model: str, payload: str, system_prompt: str, options: dict[str, Any] | None) -> str:
        return batch_adapter.batch_translate_call(
            model,
            payload,
            system_prompt,
            options,
            batch_cfg=self._batch_translation_config(),
            translate_once_func=translate_once,
        )

    def _uses_api_parallel_batches(self, batch_cfg: dict[str, Any]) -> bool:
        provider = model_provider()
        return uses_api_parallel_batches(batch_cfg, model=self.model, provider=provider)

    def _translate_api_batch_job(self, job: BatchJob) -> dict[int, str]:
        return batch_adapter.translate_api_batch_job(self, job)

    @staticmethod
    def _run_concurrent_batches(*args: Any, **kwargs: Any):
        return run_concurrent_batches(*args, **kwargs)

    @staticmethod
    def _run_dynamic_batches(*args: Any, **kwargs: Any):
        return run_dynamic_batches(*args, **kwargs)

    def _resolve_parallel_candidate_protocol(
        self,
        configured_protocol: str,
        default_protocol: str,
        candidates: list[dict[str, Any]],
        batch_cfg: dict[str, Any],
    ) -> str:
        return resolve_parallel_candidate_protocol(configured_protocol, default_protocol, candidates, batch_cfg)

    def _select_api_job_model(self, candidates: list[dict[str, Any]], batch_cfg: dict[str, Any]) -> str:
        return select_api_job_model(candidates, batch_cfg, default_model=self.model)

    def _select_api_job_options(
        self,
        candidates: list[dict[str, Any]],
        batch_options: dict[str, Any],
        batch_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        return select_api_job_options(candidates, batch_options, batch_cfg)

    @staticmethod
    def _api_job_is_short_text(candidates: list[dict[str, Any]], batch_cfg: dict[str, Any]) -> bool:
        return api_job_is_short_text(candidates, batch_cfg)

    def _update_token_usage(self, file_path: str | None = None) -> dict[str, Any]:
        return runtime_adapter.update_token_usage(self, file_path)

    def token_usage(self, file_path: str | None = None) -> dict[str, Any]:
        """Return current token usage and optionally sync it to checkpoint state."""
        return self._update_token_usage(file_path)

    def _translate_json_candidate_batch_raw(
        self,
        candidates: list[dict[str, Any]],
        batch_options: dict[str, Any],
        batch_protocol: str = "json",
        model: str | None = None,
    ) -> dict[int, str]:
        return batch_adapter.translate_candidate_batch_raw_for_pipeline(
            self,
            candidates,
            batch_options,
            batch_protocol,
            model=model,
        )

    def _translate_json_candidates(
        self,
        candidates: list[dict[str, Any]],
        file_path: str,
        batch_options: dict[str, Any],
        batch_protocol: str = "json",
        model: str | None = None,
    ) -> dict[int, tuple[str, str, list[dict[str, Any]]]]:
        return batch_adapter.translate_candidates_for_pipeline(
            self,
            candidates,
            file_path,
            batch_options,
            batch_protocol,
            model=model,
        )

    def _translate_single_candidate_after_batch_failure(
        self,
        candidate: dict[str, Any],
        file_path: str,
        exc: Exception,
    ) -> dict[int, tuple[str, str, list[dict[str, Any]]]]:
        return batch_adapter.translate_single_candidate_after_batch_failure(self, candidate, file_path, exc)

    def _finish_batch_translation(self, candidate: dict[str, Any], translated: str) -> tuple[str, str, list[dict[str, Any]]]:
        return batch_adapter.finish_batch_candidate(self, candidate, translated)

    @staticmethod
    def _strip_source_echo(source_text: str, translated: str) -> str:
        return batch_adapter.strip_source_echo(source_text, translated)

    def translate_cell(self, text: str, row_idx: int, col_idx: int) -> str:
        translated, _, _ = self._translate_cell_with_meta(text, row_idx, col_idx, "")
        return translated

    def _deterministic_translation(self, text: str) -> str:
        return deterministic_translation(text, glossary=self.glossary)

    def _is_resumable_checkpoint_entry(self, entry: dict[str, Any] | None, source: str) -> bool:
        return runtime_adapter.is_resumable_checkpoint_entry(self, entry, source)

    @staticmethod
    def _status_for_output(source: str, translated: str, issues: list[dict[str, Any]] | None = None) -> str:
        return status_for_output(source, translated, issues)

    @staticmethod
    def _progress_status(status: str) -> str:
        return progress_status(status)

    def _uses_custom_translate_cell(self) -> bool:
        return (
            "translate_cell" in self.__dict__
            or type(self).translate_cell is not TranslationPipeline.translate_cell
        )

    def _legacy_translate_cell_with_meta(self, *args: Any, **kwargs: Any) -> tuple[str, str, list[dict[str, Any]]]:
        raise NotImplementedError("Legacy cell translation path has been removed; use _translate_cell_with_meta")

    def _translate_cell_with_meta(
        self,
        text: str,
        row_idx: int,
        col_idx: int,
        file_path: str,
        context: list[str] | None = None,
        preserve_source_layout: bool = False,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        return translate_cell_with_meta(
            text=text,
            row_idx=row_idx,
            col_idx=col_idx,
            file_path=file_path,
            context=context,
            preserve_source_layout=preserve_source_layout,
            services=self._cell_translation_services(),
        )

    def _cell_translation_services(self) -> CellTranslationServices:
        return cell_services.build_cell_translation_services(self, globals())

    def _restore_protected_translation(
        self,
        original_text: str,
        prepared_text: str,
        protected_text: str,
        translated: str,
        symbol_tokens: list,
        term_tokens: list[tuple[str, str, str]],
        runtime_tokens: list,
        term_hits: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
        return translation_adapter.restore_protected_for_pipeline(
            self,
            original_text,
            prepared_text,
            protected_text,
            translated,
            symbol_tokens,
            term_tokens,
            runtime_tokens,
            term_hits,
            restore_protected_translation_func=restore_protected_translation,
        )

    def _pollution_issues(self, source: str, translated: str) -> list[dict[str, str]]:
        return translation_adapter.pollution_issues_for_pipeline(
            self,
            source,
            translated,
            translation_pollution_issues_func=translation_pollution_issues,
        )

    def _glossary_mappings_for_quality(self) -> list[dict[str, str]]:
        return translation_adapter.glossary_mappings_for_quality(self)

    def _fallback_translate(
        self,
        protected_text: str,
        file_path: str,
        row_idx: int,
        col_idx: int,
        term_hits: list[dict[str, str]],
        *,
        primary_failed: bool = False,
    ) -> str:
        return translation_adapter.fallback_translate_for_pipeline(
            self,
            protected_text,
            file_path,
            row_idx,
            col_idx,
            term_hits,
            fallback_translate_func=fallback_translate,
            translate_func=translate,
            retry_with_fallback_func=retry_with_fallback,
            chunk_translate_func=chunk_translate,
            is_refusal_func=is_refusal,
            primary_failed=primary_failed,
        )

    @staticmethod
    def _looks_like_short_label(text: str) -> bool:
        return looks_like_short_label(text)

    @staticmethod
    def _new_issues(existing: list[dict[str, Any]], new_items: list[dict[str, str]]) -> list[dict[str, str]]:
        return new_issues(existing, new_items)

    def _compose_label_prompt(self, term_hits: list[dict[str, str]] | None = None, strict: bool = False) -> str:
        return translation_adapter.compose_label_prompt_for_pipeline(self, term_hits=term_hits, strict=strict)

    def _retry_short_label(self, protected_text: str, term_hits: list[dict[str, str]]) -> str:
        return translation_adapter.retry_short_label_for_pipeline(
            self,
            protected_text,
            term_hits,
            retry_short_label_translation_func=retry_short_label_translation,
            translate_func=translate,
            is_refusal_func=is_refusal,
        )

    def _call_translate(self, text: str, system_prompt: str, options: dict[str, Any] | None = None) -> str:
        return translation_adapter.call_translate_for_pipeline(
            self,
            text,
            system_prompt,
            options=options,
            call_translate_with_options_func=call_translate_with_options,
            translate_func=translate,
        )

    def _compose_system_prompt(
        self,
        base_prompt: str,
        term_hits: list[dict[str, str]] | None = None,
        strict: bool = False,
    ) -> str:
        return translation_adapter.compose_system_prompt_for_pipeline(
            self,
            base_prompt,
            term_hits=term_hits,
            strict=strict,
        )

    @staticmethod
    def _is_mtool_json(items: list[tuple[Any, Any]]) -> bool:
        return is_mtool_items(items)

    @staticmethod
    def _json_source_text(key: Any, value: Any, mtool: bool) -> str:
        return source_text(key, value, mtool=mtool)

    @staticmethod
    def available_models():
        return list_models()

    def pause(self):
        runtime_adapter.pause(self)

    def resume(self):
        runtime_adapter.resume(self)

    def cancel(self):
        runtime_adapter.cancel(self)

    def flush_writer(self) -> None:
        runtime_adapter.flush_writer(self)

    def update_output_cell(self, row_idx: int, col_idx: int, text: str) -> bool:
        return runtime_adapter.update_output_cell(self, row_idx, col_idx, text)

    def replace_glossary(self, glossary: Glossary) -> None:
        self.glossary = glossary

    def _apply_confirmed_terms_to_outputs(self, file_path: str, confirmed_terms: list[dict[str, Any]]) -> None:
        runtime_adapter.apply_confirmed_terms_to_outputs(self, file_path, confirmed_terms)

    @staticmethod
    def _apply_term_aliases(original: str, translated: str, confirmed_terms: list[dict[str, Any]]) -> str:
        return apply_term_aliases(original, translated, confirmed_terms)

    @property
    def is_paused(self):
        return self._pause_event.is_set()

    @property
    def is_cancelled(self):
        return self._cancel_event.is_set()

    def _check_control_flags(self):
        runtime_adapter.check_pipeline_control_flags(
            self,
            cancelled_factory=lambda: TranslationCancelled("Translation task cancelled"),
        )

    @staticmethod
    def _default_output_path(file_path: str) -> str:
        return runtime_adapter.default_output_path(file_path)

    @staticmethod
    def _emit_progress(
        progress_callback: ProgressCallback | None,
        file_path: str,
        row_idx: int,
        col_idx: int,
        status: str,
        processed: int,
        total: int,
        original_text: str = "",
        translated_text: str = "",
    ) -> None:
        runtime_adapter.emit_pipeline_progress(
            progress_callback,
            file_path,
            row_idx,
            col_idx,
            status,
            processed,
            total,
            original_text=original_text,
            translated_text=translated_text,
        )
