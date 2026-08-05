[CmdletBinding()]
param(
    [switch]$InstallOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$RequirementsPath = Join-Path $RepoRoot "requirements.txt"
$BloombergRequirementsPath = Join-Path $RepoRoot "requirements-bloomberg.txt"
$RuntimeCheckPath = Join-Path $PSScriptRoot "check_runtime_compatibility.py"
$BuildDashboardPath = Join-Path $PSScriptRoot "build_dashboard.py"
$EmbeddedDataPath = Join-Path $RepoRoot "app\static\embedded_data.js"
$SampleDataPath = Join-Path $RepoRoot "data\sample_market_data.parquet"
$RootConfigPath = Join-Path $RepoRoot "config\security_roots.xlsx"
$BloombergIndexUrl = "https://blpapi.bloomberg.com/repository/releases/python/simple/"
if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    throw "USERPROFILE is not set; the managed Trade Builder environment cannot be located."
}
$VenvRoot = Join-Path $env:USERPROFILE "Pyenvs"
$VenvPath = Join-Path $VenvRoot "trade_builder"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$ActivateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
$SetupStamp = Join-Path $VenvPath ".requirements.sha256"
$CacheRoot = Join-Path $VenvPath "cache"
$VersionCheck = @"
import sys
ok = sys.version_info[:2] in {(3, 12), (3, 13)}
print(sys.version.split()[0])
raise SystemExit(0 if ok else 1)
"@

function Get-CompatiblePython {
    $Candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($Version in @("3.13", "3.12")) {
            $Candidates += [PSCustomObject]@{
                Command = "py"
                Prefix = @("-$Version")
                Label = "py -$Version"
            }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $Candidates += [PSCustomObject]@{
            Command = "python"
            Prefix = @()
            Label = "python"
        }
    }

    foreach ($Candidate in $Candidates) {
        try {
            $Arguments = @($Candidate.Prefix) + @("-c", $VersionCheck)
            $Output = & $Candidate.Command @Arguments 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [PSCustomObject]@{
                    Command = $Candidate.Command
                    Prefix = $Candidate.Prefix
                    Label = $Candidate.Label
                    Version = ($Output | Select-Object -First 1)
                }
            }
        } catch {
            continue
        }
    }
    return $null
}

function Test-CompatiblePython([string]$PythonPath) {
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        return $false
    }
    try {
        & $PythonPath -c $VersionCheck *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-Pip([string]$PythonPath) {
    try {
        & $PythonPath -m pip --version *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-ManagedDependencies([string]$PythonPath) {
    try {
        & $PythonPath -m pip check *> $null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        & $PythonPath $RuntimeCheckPath *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Remove-SetupStamp {
    Remove-Item -LiteralPath $SetupStamp -Force -ErrorAction SilentlyContinue
}

function Remove-ManagedEnvironment {
    if (-not (Test-Path -LiteralPath $VenvPath)) {
        return
    }
    $FullVenv = [System.IO.Path]::GetFullPath($VenvPath)
    $AllowedRoot = [System.IO.Path]::GetFullPath($VenvRoot)
    $Prefix = $AllowedRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $FullVenv.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to recreate a Python environment outside $AllowedRoot"
    }
    Write-Host "Rebuilding managed environment: $FullVenv"
    Remove-Item -LiteralPath $FullVenv -Recurse -Force
}

function New-ManagedEnvironment {
    $BasePython = Get-CompatiblePython
    if ($null -eq $BasePython) {
        throw "Python 3.13 or 3.12 was not found. Install 64-bit Python from python.org and rerun UPDATE_AND_OPEN.bat."
    }
    New-Item -ItemType Directory -Path $VenvRoot -Force | Out-Null
    Write-Host "Creating $VenvPath with $($BasePython.Label) (Python $($BasePython.Version))"
    $Arguments = @($BasePython.Prefix) + @("-m", "venv", $VenvPath)
    & $BasePython.Command @Arguments
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        throw "Python virtual environment creation failed."
    }
}

function Repair-Pip {
    if (Test-Pip $VenvPython) {
        return
    }
    Remove-SetupStamp
    Write-Host "pip is missing; repairing it with the managed Python interpreter..."
    & $VenvPython -m ensurepip --upgrade
    if ($LASTEXITCODE -eq 0 -and (Test-Pip $VenvPython)) {
        return
    }

    Remove-ManagedEnvironment
    New-ManagedEnvironment
    & $VenvPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0 -or -not (Test-Pip $VenvPython)) {
        throw "pip repair failed after rebuilding $VenvPath."
    }
}

function Install-ManagedDependencies {
    Remove-SetupStamp
    Write-Host "Installing Polars and all dashboard packages from requirements.txt..."
    & $VenvPython -m pip install --disable-pip-version-check --upgrade -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard package installation failed."
    }

    Write-Host "Installing Bloomberg BLPAPI from Bloomberg's official package index..."
    # Official Bloomberg command:
    # python -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
    & $VenvPython -m pip install "--index-url=$BloombergIndexUrl" blpapi
    if ($LASTEXITCODE -ne 0) {
        throw "Bloomberg BLPAPI installation failed from $BloombergIndexUrl"
    }

    & $VenvPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "Installed Python dependencies are inconsistent."
    }
    & $VenvPython $RuntimeCheckPath
    if ($LASTEXITCODE -ne 0) {
        throw "Bloomberg or Polars could not load from $VenvPath."
    }
}

function Ensure-EmbeddedDashboardData {
    $NeedsBuild = -not (Test-Path -LiteralPath $EmbeddedDataPath)
    if (-not $NeedsBuild) {
        $EmbeddedTimestamp = (Get-Item -LiteralPath $EmbeddedDataPath).LastWriteTimeUtc
        foreach ($SourcePath in @($SampleDataPath, $RootConfigPath)) {
            if ((Get-Item -LiteralPath $SourcePath).LastWriteTimeUtc -gt $EmbeddedTimestamp) {
                $NeedsBuild = $true
                break
            }
        }
    }
    if (-not $NeedsBuild) {
        return
    }

    Write-Host "Building the initial embedded dashboard data..."
    Push-Location $RepoRoot
    try {
        & $VenvPython $BuildDashboardPath
        $BuildExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($BuildExitCode -ne 0 -or -not (Test-Path -LiteralPath $EmbeddedDataPath)) {
        throw "Initial dashboard data build failed."
    }
}

foreach ($RequiredPath in @($RequirementsPath, $BloombergRequirementsPath, $RuntimeCheckPath, $BuildDashboardPath, $SampleDataPath, $RootConfigPath)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required setup file was not found: $RequiredPath"
    }
}

if ((Test-Path -LiteralPath $VenvPython) -and -not (Test-CompatiblePython $VenvPython)) {
    Remove-SetupStamp
    Remove-ManagedEnvironment
}
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Remove-SetupStamp
    New-ManagedEnvironment
}
Repair-Pip

$RequirementsHash = @(
    (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash
    (Get-FileHash -LiteralPath $BloombergRequirementsPath -Algorithm SHA256).Hash
    (Get-FileHash -LiteralPath $RuntimeCheckPath -Algorithm SHA256).Hash
) -join ":"
$InstalledHash = if (Test-Path -LiteralPath $SetupStamp) {
    (Get-Content -LiteralPath $SetupStamp -Raw).Trim()
} else {
    ""
}

if ($InstalledHash -ne $RequirementsHash -or -not (Test-ManagedDependencies $VenvPython)) {
    Install-ManagedDependencies
    Set-Content -LiteralPath $SetupStamp -Value $RequirementsHash -Encoding ASCII
}

New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $ActivateScript)) {
    throw "The managed activation script is missing: $ActivateScript"
}
. $ActivateScript
$env:VIRTUAL_ENV = $VenvPath
$env:PATH = (Join-Path $VenvPath "Scripts") + ";" + $env:PATH
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:PYTHONPYCACHEPREFIX = Join-Path $CacheRoot "pycache"
$env:PYTHONUTF8 = "1"

Write-Host "Trade Builder environment ready: $VenvPath"
& $VenvPython $RuntimeCheckPath
if ($LASTEXITCODE -ne 0) {
    throw "Managed runtime validation failed."
}

Ensure-EmbeddedDashboardData

if ($InstallOnly) {
    exit 0
}

Push-Location $RepoRoot
try {
    & $VenvPython (Join-Path $RepoRoot "scripts\run_dashboard.py") --open
    $ExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($ExitCode -ne 0) {
    throw "Pricing Dashboard stopped with exit code $ExitCode."
}
