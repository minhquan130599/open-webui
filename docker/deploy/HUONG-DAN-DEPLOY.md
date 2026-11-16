# Cập nhật và rollback NCS AI (Knight)

Tài liệu cho người vận hành server. Mục tiêu là cập nhật với thời gian gián đoạn ngắn nhất, và quay về bản cũ trong khoảng một phút.

## Cấu trúc thư mục

```
Máy build (Windows)                     Server (Ubuntu)
E:\key\open-webui\docker\               ~/ncschatui/
  release.ps1    build + gửi + deploy     deploy/  docker-compose.yaml, .env, deploy.sh,
  images\        file .tar đã đóng gói             backups/ (bản sao lưu DB), .deploy-history
                                          images/  ncs-chatui-<commit>.tar
```

Mỗi bản được gắn tag theo mã commit (ví dụ `ncs-chatui:fc06c6581`). Server giữ 5 bản gần nhất, cả dạng image lẫn file `.tar`, để rollback ngay mà không phải gửi lại.

## Cập nhật: một lệnh trên máy build

```powershell
cd E:\key\open-webui\docker
.\release.ps1 -Server dev01@<ip-server>          # lần đầu; các lần sau chỉ cần .\release.ps1
```

`release.ps1` build image của commit hiện tại, gửi file `.tar` cùng `deploy.sh` lên server, rồi chạy `deploy.sh deploy`. Trên server, script làm lần lượt:

1. **Nạp image và sao lưu database** (`pg_dump` vào `deploy/backups/`, giữ 10 bản). App cũ vẫn chạy trong suốt bước này.
2. **Chạy thử bản mới** trong container dự phòng `open-webui-standby` ở cổng `127.0.0.1:8001`. Nếu bản mới không khởi động được, script dừng lại và app cũ **không bị thay đổi gì**.
3. **Thay container chính.** Chỉ container `open-webui` được tạo lại. Postgres, Redis và RustFS không bị khởi động lại.
4. **Kiểm tra `/health`.** Nếu container chính không lên được, script **tự quay về bản cũ**.
5. **Dọn dẹp.** Tắt container dự phòng, cập nhật service `monitor`, ghi lịch sử, xóa các bản cũ hơn 5 bản gần nhất.

Muốn test bản mới trước khi thay bản đang chạy: gửi bằng `.\release.ps1 -NoDeploy`, chạy `./deploy.sh trial ../images/ncs-chatui-<commit>.tar` trên server (bản thử ở `127.0.0.1:8001`, dùng chung database với bản chính), test qua `ssh -L 8001:127.0.0.1:8001 <user>@<server>`, rồi `./deploy.sh deploy ...` hoặc `./deploy.sh trial-stop`.

Nếu chỉ muốn gửi file trước rồi cập nhật sau (ví dụ ngoài giờ làm việc), dùng `.\release.ps1 -NoDeploy`. Đến lúc cập nhật thì chạy trên server:

```bash
cd ~/ncschatui/deploy && ./deploy.sh deploy ../images/ncs-chatui-<commit>.tar
```

Nếu `docker-compose.yaml` có thay đổi (ví dụ thêm biến môi trường), thêm `-WithCompose`. Bản trên server được sao lưu thành `docker-compose.yaml.bak-<thời điểm>` trước khi ghi đè.

## Rollback

```bash
cd ~/ncschatui/deploy
./deploy.sh rollback                      # về bản chạy ngay trước bản hiện tại
./deploy.sh rollback ncs-chatui:<commit>  # về một bản chỉ định
./deploy.sh versions                      # các bản có sẵn và các bản sao lưu DB
./deploy.sh status                        # bản đang chạy, tình trạng, lịch sử
```

Rollback dùng image đã có sẵn trên server, nên không phải gửi lại file và cũng đi qua bước chạy thử như khi cập nhật.

**Database:** rollback chỉ đổi image, còn dữ liệu giữ nguyên. Nếu bản mới đã đổi cấu trúc database theo cách bản cũ không đọc được, khôi phục bằng bản sao lưu tạo ngay trước lần cập nhật. Lưu ý: mọi dữ liệu ghi sau thời điểm sao lưu sẽ mất.

```bash
./deploy.sh restore-db openwebui_<thời điểm>.dump
```

## Giảm gián đoạn xuống gần 0: cấu hình nginx một lần

Nếu không làm bước này, người dùng mất kết nối trong lúc container chính khởi động lại, thường khoảng 20–60 giây. Nếu cho nginx chuyển sang container dự phòng khi container chính chưa sẵn sàng, gián đoạn chỉ còn vài giây: tin nhắn kênh và trạng thái realtime tự kết nối lại.

Trong file site của nginx (`/etc/nginx/sites-available/ncschatui`), thêm khối `upstream` và đổi `proxy_pass`:

```nginx
upstream ncs_webui {
    server 127.0.0.1:8000 max_fails=1 fail_timeout=5s;
    server 127.0.0.1:8001 backup;      # container dự phòng, chỉ chạy trong lúc cập nhật
}

server {
    # ... giữ nguyên các dòng khác ...
    location / {
        proxy_pass http://ncs_webui;
        proxy_next_upstream error timeout http_502 http_503;
        # ... giữ nguyên các proxy_set_header, proxy_buffering, timeout ...
    }
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Ngoài lúc cập nhật, cổng 8001 không có gì chạy. nginx chỉ dùng cổng 8000 nên mọi thứ hoạt động như trước. Đổi cổng dự phòng bằng `STANDBY_PORT=8002 ./deploy.sh deploy ...`, nhớ sửa cả trong nginx.

## Tùy chọn của deploy.sh

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `STANDBY_PORT` | `8001` | Cổng của container dự phòng |
| `NO_STANDBY=1` | — | Bỏ bước chạy thử (nhanh hơn, nhưng bản lỗi chỉ bị phát hiện sau khi đã thay container chính) |
| `NO_BACKUP=1` | — | Bỏ bước sao lưu database |
| `KEEP_VERSIONS` | `5` | Số bản image và file `.tar` giữ lại |
| `HEALTH_TIMEOUT` | `300` | Số giây chờ app sẵn sàng |

User chưa thuộc nhóm `docker` thì script tự dùng `sudo docker` (`release.ps1` mở `ssh -t` để gõ được mật khẩu sudo). Muốn khỏi phải gõ mật khẩu: `sudo usermod -aG docker $USER`, rồi đăng nhập lại.

## Khi có sự cố

| Hiện tượng | Xử lý |
|---|---|
| "Bản … không khởi động được" | App cũ vẫn chạy. Xem log in ra (thường do lỗi migration hoặc thiếu biến môi trường) |
| "Đã tự quay về …" | App đang chạy bản cũ. Sửa lỗi rồi release lại |
| `status` báo container dự phòng vẫn chạy | `sudo docker rm -f open-webui-standby` |
| Gửi file chậm | File `.tar` nặng vài GB, `scp -C` đã nén khi gửi. Có thể gửi trước bằng `-NoDeploy` |
