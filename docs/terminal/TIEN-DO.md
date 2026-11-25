# Tiến độ triển khai: terminal riêng cho từng người dùng

Theo [PHUONG-AN-TACH-TERMINAL.md](PHUONG-AN-TACH-TERMINAL.md). Cập nhật sau mỗi phần.

Đã chốt thêm (23/11/2026): một hồ sơ `default` cho mọi người, admin cùng cấu hình; tổng 10 terminal, 1 chỗ dành cho admin
(admin dùng được mọi chỗ trống, người thường tối đa 9); 5 GB / người, xóa sau 7 ngày; Internet qua proxy có ghi log;
chưa cần hồ sơ không Internet; loại ổ đĩa S1 / S2 chưa rõ (dùng cách kiểm tra dung lượng định kỳ, không cần XFS).

| Giai đoạn | Trạng thái | Ghi chú |
|---|---|---|
| 0. Chặn tạm thời | ⏭️ Bỏ qua theo yêu cầu | Chuyển thẳng sang terminal riêng |
| 1. Hạ tầng, dịch vụ lõi, proxy | ✅ Code xong · ⏳ chờ chạy trên server | Xem chi tiết bên dưới |
| 2. Image terminal | ✅ Code xong · ⏳ chờ build trên máy có Docker | Xem chi tiết bên dưới |
| 3. Terminal broker | ✅ Code xong, 17/17 test qua · ⏳ chờ chạy với Docker thật | Xem chi tiết bên dưới |
| 4. Tích hợp NCS AI | ✅ Code xong · ⏳ chờ chạy trên server | Xem chi tiết bên dưới |
| 5. Kiểm thử | ✅ Bộ kiểm tra viết xong · ⏳ chờ chạy trên server thử | Xem chi tiết bên dưới |
| 6. Triển khai | ✅ Đóng gói + hướng dẫn xong · ⏳ chờ build và cài | Xem chi tiết bên dưới |

**Việc còn lại phải làm trên máy có Docker / trên server** (máy phát triển này Docker không chạy, không SSH được server):
1. Build image: `docker\export-image.ps1 -WithTerminal`. Lần build đầu cần xem image Open Terminal gốc có user `user`
   uid 1000 không (Dockerfile tự kiểm tra), và Open Terminal chạy được với hệ thống file chỉ đọc không.
2. Cài trên server thử theo [HUONG-DAN-DEPLOY.md](../../docker/deploy/HUONG-DAN-DEPLOY.md), mục "Chuyển sang terminal
   riêng cho từng người dùng".
3. Chạy `python -m broker.smoke` (và `--memory`) trong container broker: phải PASS hết.
4. Chạy ca AI: `python scripts/agent-eval/run.py term-postgres term-redis term-lan term-sudo` (gọi model Knight thật).
5. Đạt thì làm trên S1, S2.

## Giai đoạn 1 — Hạ tầng, dịch vụ lõi, proxy

Trạng thái: **code xong**, `docker compose config` hợp lệ. Chưa chạy trên server.

- [docker/deploy/docker-compose.yaml](../../docker/deploy/docker-compose.yaml):
  - mạng `ncs-terminal-net` (`internal: true`, `172.31.250.0/24`), `ncs-egress-net` (chỉ proxy), `ncs-broker-docker`
    (`internal: true`, chỉ broker và docker-socket-proxy); các service cũ giữ ở mạng mặc định;
  - Redis có mật khẩu (`REDIS_PASSWORD` bắt buộc), `REDIS_URL` của `open-webui` và `monitor` đổi theo;
  - RustFS console chỉ mở trên `127.0.0.1:9001`;
  - thêm `terminal-broker`, `docker-socket-proxy` (chỉ CONTAINERS / VOLUMES / EXEC / POST), `egress-proxy`;
  - `open-terminal` cũ chuyển vào profile `legacy-terminal` (mặc định không chạy, chỉ để quay lui).
- [docker/deploy/.env.example](../../docker/deploy/.env.example): `REDIS_PASSWORD`, `TERMINAL_BROKER_ADMIN_KEY`, image
  và giới hạn của terminal. Biến image mới tên `NCS_TERMINAL_IMAGE`, để không đụng `TERMINAL_IMAGE` cũ đang trỏ image
  gốc chưa khóa.
- [terminal-broker/egress-proxy](../../terminal-broker/egress-proxy/): squid chỉ cổng 80 / 443, chặn mọi IP nội bộ
  (kể cả tên miền trỏ vào trong), 20 Mbit/s mỗi terminal, log `ncs` giữ 90 ngày.
- Không thêm luật iptables `DOCKER-USER`: mạng terminal là `internal` (không có gateway), nên lưu lượng không ra
  ngoài được nếu không qua proxy. Luật thêm vào có thể chặn nhầm lưu lượng trong cùng bridge (terminal → proxy).
- Không đổi `pg_hba.conf`: Postgres không còn chung mạng với terminal.

## Giai đoạn 2 — Image terminal

Trạng thái: **code xong**. Chưa build: Docker trên máy phát triển không chạy.

- [terminal-broker/terminal-image/Dockerfile](../../terminal-broker/terminal-image/Dockerfile): từ
  `open-terminal:latest`, gỡ sudo, chạy user `user` (uid 1000, build lỗi nếu khác), home `700`; cài font Noto /
  DejaVu, poppler; LibreOffice và thư viện Python (pandas, openpyxl, python-docx, python-pptx, matplotlib,
  xlsxwriter) chỉ cài khi image gốc thiếu.
- Broker chạy image này với `read_only`, `/tmp` tmpfs 256 MB, `cap_drop: ALL`, `no-new-privileges`, 1 CPU, 2 GB
  không swap, 256 tiến trình.
- Cần kiểm tra khi build lần đầu: Open Terminal có chạy được khi hệ thống file chỉ đọc không. Nếu không, đặt
  `TERMINAL_READ_ONLY=false` cho broker và ghi lại thư mục nó cần ghi.

## Giai đoạn 3 — Terminal broker

Trạng thái: **code xong, 17/17 test qua** (Docker giả lập). Chưa chạy với Docker thật.

- [terminal-broker/broker](../../terminal-broker/broker/):
  - `pool.py`: mỗi người một container `term-<hash>` + volume `term-home-<hash>`, khóa API riêng; tối đa 10 chạy cùng
    lúc, 1 chỗ chỉ admin dùng (admin dùng được mọi chỗ trống); đủ chỗ thì dừng terminal rảnh lâu nhất (≥ 2 phút); cả
    10 bận thì chờ 60 giây rồi báo bận; dừng sau 15 phút rảnh, xóa container sau 1 ngày, xóa volume sau 7 ngày; đo
    dung lượng 10 phút một lần, quá 5 GB thì chỉ cho lệnh dọn dẹp;
  - `proxy.py`: chuyển tiếp REST (kể cả luồng tải file) và websocket (đổi khóa ở tin xác thực đầu tiên); lời gọi
    không có người dùng chỉ được đọc danh sách tool / system prompt (terminal mẫu không chứa dữ liệu); tiến trình bị
    dừng vì RAM (mã 137) có thêm ghi chú dễ hiểu cho AI;
  - `egress.py`: đọc log proxy, gắn với người dùng theo IP từng thời điểm, cảnh báo tải lên > 50 MB / giờ;
  - `app.py`: `/_broker/*` cho trang giám sát, dùng khóa riêng `BROKER_ADMIN_KEY` (Open WebUI không có khóa này nên
    người dùng không gọi được qua đường proxy terminal).
- [terminal-broker/tests/test_broker.py](../../terminal-broker/tests/test_broker.py): 17 test (khóa container, chỗ
  admin, admin thứ hai, nhường chỗ, không lấy terminal đang dùng, 5 yêu cầu đồng thời chỉ tạo 1 container, thu hồi,
  hạn mức, dọn container lạ, định tuyến theo người, sai khóa / thiếu người dùng, OOM, luồng file, websocket, log proxy).

## Giai đoạn 4 — Tích hợp NCS AI

Trạng thái: **code xong**, backend biên dịch được, JavaScript trang giám sát hợp lệ. Chưa chạy trên server.

- Backend gửi thêm `X-User-Role` (admin / user) cùng `X-User-Id` ở mọi đường gọi terminal: hàm
  `terminal_user_headers` trong [utils/terminals.py](../../backend/open_webui/utils/terminals.py), dùng ở
  [routers/terminals.py](../../backend/open_webui/routers/terminals.py) (HTTP, websocket, xem trình duyệt) và
  [utils/tools.py](../../backend/open_webui/utils/tools.py) (AI gọi tool terminal). Backend tự gắn theo người đã đăng
  nhập, trình duyệt không giả mạo được.
- Broker hỗ trợ cả hai cách xác thực websocket của Open WebUI: tin nhắn đầu tiên (phiên terminal, sự kiện, luồng lệnh)
  và header `Authorization` (xem trình duyệt qua relay). Test cho cả hai.
- Kết nối terminal mặc định trỏ `http://terminal-broker:8000`. Server đã chạy phải sửa URL trong Admin.
- Trang giám sát, tab mới **Terminal** ([monitoring/server/terminal.py](../../backend/open_webui/monitoring/server/terminal.py),
  `index.html`):
  - đang chạy / tối đa, giới hạn mỗi terminal, số lần hết chỗ / nhường chỗ / vượt RAM / vượt dung lượng trong 24 giờ;
  - danh sách terminal theo người dùng (tên, admin, trạng thái, rảnh bao lâu, dung lượng), nút Dừng / Xóa (cần xác thực);
  - sự kiện gần đây;
  - Truy cập Internet (cần xác thực): theo người dùng, số yêu cầu / bị chặn, tải lên / tải về, tên miền nhiều nhất,
    cảnh báo tải lên > 50 MB / giờ; xem 24 giờ / 7 / 30 / 90 ngày.
- Skill `chon-nguon`: báo cáo dự án dạng file dùng `export_project_report`; terminal là máy riêng có giới hạn, không vào
  được mạng nội bộ, không thử kết nối hay dò mật khẩu, chia nhỏ dữ liệu khi vượt RAM.

## Giai đoạn 5 — Kiểm thử

Trạng thái: **bộ kiểm tra viết xong**. Chưa chạy trên server.

- Kiểm tra hạ tầng, không cần AI: [terminal-broker/broker/smoke.py](../../terminal-broker/broker/smoke.py), chạy trong
  container broker, đóng vai 2 người dùng:
  - B không đọc được file A tạo; A và B là hai máy khác nhau;
  - không phân giải được owui-postgres / postgres / redis / rustfs / open-webui / docker-socket-proxy;
  - các cổng 22, 8000, 8090, 5432, 6379, 9000, 9001, 2375 của host không kết nối được;
  - Internet qua proxy được (pypi.org), không qua proxy thì không; proxy từ chối 192.168.x, 10.x, 172.17.0.1,
    169.254.169.254 và cổng khác 80 / 443;
  - không sudo, không ghi được `/usr`; đủ thư viện Python, LibreOffice, font; RAM giới hạn 2 GB;
  - `--memory`: tiến trình 3 GB bị dừng kèm ghi chú, terminal vẫn chạy tiếp.
- Ca hỏi AI trong [scripts/agent-eval](../../scripts/agent-eval/): thêm `term-postgres`, `term-redis`, `term-lan`,
  `term-sudo` (các câu trong ảnh lỗi). Bộ chấm soi được nội dung kết quả tool và câu trả lời. Đã thử bộ chấm offline.
- Phát hiện khi viết kiểm tra: mạng `internal` chặn đường ra ngoài, nhưng host vẫn có địa chỉ `172.31.250.1` trên
  bridge, nên SSH / cổng 8000 của host có thể vẫn kết nối được từ terminal. Đã thêm
  [terminal-firewall.sh](../../docker/deploy/terminal-firewall.sh) (chặn `172.31.250.0/24` vào host ở chuỗi INPUT).

## Giai đoạn 6 — Triển khai

Trạng thái: **đóng gói và hướng dẫn xong**. Chưa build, chưa cài.

- [docker/export-image.ps1](../../docker/export-image.ps1) `-WithTerminal`: build `ncs-terminal-broker`,
  `ncs-terminal`, `ncs-egress-proxy`, đóng gói kèm `docker-socket-proxy` và Open Terminal gốc.
- [HUONG-DAN-DEPLOY.md](../../docker/deploy/HUONG-DAN-DEPLOY.md), mục mới "Chuyển sang terminal riêng cho từng người
  dùng": nạp image, thêm biến `.env`, chạy, gỡ terminal cũ, tường lửa, sửa URL trong Admin, kiểm tra, dọn volume cũ,
  quay lui.
- `.gitattributes`: `Dockerfile`, `squid.conf` luôn dùng xuống dòng LF (chạy trong container Linux).

## Cập nhật 23/11/2026 — quy trình build / test / triển khai

- [docker/docker-compose.yaml](../../docker/docker-compose.yaml) (máy build Windows và máy thử T1/T2) có cùng cấu hình
  terminal riêng như `docker/deploy`, kèm `build:` để `update.ps1` build broker / terminal / proxy từ source; thêm
  service `terminal-image` (profile `build`, chỉ để build image `ncs-terminal`).
- [docker/update.ps1](../../docker/update.ps1): build thêm `terminal-broker`, `terminal-image`, `egress-proxy` khi compose
  có các service này.
- [docker/export-image.ps1](../../docker/export-image.ps1) `-WithTerminal`: đóng gói đúng image `update.ps1` vừa build
  (không build lại), cùng code với image NCS AI.
- `TERMINAL_READ_ONLY` truyền được qua `.env` (đặt `false` nếu Open Terminal không chạy được khi chỉ đọc).
- [docker/.env.example](../../docker/.env.example): thêm `REDIS_PASSWORD`, `TERMINAL_BROKER_ADMIN_KEY`, giới hạn terminal.
- Quy trình chi tiết cho từng máy (W → T1/T2 → S1/S2), kèm lưu ý khi sửa `.env` / compose: file
  `E:\key\deloy\QUY-TRINH-BUILD-TEST-TRIEN-KHAI.md` (ngoài repo, vì có IP và tài khoản các máy).

## 25/11: chạy thật lần đầu trên W

- Lỗi: terminal không khởi động (`term-spec` thoát mã 126, `python3-ot: bad interpreter: Operation not permitted`).
  Open Terminal bản mới gắn file capability `cap_setgid` lên `python3-ot` (cho chế độ nhiều người dùng). Container bỏ
  hết quyền nên kernel từ chối chạy file đó. Sửa: `terminal-image/Dockerfile` gỡ file capability khỏi mọi file (chép
  lại file), và dừng build nếu còn sót.
- Lỗi: `broker.smoke` gửi `wait` trong body, nhưng Open Terminal nhận `wait` trên URL, nên lệnh chạy ngầm và trả về rỗng.
  Sửa: gửi `wait` trên URL, lệnh chưa xong thì hỏi lại `/execute/{id}/status`.
- Kết quả trên W (Docker Desktop): `broker.smoke --memory` **32/32 PASS**.
