# Project Tracker — Hướng dẫn deploy

Tài liệu cho người vận hành server Open WebUI (Docker) của nhóm. Áp dụng cho nhánh `feature/project-tracker`.

## 1. Tính năng này làm gì

Mỗi **kênh (channel) là một dự án**. Hệ thống tự tổng hợp **trạng thái dự án** (hạng mục, người phụ trách, % tiến độ, hạn, vướng mắc, quyết định, rủi ro, việc tiếp theo) từ tin nhắn, file và ảnh trong kênh.

```
Tin nhắn / file / ảnh ──► đánh dấu kênh "có việc" ──► worker gom lô ──► Qwen (vLLM) ──► tracker_state
                                                         │
                                 file: tóm tắt map-reduce, ảnh: mô tả + OCR (cache theo hash)

@AI trong kênh ──► trạng thái dự án + tin nhắn mới chưa tổng hợp ──► 1 lượt LLM ──► trả lời
Nút "Tiến độ dự án" trên thanh kênh ──► panel hiển thị trạng thái, nguồn, lịch sử
```

- Worker chạy ngay trong container Open WebUI. Nhiều worker hoặc nhiều container dùng chung một hàng đợi trong PostgreSQL, nên không xử lý trùng.
- Một kênh được xử lý khi im lặng `TRACKER_IDLE_SECONDS` (mặc định 90 giây), hoặc khi dồn đủ `TRACKER_BATCH_SIZE` tin (mặc định 30). Khi có người @AI hoặc bấm "Cập nhật ngay", kênh được ưu tiên xử lý trước.
- Đính chính: nhắn *"Đính chính: …"*, hoặc sửa và xoá tin nhắn. Hệ thống tự cập nhật lại trạng thái.
- Tool mới cho model, dùng được ở cả chat thường: `get_project_status`, `list_my_projects`, `get_project_timeline`, `search_project_evidence`. Các tool này chỉ thấy những kênh người hỏi có quyền xem.

Tắt toàn bộ tính năng bằng `ENABLE_PROJECT_TRACKER=false`. Khi đó Open WebUI chạy như bản gốc.

## 2. Đưa code lên server

Server hiện `git pull` từ repo gốc của Open WebUI. Code tracker nằm trên một nhánh riêng, nên cần một **fork** trên GitHub (hoặc GitLab, Gitea...).

### 2.1. Trên máy dev (làm 1 lần)

```powershell
cd E:\key\open-webui
git remote rename origin upstream                      # repo gốc open-webui
git remote add origin https://github.com/<ban>/open-webui.git
git push -u origin feature/project-tracker
```

### 2.2. Trên server (làm 1 lần)

```powershell
cd D:\WebUi\open-webui
git remote add fork https://github.com/<ban>/open-webui.git
git fetch fork
git switch -c feature/project-tracker --track fork/feature/project-tracker
```

### 2.3. Cập nhật upstream về sau

Làm trên **máy dev**, không làm trên server:

```powershell
git fetch upstream
git switch feature/project-tracker
git merge upstream/main          # nếu xung đột: thường chỉ ở vài dòng hook trong channels.py / tools.py / main.py
git push
```

Phần sửa vào file upstream được giữ ở mức tối thiểu, gồm 5 file: `routers/channels.py`, `utils/tools.py`, `main.py`, `migrations/env.py`, `channel/Channel.svelte` + `Navbar.svelte`, và file dịch `vi-VN`. Toàn bộ logic nằm trong `backend/open_webui/tracker/`.

### 2.4. Sửa `update.ps1` trên server

```powershell
$ErrorActionPreference = "Stop"
$repo = Join-Path $PSScriptRoot "..\open-webui"

git -C $repo pull --ff-only fork feature/project-tracker
if ($LASTEXITCODE -ne 0) { Write-Error "git pull failed"; exit 1 }

$env:BUILD_HASH = git -C $repo rev-parse --short HEAD

Set-Location $PSScriptRoot
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Write-Error "docker build failed"; exit 1 }

docker image prune -f
```

## 3. Kiểm tra vLLM trên RunPod trước khi bật

### 3.1. Tên model

`TRACKER_MODEL` phải trùng **chính xác** với tên model vLLM đang phục vụ:

```powershell
curl.exe -s "$env:OPENAI_API_BASE_URL/models" -H "Authorization: Bearer $env:OPENAI_API_KEY"
```

Lấy giá trị `data[0].id`, ví dụ `Qwen/Qwen3.6-27B-AWQ`.

### 3.2. Số request đồng thời

Trong log khởi động của vLLM, tìm dòng:

```
Maximum concurrency for 32768 tokens per request: X
```

Đặt `TRACKER_MAX_CONCURRENCY` **≤ X/2**, để người đang chat vẫn còn chỗ được trả lời. Với GPU 32 GB chạy model 27B dạng nén, X thường chỉ khoảng 2–6, nên giữ mặc định là `2`.

### 3.3. JSON có cấu trúc, tắt suy nghĩ, đọc ảnh

Worker gửi `response_format: json_schema` và `chat_template_kwargs.enable_thinking=false`.
- Nếu vLLM từ chối `response_format`, worker tự chuyển sang JSON thường và ghi cảnh báo `rejected response_format` trong log.
- Nếu model không có chế độ suy nghĩ, tham số còn lại bị bỏ qua.

Thử đọc ảnh (cần bản Qwen-VL hoặc multimodal):

```powershell
$body = '{"model":"<TRACKER_MODEL>","max_tokens":50,"messages":[{"role":"user","content":[{"type":"text","text":"Ảnh này màu gì?"},{"type":"image_url","image_url":{"url":"https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png"}}]}]}'
curl.exe -s "$env:OPENAI_API_BASE_URL/chat/completions" -H "Authorization: Bearer $env:OPENAI_API_KEY" -H "Content-Type: application/json" -d $body
```

Nếu model không đọc được ảnh, đặt `TRACKER_ENABLE_IMAGES=false`.

### 3.4. Nếu bạn tự cấu hình vLLM

Các tham số nên dùng:

```
--max-model-len 32768 --enable-prefix-caching --gpu-memory-utilization 0.92
```

`--enable-prefix-caching` giúp tái dùng phần system prompt giống nhau giữa các lượt gọi, nên các lượt sau nhanh hơn.

## 4. Cấu hình `compose.yaml`

Thêm vào `environment` của service `open-webui`:

```yaml
      # --- Project tracker ---
      - ENABLE_PROJECT_TRACKER=true
      - TRACKER_MODEL=${TRACKER_MODEL}          # tên model ở bước 3.1
      - TRACKER_MAX_CONCURRENCY=2
      # URL/KEY mặc định lấy từ OPENAI_API_BASE_URL / OPENAI_API_KEY; chỉ đặt nếu dùng endpoint khác:
      # - TRACKER_LLM_BASE_URL=https://...runpod.net/v1
      # - TRACKER_LLM_API_KEY=${TRACKER_LLM_API_KEY}
```

Thêm vào `.env`:

```env
TRACKER_MODEL=Qwen/Qwen3.6-27B-AWQ
```

Tính năng **Channels** phải đang bật: Admin Panel → Settings → General → Channels, hoặc đặt env `ENABLE_CHANNELS=true` ở lần chạy đầu.

### Các tham số tinh chỉnh

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `TRACKER_MAX_CONCURRENCY` | 2 | Số lượt gọi LLM đồng thời **cho cả hệ thống** (tính trên mọi worker và container) |
| `TRACKER_IDLE_SECONDS` | 90 | Kênh im lặng bao lâu thì tổng hợp |
| `TRACKER_BATCH_SIZE` | 30 | Đủ bao nhiêu tin thì tổng hợp ngay, không chờ |
| `TRACKER_BATCH_MAX_CHARS` | 24000 | Số ký tự tối đa mỗi lô, đặt theo context 32k |
| `TRACKER_STATE_MAX_CHARS` | 12000 | Độ dài tối đa của trạng thái khi đưa vào prompt |
| `TRACKER_TAIL_LIMIT` | 50 | Số tin chưa tổng hợp tối đa được đưa vào khi @AI |
| `TRACKER_ENABLE_FILES` / `TRACKER_ENABLE_IMAGES` | true | Bật hoặc tắt việc đọc file và ảnh |
| `TRACKER_FILE_MAX_CHUNKS` | 8 | Số đoạn tối đa (12k ký tự mỗi đoạn) đọc từ một file. File dài hơn sẽ lấy mẫu rải đều |
| `TRACKER_FILE_WAIT_SECONDS` | 600 | Thời gian chờ Open WebUI trích text từ file trước khi bỏ qua file đó |
| `TRACKER_TIMEZONE` | Asia/Ho_Chi_Minh | Múi giờ dùng cho ngày tháng trong prompt |
| `TRACKER_GUIDED_JSON` | true | Gửi `response_format` cho vLLM |
| `TRACKER_DISABLE_THINKING` | true | Tắt chế độ suy nghĩ của Qwen3 khi tổng hợp |
| `TRACKER_WORKER_MODE` | app | `external` nếu muốn chạy worker ở container riêng, xem mục 9 |

## 5. Build và chạy

```powershell
cd D:\WebUi\openwenui-docker
.\update.ps1
docker compose logs -f open-webui
```

Lần build này chạy lại `npm run build` (khoảng 6–10 phút) vì giao diện có thay đổi. Phần Python dùng lại cache.

Trong log cần thấy:

```
Running upgrade 9f6c2a8d1b43 -> b7d41c2e9a05, Add project tracker state and item tables.
Running upgrade b7d41c2e9a05 -> c3e8a1f05d72, Add pending_deletions to tracker_state.
Project tracker worker started (max concurrency 2)
```

Nếu thấy `TRACKER_LLM_BASE_URL/TRACKER_MODEL are not set; worker idle` thì kiểm tra lại mục 4.

## 6. Kiểm tra sau khi deploy

1. Mở một kênh. Trên thanh tiêu đề có thêm nút **biểu đồ cột** (Tiến độ dự án). Nút chỉ hiện khi tracker đang bật.
2. Nhắn vài tin, ví dụ *"Lan: module đăng nhập xong UI, API khoảng 50%, hạn 20/11"*. Chờ khoảng 90 giây, hoặc bấm ⟳ trong panel. Panel sẽ hiện hạng mục kèm thanh %, hạn và nút **Nguồn** để mở tin gốc.
3. Gửi một file PDF hoặc DOCX có nội dung tiến độ. Sau khi tổng hợp, thông tin trong file xuất hiện trong panel.
4. Gửi một ảnh chụp màn hình có chữ. Chữ trong ảnh được dùng làm bằng chứng.
5. Nhắn *"Đính chính: API mới 30%"*. Panel cập nhật lại.
6. Xoá một tin đã được tổng hợp. Thông tin chỉ dựa vào tin đó sẽ bị bỏ ở lần cập nhật sau.
7. Gõ `@<model> tình hình dự án thế nào?` trong kênh. Model thường trả lời ngay trong 1 lượt từ trạng thái có sẵn, không còn chuỗi dài "Đã khám phá…". Nếu muốn chặn hẳn việc model đi tìm thêm, tắt nhóm tool Channels cho model đó.
8. Trong chat thường, hỏi *"Dự án nào đang trễ hạn?"*. Model gọi `list_my_projects`. Cần model ở chế độ native function calling.

Theo dõi qua API, dùng token admin (Settings → Account → API Keys):

```powershell
$H = @{ Authorization = "Bearer <admin token>" }
Invoke-RestMethod http://localhost:8000/api/v1/tracker/stats -Headers $H
# {channels, dirty, processing, failing, max_lag_seconds}
Invoke-RestMethod http://localhost:8000/api/v1/tracker/channels/<channel_id> -Headers $H
# last_error hiển thị cho admin nếu kênh đang lỗi
```

Ý nghĩa các chỉ số:
- `max_lag_seconds`: kênh chờ lâu nhất đã chờ bao nhiêu giây. Nếu số này tăng mãi, GPU không theo kịp. Khi đó tăng `TRACKER_MAX_CONCURRENCY` (nếu vLLM còn chỗ), hoặc tăng `TRACKER_IDLE_SECONDS` để gom lô to hơn.
- `failing`: số kênh đang lỗi. Hệ thống tự thử lại sau 30 giây, 1 phút, 2 phút... tối đa 1 giờ. Xem `last_error` của từng kênh.

Log của tracker:

```powershell
docker compose logs open-webui | Select-String "Tracker"
```

## 7. Test tải (tuỳ chọn)

Chạy trong container, dùng chính API của hệ thống:

```powershell
docker exec -it open-webui python -m open_webui.tracker.loadtest `
  --url http://localhost:8080 --token <admin token> `
  --channels 20 --messages 30 --concurrency 10 --cleanup
```

Kết quả gồm số tin nhắn mỗi giây lúc gửi, và thời gian để mọi kênh được tổng hợp xong. Script tạo các kênh `loadtest-*`, và xoá chúng khi có `--cleanup`. Nên chạy ngoài giờ làm việc, vì test này dùng GPU thật.

Tham khảo khi thử với vLLM giả lập (0,8 giây mỗi lượt gọi): 300 tin trong 12 kênh cần 12 lượt LLM, và xong 12 giây sau tin cuối. Với Qwen 27B thật, mỗi lượt mất vài giây đến vài chục giây, tuỳ độ dài lô.

## 8. Sự cố thường gặp

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| Không thấy nút Tiến độ | `ENABLE_PROJECT_TRACKER` chưa bằng `true`, hoặc chưa build lại frontend. Kiểm tra `GET /api/v1/tracker/channels/<id>` có trả về 404 không |
| `last_error: LLM request failed (404)` | Sai `TRACKER_MODEL` hoặc sai URL (URL phải kết thúc bằng `/v1`) |
| `last_error: ... (400) ... max_tokens` / context | Lô quá dài so với context. Giảm `TRACKER_BATCH_MAX_CHARS` (ví dụ 16000) và `TRACKER_MAX_TOKENS` (ví dụ 3000) |
| `Invalid state after retry` | Model trả về JSON hỏng hai lần liên tiếp. Kiểm tra vLLM có hỗ trợ `response_format` không (log `rejected response_format`) |
| File không được tóm tắt | Open WebUI chưa trích được text, xem trạng thái file. Bản build slim không đọc được PDF/DOCX. Sau `TRACKER_FILE_WAIT_SECONDS`, tin vẫn được xử lý nhưng chỉ có tên file |
| Người dùng chat bị chậm khi tracker chạy | Giảm `TRACKER_MAX_CONCURRENCY` xuống 1, hoặc tăng `TRACKER_IDLE_SECONDS` |
| `max_lag_seconds` tăng mãi | GPU không theo kịp. Xem mục 6 |

## 9. Tách worker ra container riêng (tuỳ chọn)

Khi muốn web và worker không ảnh hưởng nhau, đặt `TRACKER_WORKER_MODE=external` cho `open-webui`, và thêm:

```yaml
  tracker-worker:
    image: open-webui-local:latest
    container_name: owui-tracker
    depends_on: [open-webui]
    command: ["python", "-m", "open_webui.tracker.worker"]
    environment:   # cùng DATABASE_URL, REDIS_URL, S3_*, OPENAI_*, TRACKER_* như open-webui
      - ENABLE_PROJECT_TRACKER=true
      - DATABASE_URL=postgresql://openwebui:${POSTGRES_PASSWORD}@postgres:5432/openwebui
      - STORAGE_PROVIDER=s3
      # ...
    restart: unless-stopped
```

## 10. Tắt hoặc quay lại bản cũ

- Tắt tính năng: đặt `ENABLE_PROJECT_TRACKER=false` rồi chạy `docker compose up -d`. Bảng `tracker_*` vẫn còn nhưng không được dùng.
- Quay lại bản gốc: `git switch main` trên server rồi build lại. Bảng `tracker_*` vô hại với bản gốc, không cần xoá.

## 11. Giới hạn hiện tại

- **Một GPU 32 GB phù hợp với khoảng vài chục đến vài trăm kênh hoạt động.** Hàng nghìn kênh cần thêm GPU hoặc endpoint vLLM. Code không cần sửa, chỉ cần tăng `TRACKER_MAX_CONCURRENCY`.
- Tóm tắt file và ảnh tốn thêm lượt gọi LLM: mỗi file khoảng 1–9 lượt, mỗi ảnh 1 lượt. Kết quả được cache theo hash, nên cùng một file gửi lại sẽ không tốn thêm.
- Xoá một tin gốc có luồng trả lời: chỉ nội dung tin gốc được dùng để rút lại thông tin.
- Tìm bằng chứng (`search_project_evidence`) dùng tìm theo từ khoá, không phân biệt dấu, trên các bản tóm tắt. Chưa có tìm theo ý nghĩa.
- Panel tự làm mới mỗi 20 giây khi đang mở. Chưa có đẩy cập nhật theo thời gian thực.
