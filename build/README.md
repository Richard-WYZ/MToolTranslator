# Build Workspace

Run `tools\build.ps1` from the project root for an isolated development build.
It creates a unique `build/dist/MToolTranslator-dev-<timestamp>-windows-x64/`
directory.

For a release build, use `tools\build.ps1 -Version <MAJOR.MINOR.PATCH>` from a
clean `master` branch that matches `origin/master`. Release output is written to
`build/dist/MToolTranslator-v<version>-windows-x64/`.

- `build/work/` contains PyInstaller intermediate files.
- Each directory under `build/dist/` is isolated and contains only
  `MToolTranslator.exe` when packaging completes.
- Existing output directories and runtime files are never overwritten or
  cleaned automatically.

Everything in this directory except this file is generated and ignored by Git.
