# start_edge_cdp.ps1 — Copia el perfil real de Edge y lo lanza con CDP
# Uso: powershell -ExecutionPolicy Bypass -File start_edge_cdp.ps1

$ErrorActionPreference = "Stop"

Write-Host "== Cerrando Edge =="
Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

$src = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
$dst = "$env:TEMP\edge-cdp"

Write-Host "== Copiando perfil (puede tardar 1-3 min) =="
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
Copy-Item -Recurse $src $dst

Write-Host "== Lanzando Edge con CDP en puerto 9222 =="
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
    --remote-debugging-port=9222 `
    --user-data-dir="$env:TEMP\edge-cdp" `
    --profile-directory=Default `
    --disable-blink-features=AutomationControlled

Write-Host "== Verifica con: curl http://localhost:9222/json/version =="
