<#
.SYNOPSIS
    Start the Nexal Legal Ledger in local development mode.

.DESCRIPTION
    Sets NEXAL_DEV=1 and launches the Flask development server on port 5001.
    All unauthenticated requests are routed to the local /dev/login page instead
    of the Vercel Portal, so you can test every feature in full isolation from
    production.

    NEVER run with NEXAL_PRODUCTION=true.  The dev guards refuse to activate in
    production, which means /dev/login would return 404 and you would be redirected
    to the live Portal.

.EXAMPLE
    .\scripts\Start-Dev.ps1

    Opens http://127.0.0.1:5001 — log in with your local admin credentials.
    The Upload Bank Statement button and all other features are immediately usable.
#>

# Safety: refuse to start if NEXAL_PRODUCTION is set
if ($env:NEXAL_PRODUCTION -match '^(1|true|yes)$') {
    Write-Error "NEXAL_PRODUCTION is set. This script is for LOCAL DEVELOPMENT only. Aborting."
    exit 1
}

# Set development environment variables
$env:NEXAL_DEV = "1"
# Unset production flag to be absolutely safe
Remove-Item Env:\NEXAL_PRODUCTION -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  Nexal Legal Ledger — LOCAL DEVELOPMENT SERVER" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  NEXAL_DEV = 1  (dev login enabled)" -ForegroundColor Yellow
Write-Host "  NEXAL_PRODUCTION = not set (production guards inactive)" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Local Ledger URL:  http://127.0.0.1:5001" -ForegroundColor Green
Write-Host "  Dev login page:    http://127.0.0.1:5001/dev/login" -ForegroundColor Green
Write-Host ""
Write-Host "  Log in with your local database credentials (e.g. admin)." -ForegroundColor White
Write-Host "  No requests will be sent to the Portal or production Ledger." -ForegroundColor White
Write-Host ""
Write-Host "  Press Ctrl+C to stop the server." -ForegroundColor Gray
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# Change to project root (one level up from scripts/)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

python app.py
