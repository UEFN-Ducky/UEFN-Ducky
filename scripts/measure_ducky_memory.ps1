<#
.SYNOPSIS
Read-only snapshots of Ducky and its process family; outputs JSON.
.DESCRIPTION
Compare runs with the same build type, screen, window size, chat and IDE
connections. PrivateBytes is committed memory, not just resident RAM.
WorkingSetPrivateBytes is resident private RAM when Windows supplies it.
WorkingSetBytes includes shared pages and must not be treated as unique RAM.
No process is stopped, suspended or trimmed. Arguments are used only to label
WebView roles and the host version; no command lines or user content are exported.
.EXAMPLE
./scripts/measure_ducky_memory.ps1 -Samples 6 -IntervalSeconds 5 -Label 'visible-same-chat'
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 720)] [int] $Samples = 1,
    [ValidateRange(1, 60)] [int] $IntervalSeconds = 5,
    [string] $Label = 'manual'
)

$ErrorActionPreference = 'Stop'
$snapshots = [System.Collections.Generic.List[object]]::new()
for ($sampleIndex = 0; $sampleIndex -lt $Samples; $sampleIndex++) {
    if ($sampleIndex -gt 0) { Start-Sleep -Seconds $IntervalSeconds }
    $observedAt = [DateTime]::UtcNow.ToString('o')
    $processes = @(Get-CimInstance Win32_Process)
    $byId = @{}
    $members = @{}
    foreach ($process in $processes) {
        $byId[[int]$process.ProcessId] = $process
        if ($process.Name -in @('UEFN-Ducky.exe', 'UEFN-Ducky-Bridge.exe')) {
            $members[[int]$process.ProcessId] = $true
        }
    }
    # Only descendants of Ducky roots: unrelated SearchHost/Edge WebViews stay out.
    do {
        $added = $false
        foreach ($process in $processes) {
            $processId = [int]$process.ProcessId
            $parentId = [int]$process.ParentProcessId
            if ($members.ContainsKey($processId) -or -not $members.ContainsKey($parentId)) { continue }
            $parent = $byId[$parentId]
            # Guard against a parent PID that was reused after this child started.
            if ($parent.CreationDate -gt $process.CreationDate) { continue }
            $members[$processId] = $true
            $added = $true
        }
    } while ($added)

    $residentById = @{}
    try {
        foreach ($counter in Get-CimInstance Win32_PerfRawData_PerfProc_Process) {
            $residentById[[int]$counter.IDProcess] = [long]$counter.WorkingSetPrivate
        }
    } catch {
        Write-Warning 'Private working-set counters unavailable; missing values are null, not zero.'
    }

    $rows = @(
        foreach ($process in $processes) {
            $processId = [int]$process.ProcessId
            if (-not $members.ContainsKey($processId)) { continue }
            $role = 'owned-child'
            $hostVersion = $null
            if ($process.Name -eq 'UEFN-Ducky.exe') { $role = 'app' }
            elseif ($process.Name -eq 'UEFN-Ducky-Bridge.exe') { $role = 'bridge' }
            elseif ($process.Name -eq 'msedgewebview2.exe') {
                $role = 'webview-browser'
                if ($process.CommandLine -match '--type=([a-zA-Z0-9_-]+)') { $role = 'webview-' + $Matches[1] }
                if ($process.CommandLine -match '--webview-exe-version=([0-9.]+)') { $hostVersion = $Matches[1] }
            }
            $privateResident = $null
            if ($residentById.ContainsKey($processId)) { $privateResident = $residentById[$processId] }
            $parentName = $null
            $parent = $byId[[int]$process.ParentProcessId]
            if ($null -ne $parent -and $parent.CreationDate -le $process.CreationDate) { $parentName = $parent.Name }
            [PSCustomObject]@{
                Pid = $processId
                ParentPid = [int]$process.ParentProcessId
                ParentName = $parentName
                Name = $process.Name
                Role = $role
                CreatedUtc = $process.CreationDate.ToUniversalTime().ToString('o')
                HostVersion = $hostVersion
                PrivateBytes = [long]$process.PrivatePageCount
                WorkingSetBytes = [long]$process.WorkingSetSize
                WorkingSetPrivateBytes = $privateResident
                Handles = [int]$process.HandleCount
                Threads = [int]$process.ThreadCount
            }
        }
    )
    $privateTotal = ($rows | Measure-Object PrivateBytes -Sum).Sum
    $residentTotal = $null
    if ($rows.Count -gt 0 -and @($rows | Where-Object { $null -eq $_.WorkingSetPrivateBytes }).Count -eq 0) {
        $residentTotal = ($rows | Measure-Object WorkingSetPrivateBytes -Sum).Sum
    }
    $snapshots.Add([PSCustomObject]@{
        ObservedUtc = $observedAt
        ProcessCount = $rows.Count
        BridgeCount = @($rows | Where-Object Role -eq 'bridge').Count
        TotalPrivateBytes = $privateTotal
        TotalWorkingSetPrivateBytes = $residentTotal
        Processes = $rows
    })
}
[PSCustomObject]@{
    Label = $Label
    Notes = 'Process-tree fallback; detached/reparented children may be absent. Snapshots are not atomic. Compare identical workloads and connection counts.'
    Samples = @($snapshots.ToArray())
} | ConvertTo-Json -Depth 6
