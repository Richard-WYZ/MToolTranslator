# Test Workspace

This directory owns generated test and benchmark data.

- `test_work/pytest/cache/` contains pytest cache data.
- `test_work/pytest/tmp/` contains pytest temporary files.
- Benchmark fixtures and reports may also be placed directly under `test_work/`.

Run unit tests with `tools\run_tests.ps1 -q`. Everything in this directory
except this file is ignored by Git.
