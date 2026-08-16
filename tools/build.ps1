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

New-Item -ItemType Directory -Force -Path $WorkPath, $DistPath | Out-Null

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --noconfirm --workpath $WorkPath --distpath $DistPath build.spec @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    exit 0
}
finally {
    Pop-Location
}
