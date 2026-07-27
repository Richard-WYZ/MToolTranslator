from __future__ import annotations

import json
import re
from typing import Any, Callable


BatchTranslator = Callable[[str, str, str, dict[str, Any] | None], str]


class BatchTranslationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        partial_results: dict[int, str] | None = None,
        retry_indexes: set[int] | None = None,
    ):
        super().__init__(message)
        self.partial_results = dict(partial_results or {})
        self.retry_indexes = set(retry_indexes or set())


def build_batch_system_prompt(
    *,
    compact: bool = False,
    include_review: bool = False,
    include_context: bool = False,
) -> str:
    response_shape = (
        '[[0,"translation"],[1,"translation"]]'
        if compact
        else '{"items":[{"i":0,"t":"translation"}]}'
    )
    input_shape = (
        "Each input record is [id, Japanese text] or [id, Japanese text, [[term source, term target]]]. "
        if compact
        else ""
    )
    review_rule = (
        "If a record includes review metadata, translate again from the Japanese source and correct every listed "
        "issue; treat the previous translation only as a draft. For untranslated_japanese or "
        "identical_japanese_source, replace every remaining Japanese lexical fragment and meaningful vocalization "
        "with natural Chinese instead of copying kana. For composed_child_repair, translate the complete multiline "
        "source coherently while keeping every original line in the same position. For "
        "honorific_rendering_review, preserve the source title, role, respect, intimacy, or hierarchy in a natural "
        "Chinese form; do not reduce an honorific-bearing title or role to a generic person, child, or bare name. "
        "For line_break_preservation, output exactly the same number of lines as the current source and never append "
        "neighboring context, alternate drafts, or duplicate clauses. For term_placeholder_leak, translate again "
        "without emitting any __PERSON_* or __TERM_* internal placeholder. "
        if include_review
        else ""
    )
    context_rule = (
        "The input root contains deduplicated read-only contexts and items. "
        "Each item's context_refs are [context id, 1-based source line], or "
        "[context id, 1-based source line, relative entry offset] for scene neighbors. "
        "Negative offsets are earlier entries and positive offsets are later entries. "
        "Use context only to resolve speaker, tone, pronouns, names, titles, and meaning. "
        "Translate only the item's Japanese text; never translate, concatenate, or return surrounding context. "
        if include_context
        else ""
    )
    return (
        "/no_think\n"
        "Translate the input JSON from Japanese game text to natural Simplified Chinese. "
        + input_shape +
        review_rule +
        context_rule +
        "Translate adult, erotic, violent, or controversial content faithfully. "
        "Never refuse, sanitize, moralize, summarize, or skip any item. "
        "Localize Japanese interjections, moans, and meaningful vocalizations into natural Chinese; do not copy kana. "
        "In erotic context, interpret stylized イく/イク/イグ/イケ/イッ forms as climax rather than movement when that meaning fits. "
        "Never add hearts or other symbols from context; preserve only the input placeholders. "
        "Render Japanese honorifics contextually in Chinese; do not silently drop them or use a fixed mechanical mapping. "
        "Preserve identified kanji proper names exactly unless an included confirmed term explicitly overrides them. "
        "Do not invent names, titles, relationships, or places that are absent from the source. "
        "Preserve numbers, control tokens, placeholders, brackets, version markers, and item order. "
        "Keep every __KEEP_* placeholder exactly once. Preserve the relative order of nonnumeric placeholders; "
        "numeric placeholders may move only when natural Chinese grammar requires it. Do not add literal line breaks. "
        "Output JSON only, with this exact shape: "
        + response_shape +
        "\n/no_think"
    )


def build_line_batch_system_prompt() -> str:
    return (
        "/no_think\n"
        "Translate each numbered Japanese game text line to natural Simplified Chinese. "
        "Translate adult, erotic, violent, or controversial content faithfully. "
        "Never refuse, sanitize, moralize, summarize, or skip any item. "
        "Localize Japanese interjections, moans, and meaningful vocalizations into natural Chinese; do not copy kana. "
        "In erotic context, interpret stylized イく/イク/イグ/イケ/イッ forms as climax rather than movement when that meaning fits. "
        "Never add hearts or other symbols; preserve only the input placeholders. "
        "Render Japanese honorifics contextually in Chinese; do not silently drop them or use a fixed mechanical mapping. "
        "Preserve identified kanji proper names exactly unless an included confirmed term explicitly overrides them. "
        "Do not invent names, titles, relationships, or places that are absent from the source. "
        "Preserve numbers, control tokens, placeholders, brackets, version markers, and item order. "
        "Output only tab-separated lines using this exact shape: "
        "0<TAB>translation\n1<TAB>translation. "
        "Do not output JSON, markdown, notes, or explanations."
        "\n/no_think"
    )


def build_parent_batch_system_prompt() -> str:
    return (
        "/no_think\n"
        "Translate Japanese game scenes to natural Simplified Chinese. "
        "Each parent contains ordered lines with stable line IDs. "
        "Use every line as read-only scene context, but output translations only "
        "for lines where target is true. Preserve parent order and target-line order. "
        "Translate adult, erotic, violent, or controversial content faithfully. "
        "Never refuse, sanitize, moralize, summarize, concatenate, or skip a target line. "
        "Localize Japanese interjections, moans, and meaningful vocalizations into natural Chinese. "
        "Render Japanese honorifics contextually in Chinese and do not invent names or relationships. "
        "Keep every __KEEP_* placeholder exactly once. Preserve the relative order of nonnumeric placeholders; "
        "numeric placeholders may move only when natural Chinese grammar requires it. "
        "Output JSON only, with this exact shape: "
        '{"parents":[{"i":0,"lines":[{"i":123,"t":"translation"}]}]}. '
        "Do not return read-only context lines, extra fields, markdown, notes, or explanations."
        "\n/no_think"
    )


def build_parent_batch_payload(items: list[dict[str, Any]]) -> str:
    parents: list[dict[str, Any]] = []
    for item in items:
        lines: list[dict[str, Any]] = []
        for line in item.get("scene_lines", []) or []:
            if not isinstance(line, dict):
                continue
            lines.append({
                "i": int(line["i"]),
                "text": str(line.get("text", "")),
                "target": bool(line.get("target", False)),
            })
        parents.append({"i": int(item["i"]), "lines": lines})
    return json.dumps({"parents": parents}, ensure_ascii=False)


def parse_parent_batch_response(
    response: str,
    expected_lines: dict[int, list[int]],
) -> dict[int, str]:
    """Parse parent-first output while allowing missing lines to fall back individually."""
    if not response or not response.strip():
        raise BatchTranslationError("empty parent batch response")
    try:
        data = _load_json_response(response)
    except (BatchTranslationError, json.JSONDecodeError) as exc:
        raise BatchTranslationError("unable to parse parent batch JSON response") from exc
    if not isinstance(data, dict) or set(data) != {"parents"}:
        raise BatchTranslationError("parent batch response must contain only parents")
    parents = data["parents"]
    if not isinstance(parents, list):
        raise BatchTranslationError("parent batch parents is not a JSON array")

    parsed: dict[int, str] = {
        parent_id: json.dumps({}, ensure_ascii=False)
        for parent_id in expected_lines
    }
    seen_parents: set[int] = set()
    for parent in parents:
        if not isinstance(parent, dict) or set(parent) != {"i", "lines"}:
            raise BatchTranslationError("parent batch item has unexpected fields")
        parent_id = _coerce_batch_index(parent.get("i"))
        if parent_id not in expected_lines:
            raise BatchTranslationError(f"unexpected parent batch index: {parent_id}")
        if parent_id in seen_parents:
            raise BatchTranslationError(f"duplicate parent batch index: {parent_id}")
        seen_parents.add(parent_id)
        lines = parent.get("lines")
        if not isinstance(lines, list):
            raise BatchTranslationError("parent batch lines is not a JSON array")

        expected = expected_lines[parent_id]
        expected_set = set(expected)
        translated: dict[int, str] = {}
        observed_order: list[int] = []
        for line in lines:
            if not isinstance(line, dict):
                raise BatchTranslationError("parent batch line is not an object")
            if set(line) - {"i", "t", "translation", "text"}:
                raise BatchTranslationError("parent batch line has unexpected fields")
            line_id = _coerce_batch_index(line.get("i"))
            text_value = line.get("t", line.get("translation", line.get("text")))
            if line_id not in expected_set:
                raise BatchTranslationError(f"unexpected parent line index: {line_id}")
            if line_id in translated:
                raise BatchTranslationError(f"duplicate parent line index: {line_id}")
            if not isinstance(text_value, str) or not text_value.strip():
                continue
            translated[line_id] = text_value.strip()
            observed_order.append(line_id)
        expected_subsequence = [line_id for line_id in expected if line_id in translated]
        if observed_order != expected_subsequence:
            raise BatchTranslationError("parent line indexes are out of order")
        parsed[parent_id] = json.dumps(translated, ensure_ascii=False, sort_keys=True)
    return parsed


def build_batch_payload(items: list[dict[str, Any]], *, compact: bool = False) -> str:
    context_ids: dict[str, int] = {}
    contexts: list[str] = []
    context_refs_by_item: dict[int, list[list[int]]] = {}
    for item in items:
        refs: list[list[int]] = []
        for context in item.get("contexts", []) or []:
            if not isinstance(context, dict):
                continue
            text = str(context.get("text", ""))
            if not text:
                continue
            context_id = context_ids.get(text)
            if context_id is None:
                context_id = len(contexts)
                context_ids[text] = context_id
                contexts.append(text)
            ref = [context_id, max(1, int(context.get("line", 1) or 1))]
            if context.get("context_kind") == "scene_neighbor":
                offset = int(context.get("offset", 0) or 0)
                if offset:
                    ref.append(offset)
            refs.append(ref)
        if refs:
            context_refs_by_item[int(item["i"])] = refs

    payload = []
    for item in items:
        review = item.get("quality_retry") or {}
        context_refs = context_refs_by_item.get(int(item["i"]), [])
        if compact:
            entry: list[Any] = [item["i"], item["text"]]
            terms = item.get("terms") or []
            if terms or review or context_refs:
                entry.append([[t["source"], t["target"]] for t in terms])
            metadata: dict[str, Any] = {}
            if review:
                metadata.update({
                    "previous": str(review.get("previous", "")),
                    "issues": [str(issue) for issue in review.get("issues", []) if str(issue)],
                })
            if context_refs:
                metadata["context_refs"] = context_refs
            if metadata:
                entry.append(metadata)
            payload.append(entry)
            continue
        entry: dict[str, Any] = {"i": item["i"], "text": item["text"]}
        terms = item.get("terms") or []
        if terms:
            entry["terms"] = [{"source": t["source"], "target": t["target"]} for t in terms]
        if review:
            entry["review"] = {
                "previous": str(review.get("previous", "")),
                "issues": [str(issue) for issue in review.get("issues", []) if str(issue)],
            }
        if context_refs:
            entry["context_refs"] = context_refs
        payload.append(entry)
    if contexts:
        context_payload: list[Any]
        if compact:
            context_payload = [[index, text] for index, text in enumerate(contexts)]
        else:
            context_payload = [{"id": index, "text": text} for index, text in enumerate(contexts)]
        return json.dumps(
            {"contexts": context_payload, "items": payload},
            ensure_ascii=False,
        )
    return json.dumps(payload, ensure_ascii=False)


def build_line_batch_payload(items: list[dict[str, Any]]) -> str:
    return "\n".join(f"{int(item['i'])}\t{item['text']}" for item in items)


def parse_batch_response(response: str, expected_indexes: set[int]) -> dict[int, str]:
    if not response or not response.strip():
        raise BatchTranslationError("empty batch response")
    try:
        data = _load_json_response(response)
    except (BatchTranslationError, json.JSONDecodeError) as exc:
        partial = _extract_complete_json_items(response, expected_indexes)
        if partial:
            missing = expected_indexes - set(partial)
            raise BatchTranslationError(
                "malformed batch JSON; retrying missing indexes: "
                + ", ".join(str(i) for i in sorted(missing)[:10]),
                partial_results=partial,
                retry_indexes=missing,
            ) from exc
        raise BatchTranslationError("unable to parse batch JSON response") from exc
    if isinstance(data, dict) and "items" in data:
        unexpected_root = set(data) - {"items"}
        if unexpected_root:
            raise BatchTranslationError(
                "unexpected batch response fields: " + ", ".join(sorted(unexpected_root))
            )
        data = data["items"]
    if not isinstance(data, list):
        raise BatchTranslationError("batch response is not a JSON array")

    result: dict[int, str] = {}
    for item in data:
        if isinstance(item, list):
            if len(item) != 2:
                raise BatchTranslationError("compact batch response item must contain exactly id and translation")
            idx = _coerce_batch_index(item[0])
            text = item[1]
        elif isinstance(item, dict):
            unexpected_fields = set(item) - {"i", "index", "t", "translation", "text"}
            if unexpected_fields:
                raise BatchTranslationError(
                    "unexpected batch item fields: " + ", ".join(sorted(unexpected_fields))
                )
            idx = _coerce_batch_index(item.get("i", item.get("index")))
            text = item.get("t", item.get("translation", item.get("text")))
        else:
            raise BatchTranslationError("batch response item is not an object or compact pair")
        if not isinstance(idx, int) or not isinstance(text, str):
            raise BatchTranslationError("batch response item misses i/t fields")
        if idx not in expected_indexes:
            raise BatchTranslationError(f"unexpected batch index: {idx}")
        if idx in result:
            raise BatchTranslationError(f"duplicate batch index: {idx}")
        result[idx] = text.strip()

    missing = expected_indexes - set(result)
    if missing:
        raise BatchTranslationError("missing batch indexes: " + ", ".join(str(i) for i in sorted(missing)[:10]))
    return result


def parse_line_batch_response(response: str, expected_indexes: set[int]) -> dict[int, str]:
    if not response or not response.strip():
        raise BatchTranslationError("empty line batch response")
    result: dict[int, str] = {}
    current_idx: int | None = None
    for raw_line in response.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(?:[-*]\s*)?(?P<i>[0-9]+)\s*(?:\t|<TAB>|[:：．。、])\s*(?P<t>.*)$", line, flags=re.IGNORECASE)
        if match:
            idx = int(match.group("i"))
            if idx not in expected_indexes:
                raise BatchTranslationError(
                    f"unexpected line batch index: {idx}",
                    retry_indexes=set(expected_indexes),
                )
            if idx in result:
                raise BatchTranslationError(
                    f"duplicate line batch index: {idx}",
                    retry_indexes=set(expected_indexes),
                )
            result[idx] = match.group("t").strip()
            current_idx = idx
            continue
        if current_idx is not None:
            result[current_idx] = (result[current_idx] + "\n" + line).strip()
            continue
        raise BatchTranslationError(
            "line batch response contains a translation without a stable ID",
            retry_indexes=set(expected_indexes),
        )

    empty = {idx for idx, translated in result.items() if not translated.strip()}
    missing = (expected_indexes - set(result)) | empty
    if missing:
        raise BatchTranslationError(
            "missing line batch indexes: " + ", ".join(str(i) for i in sorted(missing)[:10]),
            retry_indexes=set(expected_indexes),
        )
    return result


def translate_batch(
    model: str,
    items: list[dict[str, Any]],
    translator: BatchTranslator,
    options: dict[str, Any] | None = None,
) -> dict[int, str]:
    if not items:
        return {}
    expected = {int(item["i"]) for item in items}
    compact = bool((options or {}).get("compact_json_protocol", False))
    include_review = any(item.get("quality_retry") for item in items)
    include_context = any(item.get("contexts") for item in items)
    response = translator(
        model,
        build_batch_payload(items, compact=compact),
        build_batch_system_prompt(
            compact=compact,
            include_review=include_review,
            include_context=include_context,
        ),
        options,
    )
    return parse_batch_response(response, expected)


def translate_line_batch(
    model: str,
    items: list[dict[str, Any]],
    translator: BatchTranslator,
    options: dict[str, Any] | None = None,
) -> dict[int, str]:
    if not items:
        return {}
    expected = {int(item["i"]) for item in items}
    response = translator(model, build_line_batch_payload(items), build_line_batch_system_prompt(), options)
    return parse_line_batch_response(response, expected)


def translate_parent_batch(
    model: str,
    items: list[dict[str, Any]],
    translator: BatchTranslator,
    options: dict[str, Any] | None = None,
) -> dict[int, str]:
    if not items:
        return {}
    expected_lines = {
        int(item["i"]): [
            int(line["i"])
            for line in item.get("scene_lines", []) or []
            if isinstance(line, dict) and bool(line.get("target", False))
        ]
        for item in items
    }
    response = translator(
        model,
        build_parent_batch_payload(items),
        build_parent_batch_system_prompt(),
        options,
    )
    return parse_parent_batch_response(response, expected_lines)


def _load_json_response(response: str) -> Any:
    stripped = response.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1).strip())

    start = stripped.find("[")
    end = stripped.rfind("]")
    if start != -1 and end > start:
        return json.loads(stripped[start:end + 1])

    raise BatchTranslationError("unable to parse batch JSON response")


def _coerce_batch_index(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _extract_complete_json_items(response: str, expected_indexes: set[int]) -> dict[int, str]:
    """Salvage complete, schema-valid items from a truncated JSON response."""
    normalized = response.strip()
    decoder = json.JSONDecoder()
    allowed_fields = {"i", "index", "t", "translation", "text"}
    result: dict[int, str] = {}
    duplicates: set[int] = set()
    for position, char in enumerate(normalized):
        if char not in "[{":
            continue
        try:
            item, _ = decoder.raw_decode(normalized, position)
        except json.JSONDecodeError:
            continue
        if isinstance(item, list):
            if len(item) != 2:
                continue
            idx = _coerce_batch_index(item[0])
            text = item[1]
        elif isinstance(item, dict) and set(item).issubset(allowed_fields):
            idx = _coerce_batch_index(item.get("i", item.get("index")))
            text = item.get("t", item.get("translation", item.get("text")))
        else:
            continue
        if idx not in expected_indexes or not isinstance(text, str) or not text.strip():
            continue
        if idx in result:
            duplicates.add(idx)
            continue
        result[idx] = text.strip()
    for idx in duplicates:
        result.pop(idx, None)
    return result


__all__ = [
    "BatchTranslationError",
    "build_batch_payload",
    "build_parent_batch_payload",
    "build_parent_batch_system_prompt",
    "build_batch_system_prompt",
    "build_line_batch_payload",
    "build_line_batch_system_prompt",
    "parse_batch_response",
    "parse_parent_batch_response",
    "parse_line_batch_response",
    "translate_batch",
    "translate_line_batch",
    "translate_parent_batch",
]
