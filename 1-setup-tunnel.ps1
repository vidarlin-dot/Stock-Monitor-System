# LINE Bot Named Tunnel Setup Script
# This script helps you set up a stable Cloudflare Named Tunnel
# Run this ONLY ONCE to create the tunnel and get the credentials

$ErrorActionPreference = "Stop"
$CloudflaredPath = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

Write-Host "=== LINE Bot Named Tunnel Setup ===" -ForegroundColor Cyan
Write-Host ""

# Check if cloudflared is installed
if (-not (Test-Path $CloudflaredPath)) {
    Write-Host "ERROR: cloudflared not found at $CloudflaredPath" -ForegroundColor Red
    Write-Host "Please install cloudflared from: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/" -ForegroundColor Yellow
    exit 1
}

Write-Host "Step 1: Login to Cloudflare" -ForegroundColor Green
Write-Host "This will open a browser window. Please login and authorize." -ForegroundColor Yellow
Write-Host ""
& $CloudflaredPath tunnel login

Write-Host ""
Write-Host "Step 2: Create Named Tunnel" -ForegroundColor Green
Write-Host ""
& $CloudflaredPath tunnel create line-bot-stocks

Write-Host ""
Write-Host "=== Setup Complete! ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Add your domain to Cloudflare (if not already done)" -ForegroundColor White
Write-Host "2. Run the DNS routing command (shown below)" -ForegroundColor White
Write-Host "3. Copy config.yml to %USERPROFILE%\.cloudflared\config.yml" -ForegroundColor White
Write-Host "4. Run the task registration script" -ForegroundColor White
Write-Host ""
Write-Host "DNS Routing Command (run this in PowerShell):" -ForegroundColor Yellow
Write-Host "& '$CloudflaredPath' tunnel route dns line-bot-stocks stocks.vidarlin.com" -ForegroundColor White
Write-Host ""
