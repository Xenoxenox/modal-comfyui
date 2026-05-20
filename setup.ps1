Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$TsinghuaIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-FlagVariable {
    param([string]$Name)
    $variable = Get-Variable -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $variable) {
        return $false
    }
    return [bool]$variable.Value
}

$IsWindowsOs = (Test-FlagVariable "IsWindows") -or ($env:OS -eq "Windows_NT")
$IsLinuxOs = Test-FlagVariable "IsLinux"
$IsMacOs = Test-FlagVariable "IsMacOS"
if (-not ($IsWindowsOs -or $IsLinuxOs -or $IsMacOs)) {
    $IsWindowsOs = $true
}

Write-Step "Operating system"
if ($IsWindowsOs) {
    Write-Host "Detected Windows."
} elseif ($IsMacOs) {
    Write-Host "Detected macOS."
} elseif ($IsLinuxOs) {
    Write-Host "Detected Linux."
}

function Test-GoogleNetwork {
    Write-Step "Network"
    if ($IsWindowsOs) {
        & ping -n 1 -w 2000 google.com *> $null
    } else {
        & ping -c 1 -W 2 google.com *> $null
    }
    if ($LASTEXITCODE -eq 0) {
        Write-Host "google.com is reachable. Using the default Python package index."
        return $true
    }
    Write-Host "google.com timed out. uv sync will use the Tsinghua PyPI mirror." -ForegroundColor Yellow
    return $false
}

function Get-UvCommand {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $parent = Split-Path -Parent $candidate
            if (-not (($env:PATH -split [IO.Path]::PathSeparator) -contains $parent)) {
                $env:PATH = "$parent$([IO.Path]::PathSeparator)$env:PATH"
            }
            return $candidate
        }
    }
    return $null
}

function Install-Uv {
    Write-Step "uv"
    $uv = Get-UvCommand
    if ($null -ne $uv) {
        Write-Host "Found uv: $(& $uv --version)"
        return $uv
    }

    Write-Host "uv not found. Installing uv with the official installer..."
    if ($IsWindowsOs) {
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    } else {
        $sh = Get-Command sh -ErrorAction SilentlyContinue
        if ($null -eq $sh) {
            throw "sh is required to install uv on this operating system."
        }
        if (Get-Command curl -ErrorAction SilentlyContinue) {
            curl -LsSf https://astral.sh/uv/install.sh | sh
        } elseif (Get-Command wget -ErrorAction SilentlyContinue) {
            wget -qO- https://astral.sh/uv/install.sh | sh
        } else {
            throw "curl or wget is required to install uv on this operating system."
        }
    }

    $uv = Get-UvCommand
    if ($null -eq $uv) {
        throw "uv was installed, but it is not available in PATH. Restart the shell or add the uv install directory to PATH."
    }
    Write-Host "Installed uv: $(& $uv --version)"
    return $uv
}

$googleReachable = Test-GoogleNetwork
$uv = Install-Uv

Write-Step "Virtual environment"
if (Test-Path ".venv\pyvenv.cfg") {
    Write-Host "Found existing .venv. uv will verify and update it."
} else {
    Write-Host "No .venv found. uv will create it."
}

Write-Step "Dependencies"
if ($googleReachable) {
    & $uv sync --quiet --frozen
} else {
    & $uv sync --quiet --frozen --default-index $TsinghuaIndex
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Step "Done"
Write-Host "Dependencies are ready."
Write-Host "Next: authenticate Modal when needed:"
Write-Host "  uv run modal setup"
Write-Host "Start the TUI:"
Write-Host "  uv run python manage.py"
