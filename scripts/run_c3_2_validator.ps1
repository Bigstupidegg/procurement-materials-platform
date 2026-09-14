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
$CompletionReceiptPath = [System.IO.Path]::ChangeExtension($CapturePath, ".finalizer-completion.json")
$FinalizerExitCode = 72
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
    $FinalizerExitCode = $LASTEXITCODE
} catch {
    & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --stage final
    $FinalizerExitCode = $LASTEXITCODE
} finally {
    # This is a run-scoped diagnostic only. It exposes completion/exit visibility
    # without rerunning the market job or modifying final evidence artifacts.
    $Completion = [ordered]@{
        schema_version = "c3_2_7.finalizer_completion.v1"; completed_at = (Get-Date).ToUniversalTime().ToString("o")
        exit_code = $FinalizerExitCode; capture_path = $CapturePath; scheduler_events_path = $EventPath; evidence_terminal_path = $TerminalPath
        scheduler_events_exists = (Test-Path -LiteralPath $EventPath); evidence_terminal_exists = (Test-Path -LiteralPath $TerminalPath)
    }
    try {
        $Bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes(($Completion | ConvertTo-Json -Depth 4 -Compress))
        $Stream = [System.IO.File]::Open($CompletionReceiptPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $Stream.Write($Bytes, 0, $Bytes.Length); $Stream.Flush() } finally { $Stream.Dispose() }
    } catch {
        Write-Host "C3_2_FINALIZER_COMPLETION_RECEIPT=UNAVAILABLE category=$($_.Exception.GetType().Name)"
    }
}
exit $FinalizerExitCode
