# MToolTranslator

English | [简体中文](README.md)

[![CI](https://github.com/Richard-WYZ/MToolTranslator/actions/workflows/ci.yml/badge.svg)](https://github.com/Richard-WYZ/MToolTranslator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

MToolTranslator is an experimental Japanese game-localization tool built around
the [MTool](https://mtool.app/) workflow. It reads flat JSON exported by MTool,
translates Japanese values into Simplified Chinese, preserves source keys and
syntax, and provides dynamic terminology, resumable checkpoints, validation,
and a human-review UI.

> [!WARNING]
> The current version supports **Japanese to Simplified Chinese only**. It is
> experimental and does not guarantee translation quality, speed, model
> availability, or compatibility with every MTool file. Back up the game and
> original translation files before use.

## Relationship with MTool

This is not an official MTool project and is not affiliated with, endorsed by,
or authorized by the MTool author or team.

MTool provides several translation engines, and some advanced engines or
features require a higher supporter level. Supporting the MTool author can
raise that level and unlock more translation options, which may provide better
quality or faster results than this project. Please support the original author
if you can.

If the original MTool author does not want this project to be published, I
will make this repository private.

Only translate content that you own or are authorized to process, and follow
the licenses and laws applicable to the game, model, API provider, and your
location.

## AI vibe-coding disclosure

All code, UI, tests, and documentation in this project were generated through
AI coding agents in a vibe-coding workflow. The repository maintainer supplied
the goals, authorized tests, selected approaches, and accepted results. The
project has not received a complete professional code review, security audit,
or production-grade validation. Read and verify the code before relying on it.

Before contributing, read the [contribution guide](CONTRIBUTING.md). Routine
development takes place on `dev`; `master` is reserved for stable releases.

## Usage

### Portable Windows build

1. Download the Windows x64 ZIP from [Releases](https://github.com/Richard-WYZ/MToolTranslator/releases).
2. Extract it to a writable directory and open `MToolTranslator.exe`.
3. In Settings, add a provider, enter its endpoint and API key, then confirm
   the key. Fetch the model list and choose a default model.
4. Export `ManualTransFile.json` from MTool, import it into MToolTranslator,
   and start the translation.
5. Review the translated text, then use **Save As** to export the JSON and
   load it in MTool.

### Review and export

Search by source text or translation and use the status filters to find entries
that need attention. Edits autosave as drafts. Confirm an entry once you have
reviewed it. You can also run AI review before exporting.

Project history lets you reopen a working copy and resume from a checkpoint.
Once translation or AI review has finished and the output is structurally
complete, you can export a copy, continue reviewing, and export again.

### Run from source

Windows and Python 3.10+ are required. CI currently uses Python 3.12.

```powershell
python -m pip install -r requirements.txt
python main.py
```

Open `http://127.0.0.1:8000`, or launch desktop mode:

```powershell
python main.py --desktop
```

Tests and packaging:

```powershell
python -m pip install -r requirements-dev.txt
tools\run_tests.ps1 -q
tools\build.ps1
```

Development builds are written to isolated
`build/dist/MToolTranslator-dev-<timestamp>-windows-x64/` directories. A release
build must run from a clean `master` matching `origin/master`:

```powershell
tools\build.ps1 -Version 0.2.6
```

Release output is written to
`build/dist/MToolTranslator-v0.2.6-windows-x64/` and initially contains only
`MToolTranslator.exe`. Existing output directories and their runtime files are
never overwritten or cleaned automatically.

## How it works

```text
MTool exports ManualTransFile.json
               |
               v
  classify and preserve code/resources
               |
               v
 protect control codes, variables, tags, terms
               |
               v
 batch by text category and request budget
               |
               v
 validate structure, detect pollution, retry
               |
               v
 checkpoints/review + MTool-compatible JSON
```

The output preserves every source key, key order, and JSON shape. Entries
without Japanese text keep their original values. Each entry ends as `translated`, `preserved`,
`translated_needs_review`, or `review_required`.

## Architecture

```text
ui/           browser UI, progress, settings, and review
app/          FastAPI routes, desktop lifecycle, application services
translation/  classification, glossary, protection, batching, models, quality
common/       paths and provider-neutral infrastructure
tests/        required unit tests and generic CSV fixtures
tools/        testing, packaging, auditing, and profiling tools
```

The dependency direction is `ui -> app -> translation`; `common` provides
shared infrastructure. Model clients handle transport only, while
classification, terminology, and quality policy stay provider-neutral. See
[Software Architecture](docs/software-architecture.md) for details.

## Models and configuration

Use Settings to add or switch provider profiles. Each profile keeps its own
endpoint, API key, model list, and default model. The app saves settings
automatically and asks you to confirm when replacing or clearing a key.

Connection settings and credentials are stored in `.env`. For the portable
build, keep this file next to `MToolTranslator.exe`. When running from source,
keep it in the project root. You can reuse an existing `.env` or start from
`.env.example`. Keep your configured `.env` private.

## References and acknowledgements

- [MTool](https://mtool.app/) provides the game-text export, import, and runtime loading workflow.
- [GalTransl](https://github.com/GalTransl/GalTransl) informed the batch translation and terminology workflows.

Their licenses and terms apply independently.
