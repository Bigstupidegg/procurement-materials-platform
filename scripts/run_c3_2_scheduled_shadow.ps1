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
$PythonArgs = @("scripts\c3_2_scheduled_shadow_runner.py")
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
Write-Host "log_path=$LogPath exit_code=$ChildExitCode"
exit $ChildExitCode
