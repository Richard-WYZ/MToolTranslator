from __future__ import annotations


def quality_prompt_rules() -> str:
    return (
        "Translate only the current text into Simplified Chinese. "
        "Do not use previous or next lines as context. "
        "Do not replace pronouns or deictic words with guessed names or places; translate them faithfully. "
        "Japanese may omit subjects and gender. Do not invent 他 or 她 based on a name, role, or speech style; "
        "use a natural subjectless or neutral Chinese expression when gender is not established in the current text. "
        "Render Japanese honorifics contextually in Chinese; do not silently discard them or apply a fixed global mapping. "
        "Preserve identified kanji proper names exactly unless a confirmed term explicitly overrides them. "
        "For katakana character names, use a Chinese phonetic name rather than translating a possible ordinary-word meaning. "
        "The final output should be Chinese. Do not leave ordinary English words in the translation. "
        "Omit or localize Japanese small っ/ッ; never copy them into otherwise Chinese output. "
        "When adjacent hiragana and katakana are merely alternate-script repetitions of the same reading, "
        "translate the meaning once instead of duplicating the Chinese phrase. "
        "Preserve runtime tokens, control codes, tags, variables, URLs, file names, and button/key labels exactly. "
        "Translate UI/system words such as Continue, Save, Load, Inventory, Mission, and Battle into Chinese."
    )


__all__ = ["quality_prompt_rules"]
