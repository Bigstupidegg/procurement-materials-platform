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
$env:C3_2_WRAPPER_EXECUTION_ID = $WrapperExecutionId
$PythonArgs = @("scripts\c3_2_scheduled_shadow_runner.py", "--wrapper-execution-id", $WrapperExecutionId)
if ($DryRun) { $PythonArgs += "--dry-run" }
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$LogPath = Join-Path $LogDirectory ("c3_2_scheduled_shadow-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
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
$NativeOutput | Tee-Object -FilePath $LogPath
$Hasher = [System.Security.Cryptography.SHA256]::Create()
try { $LogHash = ([System.BitConverter]::ToString($Hasher.ComputeHash([System.IO.File]::ReadAllBytes($LogPath)))).Replace("-", "").ToLowerInvariant() }
finally { $Hasher.Dispose() }
$CaptureDirectory = Join-Path $env:LOCALAPPDATA "ProcurementMaterialsPlatform\evidence\c3_2_7\captures"
New-Item -ItemType Directory -Force -Path $CaptureDirectory | Out-Null
$CapturePath = Join-Path $CaptureDirectory ("c3_2_7-capture-" + $WrapperExecutionId + ".json")
$RepositoryVersion = "UNVERIFIED"
if (Get-Command git -ErrorAction SilentlyContinue) { $RepositoryVersion = (git rev-parse HEAD) }
$Capture = [ordered]@{
    schema_version = "c3_2_7.capture.v1"
    capture_created_at = (Get-Date).ToUniversalTime().ToString("o")
    wrapper_execution_id = $WrapperExecutionId
    wrapper_result = $ChildExitCode
    runner_result = $ChildExitCode
    repository_version = $RepositoryVersion
    control_path = [ordered]@{ allow_google_sheet_write = $env:ALLOW_GOOGLE_SHEET_WRITE; allow_pending_raw_write = $env:ALLOW_PENDING_RAW_WRITE; controlled_write_approval = $env:CONTROLLED_WRITE_APPROVAL; runner = "scripts/c3_2_scheduled_shadow_runner.py" }
    runner_log = [ordered]@{ path = $LogPath; sha256 = $LogHash; wrapper_execution_id = $WrapperExecutionId }
    evidence_references = @("wrapper-log-sha256:" + $LogHash)
}
$Capture | ConvertTo-Json -Depth 6 -Compress | Set-Content -LiteralPath $CapturePath -Encoding UTF8
$ValidatorArgs = @("scripts\c3_2_run_validator.py", "--capture", $CapturePath, "--stage", "core")
& py -3 @ValidatorArgs
$ValidatorExitCode = $LASTEXITCODE
$Finalizer = Join-Path $PSScriptRoot "run_c3_2_validator.ps1"
if (Test-Path -LiteralPath $Finalizer) {
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File", $Finalizer, "-CapturePath", $CapturePath) -WindowStyle Hidden | Out-Null
}
Write-Host "log_path=$LogPath wrapper_execution_id=$WrapperExecutionId runner_exit_code=$ChildExitCode validator_exit_code=$ValidatorExitCode"
if ($ChildExitCode -ne 0) { exit $ChildExitCode }
exit $ValidatorExitCode
