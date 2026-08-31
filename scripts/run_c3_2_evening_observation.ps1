param(
    [string]$LogDirectory = (Join-Path $env:LOCALAPPDATA "ProcurementMaterialsPlatform\logs")
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Host "[FAIL] $Message"
    exit 1
}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$PilotWrapper = Join-Path $RepositoryRoot "scripts\run_c3_2_windows_pilot_dry_run.ps1"
if (-not (Test-Path -LiteralPath $PilotWrapper)) { Fail "Pilot dry-run wrapper was not found." }

$SheetId = [Environment]::GetEnvironmentVariable("GOOGLE_SHEET_ID", "User")
if ([string]::IsNullOrWhiteSpace($SheetId)) { Fail "GOOGLE_SHEET_ID is missing from the Windows User environment." }

$env:ALLOW_GOOGLE_SHEET_WRITE = "0"
Remove-Item Env:CONTROLLED_WRITE_APPROVAL -ErrorAction SilentlyContinue
$WorksheetName = -join [char[]](0x5927, 0x5B97, 0x6750, 0x6599, 0x20, 0x884C, 0x60C5, 0x7D71, 0x8A08, 0x8868)

Set-Location -LiteralPath $RepositoryRoot
& $PilotWrapper -SheetId $SheetId -WorksheetName $WorksheetName -LogDirectory $LogDirectory
$ChildExitCode = $LASTEXITCODE
exit $ChildExitCode
