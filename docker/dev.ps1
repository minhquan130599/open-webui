# Chế độ phát triển nhanh.
#   .\dev.ps1            bật: backend chạy từ source, tự reload khi lưu file .py
#   .\dev.ps1 -Frontend  bật thêm frontend dev server (Vite, cập nhật ngay khi lưu file .svelte/.ts)
#   .\dev.ps1 -Off       tắt, quay về image đã build
#   .\dev.ps1 -Logs      xem log backend
param([switch]$Frontend, [switch]$Off, [switch]$Logs)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\open-webui")
$port = (Get-Content (Join-Path $PSScriptRoot ".env") | Where-Object { $_ -match '^WEBUI_PORT=' }) -replace '^WEBUI_PORT=', ''
if (-not $port) { $port = "8000" }
# Keep Google/Microsoft sign-in in dev mode when .env enables docker-compose.oauth.yaml via COMPOSE_FILE.
$files = @("-f", "docker-compose.yaml")
if ((Get-Content (Join-Path $PSScriptRoot ".env")) -match '^COMPOSE_FILE=.*docker-compose\.oauth\.yaml') { $files += @("-f", "docker-compose.oauth.yaml") }
$files += @("-f", "compose.dev.yaml")

if ($Off) {
    docker compose up -d open-webui
    Write-Host "Da quay ve image da build: http://localhost:$port"
    exit 0
}
if ($Logs) {
    docker compose @files logs -f --tail 100 open-webui
    exit 0
}

docker compose @files up -d open-webui
if ($LASTEXITCODE -ne 0) { Write-Host "LOI: khong bat duoc che do dev" -ForegroundColor Red; exit 1 }
Write-Host "Backend dev: http://localhost:$port  (sua file .py -> tu reload sau vai giay; xem log: .\dev.ps1 -Logs)" -ForegroundColor Green

if ($Frontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Write-Host "LOI: can cai Node.js 22" -ForegroundColor Red; exit 1 }
    Set-Location $repo
    if (-not (Test-Path "node_modules")) { npm ci --force }
    $env:WEBUI_BACKEND_URL = "http://localhost:$port"
    Write-Host "Frontend dev: http://localhost:5173  (Ctrl+C de dung)" -ForegroundColor Green
    npm run dev
}
