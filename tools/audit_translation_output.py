from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translation.classification import deterministic_translation, has_source_japanese, looks_like_short_label
from translation.pollution import glossary_term_pollution_issues, translation_pollution_issues
from translation.quality import is_refusal, new_issues, translation_issues
from translation.terminology import Glossary


def _load_mapping(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a flat JSON object: {path}")
    return payload


def audit_translation_output(
    source_path: str | Path,
    output_path: str | Path,
    *,
    glossary_path: str | Path | None = None,
    example_limit: int = 10,
) -> dict[str, Any]:
    source = _load_mapping(source_path)
    output = _load_mapping(output_path)
    glossary = Glossary(file_path=str(glossary_path)) if glossary_path else Glossary.in_memory()
    glossary_mappings = [
        {"source": src, "target": tgt, "owner": owner, "type": typ}
        for src, tgt, owner, typ in glossary.iter_mappings()
    ]

    source_keys = list(source)
    output_keys = list(output)
    missing_keys = [key for key in source_keys if key not in output]
    extra_keys = [key for key in output_keys if key not in source]
    issue_counts: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    examples_by_type: dict[str, list[dict[str, Any]]] = {}
    issue_entry_count = 0

    for key in source_keys:
        if key not in output:
            continue
        source_text = str(key)
        translated = str(output[key])
        deterministic = deterministic_translation(source_text, glossary=glossary)
        explicitly_preserved = translated == source_text and deterministic == source_text
        issues = [] if explicitly_preserved else translation_issues(
            source_text,
            translated,
            short_label=looks_like_short_label(source_text),
        )
        issues.extend(new_issues(
            issues,
            translation_pollution_issues(
                source_text,
                translated,
                glossary_mappings=glossary_mappings,
            ),
        ))
        hits = glossary.find_hits(source_text)
        missing_terms = glossary.missing_hits(source_text, translated, hits)
        if missing_terms:
            issues.extend(new_issues(issues, [{
                "type": "term_preservation",
                "message": "Confirmed terms are absent: "
                + ", ".join(f"{item['source']}=>{item['target']}" for item in missing_terms[:8]),
            }]))
        if (
            translated != source_text
            and has_source_japanese(source_text)
            and not deterministic
            and not any(
                str(issue.get("type", "")) == "model_refusal"
                for issue in issues
                if isinstance(issue, dict)
            )
            and is_refusal(translated, original=source_text)
        ):
            issues.extend(new_issues(issues, [{
                "type": "model_refusal",
                "message": "Output resembles a refusal or non-translation response.",
            }]))

        if not has_source_japanese(source_text):
            status = "preserved" if translated == source_text else "translated_needs_review"
        elif deterministic == source_text and translated == source_text:
            status = "preserved"
        elif translated == source_text:
            status = "review_required"
        elif issues:
            status = "translated_needs_review"
        else:
            status = "translated"
        statuses[status] += 1
        issue_counts.update(str(issue.get("type", "translation_issue")) for issue in issues)
        if issues:
            issue_entry_count += 1
        if issues and len(examples) < max(0, example_limit):
            examples.append({
                "source": source_text,
                "translation": translated,
                "status": status,
                "issues": issues,
            })
        for issue in issues:
            issue_type = str(issue.get("type", "translation_issue"))
            typed_examples = examples_by_type.setdefault(issue_type, [])
            if len(typed_examples) < max(0, example_limit):
                typed_examples.append({
                    "source": source_text,
                    "translation": translated,
                    "status": status,
                    "message": str(issue.get("message", "")),
                })

    glossary_issue_counts: Counter[str] = Counter()
    glossary_issue_examples: list[dict[str, Any]] = []
    for term_source, term_target in glossary.terms.items():
        term_type = str((glossary.candidates.get(term_source, {}) or {}).get("type") or "")
        issues = glossary_term_pollution_issues(term_source, term_target, term_type)
        glossary_issue_counts.update(str(issue.get("type", "glossary_issue")) for issue in issues)
        if issues and len(glossary_issue_examples) < max(0, example_limit):
            glossary_issue_examples.append({
                "source": term_source,
                "target": term_target,
                "type": term_type,
                "issues": issues,
            })

    return {
        "source_file": str(Path(source_path).resolve()),
        "output_file": str(Path(output_path).resolve()),
        "glossary_file": str(Path(glossary_path).resolve()) if glossary_path else "",
        "structure": {
            "source_entries": len(source),
            "output_entries": len(output),
            "same_key_order": source_keys == output_keys,
            "missing_key_count": len(missing_keys),
            "extra_key_count": len(extra_keys),
            "missing_keys": missing_keys[:example_limit],
            "extra_keys": extra_keys[:example_limit],
        },
        "statuses": dict(statuses),
        "issue_counts": dict(issue_counts),
        "issue_entry_count": issue_entry_count,
        "issue_examples": examples,
        "issue_examples_by_type": examples_by_type,
        "glossary_terms": len(glossary.terms),
        "glossary_issue_counts": dict(glossary_issue_counts),
        "glossary_issue_examples": glossary_issue_examples,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Audit a translated flat MTool JSON artifact offline.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--glossary", default="")
    parser.add_argument("--examples", type=int, default=10)
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    result = audit_translation_output(
        args.source,
        args.output,
        glossary_path=args.glossary or None,
        example_limit=max(0, args.examples),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
