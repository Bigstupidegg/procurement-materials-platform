param(
    [string]$LogDirectory = (Join-Path $env:LOCALAPPDATA "ProcurementMaterialsPlatform\\logs")
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Host "[FAIL] $Message"
    exit 1
}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Collector = Join-Path $RepositoryRoot "scripts\\c3_2_readonly_collection_poc.py"

if (-not (Test-Path -LiteralPath $Collector)) {
    Fail "Read-only collector entry point was not found."
}
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Fail "Python launcher 'py' was not found."
}

$PythonExecutable = (& py -3 -c "import sys; print(sys.executable)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($PythonExecutable) -or -not (Test-Path -LiteralPath $PythonExecutable)) {
    Fail "Unable to resolve a Python 3 executable."
}

Set-Location -LiteralPath $RepositoryRoot
Remove-Item Env:GOOGLE_SHEET_ID -ErrorAction SilentlyContinue
Remove-Item Env:GOOGLE_SERVICE_ACCOUNT_FILE -ErrorAction SilentlyContinue
Remove-Item Env:CONTROLLED_WRITE_APPROVAL -ErrorAction SilentlyContinue
$env:ALLOW_GOOGLE_SHEET_WRITE = "0"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONUTF8 = "1"

New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogDirectory "c3_2_readonly-$Timestamp.log"

Write-Host "C3.2-1 Windows scheduled read-only collection"
Write-Host "repository=$RepositoryRoot"
Write-Host "python=$PythonExecutable"
Write-Host "google_sheet_write=DISABLED audit_persistence=DISABLED"

$Output = & $PythonExecutable $Collector 2>&1
$ExitCode = $LASTEXITCODE
$Output | Tee-Object -FilePath $LogPath
Write-Host "exit_code=$ExitCode log_path=$LogPath"
exit $ExitCode
