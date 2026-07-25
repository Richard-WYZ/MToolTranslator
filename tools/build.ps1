param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PyInstallerArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildRoot = Join-Path $ProjectRoot "build"
$WorkPath = Join-Path $BuildRoot "work"
$DistPath = Join-Path $BuildRoot "dist"
$MinicondaPython = "C:\ProgramData\miniconda3\python.exe"
$Python = if (Test-Path -LiteralPath $MinicondaPython) { $MinicondaPython } else { "python" }
$PortableEnvPath = Join-Path $DistPath ".env"
$PortableEnvTemplate = @"
# MTool 汉化工具 portable configuration
# This file is safe to edit in the application or with a text editor.

MODEL_PROVIDER=api
THIRD_PARTY_API_STYLE=opencode_go
DEFAULT_MODEL=api:qwen3.7-plus
THIRD_PARTY_API_BASE_URL=
THIRD_PARTY_API_KEY=
THIRD_PARTY_API_MODELS=
THIRD_PARTY_API_DISABLED_MODELS=
THIRD_PARTY_API_DISABLE_THINKING=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_DISABLED_MODELS=
"@

New-Item -ItemType Directory -Force -Path $WorkPath, $DistPath | Out-Null

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --noconfirm --workpath $WorkPath --distpath $DistPath build.spec @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    if (-not (Test-Path -LiteralPath $PortableEnvPath)) {
        Set-Content -LiteralPath $PortableEnvPath -Value $PortableEnvTemplate -Encoding utf8
    }
    exit 0
}
finally {
    Pop-Location
}
