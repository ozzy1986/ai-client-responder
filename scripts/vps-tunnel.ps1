# Демо на VPS: отдаём серверу локальную Ollama (-R) и открываем себе интерфейс (-L).
#   powershell -ExecutionPolicy Bypass -File scripts/vps-tunnel.ps1
# Пока окно открыто — http://127.0.0.1:8103 показывает версию с VPS, а она генерирует ответы
# моделью на этом ноутбуке. Локальная версия (scripts/dev.ps1) при этом остаётся на 8102.
$vpsHost = if ($env:ACR_DEPLOY_HOST) { $env:ACR_DEPLOY_HOST } else { "xsiblings-vps" }
Write-Host "Туннель поднят: http://127.0.0.1:8103 (Ctrl+C — закрыть)"
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 `
    -R 11434:127.0.0.1:11434 -L 8103:127.0.0.1:8102 $vpsHost
