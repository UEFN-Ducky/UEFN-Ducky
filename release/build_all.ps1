# Build the shipped artifacts at one version:
#   1. dist\UEFN-Ducky-<v>.exe           (the app)
#   2. dist\UEFN-Ducky-Setup-<v>.exe     (Inno Setup installer)
#   Optional: dist\UEFN-Ducky-Windows.zip with -Zip (portable).
#
# The release EXE is built as the installer payload, then removed from dist\.
#
# Build in a clean virtualenv, NOT your system Python — PyInstaller bundles
# whatever it can import, so a global site-packages full of unrelated libraries
# silently adds tens of MB to the EXE:
#
#   py -m venv .venv
#   .venv\Scripts\python -m pip install -r requirements.txt pyinstaller
#   .venv\Scripts\python release\build_all.ps1   # or run the steps below
param(
    [switch]$Zip,
    [switch]$Sign
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Invoke-Step([string]$Label, [scriptblock]$Action) {
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Error "$Label failed with exit code $LASTEXITCODE"
    }
}

Invoke-Step "App EXE (build_exes.py)" { py (Join-Path $Root "build\build_exes.py") }

Invoke-Step "Installer (Inno Setup)" {
    powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "installer\make_release_installer.ps1")
}

if ($Zip) {
    Invoke-Step "Portable zip" {
        powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "portable\make_release_zip.ps1")
    }
}

if ($Sign) {
    # Requires DUCKY_WINDOWS_PFX + DUCKY_WINDOWS_PFX_PASSWORD (see sign_windows.py).
    Invoke-Step "Code signing" {
        py (Join-Path $PSScriptRoot "sign_windows.py") --require (Join-Path $Root "dist")
    }
}

Write-Host "`nDone. Artifacts are in dist\." -ForegroundColor Green
