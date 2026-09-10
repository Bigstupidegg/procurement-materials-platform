param(
    [Parameter(Mandatory = $true)][string]$CapturePath,
    [int]$WaitSeconds = 15
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepositoryRoot
# Detached finalization is read-only: it re-reads the capture/log and the
# Task Scheduler operational event log; it never invokes the market runner.
Start-Sleep -Seconds $WaitSeconds
$EventPath = [System.IO.Path]::ChangeExtension($CapturePath, ".scheduler-events.json")
try {
    $records = Get-WinEvent -LogName "Microsoft-Windows-TaskScheduler/Operational" -MaxEvents 256 -ErrorAction Stop |
        Where-Object { $_.Message -like "*ProcurementMaterialsPlatform-C3_2_5-ShadowPilot*" } |
        ForEach-Object { [ordered]@{ task_name = "ProcurementMaterialsPlatform-C3_2_5-ShadowPilot"; record_id = $_.RecordId; event_id = $_.Id; event_xml = $_.ToXml(); observed_at = $_.TimeCreated.ToUniversalTime().ToString("o") } }
    [System.IO.File]::WriteAllText($EventPath, (@{ events = @($records) } | ConvertTo-Json -Depth 6 -Compress), (New-Object System.Text.UTF8Encoding($false)))
    & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --events $EventPath --stage final
} catch {
    & py -3 scripts\c3_2_run_validator.py --capture $CapturePath --stage final
}
exit $LASTEXITCODE
