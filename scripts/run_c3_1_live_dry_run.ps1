param(
    [string]$SheetId = $env:GOOGLE_SHEET_ID,
    [string]$CredentialFile = $(if ($env:GOOGLE_SERVICE_ACCOUNT_FILE) { $env:GOOGLE_SERVICE_ACCOUNT_FILE } else { "service_account.json" }),
    [string]$Worksheet = $(if ($env:GOOGLE_SHEET_WORKSHEET) { $env:GOOGLE_SHEET_WORKSHEET } else { "大宗材料 行情統計表" })
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Host "[FAIL] $Message"
    exit 1
}

Write-Host "=== C3.1 Live Dry Run ==="
Write-Host "This run uses real LME / SMM / yfinance sources and real Google Sheet validation."
Write-Host "Google Sheet write is FORCE-DISABLED for this script."

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Fail "Python launcher 'py' was not found. Install Python 3 first."
}

if (-not (Test-Path "scripts\company_market_collector.py")) {
    Fail "Run this script from the repository root."
}

if (-not (Test-Path $CredentialFile)) {
    Fail "Google service-account file not found: $CredentialFile"
}

if ([string]::IsNullOrWhiteSpace($SheetId)) {
    Fail "GOOGLE_SHEET_ID is empty. Pass -SheetId or set the environment variable."
}

$env:GOOGLE_SHEET_ID = $SheetId
$env:GOOGLE_SERVICE_ACCOUNT_FILE = $CredentialFile
$env:GOOGLE_SHEET_WORKSHEET = $Worksheet
$env:ALLOW_GOOGLE_SHEET_WRITE = "0"

Write-Host "[1/4] Installing/verifying Python dependencies..."
& py -m pip install -r scripts\company-market-requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Dependency installation failed." }

Write-Host "[2/4] Running C3.1 unit tests..."
& py -m unittest tests.test_company_market_core
if ($LASTEXITCODE -ne 0) { Fail "C3.1 unit tests failed." }

Write-Host "[3/4] Running live market-data retrieval + Google Sheet validation..."
& py scripts\company_market_collector.py
if ($LASTEXITCODE -ne 0) { Fail "Live Dry Run failed. Review the collector error above." }

Write-Host "[4/4] Complete. Google Sheet was NOT modified."
Write-Host "Please capture the '今日報價摘要' and the LIVE DRY RUN target row for acceptance review."
