# Compile the Ducky Setup host (custom UI) wrapping Setup-engine.exe (Inno).
# Run py build/build_exes.py first.
#
#   powershell -File release/installer/make_release_installer.ps1
#   powershell -File release/installer/make_release_installer.ps1 -EngineOnly
#   powershell -File release/installer/make_release_installer.ps1 -HostOnly
param(
    [switch]$EngineOnly,
    [switch]$HostOnly
)
$ErrorActionPreference = "Stop"
# Repo root is two levels up (this script lives in release/installer/).
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$DoEngine = $EngineOnly -or -not $HostOnly
$DoHost = $HostOnly -or -not $EngineOnly

$InitPy = Join-Path $Root "ducky_app\frontend\__init__.py"
$Match = [regex]::Match((Get-Content $InitPy -Raw), '(?m)^__version__\s*=\s*["'']([^"'']+)["'']')
if (-not $Match.Success) {
    Write-Error "No __version__ assignment in $InitPy"
}
$Version = $Match.Groups[1].Value
$Dist = Join-Path $Root "dist"
$EngineExe = Join-Path $Dist "Setup-engine.exe"
$SetupExe = Join-Path $Dist "UEFN-Ducky-Setup-$Version.exe"

if ($DoEngine) {
    # build_exes.py bumps __version__ and writes dist\UEFN-Ducky-<version>.exe (it sweeps
    # unversioned names). The .iss installs it as plain UEFN-Ducky.exe via DestName.
    $Exe = Join-Path $Dist "UEFN-Ducky-$Version.exe"
    $ExePending = Join-Path $Dist "UEFN-Ducky-$Version.pending.exe"

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
    if (-not (Test-Path $EngineExe)) {
        Write-Error "ISCC did not produce $EngineExe"
    }
    Write-Host "Created $EngineExe"
}

if ($DoHost) {
    if (-not (Test-Path $EngineExe)) {
        Write-Error "Missing $EngineExe — run this script without -HostOnly first (or pass -EngineOnly then sign, then -HostOnly)."
    }
    $Dotnet = @(
        @(
            (Join-Path $env:LOCALAPPDATA "Microsoft\dotnet\dotnet.exe"),
            (Join-Path ${env:ProgramFiles} "dotnet\dotnet.exe")
        ) | Where-Object { $_ -and (Test-Path $_) }
        Get-Command "dotnet" -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }
    ) | Select-Object -First 1
    if (-not $Dotnet) {
        Write-Error "dotnet SDK not found. Install .NET SDK 8 (https://dot.net) to build the Setup host."
    }
    $Icon = Join-Path $Root "build\app_icon.ico"
    $HostDir = Join-Path $PSScriptRoot "host"
    $UiDir = Join-Path $PSScriptRoot "ui"
    $License = Join-Path $Root "LICENSE"
    New-Item -ItemType Directory -Force -Path $Dist | Out-Null
    & $Dotnet restore (Join-Path $HostDir "DuckySetup.csproj") --nologo | Out-Host
    $Wv2Pkg = Join-Path $env:USERPROFILE ".nuget\packages\microsoft.web.webview2\1.0.2903.40"
    $Wv2Dir = Join-Path $HostDir "wv2"
    if (-not (Test-Path (Join-Path $Wv2Pkg "lib\net462\Microsoft.Web.WebView2.WinForms.dll"))) {
        Write-Error "WebView2 package missing at $Wv2Pkg — nuget restore should have fetched 1.0.2903.40."
    }
    New-Item -ItemType Directory -Force -Path $Wv2Dir | Out-Null
    Copy-Item (Join-Path $Wv2Pkg "lib\net462\Microsoft.Web.WebView2.Core.dll") $Wv2Dir -Force
    Copy-Item (Join-Path $Wv2Pkg "lib\net462\Microsoft.Web.WebView2.WinForms.dll") $Wv2Dir -Force
    Copy-Item (Join-Path $Wv2Pkg "runtimes\win-x64\native\WebView2Loader.dll") $Wv2Dir -Force
    $buildArgs = @(
        "build", (Join-Path $HostDir "DuckySetup.csproj"),
        "-c", "Release",
        "-t:Rebuild",
        "-p:EngineExe=$EngineExe",
        "-p:AppVersion=$Version",
        "-p:UiDir=$UiDir",
        "-p:LicenseFile=$License",
        "-p:SetupOutput=$SetupExe"
    )
    if (Test-Path $Icon) {
        $buildArgs += "-p:AppIcon=$Icon"
    }
    & $Dotnet @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "dotnet build failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path $SetupExe)) {
        Write-Error "Host build did not produce $SetupExe"
    }
    Write-Host "Created $SetupExe"
}
