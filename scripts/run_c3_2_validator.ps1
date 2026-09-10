param(
    [Parameter(Mandatory = $true)][string]$CapturePath,
    [int]$WaitSeconds = 15,
    [string]$SchedulerEventsFixturePath = ""
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepositoryRoot
# Detached finalization is read-only: it re-reads the capture/log and the
# Task Scheduler operational event log; it never invokes the market runner.
Start-Sleep -Seconds $WaitSeconds
$EventPath = [System.IO.Path]::ChangeExtension($CapturePath, ".scheduler-events.json")
try {
    if ($SchedulerEventsFixturePath) {
        $EventBytes = [System.IO.File]::ReadAllBytes($SchedulerEventsFixturePath)
    } else {
        $filter = @{ LogName = "Microsoft-Windows-TaskScheduler/Operational"; Id = @(100, 102, 107, 114, 200, 201); StartTime = (Get-Date).AddHours(-30) }
        $records = Get-WinEvent -FilterHashtable $filter -MaxEvents 512 -ErrorAction Stop |
            ForEach-Object { [ordered]@{ record_id = $_.RecordId; event_id = $_.Id; event_xml = $_.ToXml(); observed_at = $_.TimeCreated.ToUniversalTime().ToString("o") } }
        $EventBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes((@{ events = @($records) } | ConvertTo-Json -Depth 6 -Compress))
    }
    try {
        $EventStream = [System.IO.File]::Open($EventPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $EventStream.Write($EventBytes, 0, $EventBytes.Length); $EventStream.Flush() } finally { $EventStream.Dispose() }
    } catch [System.IO.IOException] {
        if (-not (Test-Path -LiteralPath $EventPath)) { throw }
    }
    & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --events $EventPath --stage final
} catch {
    & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --stage final
}
exit $LASTEXITCODE
