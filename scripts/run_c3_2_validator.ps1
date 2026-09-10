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
$TerminalPath = [System.IO.Path]::ChangeExtension($CapturePath, ".evidence-terminal.json")
try {
    if ($SchedulerEventsFixturePath) {
        $EventBytes = [System.IO.File]::ReadAllBytes($SchedulerEventsFixturePath)
    } else {
        $CaptureContext = Get-Content -Raw -LiteralPath $CapturePath | ConvertFrom-Json
        $Taipei = [System.TimeZoneInfo]::FindSystemTimeZoneById("Taipei Standard Time")
        $LocalStart = [System.TimeZoneInfo]::ConvertTime(([DateTimeOffset]::Parse($CaptureContext.wrapper_started_at)), $Taipei)
        $SlotLocal = New-Object DateTime($LocalStart.Year, $LocalStart.Month, $LocalStart.Day, 16, 30, 0, [DateTimeKind]::Unspecified)
        if ($LocalStart.DateTime -lt $SlotLocal) { $SlotLocal = $SlotLocal.AddDays(-1) }
        $SlotUtc = [System.TimeZoneInfo]::ConvertTimeToUtc($SlotLocal, $Taipei)
        $CoverageStart = $SlotUtc.AddMinutes(-2); $CoverageRequiredEnd = $SlotUtc.AddDays(1).AddMinutes(15)
        $filter = @{ LogName = "Microsoft-Windows-TaskScheduler/Operational"; Id = @(100, 102, 107, 114, 200, 201); StartTime = $CoverageStart; EndTime = $CoverageRequiredEnd }
        $records = Get-WinEvent -FilterHashtable $filter -ErrorAction Stop |
            ForEach-Object { [ordered]@{ record_id = $_.RecordId; event_id = $_.Id; event_xml = $_.ToXml(); observed_at = $_.TimeCreated.ToUniversalTime().ToString("o") } }
        $now = (Get-Date).ToUniversalTime()
        $EventBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes((@{ events = @($records); coverage = @{ slot = $SlotUtc.ToString("o"); log_enabled = $true; readable = $true; query_succeeded = $true; enumeration_complete = $true; retention_proven = $false; coverage_start = $CoverageStart.ToString("o"); coverage_end = $now.ToString("o"); required_end = $CoverageRequiredEnd.ToString("o") } } | ConvertTo-Json -Depth 6 -Compress))
    }
    try {
        $EventStream = [System.IO.File]::Open($EventPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $EventStream.Write($EventBytes, 0, $EventBytes.Length); $EventStream.Flush() } finally { $EventStream.Dispose() }
    } catch [System.IO.IOException] {
        if (-not (Test-Path -LiteralPath $EventPath)) { throw }
    }
    & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --events $EventPath --terminal $TerminalPath --emit-evidence-terminal
    if ($LASTEXITCODE -eq 0) { & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --events $EventPath --terminal $TerminalPath --stage final }
} catch {
    & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --stage final
}
exit $LASTEXITCODE
