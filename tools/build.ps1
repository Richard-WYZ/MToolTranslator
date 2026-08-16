param(
    [string]$Version = "",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PyInstallerArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildRoot = Join-Path $ProjectRoot "build"
$WorkPath = Join-Path $BuildRoot "work"
$DistRoot = Join-Path $BuildRoot "dist"
$MinicondaPython = "C:\ProgramData\miniconda3\python.exe"
$Python = if (Test-Path -LiteralPath $MinicondaPython) { $MinicondaPython } else { "python" }

function Invoke-GitText {
    param([string[]]$GitArgs)

    $Output = & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $($GitArgs -join ' ')"
    }
    return (($Output | Out-String).Trim())
}

$RequestedVersion = $Version.Trim()
if ($RequestedVersion) {
    if ($RequestedVersion -notmatch '^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$') {
        throw "Version must use Semantic Versioning, for example 0.2.5 or v0.3.0-rc.1."
    }
    if ($RequestedVersion.StartsWith("v", [System.StringComparison]::OrdinalIgnoreCase)) {
        $RequestedVersion = $RequestedVersion.Substring(1)
    }

    $CurrentBranch = Invoke-GitText -GitArgs @("branch", "--show-current")
    if ($CurrentBranch -ne "master") {
        throw "Release packages must be built from master. Current branch: $CurrentBranch"
    }
    $WorkingTreeStatus = Invoke-GitText -GitArgs @("status", "--porcelain")
    if ($WorkingTreeStatus) {
        throw "Release packages require a clean master working tree."
    }
    $HeadCommit = Invoke-GitText -GitArgs @("rev-parse", "HEAD")
    $RemoteMasterCommit = Invoke-GitText -GitArgs @("rev-parse", "origin/master")
    if ($HeadCommit -ne $RemoteMasterCommit) {
        throw "Release packages require local master to match origin/master."
    }

    $VersionTag = "v$RequestedVersion"
    $ArtifactDirectoryName = "MToolTranslator-$VersionTag-windows-x64"
}
else {
    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $ArtifactDirectoryName = "MToolTranslator-dev-$Timestamp-windows-x64"
}

$DistPath = Join-Path $DistRoot $ArtifactDirectoryName
if (Test-Path -LiteralPath $DistPath) {
    throw "Build output already exists and will not be overwritten: $DistPath"
}

New-Item -ItemType Directory -Force -Path $WorkPath, $DistRoot | Out-Null
New-Item -ItemType Directory -Path $DistPath | Out-Null

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --noconfirm --workpath $WorkPath --distpath $DistPath build.spec @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    $Artifacts = @(Get-ChildItem -LiteralPath $DistPath -Force)
    $ExecutablePath = Join-Path $DistPath "MToolTranslator.exe"
    if ($Artifacts.Count -ne 1 -or -not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
        throw "Unexpected build output. The artifact directory must contain only MToolTranslator.exe: $DistPath"
    }

    Write-Output "Packaged application: $ExecutablePath"
    exit 0
}
finally {
    Pop-Location
}
