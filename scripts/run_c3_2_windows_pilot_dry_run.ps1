param(
    [Parameter(Mandatory = $true)][string]$SheetId,
    [string]$CredentialFile = "service_account.json",
    [string]$WorksheetName = "Sheet1",
    [string]$LogDirectory = (Join-Path $env:LOCALAPPDATA "ProcurementMaterialsPlatform\logs")
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Host "[FAIL] $Message"
    exit 1
}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Pilot = Join-Path $RepositoryRoot "scripts\c3_2_pilot_dry_run.py"
$CredentialPath = Join-Path $RepositoryRoot $CredentialFile
if (-not (Test-Path -LiteralPath $Pilot)) { Fail "Pilot dry-run entry point was not found." }
if (-not (Test-Path -LiteralPath $CredentialPath)) { Fail "Credential file was not found." }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { Fail "Python launcher 'py' was not found." }

$PythonExecutable = (& py -3 -c "import sys; print(sys.executable)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($PythonExecutable)) { Fail "Unable to resolve Python 3." }

Set-Location -LiteralPath $RepositoryRoot
Remove-Item Env:CONTROLLED_WRITE_APPROVAL -ErrorAction SilentlyContinue
$env:ALLOW_GOOGLE_SHEET_WRITE = "0"
$env:GOOGLE_SHEET_ID = $SheetId
$env:GOOGLE_SERVICE_ACCOUNT_FILE = $CredentialPath
$env:GOOGLE_SHEET_WORKSHEET = $WorksheetName
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONUTF8 = "1"

New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogDirectory "c3_2_pilot_dry_run-$Timestamp.log"
Write-Host "C3.2-2 Windows Pilot Dry Run"
Write-Host "google_sheet_write=DISABLED mode=DRY_RUN"
$Output = & $PythonExecutable $Pilot 2>&1
$ExitCode = $LASTEXITCODE
$Output | Tee-Object -FilePath $LogPath
Write-Host "exit_code=$ExitCode log_path=$LogPath"
exit $ExitCode
