from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

import translation.checkpoint as checkpoint
from translation.analysis import (
    COMPOSITION_VERSION,
    NEIGHBOR_CONTEXT_VERSION,
    build_mtool_composition_plan,
    build_mtool_neighbor_context_plan,
)
from translation.batching import (
    build_batch_system_prompt,
    build_line_batch_system_prompt,
    build_parent_batch_system_prompt,
)
from translation.classification import (
    CLASSIFICATION_VERSION,
    SENSITIVITY_CLASSIFIER_VERSION,
)
from translation.config import (
    batch_translation_config,
    fallback_models,
    system_prompts,
    think_setting,
)
from translation.input import load_json_items
from translation.models import model_configuration
from translation.output import TranslationWriter, write_json_items
from translation.protection import (
    PROTECTED_RESTORATION_VERSION,
    SYMBOL_PROTECTION_VERSION,
)
from translation.quality import QUALITY_RULES_VERSION, quality_prompt_rules
from translation.terminology.candidate_policy import CANDIDATE_POLICY_VERSION


ProgressCallback = Callable[[dict[str, Any]], None]


def translate_json_workflow(
    pipeline: Any,
    file_path: str,
    output_path: str | None,
    progress_callback: ProgressCallback | None,
) -> list[tuple[Any, Any]]:
    items = load_json_items(file_path)
    translated_items = deepcopy(items)
    mtool = pipeline._is_mtool_json(items)
    total_targets = (
        len(items)
        if mtool
        else sum(1 for key, value in items if pipeline._json_source_text(key, value, mtool).strip())
    )
    if mtool and not pipeline._uses_custom_translate_cell():
        sources = [pipeline._json_source_text(key, value, mtool) for key, value in items]
        if pipeline.glossary.preseed_from_sources(sources):
            pipeline.glossary.save()
    batch_cfg = pipeline._batch_translation_config()
    freeze_glossary = bool(
        batch_cfg.get("glossary_freeze_during_translation", True)
    )
    if freeze_glossary:
        pipeline.glossary.freeze()
    pipeline._mtool_composition_plan = None
    pipeline._mtool_neighbor_context_plan = None
    if (
        mtool
        and not pipeline._uses_custom_translate_cell()
        and batch_cfg.get("mtool_composition_enabled", True)
    ):
        pipeline._mtool_composition_plan = build_mtool_composition_plan(
            items,
            context_max_chars=int(batch_cfg.get("mtool_context_max_chars", 1200)),
            context_max_per_item=int(batch_cfg.get("mtool_context_max_per_item", 2)),
        )
    if (
        mtool
        and not pipeline._uses_custom_translate_cell()
        and batch_cfg.get("mtool_neighbor_context_enabled", True)
    ):
        existing_context_indexes = (
            pipeline._mtool_composition_plan.contexts_by_child
            if pipeline._mtool_composition_plan is not None
            else ()
        )
        pipeline._mtool_neighbor_context_plan = build_mtool_neighbor_context_plan(
            items,
            excluded_child_indexes=existing_context_indexes,
            radius=int(batch_cfg.get("mtool_neighbor_context_radius", 2)),
            context_max_chars=int(batch_cfg.get("mtool_neighbor_context_max_chars", 120)),
            min_dialogue_items=int(
                batch_cfg.get("mtool_neighbor_context_min_dialogue_items", 3)
            ),
        )
    resume_model_configuration = checkpoint.build_resume_model_configuration(
        model_configuration(pipeline.model),
        batch_cfg,
        think=think_setting(),
        fallback_models=fallback_models(),
    )
    prompt_version = checkpoint.build_prompt_version({
        "prompt_style": pipeline.prompt_style,
        "active_system_prompt": pipeline.system_prompt,
        "configured_system_prompts": system_prompts(),
        "batch_json_prompt": build_batch_system_prompt(
            compact=bool(batch_cfg.get("compact_json_protocol", False))
        ),
        "batch_json_context_prompt": build_batch_system_prompt(
            compact=bool(batch_cfg.get("compact_json_protocol", False)),
            include_context=True,
        ),
        "batch_json_review_prompt": build_batch_system_prompt(
            compact=bool(batch_cfg.get("compact_json_protocol", False)),
            include_review=True,
            include_context=True,
        ),
        "batch_line_prompt": build_line_batch_system_prompt(),
        "batch_parent_prompt": build_parent_batch_system_prompt(),
        "quality_rules": quality_prompt_rules(),
        "quality_rules_version": QUALITY_RULES_VERSION,
        "classification_version": CLASSIFICATION_VERSION,
        "candidate_policy_version": CANDIDATE_POLICY_VERSION,
        "sensitivity_classifier_version": SENSITIVITY_CLASSIFIER_VERSION,
        "protected_restoration_version": PROTECTED_RESTORATION_VERSION,
        "symbol_protection_version": SYMBOL_PROTECTION_VERSION,
        "mtool_composition_version": COMPOSITION_VERSION,
        "mtool_neighbor_context_version": NEIGHBOR_CONTEXT_VERSION,
    })
    glossary_version = pipeline.glossary.version()
    pipeline._resume_context = {
        "translation_direction": "ja-Hans",
        "prompt_version": prompt_version,
        "glossary_version": glossary_version,
        "model_configuration": resume_model_configuration,
    }
    checkpoint.init_checkpoint(
        file_path,
        total=total_targets,
        task_id=pipeline.task_id,
        model=pipeline.model,
        prompt_style=pipeline.prompt_style,
        translate_columns=[1],
        file_type="json",
        model_configuration=resume_model_configuration,
        translation_direction="ja-Hans",
        prompt_version=prompt_version,
        glossary_version=glossary_version,
    )
    completed = checkpoint.load_progress(file_path)
    target_path = output_path or pipeline._default_output_path(file_path)
    api_parallel = pipeline._uses_api_parallel_batches(batch_cfg)
    pipeline._writer = TranslationWriter(
        "json",
        translated_items,
        target_path,
        json_every=5,
        periodic_enabled=(
            not api_parallel
            or bool(batch_cfg.get("api_live_output_snapshots_enabled", False))
        ),
    )
    pipeline._writer.start()
    processed_targets = 0

    if mtool and not pipeline._uses_custom_translate_cell() and batch_cfg.get("enabled", True):
        try:
            return pipeline._translate_json_batched(
                file_path,
                translated_items,
                mtool,
                completed,
                target_path,
                total_targets,
                progress_callback,
            )
        finally:
            if freeze_glossary:
                pipeline.glossary.save()
                pipeline.glossary.thaw()

    try:
        for idx, (key, value) in enumerate(translated_items):
            pipeline._check_control_flags()
            source_text = pipeline._json_source_text(key, value, mtool)
            if not source_text.strip():
                if mtool:
                    translated_items[idx] = (key, source_text)
                    pipeline._save_or_buffer_progress(
                        file_path,
                        None,
                        row=idx,
                        col=0,
                        original=source_text,
                        translated=source_text,
                        status="preserved",
                        issues=[],
                        json_key=str(key),
                        mtool=mtool,
                        entry_classification="empty",
                    )
                    processed_targets += 1
                    pipeline._writer.mark_dirty()
                    pipeline._emit_progress(
                        progress_callback,
                        file_path,
                        idx,
                        0,
                        "preserved",
                        processed_targets,
                        total_targets,
                    )
                else:
                    pipeline._emit_progress(
                        progress_callback,
                        file_path,
                        idx,
                        0,
                        "skipped_empty",
                        processed_targets,
                        total_targets,
                    )
                continue

            cp_entry = completed.get((idx, 0))
            if pipeline._is_resumable_checkpoint_entry(cp_entry, source_text):
                translated = cp_entry.get("translated", source_text)
                deterministic = pipeline._deterministic_translation(source_text)
                if (
                    not pipeline._uses_custom_translate_cell()
                    and deterministic
                    and (deterministic != translated or cp_entry.get("issues"))
                ):
                    translated = deterministic
                    pipeline._save_or_buffer_progress(
                        file_path,
                        None,
                        row=idx,
                        col=0,
                        original=source_text,
                        translated=translated,
                        status=pipeline._status_for_output(source_text, translated),
                        issues=[],
                        json_key=str(key),
                        mtool=mtool,
                    )
                translated_items[idx] = (key, translated)
                processed_targets += 1
                pipeline._writer.mark_dirty()
                pipeline._emit_progress(
                    progress_callback,
                    file_path,
                    idx,
                    0,
                    "resumed",
                    processed_targets,
                    total_targets,
                    original_text=source_text,
                    translated_text=translated,
                )
                continue

            pipeline._emit_progress(
                progress_callback,
                file_path,
                idx,
                0,
                "translating",
                processed_targets,
                total_targets,
                original_text=source_text,
            )
            if pipeline._uses_custom_translate_cell():
                translated = pipeline.translate_cell(source_text, idx, 0)
                status = pipeline._status_for_output(source_text, translated)
                issues: list[dict[str, Any]] = []
            else:
                translated, status, issues = pipeline._translate_cell_with_meta(
                    source_text,
                    idx,
                    0,
                    file_path,
                    preserve_source_layout=mtool,
                )
            translated_items[idx] = (key, translated)
            pipeline._save_or_buffer_progress(
                file_path,
                None,
                row=idx,
                col=0,
                original=source_text,
                translated=translated,
                status=status,
                issues=issues,
                json_key=str(key),
                mtool=mtool,
            )
            confirmed_terms = pipeline.glossary.auto_extract(source_text, translated)
            if confirmed_terms:
                pipeline._apply_confirmed_terms_to_outputs(file_path, confirmed_terms)
                pipeline.glossary.save()
            pipeline._writer.mark_dirty()

            processed_targets += 1
            pipeline._emit_progress(
                progress_callback,
                file_path,
                idx,
                0,
                pipeline._progress_status(status),
                processed_targets,
                total_targets,
                original_text=source_text,
                translated_text=translated,
            )
    finally:
        checkpoint.set_glossary_version(file_path, pipeline.glossary.version(), update_entries=True)
        pipeline._update_token_usage(file_path)
        if freeze_glossary:
            pipeline.glossary.save()
            pipeline.glossary.thaw()
        if pipeline._writer:
            pipeline._writer.stop()

    write_json_items(translated_items, target_path)
    return translated_items


__all__ = ["translate_json_workflow"]
