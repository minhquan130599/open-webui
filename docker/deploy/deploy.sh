#!/usr/bin/env bash
# Cập nhật / rollback NCS AI (Knight) với thời gian gián đoạn ngắn nhất.
#
# Cấu trúc trên server:
#   <thư mục>/deploy   docker-compose.yaml, .env, deploy.sh (file này), backups/, .deploy-history
#   <thư mục>/images   các file image .tar
#
# Lệnh:
#   ./deploy.sh deploy <file.tar | đường dẫn trong ../images | image:tag>   cập nhật lên bản mới
#   ./deploy.sh trial <file.tar | image:tag>   chạy thử bản mới ở 127.0.0.1:${STANDBY_PORT}, bản chính không đổi
#   ./deploy.sh trial-stop                tắt bản chạy thử
#   ./deploy.sh rollback [image:tag]      quay về bản trước (hoặc bản chỉ định)
#   ./deploy.sh status                    bản đang chạy, tình trạng, lịch sử
#   ./deploy.sh versions                  các bản có sẵn để rollback
#   ./deploy.sh restore-db <file.dump>    khôi phục database từ bản sao lưu (dừng app trong lúc khôi phục)
#
# Cách giảm downtime:
#   1. Nạp image, sao lưu database khi app cũ VẪN CHẠY.
#   2. Chạy thử bản mới trong container dự phòng (cổng 127.0.0.1:${STANDBY_PORT}); bản mới lỗi thì dừng,
#      app cũ không bị đụng tới.
#   3. Thay container chính. Nếu nginx có upstream dự phòng (xem HUONG-DAN-DEPLOY.md), người dùng được
#      chuyển sang container dự phòng trong lúc container chính khởi động lại => gần như không gián đoạn.
#   4. Container chính không lên được => tự quay về bản cũ.
#
# Tùy chọn (biến môi trường):
#   STANDBY_PORT=8001   cổng container dự phòng; NO_STANDBY=1 để bỏ bước này
#   NO_BACKUP=1         bỏ sao lưu database
#   KEEP_VERSIONS=5     số bản image (và file .tar) giữ lại để rollback
#   HEALTH_TIMEOUT=300  số giây chờ app sẵn sàng

set -Eeuo pipefail

cd "$(dirname "$(readlink -f "$0")")"
DEPLOY_DIR=$(pwd)
IMAGES_DIR=${IMAGES_DIR:-$DEPLOY_DIR/../images}
ENV_FILE=$DEPLOY_DIR/.env
HISTORY_FILE=$DEPLOY_DIR/.deploy-history
BACKUP_DIR=$DEPLOY_DIR/backups
REPO=ncs-chatui
MAIN=open-webui
STANDBY=open-webui-standby
STANDBY_PORT=${STANDBY_PORT:-8001}
KEEP_VERSIONS=${KEEP_VERSIONS:-5}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-300}

log() { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok() { printf '\033[1;32m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31mLỖI: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || die "không thấy $ENV_FILE"
[ -f "$DEPLOY_DIR/docker-compose.yaml" ] || die "không thấy docker-compose.yaml trong $DEPLOY_DIR"

if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi
compose() { "${DOCKER[@]}" compose --env-file "$ENV_FILE" "$@"; }

env_value() { { grep -E "^$1=" "$ENV_FILE" || true; } | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
WEBUI_PORT=$(env_value WEBUI_PORT); WEBUI_PORT=${WEBUI_PORT:-8000}

current_image() { "${DOCKER[@]}" inspect -f '{{.Config.Image}}' "$MAIN" 2>/dev/null || env_value OWUI_IMAGE; }
image_exists() { "${DOCKER[@]}" image inspect "$1" >/dev/null 2>&1; }

set_env_image() {
    if grep -qE '^OWUI_IMAGE=' "$ENV_FILE"; then
        sed -i "s|^OWUI_IMAGE=.*|OWUI_IMAGE=$1|" "$ENV_FILE"
    else
        printf '\nOWUI_IMAGE=%s\n' "$1" >>"$ENV_FILE"
    fi
}

wait_healthy() { # <port> <label>
    local url="http://127.0.0.1:$1/health" start=$SECONDS
    while ((SECONDS - start < HEALTH_TIMEOUT)); do
        if curl -fsS -m 5 "$url" >/dev/null 2>&1; then
            ok "$2 sẵn sàng sau $((SECONDS - start))s"
            return 0
        fi
        sleep 2
    done
    return 1
}

# Nạp file .tar (nếu cần) và in ra tên image:tag sẽ dùng.
resolve_image() {
    local source=$1 file="" loaded tag
    if [ -f "$source" ]; then file=$source
    elif [ -f "$IMAGES_DIR/$source" ]; then file=$IMAGES_DIR/$source
    fi
    if [ -z "$file" ]; then
        image_exists "$source" || die "không có image hoặc file '$source'"
        echo "$source"
        return
    fi
    log "Nạp image từ $(basename "$file") (app hiện tại vẫn chạy) ..." >&2
    loaded=$("${DOCKER[@]}" load -i "$file" | sed -n 's/^Loaded image: //p')
    [ -n "$loaded" ] || die "docker load không trả về image nào"
    # Ưu tiên tag phiên bản (mã commit) thay vì :latest, để rollback chính xác.
    tag=$(grep -v ':latest$' <<<"$loaded" | grep "^$REPO:" | head -n1 || true)
    if [ -z "$tag" ]; then
        tag="$REPO:$(date +%Y%m%d-%H%M)"
        "${DOCKER[@]}" tag "$(head -n1 <<<"$loaded")" "$tag"
    fi
    echo "$tag"
}

backup_db() {
    [ "${NO_BACKUP:-0}" = 1 ] && { log "Bỏ qua sao lưu database"; return; }
    if [ "$("${DOCKER[@]}" inspect -f '{{.State.Running}}' owui-postgres 2>/dev/null)" != true ]; then
        log "Postgres chưa chạy, bỏ qua sao lưu"
        return
    fi
    mkdir -p "$BACKUP_DIR"
    local file="$BACKUP_DIR/openwebui_$(date +%Y%m%d_%H%M%S).dump"
    log "Sao lưu database -> backups/$(basename "$file")"
    "${DOCKER[@]}" exec owui-postgres pg_dump -U openwebui -d openwebui -Fc >"$file" || { rm -f "$file"; die "pg_dump thất bại"; }
    ls -1t "$BACKUP_DIR"/openwebui_*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f
}

start_standby() { # <image>
    [ "${NO_STANDBY:-0}" = 1 ] && return 0
    "${DOCKER[@]}" rm -f "$STANDBY" >/dev/null 2>&1 || true
    log "Chạy thử $1 trong container dự phòng (127.0.0.1:$STANDBY_PORT) ..."
    OWUI_IMAGE=$1 compose run -d --no-deps --name "$STANDBY" -p "127.0.0.1:$STANDBY_PORT:8080" "$MAIN" >/dev/null
    if ! wait_healthy "$STANDBY_PORT" "Bản mới (dự phòng)"; then
        "${DOCKER[@]}" logs --tail 80 "$STANDBY" 2>&1 || true
        "${DOCKER[@]}" rm -f "$STANDBY" >/dev/null 2>&1 || true
        return 1
    fi
}

stop_standby() { "${DOCKER[@]}" rm -f "$STANDBY" >/dev/null 2>&1 || true; }

# Đổi container chính sang image; lỗi thì trả về 1 (không tự rollback).
switch_main() { # <image>
    set_env_image "$1"
    log "Thay container chính sang $1 ..."
    local start=$SECONDS
    compose up -d --no-deps "$MAIN" >/dev/null
    if ! wait_healthy "$WEBUI_PORT" "Container chính"; then
        "${DOCKER[@]}" logs --tail 80 "$MAIN" 2>&1 || true
        return 1
    fi
    log "Container chính gián đoạn khoảng $((SECONDS - start))s (người dùng được chuyển sang dự phòng nếu nginx đã cấu hình)"
    compose up -d --no-deps monitor >/dev/null 2>&1 || log "Không cập nhật được service monitor (không ảnh hưởng app)"
}

record() { printf '%s %s %s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2" "$3" >>"$HISTORY_FILE"; }

cleanup_old() {
    local keep=("$@") tags tag
    tags=$("${DOCKER[@]}" images "$REPO" --format '{{.Repository}}:{{.Tag}}' | grep -v ':latest$' | grep -v ':<none>$' || true)
    # Giữ KEEP_VERSIONS bản mới nhất theo thời gian tạo, cộng các bản được chỉ định.
    local recent
    recent=$("${DOCKER[@]}" images "$REPO" --format '{{.CreatedAt}}\t{{.Repository}}:{{.Tag}}' |
        { grep -v ':latest$' || true; } | sort -r | head -n "$KEEP_VERSIONS" | cut -f2)
    for tag in $tags; do
        if grep -qxF "$tag" <<<"$recent" || printf '%s\n' "${keep[@]}" | grep -qxF "$tag"; then continue; fi
        "${DOCKER[@]}" rmi "$tag" >/dev/null 2>&1 && log "Xóa image cũ $tag" || true
    done
    if [ -d "$IMAGES_DIR" ]; then
        ls -1t "$IMAGES_DIR"/"$REPO"-*.tar 2>/dev/null | tail -n +$((KEEP_VERSIONS + 1)) | while read -r old; do
            rm -f "$old" && log "Xóa file cũ $(basename "$old")"
        done
    fi
    "${DOCKER[@]}" image prune -f >/dev/null 2>&1 || true
}

rollout() { # <new image> <previous image> <action>
    local new=$1 previous=$2 action=$3
    if ! start_standby "$new"; then
        die "Bản $new không khởi động được. App đang chạy ($previous) KHÔNG bị thay đổi."
    fi
    if ! switch_main "$new"; then
        log "Container chính không lên với $new -> quay về $previous"
        if [ -n "$previous" ] && switch_main "$previous"; then
            stop_standby
            record rollback-auto "$previous" "$new"
            die "Đã tự quay về $previous. Xem log ở trên để sửa bản $new."
        fi
        die "Không quay về được $previous. Container dự phòng ($STANDBY, cổng $STANDBY_PORT) vẫn chạy bản mới; kiểm tra ngay."
    fi
    stop_standby
    record "$action" "$new" "${previous:--}"
    cleanup_old "$new" "$previous"
    ok "Xong: đang chạy $new (bản trước: ${previous:--}). Rollback: ./deploy.sh rollback"
}

cmd_deploy() {
    [ $# -ge 1 ] || die "cách dùng: ./deploy.sh deploy <file.tar | image:tag>"
    local new previous
    new=$(resolve_image "$1")
    previous=$(current_image)
    [ "$new" != "$previous" ] || log "Bản $new đang chạy, vẫn khởi động lại theo yêu cầu"
    backup_db
    rollout "$new" "$previous" deploy
}

cmd_trial() {
    [ $# -ge 1 ] || die "cách dùng: ./deploy.sh trial <file.tar | image:tag>"
    local new
    new=$(resolve_image "$1")
    start_standby "$new" || die "Bản $new không khởi động được (xem log ở trên)."
    ok "Bản $new đang chạy thử tại 127.0.0.1:$STANDBY_PORT; app chính vẫn chạy $(current_image)."
    echo "Mở từ máy bạn:  ssh -L $STANDBY_PORT:127.0.0.1:$STANDBY_PORT <user>@<server>  rồi vào http://localhost:$STANDBY_PORT"
    echo "Xem log:        ${DOCKER[*]} logs -f $STANDBY"
    echo "Đạt yêu cầu:    ./deploy.sh deploy $new      Bỏ:  ./deploy.sh trial-stop"
    echo "Lưu ý: bản thử dùng CHUNG database và file với bản chính; dữ liệu tạo khi test là dữ liệu thật."
}

cmd_rollback() {
    local target=${1:-} previous
    previous=$(current_image)
    if [ -z "$target" ]; then
        [ -f "$HISTORY_FILE" ] || die "chưa có lịch sử deploy; chỉ định bản: ./deploy.sh rollback ncs-chatui:<tag>"
        # Bản đang chạy trước lần chuyển gần nhất sang bản hiện tại.
        target=$(awk -v cur="$previous" '$4 == cur && $5 != "-" { t = $5 } END { print t }' "$HISTORY_FILE")
        [ -n "$target" ] || die "không tìm được bản trước của $previous trong lịch sử; xem ./deploy.sh versions"
    fi
    target=$(resolve_image "$target")
    [ "$target" != "$previous" ] || die "$target đang chạy rồi"
    log "Rollback: $previous -> $target"
    rollout "$target" "$previous" rollback
    log "Lưu ý: nếu bản mới đã đổi cấu trúc database, khôi phục dữ liệu bằng ./deploy.sh restore-db <file trong backups/>"
}

cmd_status() {
    local running
    running=$(current_image)
    echo "Đang chạy : $running"
    if curl -fsS -m 5 "http://127.0.0.1:$WEBUI_PORT/health" >/dev/null 2>&1; then echo "Tình trạng: OK"; else echo "Tình trạng: KHÔNG PHẢN HỒI"; fi
    if "${DOCKER[@]}" ps --format '{{.Names}}' | grep -qx "$STANDBY"; then echo "Cảnh báo  : container dự phòng $STANDBY vẫn chạy"; fi
    echo
    echo "Lịch sử gần đây (thời điểm, hành động, bản mới, bản trước):"
    tail -n 10 "$HISTORY_FILE" 2>/dev/null || echo "  (chưa có)"
}

cmd_versions() {
    echo "Image có sẵn (rollback tức thì):"
    "${DOCKER[@]}" images "$REPO" --format '  {{.Repository}}:{{.Tag}}\t{{.CreatedSince}}\t{{.Size}}' | grep -v ':latest' || true
    echo
    echo "File trong $IMAGES_DIR:"
    ls -1t "$IMAGES_DIR"/*.tar 2>/dev/null | sed 's|.*/|  |' || echo "  (không có)"
    echo
    echo "Bản sao lưu database:"
    ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null | head -n 5 | sed 's|.*/|  |' || echo "  (không có)"
}

cmd_restore_db() {
    local file=${1:-}
    [ -f "$file" ] || [ -f "$BACKUP_DIR/$file" ] || die "cách dùng: ./deploy.sh restore-db <file.dump trong backups/>"
    [ -f "$file" ] || file=$BACKUP_DIR/$file
    read -r -p "Khôi phục database từ $(basename "$file")? Dữ liệu sau thời điểm sao lưu sẽ MẤT. Gõ 'yes': " answer
    [ "$answer" = yes ] || die "đã hủy"
    NO_BACKUP=0 backup_db
    log "Dừng app trong lúc khôi phục ..."
    compose stop "$MAIN" monitor >/dev/null
    "${DOCKER[@]}" exec -i owui-postgres pg_restore -U openwebui -d openwebui --clean --if-exists --no-owner <"$file" ||
        log "pg_restore báo cảnh báo (thường vô hại); kiểm tra app sau khi khởi động"
    compose up -d --no-deps "$MAIN" monitor >/dev/null
    wait_healthy "$WEBUI_PORT" "App" || die "app chưa lên sau khi khôi phục; xem: docker logs $MAIN"
    record restore-db "$(current_image)" "$(basename "$file")"
}

case "${1:-}" in
    deploy) shift; cmd_deploy "$@" ;;
    trial) shift; cmd_trial "$@" ;;
    trial-stop) stop_standby; ok "Đã tắt bản chạy thử" ;;
    rollback) shift; cmd_rollback "$@" ;;
    status) cmd_status ;;
    versions) cmd_versions ;;
    restore-db) shift; cmd_restore_db "$@" ;;
    *) sed -n '2,27p' "$0"; exit 1 ;;
esac
