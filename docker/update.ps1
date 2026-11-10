# Cập nhật và chạy Open WebUI: sao lưu DB -> git pull -> build -> chạy -> kiểm tra.
# Chạy:  powershell -ExecutionPolicy Bypass -File D:\WebUi\openwenui-docker\update.ps1
# Bỏ qua sao lưu:  ... update.ps1 -NoBackup

param([switch]$NoBackup)

$ErrorActionPreference = "Stop"
$repo = Join-Path $PSScriptRoot "..\open-webui"
Set-Location $PSScriptRoot

function Fail($message) { Write-Host "LOI: $message" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $PSScriptRoot ".env"))) { Fail "Khong thay file .env canh update.ps1" }
if (-not (Test-Path (Join-Path $repo ".git"))) { Fail "Khong thay repo tai $repo" }

# 1. Sao lưu database (nếu Postgres đang chạy). Dùng định dạng custom của pg_dump và docker cp
#    để file không bị PowerShell đổi mã hóa. Khôi phục: pg_restore -U openwebui -d openwebui --clean
if (-not $NoBackup) {
    $running = docker ps --filter "name=^owui-postgres$" --format "{{.Names}}"
    if ($running -eq "owui-postgres") {
        $backupDir = Join-Path $PSScriptRoot "backups"
        New-Item -ItemType Directory -Force $backupDir | Out-Null
        $file = "openwebui_$(Get-Date -Format yyyyMMdd_HHmmss).dump"
        Write-Host "Sao luu database -> backups\$file"
        docker exec owui-postgres pg_dump -U openwebui -d openwebui -Fc -f "/tmp/$file"
        if ($LASTEXITCODE -ne 0) { Fail "pg_dump that bai" }
        docker cp "owui-postgres:/tmp/$file" (Join-Path $backupDir $file)
        docker exec owui-postgres rm -f "/tmp/$file" | Out-Null
        # Giữ 10 bản gần nhất
        Get-ChildItem $backupDir -Filter "openwebui_*.dump" | Sort-Object Name -Descending |
            Select-Object -Skip 10 | Remove-Item -Force
    } else {
        Write-Host "Postgres chua chay, bo qua sao luu"
    }
}

# 2. Lấy code mới từ fork (nhánh đang theo dõi). --ff-only: dừng nếu code trên server lệch với fork.
$branch = git -C $repo rev-parse --abbrev-ref HEAD
Write-Host "Cap nhat code (nhanh $branch)"
$dirty = git -C $repo status --porcelain --untracked-files=no
if ($dirty) { Fail "Repo co file sua tay, xu ly truoc khi cap nhat:`n$dirty" }
git -C $repo pull --ff-only
if ($LASTEXITCODE -ne 0) { Fail "git pull that bai" }
$env:BUILD_HASH = git -C $repo rev-parse --short HEAD
Write-Host "Phien ban: $(git -C $repo log -1 --format='%h %s')"

# 3. Build một lần (service open-webui; bucket-init dùng lại image này), rồi chạy
docker compose build open-webui
if ($LASTEXITCODE -ne 0) { Fail "docker compose build that bai. Neu log co 'rpc error ... EOF': Docker het RAM, xem .wslconfig" }
docker compose up -d
if ($LASTEXITCODE -ne 0) { Fail "docker compose up that bai" }
docker image prune -f | Out-Null

# 4. Chờ Open WebUI sẵn sàng
$port = (Get-Content (Join-Path $PSScriptRoot ".env") | Where-Object { $_ -match '^WEBUI_PORT=' }) -replace '^WEBUI_PORT=', ''
if (-not $port) { $port = "8000" }
Write-Host "Cho Open WebUI khoi dong tai http://localhost:$port ..."
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        if ((Invoke-WebRequest "http://localhost:$port/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) { $ok = $true; break }
    } catch {}
    Start-Sleep -Seconds 5
}
if (-not $ok) { Fail "Open WebUI chua len sau 5 phut. Xem: docker compose logs --tail 100 open-webui" }

Write-Host "Open WebUI da chay." -ForegroundColor Green
docker compose logs open-webui 2>$null | Select-String "Running upgrade|Project tracker" | Select-Object -Last 5
