# Launch UEFN Ducky from source for Remote View end-to-end runs.
#
# - loads the panel from the Vite dev server when it is running (:5173)
# - opens Chrome DevTools Protocol on :9222 so direct_e2e.py can read the
#   desktop console and cdp_tail-style tools can attach
# - auto-picks the screen for getDisplayMedia (no picker)
#
# Usage:  powershell -File tests/e2e/remote_view/run_desktop.ps1 [-Installed]
param(
    [switch]$Installed
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
# One instance only. A leftover host (or an orphaned WebView2 still holding the
# debug port) makes the next run talk to a stale page that answers nothing.
Get-Process | Where-Object { $_.ProcessName -like "UEFN-Ducky*" -or $_.ProcessName -eq "pythonw" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5
foreach ($port in 9222, 4199) {
    $owner = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess
    if ($owner) {
        Write-Host "clearing orphan on $port (pid $owner)"
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}
$env:UEFN_DUCKY_WEB_DEV = "http://localhost:5173"
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--auto-select-desktop-capture-source=creen --disable-features=WebRtcHideLocalIpsWithMdns --remote-debugging-port=9222"
if ($Installed) {
    Start-Process "$env:LOCALAPPDATA\Programs\UEFN Ducky\UEFN-Ducky.exe"
} else {
    $env:PYTHONPATH = Join-Path $root "ducky_app"
    Start-Process -FilePath (Join-Path $root ".venv\Scripts\pythonw.exe") -ArgumentList "-m", "frontend" -WorkingDirectory (Join-Path $root "ducky_app")
}
$deadline = (Get-Date).AddSeconds(40)
do {
    Start-Sleep -Seconds 2
    try { $up = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:9222/json" -TimeoutSec 2).StatusCode -eq 200 } catch { $up = $false }
} while (-not $up -and (Get-Date) -lt $deadline)
if (-not $up) { throw "desktop did not come up" }
$cdpOwner = (Get-NetTCPConnection -LocalPort 9222 -State Listen -ErrorAction SilentlyContinue).OwningProcess
"desktop up: panel on 4199, CDP on 9222 (pid $cdpOwner)"
