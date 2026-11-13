# Đóng gói image đã build (open-webui-local:latest từ update.ps1) thành file .tar để mang sang server khác.
#   .\export-image.ps1             chỉ image NCS AI (Knight) (server đích tự tải postgres/redis/rustfs từ Docker Hub)
#   .\export-image.ps1 -WithDeps   kèm cả postgres/redis/rustfs (server đích không có internet)
#   .\export-image.ps1 -WithTerminal   kèm image Open Terminal (AI tạo file Word/Excel/PDF)
#   Dùng chung được:  .\export-image.ps1 -WithTerminal -WithDeps   (đủ mọi image, server đích không cần internet)
# Server đích:  docker load -i <file>.tar  rồi dùng deploy\docker-compose.yaml
param([switch]$WithDeps, [switch]$WithTerminal)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$source = "open-webui-local:latest"
docker image inspect $source *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "LOI: chua co image $source, chay update.ps1 truoc" -ForegroundColor Red; exit 1 }

# Phiên bản = mã commit đã ghi vào image lúc build (WEBUI_BUILD_HASH)
$version = docker image inspect $source --format '{{range .Config.Env}}{{println .}}{{end}}' |
    Where-Object { $_ -match '^WEBUI_BUILD_HASH=' } | ForEach-Object { $_ -replace '^WEBUI_BUILD_HASH=', '' }
if (-not $version) { $version = Get-Date -Format yyyyMMdd_HHmm }

docker tag $source "ncs-chatui:$version"
docker tag $source "ncs-chatui:latest"
$images = @("ncs-chatui:$version", "ncs-chatui:latest")
if ($WithTerminal) {
    # Đúng bản Terminal đang chạy trên máy này (chỉ tải về nếu máy chưa có)
    $terminal = "ghcr.io/open-webui/open-terminal:latest"
    docker image inspect $terminal *> $null
    if ($LASTEXITCODE -ne 0) { docker pull $terminal; if ($LASTEXITCODE -ne 0) { exit 1 } }
    $images += $terminal
}
if ($WithDeps) {
    $deps = @("pgvector/pgvector:pg17", "redis:7-alpine", "rustfs/rustfs:latest")
    foreach ($image in $deps) { docker pull $image; if ($LASTEXITCODE -ne 0) { exit 1 } }
    $images += $deps
}

$outDir = Join-Path $PSScriptRoot "images"
New-Item -ItemType Directory -Force $outDir | Out-Null
$suffix = $(if ($WithTerminal) { "-terminal" } else { "" }) + $(if ($WithDeps) { "-full" } else { "" })
$file = Join-Path $outDir "ncs-chatui-$version$suffix.tar"
Write-Host "Dong goi $($images -join ', ') -> $file"
docker save -o $file @images
if ($LASTEXITCODE -ne 0) { Write-Host "LOI: docker save that bai" -ForegroundColor Red; exit 1 }

Write-Host ("Xong: {0} ({1:N1} GB)" -f $file, ((Get-Item $file).Length / 1GB)) -ForegroundColor Green
Write-Host "Tren server dich: docker load -i $(Split-Path $file -Leaf); dat OWUI_IMAGE=ncs-chatui:$version trong .env; docker compose up -d"
