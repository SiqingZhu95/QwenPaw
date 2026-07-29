<#
.SYNOPSIS
Build the frontend, install the local Python package, and start QwenPaw.

.EXAMPLE
.\build-and-start.ps1
Starts app mode on port 9000 with debug logging.

.EXAMPLE
.\build-and-start.ps1 app 8088 info
Starts app mode on port 8088 with info logging.

.EXAMPLE
.\build-and-start.ps1 desktop -LogLevel info
Starts desktop mode with info logging. The desktop command selects its own port.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("app", "desktop")]
    [string]$Mode = "app",

    [Parameter(Position = 1)]
    [ValidateRange(1, 65535)]
    [int]$Port = 9000,

    [Parameter(Position = 2)]
    [ValidateSet("critical", "error", "warning", "info", "debug", "trace")]
    [string]$LogLevel = "debug"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = $PSScriptRoot
$FrontendDir = Join-Path $ProjectRoot "console"
$FrontendDist = Join-Path $FrontendDir "dist"
$ConsoleTarget = Join-Path $ProjectRoot "src\qwenpaw\console"
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvActivate = Join-Path $VenvDir "Scripts\Activate.ps1"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$QwenPawExe = Join-Path $VenvDir "Scripts\qwenpaw.exe"

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Assert-LastExitCode {
    param([string]$Action)
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed (exit code: $LASTEXITCODE)."
    }
}

try {
    Set-Location $ProjectRoot

    Write-Step "Installing frontend dependencies and building the console"
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm was not found. Install Node.js/npm and add npm to PATH first."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "package.json"))) {
        throw "Frontend package file was not found: $FrontendDir\package.json"
    }

    Push-Location $FrontendDir
    try {
        npm ci
        Assert-LastExitCode "npm ci"

        npm run build
        Assert-LastExitCode "npm run build"
    }
    finally {
        Pop-Location
    }

    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDist "index.html"))) {
        throw "Frontend build output is incomplete: $FrontendDist\index.html was not found."
    }

    Write-Step "Replacing src\qwenpaw\console with the new frontend build"
    if (Test-Path -LiteralPath $ConsoleTarget) {
        Remove-Item -LiteralPath $ConsoleTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ConsoleTarget -Force | Out-Null
    Copy-Item -Path (Join-Path $FrontendDist "*") -Destination $ConsoleTarget -Recurse -Force

    Write-Step "Leaving the active Conda environment"
    $CondaLevel = 0
    if ($env:CONDA_SHLVL -and [int]::TryParse($env:CONDA_SHLVL, [ref]$CondaLevel)) {
        if ($CondaLevel -gt 0) {
            if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
                throw "A Conda environment is active, but the conda command is unavailable."
            }

            while ($CondaLevel -gt 0) {
                $PreviousLevel = $CondaLevel
                conda deactivate

                $CondaLevel = 0
                if ($env:CONDA_SHLVL) {
                    [void][int]::TryParse($env:CONDA_SHLVL, [ref]$CondaLevel)
                }
                if ($CondaLevel -ge $PreviousLevel) {
                    throw "Conda environment deactivation did not succeed."
                }
            }
        }
        else {
            Write-Host "No active Conda environment."
        }
    }
    else {
        Write-Host "No active Conda environment."
    }

    Write-Step "Activating the project virtual environment"
    if (-not (Test-Path -LiteralPath $VenvActivate)) {
        throw "Project virtual environment was not found: $VenvDir"
    }
    & $VenvActivate

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "Python executable was not found in the project virtual environment."
    }

    Write-Step "Installing QwenPaw in editable mode"
    & $VenvPython -m pip install -e $ProjectRoot
    Assert-LastExitCode "python -m pip install -e ."

    if (-not (Test-Path -LiteralPath $QwenPawExe)) {
        throw "QwenPaw executable was not created in the project virtual environment."
    }

    if ($Mode -eq "app") {
        Write-Step "Starting QwenPaw app at http://127.0.0.1:$Port (log level: $LogLevel)"
        & $QwenPawExe app --port $Port --log-level $LogLevel
    }
    else {
        Write-Step "Starting QwenPaw desktop (log level: $LogLevel)"
        & $QwenPawExe desktop --log-level $LogLevel
    }
    Assert-LastExitCode "qwenpaw $Mode"
}
catch {
    Write-Host "`nBuild/start failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Set-Location $ProjectRoot
}
