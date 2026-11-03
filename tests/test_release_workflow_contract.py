from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def root_section(text: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}:\n(.*?)(?=^\S|\Z)", text)
    assert match, f"Missing workflow root section: {name}"
    return match.group(1)


def test_release_preparation_is_manual_read_only_and_cannot_publish():
    text = WORKFLOW.read_text(encoding="utf-8")
    triggers = re.findall(r"^  ([a-z_]+):", root_section(text, "on"), re.MULTILINE)
    assert triggers == ["workflow_dispatch"]
    assert re.search(r"version:\n\s+description:.*\n\s+required: true\n\s+type: string", text)
    assert root_section(text, "permissions").strip() == "contents: read"
    assert not re.search(r"\bgh\s+release\b|\bgit\s+(?:tag|push)\b", text)
    assert "GH_TOKEN" not in text
    assert "contents: write" not in text


def test_workflow_requires_the_confirmed_version_and_clean_synced_master():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "RELEASE_VERSION: ${{ inputs.version }}" in text
    assert '$env:GITHUB_REF -ne "refs/heads/master"' in text
    assert "ref: master" in text
    assert "persist-credentials: false" in text
    assert "Get-Content -LiteralPath VERSION -Raw" in text
    assert "$version -ne $recordedVersion" in text
    assert "git branch --show-current" in text and '$branch -ne "master"' in text
    assert "git status --porcelain" in text and "$LASTEXITCODE -ne 0 -or $status" in text
    assert "git rev-parse HEAD" in text and "git rev-parse origin/master" in text
    assert "$headCommit -ne $remoteMasterCommit" in text
    assert "MAJOR.MINOR.PATCH" in text and "$version -notmatch" in text
    # Dispatch text enters PowerShell through a validated environment value,
    # never direct expression interpolation into executable script content.
    scripts = re.findall(r"(?m)^        run: \|\n((?:          .*\n|\n)+)", text)
    assert scripts
    assert all("${{ inputs.version }}" not in script for script in scripts)


def test_workflow_tests_then_builds_the_isolated_version_artifact_without_dotenv():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.index("tools\\run_tests.ps1 -q") < text.index("tools\\build.ps1 -Version $env:RELEASE_VERSION")
    assert '"artifact_name=MToolTranslator-v$version-windows-x64"' in text
    assert '$artifactDirectory = Join-Path "build\\dist" $env:ARTIFACT_NAME' in text
    assert '$executable = Join-Path $artifactDirectory "MToolTranslator.exe"' in text
    assert "$files.Count -ne 1" in text
    assert 'Test-Path -LiteralPath $executable -PathType Leaf' in text
    assert ".env" not in text
    assert "build\\dist\\MToolTranslator.exe" not in text
    assert "Compress-Archive -LiteralPath $executable -DestinationPath $archive" in text
    assert "must not be overwritten" in text


def test_uploaded_zip_is_verified_against_the_exe_and_has_a_sha256_sidecar():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '[System.IO.Compression.ZipFile]::OpenRead($archive)' in text
    assert '$zip.Entries.Count -ne 1' in text
    assert '$zip.Entries[0].FullName -ne "MToolTranslator.exe"' in text
    assert "ComputeHash($entryStream)" in text
    assert "Get-FileHash -LiteralPath $executable -Algorithm SHA256" in text
    assert "$entryHash -ne $executableHash" in text
    assert "Get-FileHash -LiteralPath $archive -Algorithm SHA256" in text
    assert '$checksum = "$archive.sha256"' in text
    assert "uses: actions/upload-artifact@v4" in text
    assert "${{ steps.package.outputs.archive }}" in text
    assert "${{ steps.package.outputs.checksum }}" in text
    assert "if-no-files-found: error" in text
    assert text.index("$entryHash -ne $executableHash") < text.index("uses: actions/upload-artifact@v4")
