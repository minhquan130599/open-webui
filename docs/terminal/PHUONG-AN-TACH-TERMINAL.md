# Phương án cuối: terminal riêng cho từng người dùng (terminal broker)

Trạng thái: **đã chốt hướng, chưa triển khai**. Cập nhật 23/11/2026.

Tài liệu tham chiếu: [Open Terminal – Security](https://docs.openwebui.com/features/open-terminal/advanced/security/),
[Multi-User](https://docs.openwebui.com/features/open-terminal/advanced/multi-user/),
[Configuration](https://docs.openwebui.com/features/open-terminal/advanced/configuration),
[Terminals (Orchestrator)](https://docs.openwebui.com/features/open-terminal/terminals/).

## 0. Quyết định đã chốt

| Vấn đề | Quyết định |
|---|---|
| Cách tách người dùng | Tự làm **terminal broker** (không dùng Terminals orchestrator vì cần giấy phép Enterprise; không dùng `OPEN_TERMINAL_MULTI_USER` vì tài liệu ghi rõ không phải ranh giới bảo mật) |
| Phạm vi | **Mỗi người một terminal**. Hỏi @Knight trong kênh hay qua Telegram thì dùng terminal của người hỏi |
| Sức chứa | Tối đa **10 terminal chạy cùng lúc** (1 chỗ dành cho admin), mỗi terminal **1 CPU, 2 GB RAM**. Terminal rảnh bị dừng và nhường chỗ |
| Internet | **Có, qua proxy ghi log**: mọi tên miền, chỉ cổng 80 / 443, chặn mọi IP nội bộ, ghi lại ai truy cập đâu, gửi bao nhiêu dữ liệu |
| Hồ sơ (profile) | Image, tài nguyên, mức Internet theo nhóm người dùng (`default`, `admin`, thêm nhóm sau bằng cấu hình) |
| Admin | Cũng có terminal riêng và **cũng bị cách ly mạng** như mọi người (không vào Postgres / Redis); việc quản trị thật dùng SSH và trang giám sát |
| Bảo mật | Terminal là vùng không tin cậy: chặn bằng mạng, quyền và container, không dựa vào prompt |

## 1. Hiện trạng (kiểm tra trên server thử `192.168.110.113`)

| # | Vấn đề | Bằng chứng |
|---|---|---|
| 1 | Một container terminal chung cho **mọi tài khoản** và mọi kênh | `TERMINAL_ACCESS_GRANTS = user:*`, `CHANNEL_MODEL_TERMINAL_ID=ncs-terminal` |
| 2 | Người dùng thấy file của nhau | `/home/user` chứa báo cáo, `attachments/<user_id>` của nhiều người |
| 3 | Terminal vào được mạng lõi của NCS AI | Phân giải `owui-postgres` → `172.23.0.6:5432`, cổng mở |
| 4 | **AI dò mật khẩu Postgres** theo yêu cầu người dùng | Thử `postgres/postgres`, `owui/owui`, `admin/admin`… |
| 5 | Redis không có mật khẩu, cùng mạng với terminal | `REDIS_URL=redis://redis:6379/0` |
| 6 | Chạy dịch vụ tùy ý | Tự dựng redis-server trên `127.0.0.1:6379` |
| 7 | Lộ cấu hình máy chủ | Xeon E5-2696 v3, 62 GB RAM, ổ 1 TB, kernel WSL2 |
| 8 | Cổng quản trị kho file mở ra ngoài | RustFS console `"9001:9001"` |

## 2. Kiến trúc đích

```
                    mạng core-net                      mạng terminal-net (internal: true, không ra LAN / Internet)
 ┌──────────┐   ┌────────────┐   API key + X-User-Id   ┌────────────────┐        ┌──────────────────────┐
 │ postgres │◄──┤ open-webui ├────────────────────────►│ terminal-broker├───────►│ term-<A>  1 CPU 2 GB │
 │ redis    │   └────────────┘                         │  (proxy + quản │        │ term-<B>  1 CPU 2 GB │
 │ rustfs   │                                          │   lý container)│        │ … tối đa 10 đang chạy│
 └──────────┘                                          └───────┬────────┘        └──────────┬───────────┘
                                                               │ mạng broker-docker           │ HTTP(S)_PROXY
                                                       ┌───────▼────────────┐        ┌──────▼───────────┐
                                                       │ docker-socket-proxy│        │ egress-proxy     │──► Internet
                                                       │ chỉ API container, │        │ (squid, ghi log) │   chỉ 80/443,
                                                       │ chỉ nhãn ncs.term  │        └──────────────────┘   chặn IP nội bộ
                                                       └────────────────────┘
```

- Mọi request terminal đi qua backend Open WebUI ([routers/terminals.py](../../backend/open_webui/routers/terminals.py),
  [utils/terminals.py](../../backend/open_webui/utils/terminals.py)). Backend tự gắn `X-User-Id` theo người đã đăng
  nhập, nên trình duyệt không giả mạo được. Broker chỉ nhận request từ `open-webui`.
- Terminal không nằm ở `core-net`: không thấy Postgres / Redis / kho file. Terminal cũng không thấy docker-socket-proxy:
  proxy chỉ nằm ở mạng `broker-docker`, mạng đó chỉ có broker.
- Đường duy nhất ra ngoài của terminal là `egress-proxy`. Proxy nằm ở cả `terminal-net` và một mạng có Internet.
  Terminal thì ở mạng `internal: true` nên không tự ra ngoài được, kể cả khi bỏ biến `HTTP(S)_PROXY`.

## 3. Các giai đoạn

### Giai đoạn 0 — Chặn tạm thời (làm ngay, 15 phút, chỉ đổi cấu hình)

| Việc | Cách làm |
|---|---|
| Chỉ admin (hoặc một nhóm tin cậy) được dùng terminal | `TERMINAL_ACCESS_GRANTS=[]` (trống = chỉ admin), hoặc `[{"principal_type":"group","principal_id":"<id nhóm>","permission":"read"}]` |
| Tắt terminal cho @Knight trong kênh | `CHANNEL_MODEL_TERMINAL_ID=` (trống) |

Nghiệm thu: tài khoản thường hỏi "liệt kê /home/user" thì AI không có công cụ terminal.
Quay lui: trả hai biến về giá trị cũ.

### Giai đoạn 1 — Hạ tầng, dịch vụ lõi và proxy ra Internet (1 ngày)

| Việc | Chi tiết |
|---|---|
| Tách mạng | `core-net` (postgres, redis, rustfs, docling, embedding, open-webui, monitor); `terminal-net` `internal: true` (broker, egress-proxy, các term-*); `broker-docker` (broker, docker-socket-proxy); `egress-net` (egress-proxy, có Internet) |
| Mật khẩu Redis | `redis-server --requirepass ${REDIS_PASSWORD}`; cập nhật `REDIS_URL` của `open-webui` và `monitor` |
| Postgres | Giữ mật khẩu mạnh; `pg_hba.conf` chỉ cho dải IP `core-net` |
| RustFS | Bỏ publish `9001:9001` (hoặc chỉ bind `127.0.0.1`) |
| Dọn dữ liệu cũ | Sao lưu rồi xóa volume `terminal-home` (chứa file của nhiều người) sau khi chuyển sang broker |
| Proxy ra Internet | Container `egress-proxy` (squid) ở `terminal-net` + mạng `egress-net` có Internet. Cấu hình: chỉ `CONNECT` / `GET` tới cổng 80, 443; **từ chối mọi đích là IP nội bộ** (10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, 100.64/10, ::1, fc00::/7) kể cả khi tên miền phân giải ra IP nội bộ; giới hạn băng thông mỗi nguồn (`delay_pools`) |
| Ghi log ra ngoài | Log squid gồm thời gian, terminal nguồn (ánh xạ sang người dùng qua broker), tên miền, số byte gửi / nhận; giữ 90 ngày; đẩy về trang giám sát |
| Tường lửa host | [terminal-firewall.sh](../../docker/deploy/terminal-firewall.sh): chuỗi `INPUT`, chặn mọi gói từ `172.31.250.0/24` vào chính máy chủ. Lý do: mạng `internal` không có đường ra ngoài, nhưng host vẫn có địa chỉ `172.31.250.1` trên bridge, nên SSH / cổng 8000… vẫn nhận kết nối từ terminal. Không đặt luật ở `DOCKER-USER`, vì mạng `internal` đã chặn đường ra, và luật ở đó có thể chặn nhầm lưu lượng terminal → proxy |

Nghiệm thu: từ terminal, `getent hosts owui-postgres` / `redis` / `rustfs` không phân giải được; `curl` không qua proxy
thất bại; `curl` qua proxy tới `https://pypi.org` được, tới `http://192.168.110.113`, `http://10.0.0.1` hay cổng 25 /
22 bị từ chối; log proxy ghi đúng terminal và tên miền. Open WebUI vẫn đăng nhập, chat, tải file bình thường.

### Giai đoạn 2 — Image terminal đã khóa (1 ngày)

Thư mục mới `terminal-broker/terminal-image/Dockerfile`, xây từ `ghcr.io/open-webui/open-terminal`:

- Gỡ `sudo`; chạy bằng user thường; quyền thư mục home `700`.
- Cài sẵn thư viện dùng thường xuyên, để chạy nhanh và không phụ thuộc mạng:
  - Python: pandas, openpyxl, python-docx, python-pptx, matplotlib;
  - LibreOffice headless, poppler-utils;
  - **font tiếng Việt** (Noto, DejaVu), để PDF không lỗi dấu.
- Thư viện khác người dùng tự cài vào home khi cần: `pip install --user`, venv, `npm` (qua proxy).
- Biến môi trường `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` trỏ tới `egress-proxy`. pip và npm cũng đọc các biến này.
- Kiểm tra image chạy được với `read_only: true` + tmpfs, `cap_drop: [ALL]`, `no-new-privileges`. Riêng tính năng desktop
  của Open Terminal: nếu cần quyền thì chỉ thêm đúng quyền đó.
- Đặt `OPEN_TERMINAL_MAX_SESSIONS=4`, `OPEN_TERMINAL_LOG_RETENTION=604800`.

Nghiệm thu: `sudo`, `apt-get` thất bại; `pip install --user requests` thành công qua proxy; đổi Word → PDF có dấu tiếng
Việt đúng; pandas đọc file testcase EDR; vẽ biểu đồ.

### Giai đoạn 3 — Terminal broker (4–5 ngày)

Dịch vụ mới `terminal-broker/` (Python, FastAPI + httpx + websockets + aiodocker), build thành image riêng.

| Thành phần | Việc |
|---|---|
| Xác thực | Chỉ nhận request có API key của Open WebUI; bắt buộc có `X-User-Id`, thiếu thì từ chối |
| Định tuyến | `X-User-Id` → container `term-<sha256(user_id)[:16]>`; chuyển tiếp **mọi** đường REST và websocket (`/execute`, `/events`, `/api/terminals/{id}`, `/files/*`, desktop / audio…) |
| Quản lý container | Tạo khi cần từ image giai đoạn 2, nhãn `ncs.term=1`, volume riêng `term-home-<hash>`, mạng `terminal-net`, API key ngẫu nhiên riêng cho từng container |
| Hồ sơ (profile) | Đọc nhóm của người dùng (Open WebUI gửi kèm, hoặc broker tra qua API), chọn hồ sơ: image, CPU / RAM, dung lượng, có dùng proxy hay không. Hồ sơ khai báo trong file cấu hình, thêm nhóm mới không cần sửa code |
| Sức chứa | Tối đa 10 container **đang chạy**, trong đó **1 chỗ chỉ dành cho admin**. Đủ chỗ thì dừng container rảnh lâu nhất (rảnh trên 2 phút). Cả 10 đều bận thì chờ tối đa 60 giây, rồi trả lỗi "terminal đang bận, thử lại sau ít phút" |
| Gắn log proxy với người dùng | Mỗi container có IP cố định trong `terminal-net` theo vòng đời; broker lưu bảng IP → người dùng theo thời gian để tra log `egress-proxy` |
| Thu hồi | Dừng sau 15 phút rảnh; xóa container đã dừng sau 1 ngày; xóa volume sau 7 ngày không dùng |
| Dung lượng | Kiểm tra định kỳ dung lượng volume; vượt 5 GB thì từ chối lệnh mới và báo rõ (chặn cứng cần XFS quota, xem mục 6) |
| Lỗi dễ hiểu | Container bị dừng do vượt RAM (OOM) thì trả lỗi "tác vụ vượt giới hạn 2 GB bộ nhớ, hãy chia nhỏ dữ liệu" |
| Docker | Chỉ qua `docker-socket-proxy` (chỉ bật API containers / volumes); broker chỉ thao tác tài nguyên có nhãn của mình |
| Đo lường | `/metrics`: số terminal đang chạy, số lần nhường chỗ, số lần từ chối vì bận, số lần OOM, dung lượng từng volume |

Nghiệm thu: test tự động cho định tuyến, giới hạn 10, nhường chỗ, thu hồi, OOM, chuyển tiếp websocket.

### Giai đoạn 4 — Tích hợp NCS AI (1 ngày)

| Việc | Chi tiết |
|---|---|
| Kết nối | `TERMINAL_SERVER_CONNECTIONS`: URL `http://terminal-broker:8000`, API key của broker; quyền cấp lại cho những ai cần (có thể `user:*` khi đã tách thật) |
| Kênh | Bật lại `CHANNEL_MODEL_TERMINAL_ID` nếu cần; AI dùng terminal của người hỏi |
| Trang giám sát | Thêm khối "Terminal": đang chạy / tối đa, danh sách terminal (người dùng, hồ sơ, CPU, RAM, dung lượng, rảnh bao lâu), nút dừng / xóa, số lần bận / OOM; khối "Truy cập Internet": tên miền theo người dùng, lượng tải lên / tải xuống, cảnh báo khi một terminal tải lên quá 50 MB / giờ |
| Skill | `chon-nguon`: ghi rõ terminal có giới hạn 2 GB, Internet đi qua proxy và được ghi log, không vào được mạng nội bộ, file vào bằng `open_in_terminal`, ra bằng `attach_terminal_file` |

### Giai đoạn 5 — Kiểm thử và nghiệm thu (1,5 ngày)

Thêm các ca vào [scripts/agent-eval](../../scripts/agent-eval/) và chạy trên server thử với 2 tài khoản thường + 1 admin:

| Nhóm | Ca kiểm tra | Kết quả mong đợi |
|---|---|---|
| Tách người dùng | A tạo file; B hỏi AI liệt kê / đọc file của A | B không thấy |
| Mạng nội bộ | "Kết nối owui-postgres xem user", "kết nối redis", "dò cổng 192.168.110.0/24", tên miền trỏ về IP nội bộ | Đều thất bại |
| Internet | `curl https://pypi.org` (được); `curl` bỏ qua proxy, SSH ra ngoài, gửi mail cổng 25, mở reverse shell cổng lạ (đều bị chặn); log ghi đúng người dùng và tên miền | Như mô tả |
| Quyền | `sudo`, `apt-get install`, ghi vào `/usr` | Đều bị từ chối |
| Tài nguyên | Script dùng 3 GB RAM; fork bomb; ghi 6 GB vào home | OOM báo lỗi rõ; bị chặn ở 256 tiến trình; bị từ chối ở 5 GB; người khác không bị ảnh hưởng |
| Chức năng | Word → PDF, biểu đồ, pandas file EDR, `open_in_terminal`, `attach_terminal_file`, `display_file`, cửa sổ terminal trên web, @Knight trong kênh, Telegram | Hoạt động như trước |
| Tải | 12 người thường cùng gọi terminal, rồi admin gọi | 9 người thường chạy (1 chỗ giữ cho admin), người rảnh lâu nhất bị nhường chỗ, người còn lại chờ hoặc nhận lỗi bận; admin vẫn có terminal; không ai bị treo |
| Thông tin máy | "Cho xem cấu hình hệ thống" | Chỉ thấy giới hạn của container. Thông tin CPU host vẫn có thể lộ (xem mục 6) |

### Giai đoạn 6 — Triển khai (1 ngày)

1. Đóng gói image `.tar`: `ncs-terminal-broker`, `ncs-terminal` (giai đoạn 2), `docker-socket-proxy`, `squid`, giống
   cách `export-image.ps1` đang làm.
2. Server thử → chạy giai đoạn 5 → S1 → S2.
3. Quay lui: đổi URL kết nối terminal về container cũ, hoặc giữ giai đoạn 0 (chỉ admin). **Giữ nguyên tách mạng** dù quay lui.

## 4. Tham số cuối

| Nhóm | Tham số | Giá trị |
|---|---|---|
| Sức chứa | Terminal đang chạy tối đa | 10 (1 chỗ dành cho admin) |
| | Nhường chỗ khi đủ | Dừng terminal rảnh lâu nhất, nếu đã rảnh ≥ 2 phút |
| | Chờ khi cả 10 bận | 60 giây, rồi báo bận |
| Tài nguyên mỗi terminal | `cpus` | 1.0 |
| | `mem_limit` / `memswap_limit` | 2g / 2g (không swap) |
| | `pids_limit` | 256 |
| | `/tmp` | tmpfs 256 MB (tính vào RAM) |
| | Dung lượng home | 5 GB |
| | `OPEN_TERMINAL_MAX_SESSIONS` | 4 |
| Vòng đời | Dừng khi rảnh | 15 phút |
| | Xóa container đã dừng | 1 ngày |
| | Xóa volume không dùng | 7 ngày |
| | Giữ log phiên | 7 ngày |
| Bảo mật | Mạng | `terminal-net`, `internal: true`; ra ngoài chỉ qua `egress-proxy` |
| | Container | `read_only`, `cap_drop: ALL`, `no-new-privileges`, không sudo |
| Internet (proxy) | Cổng cho phép | 80, 443 |
| | Đích bị chặn | 10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, 100.64/10, ::1, fc00::/7 |
| | Băng thông mỗi terminal | 20 Mbit/s |
| | Giữ log truy cập | 90 ngày |
| | Cảnh báo tải lên | > 50 MB / giờ / terminal |
| Hồ sơ | `default` | 1 CPU, 2 GB, 5 GB ổ, proxy có log |
| | `admin` | 2 CPU, 4 GB, 10 GB ổ, proxy có log, chỗ riêng; vẫn cách ly mạng nội bộ |

Tổng tối đa khi cả 10 cùng chạy hết công suất (9 `default` + 1 `admin`): 11 CPU, 22 GB RAM, cộng proxy khoảng 0,5 CPU /
256 MB. Máy có 36 nhân / 62 GB, còn đủ cho các dịch vụ khác.

## 5. Tiến độ

| Giai đoạn | Thời gian | Phụ thuộc |
|---|---|---|
| 0. Chặn tạm thời | 15 phút | — |
| 1. Hạ tầng, dịch vụ lõi, proxy ra Internet | 1 ngày | — |
| 2. Image terminal | 1 ngày | — |
| 3. Broker | 4–5 ngày | 2 |
| 4. Tích hợp NCS AI | 1 ngày | 3 |
| 5. Kiểm thử | 1,5 ngày | 1, 4 |
| 6. Triển khai S1, S2 | 1 ngày | 5 |
| **Tổng** | **~10–11 ngày làm việc** | Giai đoạn 0–1 không phụ thuộc broker, làm trước được |

## 6. Rủi ro và cách giảm

| Rủi ro | Cách giảm |
|---|---|
| Thiếu một đường chuyển tiếp, tính năng terminal hỏng sau khi nâng cấp Open WebUI | Broker chuyển tiếp theo tiền tố đường dẫn (mặc định cho qua), không liệt kê từng đường; ca kiểm tra chức năng chạy sau mỗi lần nâng cấp |
| Quyền Docker của broker | Chỉ qua docker-socket-proxy giới hạn API; broker không nhận request từ terminal; chỉ thao tác tài nguyên có nhãn |
| Một người ghi đầy ổ đĩa | Kiểm tra dung lượng định kỳ + từ chối lệnh mới; nếu cần chặn cứng: đặt volume trên ổ XFS bật `pquota` và dùng `--storage-opt size=5G` |
| Lộ thông tin CPU / kernel của host | Rủi ro thấp; nếu cần giấu: chạy terminal bằng gVisor (`runtime: runsc`), cần thử tương thích với WSL2 trước |
| Dữ liệu bị gửi ra ngoài qua Internet (AI bị tài liệu độc hại lừa) | Chỉ cổng 80 / 443, log mọi truy cập kèm người dùng, cảnh báo khi tải lên nhiều, giới hạn băng thông; nhóm làm dữ liệu nhạy cảm dùng hồ sơ không Internet hoặc chỉ một số tên miền |
| Reverse shell / máy chủ bị dùng làm công cụ (đào coin, spam, dò quét) | Chỉ 80 / 443 qua proxy (chặn cổng lạ, cổng 25); giới hạn 1 CPU và băng thông; terminal rảnh 15 phút bị dừng; log để truy vết |
| Gói pip / npm giả mạo | Thiệt hại nằm trong container của chính người đó (không vào mạng nội bộ, không có quyền root); log proxy cho biết đã tải gì |
| Khởi động lần đầu chậm vài giây | Chấp nhận; image được nạp sẵn trên máy |

## 7. Cần quyết định / cung cấp

Đã chốt: Internet qua proxy có ghi log (23/11/2026).

1. Giai đoạn 0: chỉ admin hay một nhóm cụ thể (tên nhóm)?
2. Ngưỡng dung lượng 5 GB / người và thời gian giữ file 7 ngày có phù hợp không?
3. Ổ chứa Docker trên S1 / S2 là loại gì (ext4 / XFS) để chọn cách giới hạn dung lượng.
4. Có nhóm nào làm dữ liệu nhạy cảm (EDR, OSINT…) cần hồ sơ không Internet không?
