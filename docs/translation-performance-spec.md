# Translation Performance And Quality Spec

## Target

Translate `test_work/ManualTransFile.json` from a clean checkpoint with high-quality Simplified Chinese output. Five minutes is the stretch target; the hard acceptance limit is under 30 minutes.

## Current Baseline Facts

- Non-empty entries: 61977.
- Duplicate entries: 0 in the current sample, so cache hits cannot solve throughput.
- Current deterministic coverage: 6103 entries, including non-Japanese resources, code, URLs, plugin fields, numeric IDs, and fixed UI terms.
- Current model-bound entries: 55874.
- Per-cell model calls cannot meet the time target.

## Required Strategy

1. Run deterministic rules first.
2. Batch remaining MTool/JSON entries by prompt profile. Prefer stable medium batches over oversized batches that risk item drift.
3. Use an uncensored, localization-specific batch prompt for adult/NSFW labels and descriptions.
4. Parse batch output as structured JSON and validate item counts/indexes.
5. Fall back only failed batch items to smaller batches or per-cell translation.
6. Store quality issues in checkpoint entries.
7. Keep glossary confirmation conservative; use confirmed terms in prompts but do not auto-confirm generic labels.

## Quality Gates

Each translated cell should be checked for:

- Remaining Japanese kana.
- Ordinary English residue.
- Suspicious model artifacts.
- Lost version/bracket markers.
- Excessive expansion, especially for short labels.
- Missing confirmed terminology.

## Benchmark Commands

Dry-run classification, no model calls:

```bash
python tools/benchmark_manual_trans_file.py
```

Small real-model sample after Ollama is running:

```bash
python tools/benchmark_manual_trans_file.py --sample-size 40 --batch-size 40 --max-batch-chars 4000
python tools/benchmark_manual_trans_file.py --sample-size 80 --batch-size 80 --max-batch-chars 8000
```

Use the reported `estimated_full_minutes`, `refusal_count`, and `issue_counts` to choose batch settings. Prefer the largest batch that keeps parsing stable and quality issue rates low. On the current qwen3:4b-instruct setup, 40/4000 is the quality-first default; 80-item batches are slower on this machine, and 160-item batches have shown missing indexes.

## Architecture Direction

- `label_rules`: classification and deterministic translations.
- `quality`: output issue detection.
- `batch`: batch prompt construction, parsing, and validation.
- `pipeline`: translation orchestration and persistence.
- API/UI layers should not contain translation policy.
