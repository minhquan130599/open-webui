# Build image từ code hiện tại, đưa lên server và cập nhật với thời gian gián đoạn ngắn nhất.
#
#   .\release.ps1 -Server dev01@10.0.0.5                 build + gửi + deploy (thư mục server mặc định ~/ncschatui)
#   .\release.ps1 -Server dev01@10.0.0.5 -RemoteDir ~/ncschatui
#   .\release.ps1                                        dùng lại server/thư mục của lần chạy trước
#   .\release.ps1 -SkipBuild                             gửi lại image đã build cho commit hiện tại
#   .\release.ps1 -NoDeploy                              chỉ build + gửi file .tar, deploy sau trên server
#   .\release.ps1 -WithCompose                           gửi kèm deploy\docker-compose.yaml (bản trên server được sao lưu)
#
# Trên server: <RemoteDir>/deploy (docker-compose.yaml, .env, deploy.sh) và <RemoteDir>/images (file .tar).
# Rollback trên server:  cd <RemoteDir>/deploy && ./deploy.sh rollback
# Cần OpenSSH client (có sẵn trên Windows 10+); nên cài SSH key để không phải gõ mật khẩu nhiều lần.
param(
    [string]$Server,
    [string]$RemoteDir,
    [switch]$SkipBuild,
    [switch]$NoDeploy,
    [switch]$WithCompose
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$targetFile = Join-Path $PSScriptRoot ".release-target"

function Fail($message) { Write-Host "LOI: $message" -ForegroundColor Red; exit 1 }
function Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }

# Server đích: tham số, hoặc lần chạy trước
if (Test-Path $targetFile) {
    $saved = Get-Content $targetFile | ConvertFrom-Json
    if (-not $Server) { $Server = $saved.Server }
    if (-not $RemoteDir) { $RemoteDir = $saved.RemoteDir }
}
if (-not $Server) { Fail "chua chi dinh server: .\release.ps1 -Server user@host" }
if (-not $RemoteDir) { $RemoteDir = "~/ncschatui" }
@{ Server = $Server; RemoteDir = $RemoteDir } | ConvertTo-Json | Set-Content $targetFile -Encoding utf8

# 1. Phiên bản = mã commit hiện tại
$dirty = git -C $repo status --porcelain --untracked-files=no
if ($dirty) { Write-Host "Canh bao: co thay doi chua commit, image se gom ca cac thay doi nay" -ForegroundColor Yellow }
$version = git -C $repo rev-parse --short HEAD
if ($LASTEXITCODE -ne 0) { Fail "khong doc duoc git commit" }
$image = "ncs-chatui:$version"
Step "Phien ban: $(git -C $repo log -1 --format='%h %s')"

# 2. Build (thương hiệu lấy từ docker\.env nếu có)
if (-not $SkipBuild) {
    $brand = "NCS AI (Knight)"; $logo = "light"
    $envFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^BRAND_NAME=(.+)$') { $brand = $Matches[1].Trim('"', "'") }
            if ($line -match '^LOGO_STYLE=(.+)$') { $logo = $Matches[1].Trim('"', "'") }
        }
    }
    Step "Build $image"
    docker build $repo -t $image -t ncs-chatui:latest -t open-webui-local:latest `
        --build-arg BUILD_HASH=$version --build-arg "BRAND_NAME=$brand" --build-arg LOGO_STYLE=$logo
    if ($LASTEXITCODE -ne 0) { Fail "docker build that bai. Neu log co 'rpc error ... EOF': Docker het RAM, xem .wslconfig" }
} else {
    docker image inspect $image *> $null
    if ($LASTEXITCODE -ne 0) { Fail "chua co image $image, bo -SkipBuild de build" }
}

# 3. Đóng gói (chỉ tag phiên bản: server không bị đổi :latest ngoài ý muốn)
$outDir = Join-Path $PSScriptRoot "images"
New-Item -ItemType Directory -Force $outDir | Out-Null
$tarName = "ncs-chatui-$version.tar"
$tar = Join-Path $outDir $tarName
if (-not (Test-Path $tar)) {
    Step "Dong goi $image -> images\$tarName"
    docker save -o $tar $image
    if ($LASTEXITCODE -ne 0) { Remove-Item $tar -ErrorAction SilentlyContinue; Fail "docker save that bai" }
}
Write-Host ("Kich thuoc: {0:N2} GB" -f ((Get-Item $tar).Length / 1GB))
# Giữ 3 file .tar gần nhất trên máy build
Get-ChildItem $outDir -Filter "ncs-chatui-*.tar" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 | Remove-Item -Force

# 4. Gửi lên server (app trên server vẫn chạy bình thường trong lúc gửi)
Step "Gui len ${Server}:$RemoteDir"
ssh $Server "mkdir -p $RemoteDir/images $RemoteDir/deploy"
if ($LASTEXITCODE -ne 0) { Fail "khong ket noi duoc $Server" }
scp -C $tar "${Server}:$RemoteDir/images/$tarName.part"
if ($LASTEXITCODE -ne 0) { Fail "gui file .tar that bai" }
ssh $Server "mv $RemoteDir/images/$tarName.part $RemoteDir/images/$tarName"
scp (Join-Path $PSScriptRoot "deploy\deploy.sh") "${Server}:$RemoteDir/deploy/deploy.sh"
if ($LASTEXITCODE -ne 0) { Fail "gui deploy.sh that bai" }
ssh $Server "sed -i 's/\r$//' $RemoteDir/deploy/deploy.sh && chmod +x $RemoteDir/deploy/deploy.sh"
if ($WithCompose) {
    ssh $Server "cp $RemoteDir/deploy/docker-compose.yaml $RemoteDir/deploy/docker-compose.yaml.bak-$(Get-Date -Format yyyyMMdd_HHmmss) 2>/dev/null; true"
    scp (Join-Path $PSScriptRoot "deploy\docker-compose.yaml") "${Server}:$RemoteDir/deploy/docker-compose.yaml"
    if ($LASTEXITCODE -ne 0) { Fail "gui docker-compose.yaml that bai" }
}

# 5. Deploy (-t: cho phép sudo hỏi mật khẩu nếu user chưa thuộc nhóm docker)
if ($NoDeploy) {
    Write-Host "Da gui xong. Tren server: cd $RemoteDir/deploy && ./deploy.sh deploy ../images/$tarName" -ForegroundColor Green
    exit 0
}
Step "Deploy tren server"
ssh -t $Server "cd $RemoteDir/deploy && ./deploy.sh deploy ../images/$tarName"
if ($LASTEXITCODE -ne 0) { Fail "deploy that bai (xem log o tren). Ban dang chay tren server khong bi doi neu loi o buoc chay thu." }
Write-Host "Hoan tat $image. Rollback: ssh -t $Server 'cd $RemoteDir/deploy && ./deploy.sh rollback'" -ForegroundColor Green
