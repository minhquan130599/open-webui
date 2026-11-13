# Trang giám sát hiệu năng NCS AI (Knight)

Trang giám sát là một container riêng (`ncs-monitor`), không nằm trong giao diện NCS AI. Container dùng chung
image với NCS AI nên không phải build hay xuất thêm image nào.

## 1. Cấu hình `.env`

Thêm vào file `.env` (trên máy build và server triển khai):

```env
MONITOR_PORT=8090
MONITOR_USERNAME=admin
MONITOR_PASSWORD=<mật khẩu đăng nhập trang giám sát>
MONITOR_API_KEY=<chuỗi ngẫu nhiên dài, vd: openssl rand -hex 32>
PERF_RETENTION_DAYS=90
```

- `MONITOR_PASSWORD` là bắt buộc. Để trống thì trang báo lỗi và không hiển thị số liệu.
- `MONITOR_API_KEY` là khóa nội bộ giữa trang giám sát và NCS AI. Không cần nhập ở đâu khác.

## 2. Chạy

```bash
cd ~/ncschatui/deploy
sudo docker compose up -d          # tạo thêm container ncs-monitor, khởi động lại open-webui với khóa mới
sudo docker compose logs -f monitor
```

Mặc định trang chỉ mở trên chính server: `http://127.0.0.1:8090`.

### Mở cho các máy trong mạng LAN

1. Xem IP LAN của server: `ip -4 addr` (vd `192.168.1.20`).
2. Trong `.env` đặt:
   ```env
   MONITOR_BIND=192.168.1.20
   MONITOR_PORT=8090
   ```
3. `sudo docker compose up -d monitor`
4. Từ máy khác trong LAN mở `http://192.168.1.20:8090`, đăng nhập bằng `MONITOR_USERNAME` / `MONITOR_PASSWORD`.

Lưu ý:
- **Không đặt `MONITOR_BIND=0.0.0.0`** nếu server có IP public (vd server đang phục vụ `ai.ncsgroup.vn`). Docker tự mở
  cổng và **bỏ qua tường lửa ufw**, nên `0.0.0.0` sẽ mở trang ra Internet. Gắn vào đúng IP LAN thì chỉ card mạng LAN nhận.
- Trang dùng HTTP thường: mật khẩu đi trong mạng LAN không mã hóa. Đặt mật khẩu riêng, đủ mạnh, không trùng mật khẩu khác.
- Server có nhiều card mạng LAN (nhiều VLAN) thì chỉ VLAN chứa IP đã đặt truy cập được.

### Từ ngoài LAN

- SSH tunnel: `ssh -L 8090:127.0.0.1:8090 <user>@<server>` rồi mở `http://localhost:8090` (với `MONITOR_BIND=127.0.0.1`;
  nếu đã bind IP LAN thì dùng `ssh -L 8090:192.168.1.20:8090 ...`).
- Hoặc qua nginx có HTTPS: thêm `location`/subdomain trỏ tới `http://<MONITOR_BIND>:8090`.

## 3. Các tab

| Tab | Nội dung |
|---|---|
| Tổng quan | Số lượt trả lời, thời gian trả lời TB/p95, thời gian ra chữ đầu tiên, tốc độ sinh (token/s), token vào/ra, số function call, tỷ lệ lỗi, biểu đồ theo thời gian |
| Chi tiết lượt | Danh sách từng lượt trả lời (người hỏi, câu hỏi, model, số lần gọi LLM, số tool call, tổng thời gian; lọc theo message id, người dùng, lượt > 10 s/30 s/1 phút). Bấm một lượt để xem **timeline**: chuẩn bị → LLM vòng 1 (thời gian chờ chữ đầu, token, tốc độ) → từng function call → LLM vòng 2…, phân bổ thời gian LLM/tool/chuẩn bị, bước lâu nhất, nút mở lại cuộc chat (cần `OPEN_WEBUI_PUBLIC_URL`). Nội dung câu hỏi/câu trả lời bị ẩn: bấm **Truy xuất nội dung** và nhập `MONITOR_REVEAL_KEY` mới xem được (sai 5 lần bị khóa 10 phút, mỗi lần xem được ghi log) |
| Người dùng & tải | Số người đang online, số yêu cầu tới model chạy cùng lúc (lấy mẫu mỗi 30 giây), các đỉnh trong kỳ, và bảng **tốc độ theo mức tải** cho biết từ mức đồng thời nào thì hệ thống bắt đầu chậm |
| Token | Bảng người dùng × model: số lần gọi, token vào/ra. Lọc theo chat, kênh, tác vụ nền, tracker |
| Tốc độ model | Mỗi model: thời gian ra chữ đầu (TB/p95), tổng thời gian, token/s, lỗi; biểu đồ theo giờ; các lần gọi chậm nhất |
| Function call | Mỗi function: số lần gọi, thời gian TB/p95/lâu nhất, lỗi; bảng **function bị lỗi** kèm nội dung lỗi và tham số AI đã truyền; phân bố số function trên mỗi lượt; chi tiết từng lượt (gọi function nào, bao nhiêu lần, mất bao lâu) |
| Kiến thức | Cách tài liệu đang được xử lý theo cấu hình thật, model đang dùng, thời gian trích xuất chữ/embedding, file vừa xử lý, các công cụ đọc kiến thức mà AI đã gọi  Cuối tab có bảng **Tài liệu kiến thức: trích xuất và embedding** (file nào đủ/thiếu/chưa embedding/không có chữ); **Xem chữ** cần khóa truy xuất, **Xử lý lại** cần phiên chỉnh sửa |
| Tracker | Số lần tổng hợp, token, thời gian, lỗi, số lần chuyển kết nối, kết nối và model đang dùng, số tin chờ |
| Thành phần & tài nguyên | Mọi thành phần đang dùng (LLM, embedding, reranker, STT, TTS, terminal, DB, Redis, lưu file), RAM/CPU từng container, dung lượng DB, Redis, nút **Đo ngay** (cơ sở dữ liệu, Redis, kho file, embedding, reranker, trích xuất tài liệu/OCR bằng PDF mẫu, một model mỗi kết nối), **Đo tất cả model** (thử từng model chat của mọi kết nối) và **Đo nhanh** (không gọi model) |

Lưu ý về số liệu:

- Số liệu chỉ bắt đầu có từ khi chạy bản mới, không có số liệu của các lần dùng trước đó.
- Số token chỉ đúng khi model trả về `usage`. vLLM và OpenAI đều có trả về.
- **p95**: 95% số lượt nhanh hơn mức này. Dùng để thấy các lượt chậm bất thường.
- **Ra chữ đầu tiên**: thời gian từ lúc gửi câu hỏi đến khi model bắt đầu trả chữ. Chỉ số này phụ thuộc chủ yếu vào model và GPU (RunPod).

## 4. Quyền Docker

Container giám sát gắn `/var/run/docker.sock` ở chế độ chỉ đọc để lấy RAM/CPU của từng container. Chế độ
chỉ đọc không giới hạn API Docker, nên ai điều khiển được container này thì cũng điều khiển được Docker.
Trang chỉ dùng hai lệnh đọc (danh sách container, thống kê), nhưng vẫn phải giữ cổng 8090 ở 127.0.0.1 và
đặt mật khẩu mạnh.

Không muốn cấp quyền này thì xóa dòng `- /var/run/docker.sock:/var/run/docker.sock:ro` trong
`docker-compose.yaml`. Trang vẫn hiện RAM/CPU của container NCS AI, PostgreSQL và Redis, nhưng không hiện
bảng tất cả container.

## 5. Tắt hoặc giảm ghi số liệu

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `ENABLE_PERF_MONITOR` | `true` | `false` thì NCS AI không ghi số liệu nữa |
| `PERF_RETENTION_DAYS` | `90` | Số liệu cũ hơn số ngày này tự xóa |
| `PERF_REQUEST_USAGE` | `true` | Yêu cầu provider trả số token khi stream. Chỉ tắt khi provider báo lỗi với `stream_options` |

Số liệu được ghi theo lô mỗi 3 giây, chạy nền, không làm chậm câu trả lời. Dữ liệu nằm trong bảng `perf_event`
của PostgreSQL. Bảng này được tự tạo khi NCS AI khởi động bản mới.

## 6. Phần kiến thức đang hoạt động thế nào (cấu hình hiện tại)

Server đang đặt `BYPASS_EMBEDDING_AND_RETRIEVAL=true` (mục "Upload nhanh"). Admin đổi được trong
**Cài đặt quản trị → Tài liệu → Bỏ qua embedding và truy xuất**. Giá trị trong giao diện được ưu tiên hơn `.env`.

### Khi tải tài liệu lên kho kiến thức

1. File gốc được lưu vào RustFS (S3). Giới hạn 50 MB mỗi file.
2. Chữ được trích xuất ngay trong container NCS AI bằng thư viện có sẵn:
   - PDF dùng pypdf, đọc theo trang, không đọc chữ trong ảnh.
   - Word dùng python-docx.
   - Excel, PowerPoint dùng unstructured (hoặc pandas, python-pptx).
   - CSV, HTML, Markdown, văn bản thường được đọc trực tiếp.
3. Toàn bộ chữ được lưu vào cơ sở dữ liệu, cùng với thông tin của file.
4. **Không chia đoạn, không tạo embedding, không ghi vào vector DB (pgvector).** Vì vậy tải lên nhanh và
   không cần GPU.

PDF dạng ảnh scan không có lớp chữ thì trích xuất được rất ít. Muốn đọc được loại này cần bật OCR, ví dụ
Docling, Tika hoặc Mistral OCR trong mục "Trích xuất nội dung".

### Khi người dùng hỏi

Model dùng chế độ gọi tool (native function calling). AI tự gọi các công cụ đọc kiến thức:

| Công cụ | Việc làm |
|---|---|
| `list_knowledge` | Liệt kê kho kiến thức và file được gắn cho model |
| `search_knowledge_files` | Tìm file theo tên |
| `grep_knowledge_files` | Tìm các dòng chứa từ khóa hoặc mẫu trong nội dung file |
| `view_file`, `view_knowledge_file` | Đọc từng phần nội dung file. Mặc định khoảng 12.000 ký tự mỗi lần, có thể đọc tiếp |
| `query_knowledge_files` | Tìm theo ngữ nghĩa trong vector DB. **Với cấu hình hiện tại công cụ này luôn rỗng**, vì không có vector nào |

Như vậy, chất lượng trả lời phụ thuộc vào việc AI chọn đúng từ khóa để grep và đọc đúng đoạn. Câu hỏi dùng
từ khác với tài liệu ("nghỉ phép" trong câu hỏi, "phép năm" trong tài liệu) có thể không tìm ra.

### Model đang dùng

| Việc | Model |
|---|---|
| Trả lời, chọn công cụ đọc kiến thức | Model chat đang chọn (vd: `nai` trên RunPod) |
| Embedding | `sentence-transformers/all-MiniLM-L6-v2`. Model này **vẫn được nạp vào RAM** của container NCS AI dù không dùng (vài trăm MB), vì NCS AI nạp nó lúc khởi động. Tab "Thành phần & tài nguyên" cho biết nó đã nạp hay chưa |
| Reranker | Không dùng |

### Tốc độ

Tab **Kiến thức** đo thời gian trích xuất chữ của từng file (ms), số ký tự, và thời gian các công cụ đọc
kiến thức. Ở chế độ hiện tại, trích xuất thường mất dưới 1 giây với văn bản thường, vài giây với PDF dài.
Phần tốn thời gian nhất khi trả lời là model đọc file (nhiều vòng gọi tool), xem ở tab **Function call**.

### Nếu muốn tìm kiếm theo ngữ nghĩa

Tắt "Bỏ qua embedding và truy xuất", rồi chọn model embedding:

- Nên dùng **embedding qua API**, vd. vLLM chạy `bge-m3` hoặc `multilingual-e5` trên RunPod. Model mặc định
  `all-MiniLM-L6-v2` chỉ hỗ trợ tốt tiếng Anh.
- Sau khi đổi model embedding, phải **xử lý lại các file đã tải** trong mỗi kho kiến thức.

Khi đó tab Kiến thức sẽ có thêm thời gian embedding lúc tải lên, và thời gian embedding câu hỏi, tìm vector
lúc trả lời.

## 7. Sửa cấu hình ngay trên trang giám sát

Tab **Thành phần & tài nguyên → Chỉnh sửa cấu hình** sửa được các nhóm sau mà không cần sửa `.env`, không
build lại, không khởi động lại. NCS AI tự áp dụng ở mọi worker trong tối đa 15 giây:

- Tracker: model ưu tiên, tự chuyển kết nối, số kênh tổng hợp cùng lúc, thời gian chờ, số tin.
- Model tác vụ nền.
- Kiến thức: bỏ qua embedding, engine/model/URL/khóa embedding, chia đoạn, Top K, tìm kiếm kết hợp.
- Reranker, trích xuất tài liệu, STT, TTS, tìm kiếm web, tạo ảnh, chạy code.

- **Kết nối model** (RunPod, OpenAI…): URL, khóa API, bật/tắt, prefix, thứ tự, thêm/xóa kết nối, nút **Kiểm tra**
  để thử URL và khóa trước khi lưu. Lưu xong, NCS AI nạp lại danh sách model trong tối đa 15 giây. Pod RunPod đổi
  địa chỉ thì chỉ cần sửa URL ở đây. Giữ nguyên **Prefix** để model, quyền truy cập và lịch sử chat vẫn dùng đúng
  tên model cũ.

- **Quyền truy cập model**: danh sách mọi model (theo kết nối, kèm model tùy chỉnh) với trạng thái **Công khai**
  (mọi người dùng) / **Riêng tư** (chỉ admin và nhóm/người được chia sẻ trong NCS AI). Bấm một nút để đổi.
  Chọn được cả quy tắc cho **model mới xuất hiện**: không tự công khai / tự công khai model của kết nối #1 /
  tự công khai model của mọi kết nối. Quy tắc này thay cho `DEFAULT_MODEL_ACCESS` trong `.env` và không đổi quyền
  của model đã có.

Hạ tầng (kết nối database, Redis, lưu file) **không** sửa ở đây.

Với Tracker, giá trị lưu trong database được ưu tiên hơn `.env`. Để trống thì dùng giá trị trong `.env`.

### Cài đặt bảo mật (một lần)

1. Đảm bảo `.env` đã có `MONITOR_REVEAL_KEY` (khóa truy xuất, cũng là khóa sửa cấu hình), rồi chạy
   `sudo docker compose up -d monitor`:
   ```env
   MONITOR_REVEAL_KEY=<chuỗi ngẫu nhiên dài, vd: openssl rand -hex 32>
   ```
2. Tạo mã Google Authenticator **trên terminal của server**:
   ```bash
   sudo docker compose exec monitor python -m open_webui.monitoring.server.totp init
   ```
   - Lệnh hỏi khóa truy xuất, rồi in **QR code và secret** ra terminal.
   - Quét QR bằng Google Authenticator (hoặc Microsoft Authenticator, Authy), sau đó nhập mã 6 số để xác nhận.
   - QR và secret **chỉ hiện một lần ở đây, không bao giờ hiện trên trang web**. Secret được lưu trong database
     dưới dạng **đã mã hóa bằng `MONITOR_REVEAL_KEY`**.

### Sửa cấu hình

1. Trên trang giám sát, bấm **Chỉnh sửa cấu hình**.
2. Nhập **khóa truy xuất (`MONITOR_REVEAL_KEY`)** và **mã 6 số** đang hiện trên ứng dụng.
   - Mỗi mã chỉ dùng được một lần.
   - Sai 5 lần thì bị khóa 10 phút.
3. Sau khi xác thực, bạn có phiên chỉnh sửa **15 phút**. Bấm **Khóa lại** để đóng phiên sớm.
4. Sửa từng nhóm rồi bấm **Lưu** của nhóm đó.
   - Với ô khóa API, để trống nghĩa là giữ khóa cũ. Khóa không bao giờ hiện lại trên trang.
5. Mọi thay đổi được ghi vào **Lịch sử thay đổi**: thời gian, giá trị cũ → mới, từ máy nào. Khóa bí mật chỉ ghi
   "(đã đổi)". Lịch sử giữ theo `PERF_RETENTION_DAYS`.

Nhóm **Trích xuất tài liệu** có nút **Kiểm tra**: NCS AI tạo file mẫu có nội dung biết trước (PDF có chữ, PDF scan, ảnh PNG),
chạy qua đúng engine đang chọn (kể cả giá trị chưa lưu) và báo % chữ đọc đúng, thời gian, chữ đọc được.
**Đạt** khi PDF có chữ đọc đúng ≥ 80%.
Với PaddleOCR-VL chọn được **Cách gửi khóa**: `token <khóa>` (mặc định) hoặc `Bearer <khóa>` (pod có xác thực bằng API key);
khi cài mới có thể đặt sẵn bằng `PADDLEOCR_VL_AUTH_SCHEME=bearer` trong `.env`. PDF scan và ảnh để tham khảo: engine có OCR tốt cho tiếng Việt phải đọc được
phần lớn; nếu % khi bỏ dấu cao hơn nhiều thì engine đang làm mất dấu tiếng Việt.

Mục **Tài liệu kiến thức: trích xuất và embedding** (bấm **Kiểm tra tài liệu**) liệt kê mọi file trong kho kiến
thức với số ký tự trích xuất được, số đoạn vector và trạng thái: **Đủ**, **Có thể thiếu** (ít đoạn hơn nhiều so với
độ dài chữ), **Chưa embedding**, **Không có chữ** (PDF scan/ảnh mà engine không đọc được), **Lỗi xử lý**.
- **Xem chữ**: đặt file gốc (PDF/ảnh) cạnh chữ engine trích xuất được, xem toàn bộ chữ hoặc theo từng đoạn vector
  (kèm số trang, engine), để đánh giá độ chính xác OCR.
- **Xử lý lại**: trích xuất và embedding lại riêng file đó bằng engine/model hiện tại (không cần lập chỉ mục lại cả kho).

Đổi model embedding xong vẫn phải bấm **Lập chỉ mục lại** trong NCS AI (Cài đặt quản trị → Tài liệu).

### Đổi hoặc khôi phục mã Google Authenticator

| Việc | Lệnh (trong thư mục có docker-compose.yaml) |
|---|---|
| Xem trạng thái | `sudo docker compose exec monitor python -m open_webui.monitoring.server.totp status` |
| Đổi QR/secret định kỳ (cần khóa **và** mã hiện tại) | `... totp rotate` |
| Mất điện thoại (cần khóa, gõ XOA để xác nhận) | `... totp reset` rồi `... totp init` |
| Đổi `MONITOR_REVEAL_KEY` | Secret cũ không giải mã được nữa: chạy `reset` rồi `init` |

Sau khi `rotate` hoặc `reset`, mã trên ứng dụng cũ hết hiệu lực ngay. Xóa mục cũ trong ứng dụng để khỏi nhầm.
