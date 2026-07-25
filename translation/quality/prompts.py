from __future__ import annotations


def quality_prompt_rules() -> str:
    return (
        "Translate only the current text into Simplified Chinese. "
        "Do not use previous or next lines as context. "
        "Do not replace pronouns or deictic words with guessed names or places; translate them faithfully. "
        "Render Japanese honorifics contextually in Chinese; do not silently discard them or apply a fixed global mapping. "
        "Preserve identified kanji proper names exactly unless a confirmed term explicitly overrides them. "
        "The final output should be Chinese. Do not leave ordinary English words in the translation. "
        "Preserve runtime tokens, control codes, tags, variables, URLs, file names, and button/key labels exactly. "
        "Translate UI/system words such as Continue, Save, Load, Inventory, Mission, and Battle into Chinese."
    )


__all__ = ["quality_prompt_rules"]
