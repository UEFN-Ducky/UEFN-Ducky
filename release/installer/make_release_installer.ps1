# Compile UEFN-Ducky-Setup-<version>.exe with Inno Setup (run py build/build_exes.py first)
$ErrorActionPreference = "Stop"
# Repo root is two levels up (this script lives in release/installer/).
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$InitPy = Join-Path $Root "ducky_app\frontend\__init__.py"
$Match = [regex]::Match((Get-Content $InitPy -Raw), '(?m)^__version__\s*=\s*["'']([^"'']+)["'']')
if (-not $Match.Success) {
    Write-Error "No __version__ assignment in $InitPy"
}
$Version = $Match.Groups[1].Value

# build_exes.py bumps __version__ and writes dist\UEFN-Ducky-<version>.exe (it sweeps
# unversioned names). The .iss installs it as plain UEFN-Ducky.exe via DestName.
$Exe = Join-Path $Root "dist\UEFN-Ducky-$Version.exe"
$ExePending = Join-Path $Root "dist\UEFN-Ducky-$Version.pending.exe"

if (-not (Test-Path $Exe)) {
    if (Test-Path $ExePending) {
        $Exe = $ExePending
        Write-Host "Using dist\UEFN-Ducky-$Version.pending.exe (primary exe was locked during build)."
    } else {
        Write-Error "Build first: py build/build_exes.py (outputs dist\UEFN-Ducky-$Version.exe)"
    }
}

$IsccCandidates = @(
    @(
        $env:INNO_SETUP_ISCC,
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
)
if (-not $IsccCandidates) {
    $Cmd = Get-Command "iscc" -ErrorAction SilentlyContinue
    if ($Cmd) { $IsccCandidates = @($Cmd.Source) }
}
if (-not $IsccCandidates) {
    Write-Error "Inno Setup 6 not found. Install it (https://jrsoftware.org/isinfo.php) or set INNO_SETUP_ISCC to ISCC.exe."
}
$Iscc = $IsccCandidates[0]

& $Iscc "/DMyAppVersion=$Version" "/DMyAppExe=$Exe" (Join-Path $PSScriptRoot "UEFN-Ducky.iss")
if ($LASTEXITCODE -ne 0) {
    Write-Error "ISCC failed with exit code $LASTEXITCODE"
}
Write-Host "Created $(Join-Path $Root "dist\UEFN-Ducky-Setup-$Version.exe")"
