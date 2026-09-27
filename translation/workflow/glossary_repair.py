"""Source-conditioned repair of translations predating a confirmed term."""
from __future__ import annotations

import json
from typing import Any, Callable

import translation.checkpoint as checkpoint
from translation.batching import default_batch_options, prepare_model_candidate
from translation.quality import status_for_output


def _verify_repair_batch(
    pipeline: Any,
    candidates: list[dict[str, Any]],
    outputs: dict[int, tuple[str, str, list[dict[str, Any]]]],
    model: str,
) -> dict[int, bool]:
    verifier_model = next((
        str(candidate) for candidate in (
            pipeline._batch_translation_config().get("api_fast_model"),
            pipeline._batch_translation_config().get("api_quality_model"),
        )
        if candidate and str(candidate) != model
    ), "")
    if not verifier_model:
        return {}
    items = [
        {
            "i": int(candidate["idx"]),
            "source": candidate["source"],
            "translation": outputs[int(candidate["idx"])][0],
            "terms": [
                {"source": hit["source"], "target": hit["target"]}
                for hit in candidate["term_hits"]
            ],
        }
        for candidate in candidates if int(candidate["idx"]) in outputs
    ]
    if not items:
        return {}
    prompt = (
        "Independently verify Japanese-to-Simplified-Chinese game translations. "
        "Check complete meaning, confirmed character names, honorifics, line breaks, "
        "runtime tokens, and absence of repeated or copied neighboring text. "
        "Reject any meaning change, omission, added context, semantic translation of a person name, "
        "or incorrect code placement. Return only strict JSON: "
        '{"items":[{"i":0,"acceptable":true}]}. '
        "Include every input id exactly once."
    )
    try:
        response = pipeline._batch_translate_call(
            verifier_model,
            json.dumps({"items": items}, ensure_ascii=False),
            prompt,
            {"temperature": 0, "num_predict": 1024},
        )
        parsed = json.loads(str(response).strip())
        decisions = parsed["items"]
        expected = {item["i"] for item in items}
        if (
            set(parsed) != {"items"}
            or not isinstance(decisions, list)
            or len(decisions) != len(items)
            or any(
                not isinstance(item, dict)
                or set(item) != {"i", "acceptable"}
                or type(item["i"]) is not int
                or type(item["acceptable"]) is not bool
                for item in decisions
            )
            or {item["i"] for item in decisions} != expected
        ):
            return {}
        return {item["i"]: item["acceptable"] for item in decisions}
    except Exception:
        return {}


def repair_pending_glossary_entries(
    pipeline: Any,
    file_path: str,
    translated_items: list[tuple[Any, Any]],
    *,
    update_output_cell: Callable[[int, int, str], Any] | None = None,
) -> int:
    """Retranslate affected leaves with the new glossary, preserving failed rows."""
    pending = [
        entry for entry in checkpoint.load_progress(file_path).values()
        if isinstance(entry, dict) and entry.get("glossary_pending_sources")
    ]
    if not pending:
        return 0
    batch_cfg = pipeline._batch_translation_config()
    model = str(batch_cfg.get("api_quality_model") or pipeline.model)
    has_verifier = any(
        candidate and str(candidate) != model
        for candidate in (batch_cfg.get("api_fast_model"), batch_cfg.get("api_quality_model"))
    )
    options = default_batch_options(batch_cfg)
    batch_size = max(1, min(20, int(batch_cfg.get("json_batch_size", 40))))
    max_chars = max(500, min(4000, int(batch_cfg.get("max_batch_chars", 4000))))
    repaired = 0
    candidates: list[dict[str, Any]] = []
    chars = 0

    def flush() -> None:
        nonlocal repaired, chars
        if not candidates:
            return
        pipeline._check_control_flags()
        try:
            outputs = pipeline._translate_json_candidates(
                candidates, file_path, options, "json", model=model,
            )
        except Exception:
            candidates.clear()
            chars = 0
            return
        verified = _verify_repair_batch(pipeline, candidates, outputs, model) if has_verifier else {}
        records: list[dict[str, Any]] = []
        for candidate in candidates:
            row = int(candidate["idx"])
            result = outputs.get(row)
            if result is None:
                continue
            translated, status, issues = result
            sources = candidate["glossary_pending_sources"]
            if status == "review_required" or any(
                not any(
                    hit["source"] == source and hit["target"] in translated
                    for hit in candidate["term_hits"]
                )
                for source in sources
            ):
                continue
            if has_verifier and not verified.get(row, False):
                continue
            if not has_verifier:
                issues = [*issues, {
                    "type": "glossary_repair_unverified",
                    "message": "The new terminology is applied, but no independent verifier model was available.",
                }]
                status = status_for_output(candidate["source"], translated, issues)
            key, _old = translated_items[row]
            translated_items[row] = (key, translated)
            if update_output_cell is None:
                pipeline.update_output_cell(row, 0, translated)
            else:
                update_output_cell(row, 0, translated)
            records.append({
                "row": row,
                "col": 0,
                "original": candidate["source"],
                "translated": translated,
                "status": status,
                "issues": issues,
                "json_key": str(key),
                "mtool": True,
                "entry_classification": candidate["entry_classification"],
                "batch_id": "glossary-repair",
                "model_identifier": model,
                "glossary_version": pipeline.glossary.version(),
                "retry_count": 1,
            })
            repaired += 1
        checkpoint.save_progress_many(file_path, records)
        candidates.clear()
        chars = 0

    for entry in sorted(pending, key=lambda item: int(item.get("row", 0))):
        row = int(entry.get("row", -1))
        if row < 0 or row >= len(translated_items):
            continue
        source = str(entry.get("original", ""))
        if str(translated_items[row][0]) != source:
            continue
        candidate = prepare_model_candidate(
            batch_i=len(candidates), idx=row, source=source, glossary=pipeline.glossary,
        )
        candidate["preserve_source_layout"] = True
        candidate["glossary_pending_sources"] = list(entry["glossary_pending_sources"])
        length = len(candidate["protected"])
        if candidates and (len(candidates) >= batch_size or chars + length > max_chars):
            flush()
            candidate["i"] = 0
        candidates.append(candidate)
        chars += length
    flush()
    return repaired


__all__ = ["repair_pending_glossary_entries"]
