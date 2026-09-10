param(
    [switch]$DryRun,
    [string]$LogDirectory = (Join-Path $env:LOCALAPPDATA "ProcurementMaterialsPlatform\logs")
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$SheetId = [Environment]::GetEnvironmentVariable("GOOGLE_SHEET_ID", "User")
if ([string]::IsNullOrWhiteSpace($SheetId)) { Write-Host "[FAIL] GOOGLE_SHEET_ID is missing"; exit 1 }
$env:ALLOW_GOOGLE_SHEET_WRITE = "0"
$env:ALLOW_PENDING_RAW_WRITE = "0"
Remove-Item Env:CONTROLLED_WRITE_APPROVAL -ErrorAction SilentlyContinue
$env:GOOGLE_SHEET_ID = $SheetId
$env:GOOGLE_SERVICE_ACCOUNT_FILE = (Join-Path $RepositoryRoot "service_account.json")
Set-Location -LiteralPath $RepositoryRoot
$WrapperExecutionId = [guid]::NewGuid().ToString()
$WrapperStartedAt = (Get-Date).ToUniversalTime().ToString("o")
$env:C3_2_WRAPPER_EXECUTION_ID = $WrapperExecutionId
$PythonArgs = @("scripts\c3_2_scheduled_shadow_runner.py", "--wrapper-execution-id", $WrapperExecutionId)
if ($DryRun) { $PythonArgs += "--dry-run" }
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$LogPath = Join-Path $LogDirectory ("c3_2_scheduled_shadow-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + $WrapperExecutionId + ".log")
Get-Command py -ErrorAction Stop | Out-Null
$NativeErrorActionPreference = $ErrorActionPreference
try {
    # Windows PowerShell turns native stderr into NativeCommandError records.
    # Capture those records for the log, but let the Python exit code decide
    # whether the native process itself succeeded.
    $ErrorActionPreference = "Continue"
    $NativeOutput = & py -3 @PythonArgs *>&1
    $ChildExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $NativeErrorActionPreference
}
if ($null -eq $ChildExitCode) { throw "Native Python did not report an exit code." }
$PostRunExitCode = 0
$ValidatorExitCode = 0
try {
    $NativeText = (@($NativeOutput | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine) + [Environment]::NewLine
    [System.IO.File]::WriteAllText($LogPath, $NativeText, (New-Object System.Text.UTF8Encoding($false)))
    $NativeOutput | ForEach-Object { Write-Host $_.ToString() }
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try { $LogHash = ([System.BitConverter]::ToString($Hasher.ComputeHash([System.IO.File]::ReadAllBytes($LogPath)))).Replace("-", "").ToLowerInvariant() }
    finally { $Hasher.Dispose() }
    $CaptureDirectory = if ($env:C3_2_EVIDENCE_ROOT) { Join-Path $env:C3_2_EVIDENCE_ROOT "captures" } else { Join-Path $env:LOCALAPPDATA "ProcurementMaterialsPlatform\evidence\c3_2_7\captures" }
    New-Item -ItemType Directory -Force -Path $CaptureDirectory | Out-Null
    $CapturePath = Join-Path $CaptureDirectory ("c3_2_7-capture-" + $WrapperExecutionId + ".json")
    $RepositoryVersion = "UNVERIFIED"
    if (Get-Command git -ErrorAction SilentlyContinue) { $RepositoryVersion = (git rev-parse HEAD) }
    $RunnerSummary = $null
    $SummaryLine = @($NativeOutput | ForEach-Object { $_.ToString() } | Where-Object { $_.StartsWith("C3_2_RUNNER_SUMMARY=") } | Select-Object -Last 1)
    if ($SummaryLine.Count -eq 1) { $RunnerSummary = $SummaryLine[0].Substring("C3_2_RUNNER_SUMMARY=".Length) | ConvertFrom-Json }
    $FixtureEvidence = $env:C3_2_FIXTURE_MODE -eq "1"
    $WrapperCompletedAt = (Get-Date).ToUniversalTime().ToString("o")
    $Capture = [ordered]@{
        schema_version = "c3_2_7.capture.v1"; capture_created_at = (Get-Date).ToUniversalTime().ToString("o")
        wrapper_execution_id = $WrapperExecutionId; wrapper_result = $ChildExitCode; runner_result = $ChildExitCode
        wrapper_started_at = $WrapperStartedAt; wrapper_completed_at = $WrapperCompletedAt
        exit_origin = if ($ChildExitCode -ne 0) { "RUNNER" } else { "VALIDATOR" }; repository_version = $RepositoryVersion
        fixture_evidence = [ordered]@{ non_operational = $FixtureEvidence }
        control_path = [ordered]@{ allow_google_sheet_write = $env:ALLOW_GOOGLE_SHEET_WRITE; allow_pending_raw_write = $env:ALLOW_PENDING_RAW_WRITE; controlled_write_approval = $env:CONTROLLED_WRITE_APPROVAL; runner = "scripts/c3_2_scheduled_shadow_runner.py" }
        runner_log = [ordered]@{ path = $LogPath; sha256 = $LogHash; wrapper_execution_id = $WrapperExecutionId }
        date_context = $RunnerSummary.date_context; source_readiness = $RunnerSummary.source_readiness
        canonical_evaluation = $RunnerSummary.canonical_evaluation; append = $RunnerSummary.append
        runner_summary = $RunnerSummary; evidence_references = @("wrapper-log-sha256:" + $LogHash)
    }
    $CaptureBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes(($Capture | ConvertTo-Json -Depth 10 -Compress))
    $CaptureStream = [System.IO.File]::Open($CapturePath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try { $CaptureStream.Write($CaptureBytes, 0, $CaptureBytes.Length); $CaptureStream.Flush() } finally { $CaptureStream.Dispose() }
    & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --stage core
    $ValidatorExitCode = $LASTEXITCODE
    $Finalizer = Join-Path $PSScriptRoot "run_c3_2_validator.ps1"
    if (-not (Test-Path -LiteralPath $Finalizer)) {
        if (-not $FixtureEvidence) { throw "Detached finalizer is unavailable." }
    } else {
        $FinalizerWait = if ($FixtureEvidence) { 0 } else { 15 }
        Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File", $Finalizer, "-CapturePath", $CapturePath, "-WaitSeconds", $FinalizerWait) -WindowStyle Hidden | Out-Null
    }
} catch {
    $PostRunExitCode = 73
    Write-Host "C3_2_POST_RUN_DIAGNOSTIC=FAILED category=$($_.Exception.GetType().Name)"
}
$ExitOrigin = if ($ChildExitCode -ne 0) { "RUNNER" } else { "VALIDATOR" }
Write-Host "log_path=$LogPath wrapper_execution_id=$WrapperExecutionId runner_exit_code=$ChildExitCode exit_code=$ChildExitCode validator_exit_code=$ValidatorExitCode exit_origin=$ExitOrigin"
if ($ChildExitCode -ne 0) { exit $ChildExitCode }
if ($PostRunExitCode -ne 0) { exit $PostRunExitCode }
exit $ValidatorExitCode
