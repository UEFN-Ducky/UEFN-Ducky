# Package UEFN-Ducky.exe + START_HERE + THIRD_PARTY_NOTICES + LICENSE (run py build/build_exes.py first)
$ErrorActionPreference = "Stop"
# Repo root is two levels up (this script lives in release/portable/).
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# build_exes.py bumps __version__ and writes dist\UEFN-Ducky-<version>.exe (it sweeps
# unversioned names), so resolve the EXE from the current version.
$InitPy = Join-Path $Root "ducky_app\frontend\__init__.py"
$Match = [regex]::Match((Get-Content $InitPy -Raw), '(?m)^__version__\s*=\s*["'']([^"'']+)["'']')
if (-not $Match.Success) {
    Write-Error "No __version__ assignment in $InitPy"
}
$Version = $Match.Groups[1].Value

$Exe = Join-Path $Root "dist\UEFN-Ducky-$Version.exe"
$ExePending = Join-Path $Root "dist\UEFN-Ducky-$Version.pending.exe"
$OutDir = Join-Path $Root "dist\.release-staging"
$Zip = Join-Path $Root "dist\UEFN-Ducky-Windows.zip"

if (-not (Test-Path $Exe)) {
    if (Test-Path $ExePending) {
        $Exe = $ExePending
        Write-Host "Using dist\UEFN-Ducky-$Version.pending.exe (primary exe was locked during build)."
    } else {
        Write-Error "Build first: py build/build_exes.py (outputs dist\UEFN-Ducky-$Version.exe)"
    }
}

Remove-Item -Recurse -Force $OutDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $OutDir | Out-Null

try {
    # Ship as plain UEFN-Ducky.exe — START_HERE.txt refers to that name.
    Copy-Item $Exe (Join-Path $OutDir "UEFN-Ducky.exe")
    Copy-Item (Join-Path $PSScriptRoot "START_HERE.txt") $OutDir
    Copy-Item (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.txt") $OutDir
    Copy-Item (Join-Path $Root "LICENSE") (Join-Path $OutDir "LICENSE.txt")

    Remove-Item -Force $Zip -ErrorAction SilentlyContinue
    Compress-Archive -Path $OutDir -DestinationPath $Zip
    Write-Host "Created $Zip"
}
finally {
    Remove-Item -Recurse -Force $OutDir -ErrorAction SilentlyContinue
}
