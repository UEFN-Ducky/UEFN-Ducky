# Compile the Ducky Setup host (custom UI) wrapping Setup-engine.exe (Inno).
# Run py build/build_exes.py first.
#
#   powershell -File release/installer/make_release_installer.ps1
#   powershell -File release/installer/make_release_installer.ps1 -EngineOnly
#   powershell -File release/installer/make_release_installer.ps1 -HostOnly
#   powershell -File release/installer/make_release_installer.ps1 -Web [-PublicDir <small images>]
#
# -Web builds dist\UEFN-Ducky-WebSetup.exe: the same host with no engine inside, which
# downloads the Store's latest full Setup at install time (release/build_web_setup.py
# runs this and signs it). The same build named "Uninstall UEFN Ducky.exe" (-Out <exe>)
# is the app's uninstaller.
#
# -Arch arm64 -AppDir <one-dir> -Out <exe> packs the native arm64 build (frozen on an
# arm64 runner) into its own Setup; the host stays x64 and runs emulated there.
param(
    [switch]$EngineOnly,
    [switch]$HostOnly,
    [switch]$Web,
    [string]$PublicDir = "",
    [string]$Out = "",
    [string]$Arch = "",
    [string]$AppDir = ""
)
if ($Web) {
    $EngineOnly = $false
    $HostOnly = $true
}
$ErrorActionPreference = "Stop"
# Repo root is two levels up (this script lives in release/installer/).
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# True when a store_signing_key.json holds a base64 32-byte Ed25519 public key.
function Test-StoreSigningKey([string]$Path) {
    if (-not (Test-Path $Path)) { return $false }
    try {
        $Key = [string]((Get-Content $Path -Raw | ConvertFrom-Json).public_key)
        return [Convert]::FromBase64String($Key.Trim()).Length -eq 32
    } catch { return $false }
}

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
if ($Web) {
    $SetupExe = if ($Out) { $Out } else { Join-Path $Dist "UEFN-Ducky-WebSetup.exe" }
} elseif ($Out) {
    $SetupExe = $Out
}

if ($DoEngine) {
    # build_exes.py bumps __version__ and writes the one-dir folder
    # dist\UEFN-Ducky-<version>\ (UEFN-Ducky.exe + UEFN-Ducky-Bridge.exe + payload).
    # The .iss installs the whole folder into {app}.
    if (-not $AppDir) { $AppDir = Join-Path $Dist "UEFN-Ducky-$Version" }

    if (-not (Test-Path (Join-Path $AppDir "UEFN-Ducky.exe"))) {
        if ($Arch) { Write-Error "No UEFN-Ducky.exe in $AppDir" }
        $alt = Get-ChildItem $Dist -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^UEFN-Ducky-\d+\.\d+\.\d+$' -and (Test-Path (Join-Path $_.FullName "UEFN-Ducky.exe")) } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($alt) {
            $AppDir = $alt.FullName
            $Version = [regex]::Match($alt.Name, '\d+\.\d+\.\d+').Value
            $SetupExe = Join-Path $Dist "UEFN-Ducky-Setup-$Version.exe"
            Write-Host "Using $AppDir (__version__ was stale)."
        } else {
            Write-Error "Build first: py build/build_exes.py (outputs dist\UEFN-Ducky-$Version\)"
        }
    }

    # The app being packed must carry the Store's signing public key, or its Store
    # installs would go unverified. Releases fetch it before freezing the app.
    $ShippedKey = Join-Path $AppDir "_internal\backend\uefn_plugins\store_signing_key.json"
    if (-not (Test-StoreSigningKey $ShippedKey)) {
        Write-Error ("Refusing to build the installer: $AppDir doesn't carry the Store's signing key. " +
            "Release builds fetch it from the Store before building the app; build the release with " +
            "release\publish_app.py or release\build_all.ps1 (both stop if the Store can't be reached).")
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

    $IsccArgs = @("/DMyAppVersion=$Version", "/DMyAppDir=$AppDir")
    if ($Arch -eq "arm64") { $IsccArgs += "/DMyArch=arm64" }
    elseif ($Arch) { Write-Error "-Arch takes arm64 (x64 is the default)" }
    # Signing on (publish_app.py passes its Python only then): ISCC signs the
    # uninstaller it packs and Setup-engine.exe through sign_windows.py. $q and
    # $f are ISCC's quote and file-name placeholders, so they stay single-quoted.
    if ($env:DUCKY_SIGN_PYTHON) {
        $SignScript = Join-Path $Root "release\sign_windows.py"
        $IsccArgs += '/Sducky=$q' + $env:DUCKY_SIGN_PYTHON + '$q $q' + $SignScript + '$q --require $f'
        $IsccArgs += "/DDuckySign"
    }
    & $Iscc @IsccArgs (Join-Path $PSScriptRoot "UEFN-Ducky.iss")
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ISCC failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path $EngineExe)) {
        Write-Error "ISCC did not produce $EngineExe"
    }
    Write-Host "Created $EngineExe"
}

# Unsigned custom host embeds Setup-engine.exe and extracts it at runtime —
# Defender ML treats that as a dropper (Wacatac). Default: copy the Inno stub
# as the published Setup. Pass -HostOnly (or set a signing cert) for the Ducky UI.
$Provider = "$env:DUCKY_SIGN_PROVIDER".Trim().ToLower()
$SigningOn = [bool]$env:DUCKY_SIGN_PYTHON -or (
    ($Provider -notin @("none", "off", "unsigned")) -and
    [bool]($Provider -or $env:DUCKY_WINDOWS_PFX -or $env:DUCKY_SIGNTOOL_EXTRA))
if ($DoHost -and -not $HostOnly -and -not $SigningOn) {
    Copy-Item $EngineExe $SetupExe -Force
    Write-Host "Created $SetupExe (Inno stub). Custom host skipped: unsigned extract-and-run trips Defender."
    $DoHost = $false
}

if ($DoHost) {
    if (-not $Web -and -not (Test-Path $EngineExe)) {
        Write-Error "Missing $EngineExe — run this script without -HostOnly first (or pass -EngineOnly then sign, then -HostOnly)."
    }
    $Dotnet = @(
        @(
            (Join-Path $env:LOCALAPPDATA "Microsoft\dotnet\dotnet.exe"),
            (Join-Path ${env:ProgramFiles} "dotnet\dotnet.exe")
        ) | Where-Object { $_ -and (Test-Path $_) }
        Get-Command "dotnet" -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }
    ) | Where-Object { & $_ --list-sdks 2>$null } | Select-Object -First 1
    if (-not $Dotnet) {
        Write-Error "No .NET SDK found (a dotnet runtime alone cannot build). Install the .NET 8 SDK: winget install -e --id Microsoft.DotNet.SDK.8"
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
        "-p:AppVersion=$Version",
        "-p:UiDir=$UiDir",
        "-p:LicenseFile=$License",
        "-p:SetupOutput=$SetupExe"
    )
    if ($Web) {
        $buildArgs += "-p:WebInstaller=true"
        if ($PublicDir) {
            $buildArgs += "-p:PublicDir=$PublicDir"
        }
    } else {
        $buildArgs += "-p:EngineExe=$EngineExe"
    }
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
