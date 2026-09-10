param(
    [Parameter(Mandatory = $true)][string]$CapturePath,
    [int]$WaitSeconds = 15
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepositoryRoot
# Detached finalization is read-only: it only re-reads the capture/log and
# emits local evidence.  It never invokes the market runner or Scheduler APIs.
Start-Sleep -Seconds $WaitSeconds
& py -3 scripts\c3_2_run_validator.py --capture $CapturePath --stage final
exit $LASTEXITCODE
