# Translation Quality Policy

This project translates game resource text into Simplified Chinese. The policy is
generic: it must work across input files and must not encode fixed translations
from a benchmark file.

## Evidence Base

- IGDA Best Practices for Game Localization:
  https://igda-website.s3.us-east-2.amazonaws.com/wp-content/uploads/2021/04/09142137/Best-Practices-for-Game-Localization-v22.pdf
- Microsoft Globalization, message formatting:
  https://learn.microsoft.com/en-us/globalization/internationalization/message-formatting
- Microsoft Globalization, software internationalization:
  https://learn.microsoft.com/en-us/globalization/methodology/software-internationalization
- W3C Internationalization Tag Set 2.0:
  https://www.w3.org/TR/its20/
- Found in Translation: Evolving Approaches for the Localization of Japanese Video Games:
  https://www.mdpi.com/2076-0752/10/1/9
- Evidence from the Korean Translation of Immortals of Aveum:
  https://www.sejongjul.org/archive/view_article?pid=jul-27-1-27

## Preserve Exactly

Preserve syntax that is part of the game runtime or resource system:

- Placeholders and variables: `%s`, `%1$d`, `{name}`, `<tag>`, `\V[1]`.
- Code-like expressions and identifiers: `this.character()`, `EV003`,
  `HENTAI_progress`, `TMAnimeLight3`, `QueenKnight`.
- URLs, file names, asset paths, plugin names, script calls, and pure numeric or
  punctuation-only values.
- Button/key labels when used as controls, such as `[Shift]`, `(A)`, `LB`.

These tokens should be protected before model calls and restored afterward.
Quality checks should not flag them as untranslated English when they came from
the source.

## Translate Directly

Use concise direct translation for stable interface and gameplay concepts:

- Common UI actions: Continue, Save, Load, Options, Back, Cancel, Confirm.
- Common gameplay menu categories: Item, Skill, Weapon, Armor, Status, Level.
- Short labels where the source is a normal player-visible label and not a code
  token.

Only broad, cross-game UI/system terms belong in global deterministic rules.
Game-specific items, places, people, event titles, and story terms do not.

## Localize Or Rephrase

Use contextual localization rather than word-for-word translation for:

- Dialogue, narration, jokes, idioms, tone, honorifics, and emotional register.
- Adult, violent, or controversial content. Translate faithfully without refusal,
  sanitization, moral commentary, or omission.
- Gameplay terms that carry both mechanics and narrative meaning. Keep them
  consistent through the dynamic glossary after a validated translation appears.
- Ambiguous short strings. Prefer model translation with surrounding constraints
  over a global fixed mapping unless the term is a generic UI/system word.

## Dynamic Consistency

- Extract terminology from the current input file and model outputs.
- Confirm terms only when evidence is strong enough and the target passes review.
- Apply confirmed terms consistently within the same run and checkpoint.
- Do not use benchmark-file-specific dictionaries to improve speed.

## QA Implications

- Missing placeholders, symbols, brackets, or version markers are quality issues.
- Residual Japanese kana is a quality issue unless the preserved token is clearly
  non-linguistic.
- Residual English is a quality issue only when it is ordinary text, not a
  preserved source token.
- Long expansions for short labels are quality issues.
- A refusal or untranslated source fallback must remain visible in checkpoint
  issues for review.
