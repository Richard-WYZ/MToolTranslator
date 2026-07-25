param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TestRoot = Join-Path $ProjectRoot "test_work\pytest"
$MinicondaPython = "C:\ProgramData\miniconda3\python.exe"
$Python = if (Test-Path -LiteralPath $MinicondaPython) { $MinicondaPython } else { "python" }

New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null
$env:PYTHONDONTWRITEBYTECODE = "1"

Push-Location $ProjectRoot
try {
    & $Python -m pytest @PytestArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
