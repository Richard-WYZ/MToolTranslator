from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import translation.checkpoint as checkpoint
from translation.classification import deterministic_translation, looks_like_short_label
from translation.pollution import translation_pollution_issues
from translation.quality import new_issues, status_for_output, translation_issues
from translation.terminology import Glossary


# These issues describe the serialized output itself, so a terminal audit must
# replace stale pipeline-era findings even when an older checkpoint did not
# record which issue types came from a prior artifact audit. Operational
# diagnostics are intentionally excluded and remain available for telemetry.
RECOMPUTED_FINAL_ISSUE_TYPES = {
    "composed_dependency_needs_review",
    "composed_dependency_review_required",
    "context_contamination",
    "empty_translation",
    "english_residue",
    "glossary_pollution",
    "glossary_proper_name_pollution",
    "honorific_rendering_review",
    "identical_japanese_source",
    "internal_placeholder_leak",
    "length_expansion",
    "line_break_preservation",
    "marker_lost",
    "missing_output_key",
    "model_refusal",
    "numeric_preservation",
    "resource_identifier_preservation",
    "short_label_expansion",
    "suspicious_artifact",
    "symbol_preservation",
    "term_placeholder_leak",
    "term_preservation",
    "unsupported_context_expansion",
    "unsupported_proper_name",
    "untranslated_japanese",
    "version_marker_lost",
}


def reconcile_final_artifact(
    file_path: str,
    output_path: str,
) -> dict[str, Any]:
    """Re-audit the serialized artifact and persist only changed final states.

    Translation, composition, and AI review can all modify text after the first
    per-entry validation. This terminal pass makes the actual JSON artifact the
    source of truth for review status without re-translating any entry.
    """
    source_target = Path(file_path)
    output_target = Path(output_path)
    if not source_target.is_file() or not output_target.is_file():
        return {"performed": False, "reason": "source_or_output_missing"}

    source = _load_mapping(source_target)
    output = _load_mapping(output_target)
    source_keys = list(source)
    output_keys = list(output)
    data = checkpoint.load_checkpoint(file_path)
    entries = data.get("entries", {}) if isinstance(data.get("entries"), dict) else {}
    glossary = Glossary(file_path=checkpoint.get_glossary_path(file_path))
    glossary_mappings = [
        {"source": src, "target": tgt, "owner": owner, "type": typ}
        for src, tgt, owner, typ in glossary.iter_mappings()
    ]
    records: list[dict[str, Any]] = []

    for row, source_key in enumerate(source_keys):
        entry_key = f"{row}_0"
        entry = entries.get(entry_key, {})
        if not isinstance(entry, dict):
            entry = {}
        source_text = str(source_key)
        translated = str(output[source_key]) if source_key in output else ""
        existing_status = checkpoint.normalize_status(
            str(entry.get("status", "")),
            entry.get("issues", []) if isinstance(entry.get("issues"), list) else [],
            translated=str(entry.get("translated", "")),
            original=str(entry.get("original", source_text)),
        )
        previous_artifact_issue_types = {
            str(issue_type)
            for issue_type in entry.get("final_artifact_issue_types", []) or []
        }
        existing_issues = [
            dict(issue)
            for issue in entry.get("issues", []) or []
            if isinstance(issue, dict)
            and str(issue.get("type", "")) not in (
                previous_artifact_issue_types | RECOMPUTED_FINAL_ISSUE_TYPES
            )
        ]
        explicitly_preserved = (
            existing_status == "preserved"
            and source_key in output
            and translated == source_text
            and deterministic_translation(source_text, glossary=glossary) == source_text
        )
        artifact_issues: list[dict[str, Any]] = []
        if source_key not in output:
            artifact_issues.append({
                "type": "missing_output_key",
                "message": "The final artifact is missing an authoritative source key.",
            })
        elif not explicitly_preserved:
            artifact_issues.extend(translation_issues(
                source_text,
                translated,
                short_label=looks_like_short_label(source_text),
            ))
            artifact_issues.extend(new_issues(
                artifact_issues,
                translation_pollution_issues(
                    source_text,
                    translated,
                    glossary_mappings=glossary_mappings,
                ),
            ))
            missing_terms = glossary.missing_hits(
                source_text,
                translated,
                glossary.find_hits(source_text),
            )
            if missing_terms:
                artifact_issues.extend(new_issues(artifact_issues, [{
                    "type": "term_preservation",
                    "message": "Confirmed terms are absent: "
                    + ", ".join(
                        f"{item['source']}=>{item['target']}"
                        for item in missing_terms[:8]
                    ),
                }]))

        issues = existing_issues + new_issues(existing_issues, artifact_issues)
        if explicitly_preserved and not artifact_issues:
            status = "preserved"
        else:
            status = status_for_output(source_text, translated, issues)
        if (
            translated == str(entry.get("translated", ""))
            and status == existing_status
            and issues == existing_issues
            and previous_artifact_issue_types == {
                str(issue.get("type", "translation_issue"))
                for issue in artifact_issues
            }
        ):
            continue
        records.append({
            "row": row,
            "col": 0,
            "original": source_text,
            "translated": translated,
            "status": status,
            "issues": issues,
            "json_key": source_text,
            "entry_classification": entry.get("entry_classification", ""),
            "batch_id": entry.get("batch_id", "final_artifact_audit"),
            "model_identifier": entry.get("model_identifier", data.get("model", "")),
            "model_configuration": entry.get("model_configuration", data.get("model_configuration", {})),
            "translation_direction": entry.get("translation_direction", data.get("translation_direction", "ja-Hans")),
            "prompt_version": entry.get("prompt_version", data.get("prompt_version", "default")),
            "glossary_version": entry.get("glossary_version", data.get("glossary_version", "0")),
            "retry_count": int(entry.get("retry_count", 0) or 0),
            "review_reasons": [
                str(issue.get("type", "translation_issue"))
                for issue in issues
            ],
            "final_artifact_issue_types": sorted({
                str(issue.get("type", "translation_issue"))
                for issue in artifact_issues
            }),
        })

    if records:
        checkpoint.save_progress_many(file_path, records)
    return {
        "performed": True,
        "source_entries": len(source_keys),
        "output_entries": len(output_keys),
        "same_key_order": source_keys == output_keys,
        "missing_key_count": sum(key not in output for key in source_keys),
        "extra_key_count": sum(key not in source for key in output_keys),
        "reconciled_entry_count": len(records),
    }


def _load_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a flat JSON object: {path}")
    return payload


__all__ = ["RECOMPUTED_FINAL_ISSUE_TYPES", "reconcile_final_artifact"]
