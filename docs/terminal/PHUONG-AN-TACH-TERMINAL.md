# Phương án tách terminal dùng chung

Trạng thái: **đề xuất, chưa triển khai**. Kiểm tra trên server thử `192.168.110.113` ngày 23/11/2026.

## 1. Hiện trạng

Cấu hình trong [docker/deploy/docker-compose.yaml](../../docker/deploy/docker-compose.yaml):

| Điểm | Thực tế | Hệ quả |
|---|---|---|
| Số terminal | Một container `owui-terminal` (`open-terminal`), một ổ `terminal-home:/home/user` | Mọi người dùng chung một ổ đĩa và một tài khoản Linux |
| Ai dùng được | `TERMINAL_ACCESS_GRANTS = user:*` (mọi tài khoản) | Mọi cuộc chat có bật terminal đều vào chung máy này |
| Kênh | `CHANNEL_MODEL_TERMINAL_ID=ncs-terminal` | Mọi câu @Knight trong mọi kênh cũng vào chung máy này |
| Tách theo phiên | Không có. Kết nối là Open Terminal đơn lẻ, không phải "Terminals orchestrator", nên Open WebUI không gửi `X-Terminal-Context-Id` | Không có ranh giới giữa người dùng / kênh / chat |
| Thư mục `attachments/<user_id>` | Chỉ là quy ước đặt tên khi `open_in_terminal` chép file vào | Lệnh `ls`, `cat` đọc được thư mục của người khác |
| Mạng | Terminal chung mạng Docker mặc định với `postgres`, `redis`, `rustfs`, `open-webui`, `docling`, `embedding` | Lệnh do AI chạy kết nối tới được các dịch vụ nội bộ |
| Redis | Không đặt mật khẩu (`redis://redis:6379/0`) | Từ terminal đọc/ghi được Redis của NCS AI (phiên làm việc, hàng đợi tác vụ) |
| Dữ liệu tồn đọng | `list_files /home/user` thấy báo cáo của nhiều người / kênh: `bao_cao_cong_viec_quan_*`, OSINT, EDR, `make_report.py`… | File của người A nằm lại vô thời hạn, người B hỏi AI vẫn có thể đọc |

Lệnh trong terminal do AI tự viết, và AI có thể bị điều khiển bằng nội dung tài liệu (prompt injection). Vì vậy coi
terminal là **vùng không tin cậy**: mọi thứ terminal chạm tới được đều có thể bị lộ.

## 2. Mục tiêu

1. Người này không đọc được file người khác tạo hoặc chép vào terminal.
2. Kênh này không đọc được file của kênh khác. Thành viên cùng một kênh dùng chung là chấp nhận được, vì họ đã thấy
   chung dữ liệu kênh.
3. Terminal không kết nối được tới Postgres, Redis, kho file và các dịch vụ nội bộ khác.
4. File trong terminal tự xóa sau một thời gian. File cần giữ phải đưa ra kho file NCS AI qua `attach_terminal_file`.
5. Không đưa khóa S3, mật khẩu DB hay token nào vào terminal. File vào ra chỉ qua `open_in_terminal` /
   `attach_terminal_file`, hai tool này có kiểm tra quyền.

## 3. Các bước

### Bước 0 — Làm ngay (vài giờ, không đổi kiến trúc)

- **Tách mạng:**
  - Đưa `open-terminal` sang một mạng riêng `terminal-net`, chỉ `open-webui` tham gia cả hai mạng.
  - Terminal không còn thấy `postgres`, `redis`, `rustfs`, `docling`, `embedding`.
  - Giữ đường ra Internet nếu cần `pip install`. Nếu không cần thì đặt `internal: true` cho `terminal-net`.
- **Đặt mật khẩu Redis** (`--requirepass`, cập nhật `REDIS_URL`). Đây là lớp phòng thủ thứ hai nếu mạng bị cấu hình sai.
- **Dọn dữ liệu cũ** trong volume `terminal-home`: sao lưu nếu cần, rồi xóa các báo cáo / file đính kèm của người dùng.
- **Thu hẹp quyền:**
  - Đổi `TERMINAL_ACCESS_GRANTS` từ `user:*` sang một nhóm cần dùng (ví dụ nhóm kỹ thuật).
  - Cân nhắc để trống `CHANNEL_MODEL_TERMINAL_ID`: báo cáo dự án, đọc bảng tính, đọc file trong kênh đều không cần terminal.
- **Giới hạn tài nguyên:** `mem_limit`, `cpus`, `pids_limit` cho container terminal.

Sau bước 0, rủi ro lớn nhất (vào được DB / Redis / kho file) được chặn. Rủi ro còn lại: người dùng terminal vẫn
thấy file của nhau.

### Bước 1 — Tách theo cuộc trò chuyện / kênh (khuyến nghị, 2–4 ngày)

Open WebUI đã hỗ trợ sẵn việc này với **Terminals orchestrator** (trình quản lý cấp terminal riêng). Code xử lý nằm ở
[utils/terminals.py](../../backend/open_webui/utils/terminals.py): `terminal_context_id`, header `X-Terminal-Context-Id`.

- Thay container `open-terminal` đơn lẻ bằng dịch vụ orchestrator. Nó tạo một container terminal riêng cho mỗi
  context và tự thu hồi khi không dùng.
- Khai báo kết nối với `server_type: "orchestrator"` và
  `config.contexts = {"chat": {"context_id": "chat_id"}, "automation": {"context_id": "automation_id"}}`.
- Kết quả:
  - **mỗi cuộc chat trên web một terminal riêng**;
  - **mỗi kênh một terminal riêng**, vì câu hỏi trong kênh có `chat_id = channel:<id>`;
  - mỗi automation một terminal riêng.
- Cần kiểm tra khi dựng:
  1. Phiên bản orchestrator tương thích với bản Open WebUI đang chạy.
  2. Thời gian thu hồi container rảnh, và ổ đĩa riêng của mỗi context bị xóa khi thu hồi.
  3. `open_in_terminal` / `attach_terminal_file` vẫn chạy (chúng gọi qua `resolve_terminal_connection`, đã gửi
     header context).
  4. Mạng của các container con vẫn tách như bước 0.
  5. Tài nguyên máy chủ: số terminal chạy đồng thời × RAM mỗi container.

### Phương án dự phòng — nếu không dùng được orchestrator

Tự chạy mỗi kênh / nhóm một container `open-terminal` và khai báo nhiều kết nối, mỗi kết nối cấp quyền cho đúng nhóm
của nó. Cách này đơn giản, nhưng không tách được từng người trong cùng nhóm và không tự thu hồi. Chỉ hợp làm cầu nối
trong lúc chờ bước 1.

### Bước 2 — Vận hành

- Xóa file trong terminal quá N ngày (cron trong container hoặc do orchestrator thu hồi).
- Ghi log lệnh terminal theo người dùng (header `X-User-Id` đã có) để tra soát.
- Đưa vào bộ kiểm tra [scripts/agent-eval](../../scripts/agent-eval/) một ca "đọc file người khác". Ca này bắt AI thử
  `ls /home/user/attachments`. Sau bước 1, câu trả lời phải là không thấy gì của người khác.

## 4. Không làm

- **Không cho terminal truy cập S3.** Bucket chứa file của mọi người dùng. Một khóa trong terminal là bỏ qua toàn bộ
  phân quyền, và lệnh do AI viết có thể bị lừa để tải hết dữ liệu ra ngoài. Đã có `open_in_terminal` (vào) và
  `attach_terminal_file` (ra), cả hai đều kiểm tra quyền.
- Không đặt token, mật khẩu, khóa API vào biến môi trường của terminal.

## 5. Đề xuất thứ tự

| Việc | Công sức | Giảm rủi ro |
|---|---|---|
| Bước 0: tách mạng + mật khẩu Redis | ~2 giờ | Cao nhất: chặn đường vào DB / Redis / kho file |
| Bước 0: dọn file cũ + thu hẹp quyền / tắt terminal ở kênh | ~1 giờ | Cao: bỏ dữ liệu đang lộ |
| Bước 1: orchestrator, terminal theo chat / kênh | 2–4 ngày (gồm thử tải) | Cao: tách hẳn người / kênh |
| Bước 2: dọn định kỳ, log, ca kiểm tra | ~1 ngày | Duy trì |
