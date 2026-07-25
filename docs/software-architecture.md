# Software Architecture

## Purpose

MToolTranslator translates Japanese MTool JSON into Simplified Chinese.
The architecture separates user interaction, application lifecycle, translation
policy, and shared infrastructure so each localization step can evolve without
rewiring the whole program.

The canonical production layers are:

```text
ui -> app -> translation
      |          |
      +-> common <-+
```

Dependencies point inward. `translation` does not import `app` or `ui`, and
`common` does not import any upper layer.

## Directory Ownership

### `ui/`

Owns browser templates and static assets. It contains no translation policy,
checkpoint persistence, file parsing, or model transport.

### `app/`

Owns HTTP routes, desktop startup, request schemas, file upload/export, task
lifecycle, and the human proofreading interface.

- `app/main.py`: application composition root.
- `app/entrypoint.py`: CLI/desktop launch selection.
- `app/desktop.py`: desktop window lifecycle.
- `app/routes/`: HTTP adapters only.
- `app/services/`: application use cases and task coordination.
- `app/schemas/`: request contracts.

Routes validate HTTP concerns and delegate to services. They must not implement
translation, terminology, or checkpoint policy.

### `translation/`

Owns the complete localization domain and is the only canonical translation
implementation.

- `translation/translate.py`: public translation entrypoint and workflow
  composition.
- `translation/context.py`: workflow request, result, and context contracts.
- `translation/runtime.py`: controllable workflow adapter for app tasks.
- `translation/settings.py`: translation defaults and `.env` loading.
- `translation/workflow/`: ordered process stages and orchestration.
- `translation/analysis/`: deterministic source-file analysis.
- `translation/classification/`: preservation and model-bound classification.
- `translation/terminology/`: glossary state, candidate policy, persistence,
  aliases, dictionary providers, and confirmed-term backfill.
- `translation/protection/`: placeholders and protected-token restoration.
- `translation/batching/`: payloads, protocol choice, routing, scheduling,
  candidate windows, result application, and batch finishing.
- `translation/models/`: provider-neutral routing plus provider transports.
- `translation/quality/`: validation, issue generation, status selection,
  constraints, refusal checks, and quality retries.
- `translation/pollution/`: glossary and output pollution detection.
- `translation/checkpoint/`: resumable state and progress persistence.
- `translation/output/`: JSON serialization, output paths, and background writer.
- `translation/review/`: post-translation proofreading handoff summaries.
- `translation/diagnostics/`: explicit adapters for benchmarks and profilers.

### `common/`

Owns infrastructure that is independent of translation policy and application
transport, such as runtime paths and general file helpers.

## Translation Entrypoint

All normal translation runs enter through:

```python
from translation.translate import TranslationRequest, translate

result = translate(TranslationRequest(file_path="game.json"))
```

`translation.translate` only composes and runs modules. Translation rules,
provider calls, JSON loops, persistence, and UI behavior do not belong there.

Application tasks use `TranslationRuntime`. It creates a controllable pipeline
for pause/resume/cancel, injects that pipeline as a workflow resource, and then
runs the same canonical workflow. There is no separate desktop translation
path.

## Workflow

The default workflow is explicit and ordered:

```text
MToolAnalysisStage
  -> PipelineBuildStage
  -> TranslationStage
  -> ReviewPreparationStage
```

### 1. Analysis

`translation/workflow/analysis.py` classifies the MTool file before execution
and resolves the dynamic glossary path. Analysis output is stored in
`TranslationWorkflowContext.analysis`.

### 2. Runtime Construction

`translation/workflow/execution.py` creates the canonical pipeline resource.
If `TranslationRuntime` already supplied a controllable pipeline, the stage
reuses it. Runtime objects are stored in `TranslationWorkflowContext.resources`.

### 3. Translation

`TranslationStage` invokes the pipeline facade. The facade preserves a stable
extension surface while delegating work to focused modules:

- `file_entry.py`: file validation, cancellation reset, and usage reset.
- `json_flow.py`: top-level JSON orchestration and resume handling.
- `json_batch.py`: sequential batch workflow.
- `json_parallel.py`: API-parallel batch workflow.
- `batch_adapter.py`: batching compatibility adapters.
- `cell.py`: one-entry translation and validation flow.
- `cell_services.py`: cell dependency assembly.
- `translation_adapter.py`: prompt, fallback, protection, and pollution wiring.
- `runtime_adapter.py`: control, progress, writer, usage, and checkpoint effects.

Model transports remain under `translation/models/`. Workflow and quality code
must remain provider-neutral.

### 4. Proofreading Handoff

`translation/workflow/review.py` builds a `ReviewSummary` after translation.
It reports translated, preserved, needs-review, review-required, pending, and
issue-bearing entries. The summary is attached to `TranslationResult` and is
exposed by application task progress.

This stage prepares a human proofreading queue; it never claims that human
review has occurred. The review UI reads translations and checkpoint issues,
supports filtering and edits, and writes approved changes through application
services.

## Terminology Management

`Glossary` is the stateful terminology aggregate. Its responsibilities are
limited to confirmed terms, candidates, conflicts, promotion, matching, and
workflow state transitions.

Focused terminology modules own the details:

- `candidate_policy.py`: extraction, name detection, type classification,
  scoring, evidence thresholds, target validation, and alias generation.
- `store.py`: versioned JSON read/write and legacy payload parsing.
- `aliases.py`: applying approved aliases to translated output.
- `backfill.py`: revalidating outputs affected by confirmed-term changes.
- `dictionary.py`: optional offline dictionary evidence.

Candidate states are `candidate`, `confirmed`, `official`, `needs_review`, and
`rejected`. Only confirmed and official terms may be enforced. Common nouns and
unsupported contextual phrases must not be auto-confirmed.

## Quality Contract

Each eligible entry ends in one of four canonical statuses:

- `translated`
- `preserved`
- `translated_needs_review`
- `review_required`

Quality validation occurs before a model output is accepted. Checkpoints retain
issues and review reasons, so proofreading is driven by explicit state rather
than by comparing source and target values.

The final MTool output preserves source keys, order, and JSON shape. Only values
may change. Protected tokens, identifiers, numbers, and required line breaks
are validated before acceptance.

## Configuration

`translation/settings.py` owns mutable defaults and `.env` loading.
`translation/config.py` exposes focused accessors used by domain modules.
Provider credentials remain local and must not be committed.

Packaged builds look for `.env` beside the executable before checking the
working directory and project root.

## Compatibility Surface

The following paths exist only for older tests and callers:

- root `main.py`, `desktop.py`, and `config.py`
- `translator/`
- `parser/`
- `app/legacy_main.py`
- `translation/workflow/legacy_pipeline.py`

Compatibility modules alias or re-export canonical objects. They must not own
business logic, be imported by canonical layers, or be listed as production
implementations in the build configuration.

New code uses `app`, `translation`, and `common` imports exclusively.

## Build Contract

`build.spec` packages UI assets and canonical `app`/`translation` modules.
It does not package `translator`, `parser`, or root `config.py` as production
implementations. PyInstaller hidden imports are reserved for genuinely dynamic
canonical imports.

## Engineering Gates

`tests/test_translation_quality_architecture.py` enforces:

- canonical layers do not import `translator`, `parser`, or root `config`;
- workflow stages run in the required order;
- app runtime executes the canonical workflow;
- legacy modules remain thin compatibility facades;
- translation policy lives in focused domain modules;
- terminology delegates candidate policy and storage;
- checkpoint, input/output, model, and runtime adapters share canonical state;
- build configuration references canonical packages.

Every policy or workflow change needs focused regression coverage. Before a
refactor is considered complete, run:

```powershell
tools\run_tests.ps1 -q
```

Generated pytest state stays under `test_work/pytest/`. Windows packaging must
run through `tools/build.ps1`, which keeps all intermediates and distributions
under `build/`.

## Extension Rules

To add a localization process step:

1. Add a focused module under `translation/workflow/`.
2. Give the stage an explicit context input/output contract.
3. Register it in `translation.translate.build_workflow()`.
4. Add ordering and behavior tests.
5. Keep transport, UI, and persistence details behind their owning modules.

To add a provider, implement transport under `translation/models/` and expose it
through the model router. Do not branch on providers in workflow, glossary,
classification, pollution, or quality modules.

To add UI behavior, update `ui/` and an `app/routes` or `app/services` adapter.
Do not call pipeline private methods from the UI or route layer.
