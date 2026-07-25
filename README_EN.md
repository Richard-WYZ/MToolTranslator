# MToolTranslator

English | [简体中文](README.md)

[![CI](https://github.com/Richard-WYZ/MToolTranslator/actions/workflows/ci.yml/badge.svg)](https://github.com/Richard-WYZ/MToolTranslator/actions/workflows/ci.yml)

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

Quality currently takes priority over speed. One representative 580-entry
sample completed in about **169 seconds**. On 2026-07-25, the Scheme 8 hybrid
route using **MiniMax M3 + Qwen 3.7 Plus** completed all **61,978 entries** in
`ManualTransFile.json`. The latest complete record reports
**4000.095 seconds (about 66.7 minutes)** for the translation phase and about
5.4 million tokens. It produced a complete output, but retained 1,201 entries
in the review queue, while an independent audit flagged 904 issue entries. It
should therefore not be treated as a finished translation requiring no human
review.

Two older results are easy to confuse with this run. A fast, MiniMax-heavy
route completed the full file in about **28 minutes**, but produced broad
alignment and review problems. An earlier quality-oriented pipeline completed
only about 20,278 entries in four hours; that was not the final Scheme 8 result.
These figures measure only the model translation phase. Analysis, glossary
processing, checkpoint I/O, final validation, and human review increase the
real wall-clock time. Actual performance also varies with the text, model,
concurrency, network, and provider rate limits.

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
8. Review progress and flagged entries, export the completed JSON, and load it
   through MTool.

Runtime files beside the executable include:

- `.env`: local connection settings and credentials; ignored by Git;
- `.model-status.json`: model test status and timestamps, never model replies;
- `.checkpoints/`: resumable translation state.

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

## Models and configuration

Copy `.env.example` to `.env`, or edit settings in the UI. Known endpoints are
filled automatically, while advanced connection fields stay collapsed by
default. Catalog discovery does not run inference. Basic and NSFW availability
tests send one short request per model and may consume tokens.

Test status is persisted by model ID. Newly discovered models start enabled and
untested; existing models retain history; changes to protocol, endpoint, key,
or Ollama host mark older results as stale references.

## CI and releases

- Pushes and pull requests targeting `master` or `dev` run GitHub Actions tests.
- Updating `VERSION` and merging it into `master` runs the full tests, builds
  the portable Windows package, creates a `vX.Y.Z` tag, and publishes a release.
- GitHub generates release notes from merged pull requests, labels, and commits.

Normal development happens on `dev`. Reviewed work is merged into `master`;
release changes also update `VERSION`.

## References and acknowledgements

The workflow and engineering ideas were informed by the following projects and
services. This does not imply affiliation or direct code copying:

- [MTool](https://mtool.app/) for game-text export/import and runtime loading;
- [GalTransl](https://github.com/GalTransl/GalTransl) for batch visual-novel translation workflows;
- [SakuraLLM](https://github.com/SakuraLLM/SakuraLLM) for Japanese-to-Chinese game/novel translation research;
- [OpenCode Go](https://opencode.ai/docs/providers#opencode-go), the only third-party API path validated so far;
- [Ollama](https://ollama.com/) for the experimental local-model transport.

Their licenses and service terms apply independently.

## AI vibe-coding disclosure

All code, UI, tests, and documentation in this project were generated through
AI coding agents in a vibe-coding workflow. The repository maintainer supplied
the goals, authorized tests, selected approaches, and accepted results. The
project has not received a complete professional code review, security audit,
or production-grade validation. Read and verify the code before relying on it.

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

## License

Project code is released under the [MIT License](LICENSE). Third-party
dependencies, models, and services remain subject to their own licenses; the
MIT License does not cover them.
