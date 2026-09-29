# Локальный запуск на Windows: туннель к PostgreSQL на VPS + uvicorn с автоперезагрузкой.
#   powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
# Ollama должна быть запущена локально (приложение Ollama или `ollama serve`).
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$vpsHost = if ($env:ACR_DEPLOY_HOST) { $env:ACR_DEPLOY_HOST } else { "xsiblings-vps" }

if (-not (Test-NetConnection 127.0.0.1 -Port 15432 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    Write-Host "Открываю туннель к PostgreSQL ($vpsHost) на 127.0.0.1:15432..."
    Start-Process ssh -ArgumentList "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
        "-L", "15432:127.0.0.1:5432", $vpsHost -WindowStyle Hidden
    Start-Sleep 3
}

Set-Location "$root\backend"
& .\.venv\Scripts\alembic upgrade head
& .\.venv\Scripts\python -m app.seed
Write-Host "Откройте http://127.0.0.1:8102"
& .\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8102 --reload
