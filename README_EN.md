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

## Usage

### Portable Windows build

1. Download `MToolTranslator-vX.Y.Z-windows-x64.zip` from GitHub Releases.
2. Extract it to any writable directory. Installation is not required.
3. Run `MToolTranslator.exe`.
4. Enter an OpenCode Go API key in Settings, fetch the model catalog, and
   enable the models that may be used for translation.
5. Run the per-model basic and NSFW checks first. Tests send requests and may
   consume tokens.
6. Export `ManualTransFile.json` from MTool.
7. Import the JSON on the Translation page and start a translation profile.
8. Review progress and flagged entries. You can prioritize required-review
   items or run AI review with separately selected review and verifier models.
9. Once translation has finished and the output is structurally complete, you
   may export the current snapshot before clearing the review queue, continue
   reviewing, and export again before loading it through MTool.

Runtime files beside the executable include:

- `.env`: local connection settings and credentials; ignored by Git;
- `.model-status.json`: model test status and timestamps, never model replies;
- `.checkpoints/`: resumable translation state.

### Review and export

- The review workspace separates required review, advisory review, system-
  preserved content, and confirmed translations, so numbers and symbols do not
  crowd the actionable queue.
- AI review supports separate primary, verifier, and sensitive-content models.
  Only results that pass structural and quality validation are auto-applied.
- Refusal detection hard-blocks only explicit AI, policy, or inability-to-assist
  meta responses. Ordinary narrative wording such as "unsuitable" or "refused
  a request" is not rejected by keywords alone; ambiguous meta responses are
  advisory review items.
- AI review, single-entry saves, and batch confirmation lock conflicting
  controls and show a busy indicator to prevent duplicate submissions.
- Export is blocked while translation or AI review is writing. After the task
  reaches a structurally complete snapshot, export remains available even if
  review items remain.
- Export keeps the working copy, so review and repeated exports can continue.
  Desktop exit uses a native confirmation dialog and preserves checkpoints and
  temporary results.

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

The portable output is written to `build/dist/`.

## Current status

- Only the **OpenCode Go** integration path has received practical validation.
- A model appearing in the OpenCode Go catalog does not mean that its quality,
  NSFW behavior, or full-file reliability has been validated. Use the built-in
  basic and NSFW tests.
- Ollama, OpenAI-compatible APIs, and Anthropic Messages transports remain
  experimental and are not currently supported as validated production paths.
- Entries without Japanese text are preserved. English-to-Chinese and other
  translation directions are not supported yet.
- No API key is bundled. Credentials remain in the local `.env` file or are
  written there through the settings UI.

## Runtime and cost

Quality currently takes priority over speed. The latest validated external
fixture contained **14,655 entries**. Using **MiniMax M3 + Qwen 3.7 Plus**, its
translation phase completed in **253.396 seconds (about 4.22 minutes)**, with
about 4.48 minutes total wall time and **334,922 tokens**. Manual inspection
still found semantic alignment risk in short-text batches, so meeting the speed
target does not imply production quality.

The larger **61,978-entry** benchmark most recently required
**3476.365 seconds (about 57.94 minutes)** and **6,137,210 tokens**, still above
the 30-minute hard limit. This version adds semantic batch guards, executable-
code protection, context-contamination isolation, and an AI review workflow,
but a new full-scale live run has not yet validated every fix end to end.

In plain terms: this project is for people who do not mind waiting and have far
more tokens than they know what to do with. Do not rely on it yet if you need a
stable, fast, or inexpensive production tool.

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

The output preserves every source key, key order, and JSON shape. Only values
may change. Each entry ends as `translated`, `preserved`,
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

Copy `.env.example` to `.env`, or edit settings in the UI. Known endpoints are
filled automatically, while advanced connection fields stay collapsed by
default. Catalog discovery does not run inference. Basic and NSFW availability
tests send one short request per model and may consume tokens.

Test status is persisted by model ID. Newly discovered models start enabled and
untested; existing models retain history; changes to protocol, endpoint, key,
or Ollama host mark older results as stale references.

## References and acknowledgements

The workflow and engineering ideas were informed by the following projects and
services. This does not imply affiliation or direct code copying:

- [MTool](https://mtool.app/) for game-text export/import and runtime loading;
- [GalTransl](https://github.com/GalTransl/GalTransl) for batch visual-novel translation workflows;
- [SakuraLLM](https://github.com/SakuraLLM/SakuraLLM) for Japanese-to-Chinese game/novel translation research;
- [OpenCode Go](https://opencode.ai/docs/providers#opencode-go), the only third-party API path validated so far;
- [Ollama](https://ollama.com/) for the experimental local-model transport.

Their licenses and service terms apply independently.
