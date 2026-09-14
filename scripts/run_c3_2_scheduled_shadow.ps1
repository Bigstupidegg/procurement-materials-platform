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

function ConvertTo-C3_2WindowsProcessArgument {
    param([AllowEmptyString()][string]$Value)

    # Start-Process joins ArgumentList values before handing them to CreateProcess.
    # Quote according to CommandLineToArgvW so a -File or -CapturePath under a
    # repository path with spaces remains one child-process argument.
    $Builder = New-Object System.Text.StringBuilder
    [void]$Builder.Append('"')
    $Backslashes = 0
    foreach ($Character in $Value.ToCharArray()) {
        if ($Character -eq [char]'\') { $Backslashes++; continue }
        if ($Character -eq [char]'"') {
            [void]$Builder.Append([char]'\', ($Backslashes * 2) + 1)
            [void]$Builder.Append('"')
            $Backslashes = 0
            continue
        }
        if ($Backslashes -gt 0) { [void]$Builder.Append([char]'\', $Backslashes); $Backslashes = 0 }
        [void]$Builder.Append($Character)
    }
    if ($Backslashes -gt 0) { [void]$Builder.Append([char]'\', $Backslashes * 2) }
    [void]$Builder.Append('"')
    return $Builder.ToString()
}

function Write-C3_2FinalizerReceipt {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Receipt)
    $Bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes(($Receipt | ConvertTo-Json -Depth 6 -Compress))
    $Stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try { $Stream.Write($Bytes, 0, $Bytes.Length); $Stream.Flush() } finally { $Stream.Dispose() }
}

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
    $RunnerSummaryDigest = ""
    if ($null -ne $RunnerSummary) {
        $SummaryBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($SummaryLine[0].Substring("C3_2_RUNNER_SUMMARY=".Length))
        $SummaryHasher = [System.Security.Cryptography.SHA256]::Create()
        try { $RunnerSummaryDigest = ([System.BitConverter]::ToString($SummaryHasher.ComputeHash($SummaryBytes))).Replace("-", "").ToLowerInvariant() } finally { $SummaryHasher.Dispose() }
    }
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
        runner_summary_digest = $RunnerSummaryDigest
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
        $EventPath = [System.IO.Path]::ChangeExtension($CapturePath, ".scheduler-events.json")
        $TerminalPath = [System.IO.Path]::ChangeExtension($CapturePath, ".evidence-terminal.json")
        $LaunchReceiptPath = [System.IO.Path]::ChangeExtension($CapturePath, ".finalizer-launch.json")
        $CompletionReceiptPath = [System.IO.Path]::ChangeExtension($CapturePath, ".finalizer-completion.json")
        $FinalizerStdoutPath = [System.IO.Path]::ChangeExtension($CapturePath, ".finalizer.stdout.log")
        $FinalizerStderrPath = [System.IO.Path]::ChangeExtension($CapturePath, ".finalizer.stderr.log")
        $ReportRoot = if ($env:C3_2_EVIDENCE_ROOT) { Split-Path -Parent $CaptureDirectory } else { Join-Path $env:LOCALAPPDATA "ProcurementMaterialsPlatform\evidence\c3_2_7" }
        $ReportPattern = Join-Path $ReportRoot ((Get-Date -Format "yyyy-MM-dd") + "\c3_2_7-*.json")
        $FinalizerTokens = @("-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File", $Finalizer, "-CapturePath", $CapturePath, "-WaitSeconds", [string]$FinalizerWait)
        $FinalizerCommandLine = (($FinalizerTokens | ForEach-Object { ConvertTo-C3_2WindowsProcessArgument ([string]$_) }) -join " ")
        $LaunchReceipt = [ordered]@{
            schema_version = "c3_2_7.finalizer_launch.v1"; launch_attempted_at = (Get-Date).ToUniversalTime().ToString("o")
            launch_attempted = $true; command = "powershell.exe " + (($FinalizerTokens | ForEach-Object { if ($_ -eq $Finalizer) { "<finalizer-script>" } elseif ($_ -eq $CapturePath) { "<capture-path>" } else { [string]$_ } }) -join " ")
            finalizer_script_path = $Finalizer; capture_path = $CapturePath; working_directory = $RepositoryRoot
            stdout_path = $FinalizerStdoutPath; stderr_path = $FinalizerStderrPath; launch_receipt_path = $LaunchReceiptPath; completion_receipt_path = $CompletionReceiptPath
            expected_artifacts = [ordered]@{ scheduler_events = $EventPath; evidence_terminal = $TerminalPath; final_json_pattern = $ReportPattern; final_markdown_pattern = ($ReportPattern -replace "\\.json$", ".md") }
        }
        try {
            $FinalizerProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $FinalizerCommandLine -WorkingDirectory $RepositoryRoot -WindowStyle Hidden -RedirectStandardOutput $FinalizerStdoutPath -RedirectStandardError $FinalizerStderrPath -PassThru
            $LaunchReceipt.process_id = $FinalizerProcess.Id
            $LaunchReceipt.launch_succeeded = $true
            Write-C3_2FinalizerReceipt -Path $LaunchReceiptPath -Receipt $LaunchReceipt
        } catch {
            $LaunchReceipt.launch_succeeded = $false
            $LaunchReceipt.failure_category = $_.Exception.GetType().Name
            Write-C3_2FinalizerReceipt -Path $LaunchReceiptPath -Receipt $LaunchReceipt
            throw
        }
        Write-Host "C3_2_FINALIZER_LAUNCH=STARTED process_id=$($FinalizerProcess.Id) receipt_path=$LaunchReceiptPath stdout_path=$FinalizerStdoutPath stderr_path=$FinalizerStderrPath"
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
