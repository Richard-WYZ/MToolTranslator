param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TestRoot = Join-Path $ProjectRoot "test_work\pytest"
$RunId = "run-{0:yyyyMMdd-HHmmss-fff}-{1}-{2}" -f (Get-Date), $PID, ([guid]::NewGuid().ToString("N").Substring(0, 8))
$RunRoot = Join-Path $TestRoot $RunId
$CacheRoot = Join-Path $RunRoot "cache"
$BaseTemp = Join-Path $RunRoot "pytest-temp"
$PreviousTestRoot = [Environment]::GetEnvironmentVariable("LOCAL_GAME_TRANSLATOR_TEST_ROOT", "Process")
$MinicondaPython = "C:\ProgramData\miniconda3\python.exe"
$Python = if (Test-Path -LiteralPath $MinicondaPython) { $MinicondaPython } else { "python" }

New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null
$null = New-Item -ItemType Directory -Path $RunRoot
$null = New-Item -ItemType Directory -Path $CacheRoot
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:LOCAL_GAME_TRANSLATOR_TEST_ROOT = $RunRoot

Push-Location $ProjectRoot
try {
    & $Python -m pytest -o "cache_dir=$CacheRoot" --basetemp="$BaseTemp" @PytestArgs
    exit $LASTEXITCODE
}
finally {
    if ($null -eq $PreviousTestRoot) {
        Remove-Item Env:LOCAL_GAME_TRANSLATOR_TEST_ROOT -ErrorAction SilentlyContinue
    } else {
        $env:LOCAL_GAME_TRANSLATOR_TEST_ROOT = $PreviousTestRoot
    }
    Pop-Location
}
