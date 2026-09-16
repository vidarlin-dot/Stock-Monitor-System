# LINE Bot Windows Task Registration Script
# This script registers two scheduled tasks:
# 1. Cloudflared Named Tunnel (auto-starts on login, auto-restarts on crash)
# 2. Flask Webhook Server (auto-starts on login, auto-restarts on crash)

$ErrorActionPreference = "Stop"

# === CONFIGURATION - Edit these paths ===
$PythonExe = "C:\Python314\python.exe"
$WebhookPath = "C:\PROGRAM\Stock-Monitor-System\src\line_bot_webhook.py"
$WorkingDir = "C:\PROGRAM\Stock-Monitor-System"
$CloudflaredPath = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$ConfigPath = "$env:USERPROFILE\.cloudflared\config.yml"
# ========================================

Write-Host "=== Registering LINE Bot Windows Tasks ===" -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Python not found at $PythonExe" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $WebhookPath)) {
    Write-Host "ERROR: Webhook script not found at $WebhookPath" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $CloudflaredPath)) {
    Write-Host "ERROR: cloudflared not found at $CloudflaredPath" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $ConfigPath)) {
    Write-Host "WARNING: Config file not found at $ConfigPath" -ForegroundColor Yellow
    Write-Host "Please copy config.yml to $ConfigPath first" -ForegroundColor Yellow
}

# Create log directory
$LogDir = "$WorkingDir\logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# === Task 1: Cloudflared Named Tunnel ===
$TaskName1 = "LINE-Bot-Tunnel"
$Action1 = New-ScheduledTaskAction -Execute $CloudflaredPath -Argument "tunnel run line-bot-stocks" -WorkingDirectory $WorkingDir
$Settings1 = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 1) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$Trigger1 = New-ScheduledTaskTrigger -AtLogOn
$Principal1 = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName1 -Action $Action1 -Settings $Settings1 -Trigger $Trigger1 -Principal $Principal1 -Description "Cloudflared Named Tunnel for LINE Bot" -Force | Out-Null
Write-Host "[OK] Task '$TaskName1' registered" -ForegroundColor Green

# === Task 2: Flask Webhook Server ===
$TaskName2 = "LINE-Bot-Webhook"
$Action2 = New-ScheduledTaskAction -Execute $PythonExe -Argument "src/line_bot_webhook.py" -WorkingDirectory $WorkingDir
$Settings2 = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 24) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$Trigger2 = New-ScheduledTaskTrigger -AtLogOn
$Principal2 = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName2 -Action $Action2 -Settings $Settings2 -Trigger $Trigger2 -Principal $Principal2 -Description "Flask Webhook Server for LINE Bot" -Force | Out-Null
Write-Host "[OK] Task '$TaskName2' registered" -ForegroundColor Green

Write-Host ""
Write-Host "=== Setup Complete! ===" -ForegroundColor Green
Write-Host ""
Write-Host "What's next:" -ForegroundColor Cyan
Write-Host "1. Restart your computer (or manually start the tasks)" -ForegroundColor White
Write-Host "2. Verify the tunnel is running: Get-Service or Task Manager" -ForegroundColor White
Write-Host "3. Update LINE Console with: https://stocks.vidarlin.com/webhook/line" -ForegroundColor White
Write-Host ""
Write-Host "To start/stop tasks manually:" -ForegroundColor Yellow
Write-Host "  Start-Task -TaskName LINE-Bot-Tunnel" -ForegroundColor White
Write-Host "  Start-Task -TaskName LINE-Bot-Webhook" -ForegroundColor White
Write-Host "  Stop-Task -TaskName LINE-Bot-Tunnel" -ForegroundColor White
Write-Host "  Stop-Task -TaskName LINE-Bot-Webhook" -ForegroundColor White
Write-Host ""
