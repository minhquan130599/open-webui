# Mở NCS AI bằng HTTPS với nginx (Ubuntu)

Sau khi làm xong, người dùng truy cập **`https://ai.ncsgroup.vn`** (có ổ khóa). nginx nhận kết nối
HTTPS ở cổng 443 rồi chuyển vào NCS AI đang chạy nội bộ ở `127.0.0.1:8000`.

```
Trình duyệt ──HTTPS:443──> nginx (chứng chỉ *.ncsgroup.vn) ──HTTP──> 127.0.0.1:8000 (container open-webui)
```

HTTPS là bắt buộc nếu dùng đăng nhập Microsoft/Google (xem `HUONG-DAN-DANG-NHAP-GOOGLE-MICROSOFT.md`).

Thông tin dùng trong tài liệu (thay nếu khác):

| Mục | Giá trị |
|---|---|
| Server | Ubuntu, IP `10.11.33.97`, user `dev01` |
| Thư mục chạy NCS AI | `~/ncschatui/deploy` (chứa `docker-compose.yaml`, `.env`) |
| Tên miền | `ai.ncsgroup.vn` |
| Chứng chỉ | `ai.ncsgroup.vn.crt` + `ai.ncsgroup.vn.key` (wildcard `*.ncsgroup.vn`, Sectigo) |
| Thư mục chứng chỉ trên server | `/etc/ssl/ncschatui/` |

---

## 1. Chép chứng chỉ lên server

Trên máy Windows, mở PowerShell tại thư mục chứa 2 file chứng chỉ:

```powershell
scp .\ai.ncsgroup.vn.crt .\ai.ncsgroup.vn.key dev01@10.11.33.97:~/
```

## 2. Kiểm tra chứng chỉ

Trên server:

```bash
cd ~
grep -c "BEGIN CERTIFICATE" ai.ncsgroup.vn.crt
head -1 ai.ncsgroup.vn.key
openssl x509 -in ai.ncsgroup.vn.crt -noout -subject -issuer -dates
```

Kết quả mong đợi (đã kiểm tra với bộ chứng chỉ hiện tại):

| Lệnh | Kết quả đúng | Nếu khác |
|---|---|---|
| Đếm `BEGIN CERTIFICATE` | `2` (chứng chỉ + chứng chỉ trung gian Sectigo) | Ra `1`: xin file trung gian (ca-bundle) từ nơi cấp, nối vào sau: `cat ai.ncsgroup.vn.crt ca-bundle.crt > fullchain.pem` |
| Dòng đầu file key | `-----BEGIN PRIVATE KEY-----` | `ENCRYPTED PRIVATE KEY`: gỡ mật khẩu `openssl pkey -in ai.ncsgroup.vn.key -out key.plain` rồi dùng `key.plain` |
| `subject` | `CN=*.ncsgroup.vn` (dùng được cho mọi `*.ncsgroup.vn`) | Phải chứa đúng tên miền sẽ dùng |
| `notAfter` | Ngày hết hạn (bộ hiện tại: **04/01/2027**) | Hết hạn thì trình duyệt báo lỗi, xem mục 9 |

## 3. Đặt chứng chỉ vào thư mục cố định

```bash
sudo mkdir -p /etc/ssl/ncschatui
sudo mv ~/ai.ncsgroup.vn.crt /etc/ssl/ncschatui/fullchain.pem
sudo mv ~/ai.ncsgroup.vn.key /etc/ssl/ncschatui/privkey.pem
sudo chown root:root /etc/ssl/ncschatui/*
sudo chmod 644 /etc/ssl/ncschatui/fullchain.pem
sudo chmod 600 /etc/ssl/ncschatui/privkey.pem
```

Kiểm tra chứng chỉ khớp với khóa — **hai dòng in ra phải giống hệt nhau**:

```bash
sudo openssl x509 -in /etc/ssl/ncschatui/fullchain.pem -noout -pubkey | md5sum
sudo openssl pkey -in /etc/ssl/ncschatui/privkey.pem -pubout | md5sum
```

Không để bản sao file `.key` ở nơi khác trên server (thư mục home, thư mục repo...).

## 4. Đưa NCS AI về cổng nội bộ 8000

nginx cần cổng 80 và 443, và NCS AI không nên mở thẳng ra mạng (cổng do Docker mở **không bị
`ufw` chặn**).

Trong `~/ncschatui/deploy/.env`:

```ini
WEBUI_PORT=8000
```

Trong `~/ncschatui/deploy/docker-compose.yaml`, service `open-webui`, sửa `ports` thành:

```yaml
    ports:
      - "127.0.0.1:${WEBUI_PORT:-8000}:8080"
```

Nên làm tương tự cho trang quản lý file RustFS (service `rustfs`) nếu không cần truy cập từ máy khác:

```yaml
    ports:
      - "127.0.0.1:9001:9001"
```

Áp dụng và kiểm tra:

```bash
cd ~/ncschatui/deploy
sudo docker compose up -d
curl -I http://127.0.0.1:8000      # phải ra HTTP/1.1 200
```

## 5. Cài nginx

```bash
sudo apt update
sudo apt install -y nginx
nginx -v
```

## 6. Tạo cấu hình nginx cho NCS AI

Chạy nguyên khối lệnh sau (tạo file `/etc/nginx/sites-available/chatui`):

```bash
sudo tee /etc/nginx/sites-available/chatui > /dev/null <<'EOF'
# Giữ kết nối WebSocket (realtime, kênh chat)
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

# HTTP -> HTTPS
server {
    listen 80;
    server_name ai.ncsgroup.vn;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name ai.ncsgroup.vn;

    ssl_certificate     /etc/ssl/ncschatui/fullchain.pem;
    ssl_certificate_key /etc/ssl/ncschatui/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_session_cache   shared:SSL:10m;

    # Upload file (giới hạn 50 MB trong compose) cộng phần dư
    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        $connection_upgrade;

        # Câu trả lời của AI hiện dần; câu trả lời dài không bị cắt
        proxy_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
EOF
```

Ý nghĩa các phần quan trọng:

| Cấu hình | Lý do |
|---|---|
| `return 301 https://...` | Ai gõ `http://` cũng được chuyển sang `https://` |
| `Upgrade` / `Connection` | WebSocket — thiếu thì tin nhắn kênh, trạng thái realtime không cập nhật |
| `X-Forwarded-Proto https` | NCS AI biết đang chạy sau HTTPS (cookie, đăng nhập Microsoft) |
| `proxy_buffering off` | Câu trả lời của model hiện dần thay vì đợi xong mới hiện |
| `proxy_read_timeout 600s` | Câu trả lời dài hoặc tác vụ chạy lâu không bị ngắt ở 60 giây |
| `client_max_body_size 100m` | Mặc định nginx chỉ cho upload 1 MB |

## 7. Bật cấu hình

```bash
sudo ln -sf /etc/nginx/sites-available/chatui /etc/nginx/sites-enabled/chatui
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t                         # phải báo "syntax is ok" và "test is successful"
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

Nếu server bật firewall `ufw`:

```bash
sudo ufw allow 'Nginx Full'           # mở cổng 80 và 443
sudo ufw status
```

## 8. DNS và kiểm tra

`ai.ncsgroup.vn` phải trỏ về IP server (`10.11.33.97`):

- **Chính thức:** nhờ IT thêm bản ghi DNS (DNS nội bộ nếu chỉ dùng trong công ty, DNS công khai nếu
  dùng từ ngoài).
- **Thử tạm:** thêm dòng `10.11.33.97 ai.ncsgroup.vn` vào `/etc/hosts` trên server và vào
  `C:\Windows\System32\drivers\etc\hosts` trên máy dùng thử (mở Notepad bằng quyền Administrator).

Kiểm tra:

```bash
getent hosts ai.ncsgroup.vn           # phải ra 10.11.33.97
curl -I https://ai.ncsgroup.vn        # phải ra mã 200 hoặc 30x, không báo lỗi chứng chỉ
echo | openssl s_client -connect ai.ncsgroup.vn:443 -servername ai.ncsgroup.vn 2>/dev/null | grep -E "subject=|issuer=|Verify return code"
```

`Verify return code: 0 (ok)` là chứng chỉ và chuỗi trung gian đúng.

Mở trình duyệt vào **`https://ai.ncsgroup.vn`** — phải thấy ổ khóa. Từ đây luôn dùng địa chỉ này,
không dùng `http://10.11.33.97:...` (đăng nhập Microsoft sẽ lỗi nếu mở bằng IP).

Nếu dùng đăng nhập Microsoft/Google, trong `.env` phải có `PUBLIC_URL=https://ai.ncsgroup.vn`.

## 9. Gia hạn chứng chỉ

Chứng chỉ hiện tại hết hạn **04/01/2027**. Trước ngày đó, lấy bộ chứng chỉ mới rồi:

```bash
# chép 2 file mới lên server như mục 1, rồi:
sudo mv ~/ai.ncsgroup.vn.crt /etc/ssl/ncschatui/fullchain.pem
sudo mv ~/ai.ncsgroup.vn.key /etc/ssl/ncschatui/privkey.pem
sudo chown root:root /etc/ssl/ncschatui/*
sudo chmod 644 /etc/ssl/ncschatui/fullchain.pem
sudo chmod 600 /etc/ssl/ncschatui/privkey.pem
sudo nginx -t && sudo systemctl reload nginx
```

Kiểm tra lại ngày hết hạn đang dùng:

```bash
echo | openssl s_client -connect ai.ncsgroup.vn:443 -servername ai.ncsgroup.vn 2>/dev/null | openssl x509 -noout -dates
```

Không cần khởi động lại NCS AI khi đổi chứng chỉ.

## 10. Thêm tên miền khác (cùng chứng chỉ wildcard)

Chứng chỉ `*.ncsgroup.vn` dùng được cho mọi tên miền con. Ví dụ mở thêm `chat.ncsgroup.vn`: sửa
`server_name` trong cả hai khối `server` thành `server_name ai.ncsgroup.vn chat.ncsgroup.vn;`, chạy
`sudo nginx -t && sudo systemctl reload nginx`, và thêm bản ghi DNS. Đăng nhập Microsoft chỉ chạy
trên tên miền khớp với `PUBLIC_URL` và redirect URI đã đăng ký.

---

## 11. Lỗi thường gặp

| Hiện tượng | Nguyên nhân / cách sửa |
|---|---|
| `nginx -t` báo `cannot load certificate` / `no such file` | Sai đường dẫn hoặc tên file ở mục 3; kiểm tra `ls -l /etc/ssl/ncschatui/` |
| `nginx -t` báo `key values mismatch` | File `.key` không thuộc chứng chỉ này; chạy lại kiểm tra `md5sum` ở mục 3 |
| `bind() to 0.0.0.0:443 failed (Address already in use)` | Cổng 443 đang bị chiếm (thường là container NCS AI cũ); làm lại mục 4, xem ai đang giữ cổng: `sudo ss -ltnp \| grep -E ':80 \|:443 '` |
| Trình duyệt báo **502 Bad Gateway** | NCS AI chưa chạy hoặc không ở `127.0.0.1:8000`; `curl -I http://127.0.0.1:8000`, `sudo docker compose ps`, xem log `sudo tail -50 /var/log/nginx/error.log` |
| Trình duyệt báo **504 Gateway Timeout** khi AI trả lời lâu | Tăng `proxy_read_timeout` trong mục 6 |
| Upload file báo **413 Request Entity Too Large** | Tăng `client_max_body_size` |
| Tin nhắn kênh không tự cập nhật, phải tải lại trang | Thiếu 2 dòng `Upgrade` / `Connection` hoặc khối `map` ở mục 6 |
| `curl` báo `Could not resolve host` | DNS chưa trỏ về server (mục 8) |
| Trình duyệt báo chứng chỉ không hợp lệ trên điện thoại / một số máy | Thiếu chứng chỉ trung gian: kiểm tra mục 2 phải đếm ra `2` |
| Vẫn vào được bằng `http://10.11.33.97:8000` từ máy khác | `ports` chưa có `127.0.0.1:` ở mục 4 |

Log nginx: `/var/log/nginx/access.log`, `/var/log/nginx/error.log`.
Log NCS AI: `cd ~/ncschatui/deploy && sudo docker compose logs --tail 200 open-webui`.
