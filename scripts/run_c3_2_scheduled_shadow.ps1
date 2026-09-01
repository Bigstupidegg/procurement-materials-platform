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
& py -3 @PythonArgs *>&1 | Tee-Object -FilePath $LogPath
$ChildExitCode = $LASTEXITCODE
Write-Host "log_path=$LogPath exit_code=$ChildExitCode"
exit $ChildExitCode
