from __future__ import annotations


def compose_label_prompt(
    base_prompt: str,
    glossary_prompt: str = "",
    *,
    strict: bool = False,
    quality_rules: str = "",
) -> str:
    """Compose the prompt used for short UI labels and item names."""
    parts = [
        base_prompt,
        "You are translating a short Japanese game UI label, menu item, item name, or event title into Simplified Chinese.",
        "Output only the translation, with no explanation, notes, quotes, prefixes, or alternatives.",
        "Translate literally and concisely. Preserve numbers, brackets, punctuation, symbols, and version markers.",
        "Do not invent extra context. Do not romanize Japanese. Do not leave ordinary English words.",
        "If the label contains mature or explicit game terminology, still translate it faithfully as a localization label.",
    ]
    if quality_rules:
        parts.append(quality_rules)
    if glossary_prompt:
        parts.append(glossary_prompt)
    if strict:
        parts.append("Strict retry: remove ordinary English residue and keep only valid control tokens unchanged.")
    return "\n\n".join(parts)


def compose_translation_prompt(
    base_prompt: str,
    glossary_prompt: str = "",
    *,
    strict: bool = False,
    quality_rules: str = "",
) -> str:
    """Compose the prompt used for normal translation requests."""
    parts = [base_prompt]
    if quality_rules:
        parts.append(quality_rules)
    if glossary_prompt:
        parts.append(glossary_prompt)
    if strict:
        parts.append(
            "Strict terminology retry: preserve placeholders like __SYM_0__, __KEEP_0__, __TERM_0__, and __PERSON_0__ exactly. "
            "Do not translate, remove, rename, or explain placeholders."
        )
    else:
        parts.append("Preserve placeholders like __SYM_0__ and __KEEP_0__ exactly and keep them in their original relative positions.")
    return "\n\n".join(parts)


__all__ = ["compose_label_prompt", "compose_translation_prompt"]
