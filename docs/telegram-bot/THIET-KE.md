# Bot Telegram NCS AI: liên kết tài khoản, hỏi đáp AI, nhận file và báo cáo — thiết kế

Ngày 23/11/2026. Trạng thái: **đã triển khai** (chưa chạy thử với bot thật).

Code:
- backend: `backend/open_webui/telegram/` (settings, api, models, access, ask, format, keyboards, bot, router), migration `b7d2f4a6c8e1`;
- web: `src/lib/components/admin/Settings/Telegram.svelte`, `src/lib/components/telegram/TelegramAccountLink.svelte`, `src/lib/apis/telegram/`.

Khác với bản thiết kế:
- Chưa làm nút "Hỏi về báo cáo này" trên tin báo cáo, và ô "gửi Telegram cho người nhận đã liên kết" (mục 7, tùy chọn).
- Câu hỏi soạn sẵn ở bàn phím kênh nằm trong code (`keyboards.py`), quản trị viên chưa sửa được trên giao diện.

## 1. Mục tiêu

Một bot Telegram duy nhất cho NCS AI. Người dùng liên kết **tài khoản Telegram** với **tài khoản NCS AI** một lần, rồi:

1. **Hỏi AI trực tiếp**: như chat riêng với AI trên web (chọn model, có lịch sử).
2. **Hỏi AI trong một kênh**: như gọi `@Knight` trong kênh trên web (ngữ cảnh kênh, bảng tiến độ, file của kênh).
3. **Nhận file PDF / Word / Excel**:
   - file AI tạo ra (ví dụ "xuất báo cáo tiến độ ra Word");
   - hoặc bấm nút để xuất bất kỳ câu trả lời nào thành file.
4. **Nhận báo cáo định kỳ**: tài khoản Telegram đã liên kết tự có trong danh sách nơi nhận khi tạo lịch báo cáo.

Bot luôn hành động **thay mặt đúng người đã liên kết**, với quyền giống hệt trên web.

**Cấu hình:**
- Bot báo cáo và bot hỏi đáp là **một bot, một chỗ cấu hình**: Cài đặt quản trị → Telegram.
- Mỗi môi trường (server thật / máy thử / máy dev) dùng một bot riêng.

**Ngoài phạm vi giai đoạn này:**
- nối nhóm Telegram với kênh (liên kết nhóm đang tạm tắt);
- tin nhắn giọng nói;
- terminal / code interpreter.

## 2. Hiện trạng có thể dùng lại

| Thành phần | Ở đâu | Dùng lại |
|---|---|---|
| Đọc tin bot (`getUpdates`), khóa 1 worker đọc, gửi tin, cắt 4096 ký tự | `reports/telegram.py` | Chuyển thành module chung `telegram/` |
| Mã liên kết 1 lần, liên kết chat nhận báo cáo | `reports/models.py` (`report_telegram_link`) | Giữ cho dữ liệu cũ; liên kết mới đi qua tài khoản |
| Gọi AI không cần trình duyệt | `utils/automations.py` (`_build_request`, `_resolve_model_defaults`, `CHAT_COMPLETION_HANDLER`) | Mẫu cho cả hỏi trực tiếp và hỏi trong kênh |
| AI trả lời trong kênh | `routers/channels.py` (`new_message_handler`, `model_response_handler`) | Đăng câu hỏi có mention `<@M:model_id\|Tên>` rồi gọi AI |
| Biết lúc AI trả lời xong | `socket/main.py` `_make_channel_emitter` (`meta.done = True`); chat: message `done` | Chờ cờ này rồi gửi về Telegram |
| AI tạo file và gắn vào câu trả lời | `tools/builtin.py` `attach_to_reply`; `tracker/tools.py` `export_project_report` (PDF/Word/Excel) | File gắn vào câu trả lời được gửi tiếp sang Telegram |
| Chuyển Markdown sang PDF / Word | `tracker/export.py` `to_pdf`, `to_docx` (có font tiếng Việt, fallback Word khi lỗi PDF) | Nút "Xuất PDF / Word" cho mọi câu trả lời |
| Lọc model theo quyền, kiểm tra quyền kênh | `get_filtered_models`, `new_message_handler` | Như web |

## 3. Một bot, một chỗ cấu hình

### 3.1. Cài đặt quản trị → Telegram (trang mới; monitor có tab tương ứng)

| Nhóm | Cài đặt | Mặc định |
|---|---|---|
| **Bot** | Bot token (ẩn), nút **Kiểm tra** (hiện `@username`, tên bot) | |
| | Trạng thái: đang đọc tin / lỗi gần nhất / **cảnh báo có máy khác dùng chung bot** (khi gặp `409 Conflict`) | |
| **Tính năng** | Gửi báo cáo định kỳ qua Telegram | tắt |
| | Hỏi đáp AI qua Telegram | tắt |
| | Cho hỏi trong kênh | bật |
| | Gửi file qua Telegram (PDF / Word / Excel) | bật |
| | Liên kết nhóm Telegram | tắt (đang tạm khóa trong code) |
| **Ai được dùng** | Quyền nhóm `features.telegram`: quản trị viên bật cho nhóm người dùng | không ai |
| **Giới hạn** | Câu hỏi / phút / người; câu đang chạy / người; tổng câu chạy cùng lúc | 10 / 1 / 5 |
| | Tự sang hội thoại mới sau | 4 giờ |
| | Kênh không cho hỏi qua Telegram | trống |
| | Model mặc định cho Telegram | model mặc định hệ thống |
| **Người đã liên kết** | Bảng: người dùng, @username, ngày liên kết, lần dùng cuối, nút **Gỡ** | |

**Chuyển cấu hình cũ:**
- `reports.telegram.enable / bot_token / bot_username` chuyển sang `telegram.*`. Lần chạy đầu tự chép giá trị cũ sang, sau đó đọc khóa mới.
- Trang Cài đặt quản trị → Báo cáo định kỳ bỏ ô bot token, chỉ còn link "Cấu hình bot ở trang Telegram".

### 3.2. Module chung

```
backend/open_webui/telegram/
  api.py         call / send / edit / sendDocument / answerCallbackQuery (từ reports/telegram.py)
  poller.py      poll_loop + khóa: đọc tin một nơi, đẩy vào dispatcher
  dispatcher.py  phân loại: /start <mã cũ> (báo cáo) · /start acc_<mã> (tài khoản) · lệnh · nút · tin thường
  accounts.py    liên kết tài khoản, kiểm tra quyền mỗi tin
  ask.py         hỏi trực tiếp / hỏi trong kênh, chờ kết quả
  reply.py       gửi câu trả lời: sửa dần, cắt tin, HTML, nút, gửi file
  keyboards.py   bàn phím và nút bấm (mục 4)
  settings.py    telegram.* (mục 3.1)
reports/  dùng telegram/api.py để gửi báo cáo; không tự đọc tin nữa
```

## 4. Thiết kế nút bấm

Nguyên tắc:
- **Không bắt nhớ lệnh**: mọi việc đều có nút. Lệnh `/…` chỉ là đường tắt.
- **Luôn biết mình đang hỏi ở đâu**: mỗi câu trả lời có dòng đầu ghi chế độ.
- **Việc hay làm thì 1 chạm.**
- **Nút đổi theo ngữ cảnh**: hỏi trực tiếp và hỏi trong kênh có bộ nút khác nhau.

Telegram có 2 loại nút, dùng cả hai:
- **Bàn phím dưới ô nhập** (reply keyboard, luôn hiện): điều hướng chính.
- **Nút dưới tin nhắn** (inline keyboard): thao tác với chính tin đó.

### 4.1. Chưa liên kết

```
Bot: Chào bạn 👋 Đây là trợ lý NCS AI.
     Để hỏi AI và nhận báo cáo tại đây, hãy liên kết với tài khoản NCS AI của bạn.
     [🔗 Liên kết tài khoản]            → mở https://ai.ncsgroup.vn/settings/telegram
                                          (đăng nhập web, bấm "Liên kết", quay lại Telegram bấm Start)
```

### 4.2. Liên kết xong

```
Bot: ✅ Đã liên kết với Nguyễn Văn A (a@ncsgroup.vn).
     Gõ câu hỏi bất kỳ để hỏi AI, hoặc chọn bên dưới.
     Báo cáo định kỳ chọn "Telegram của tôi" sẽ được gửi vào đây.

Bàn phím (luôn hiện):
┌──────────────────┬──────────────────┐
│ 💬 Hỏi trực tiếp  │ 📁 Hỏi trong kênh │
├──────────────────┼──────────────────┤
│ 🆕 Cuộc trò chuyện mới │ ⚙️ Cài đặt   │
└──────────────────┴──────────────────┘
```

### 4.3. Hỏi trực tiếp

```
Người dùng: Soạn giúp thông báo họp giao ban thứ 2
Bot:  💬 Trực tiếp · Knight
      ⏳ Đang trả lời…                         [⏹ Dừng]
      → được sửa dần thành câu trả lời đầy đủ, rồi thêm nút:
      [📄 PDF] [📝 Word] [🔁 Trả lời lại] [🌐 Mở trên web]
```

- **📄 PDF / 📝 Word**: xuất câu trả lời này thành file (mục 6.2), gửi ngay vào chat. File cũng gắn vào tin trên web.
- **🔁 Trả lời lại**: hỏi lại câu trước, tạo phiên bản trả lời mới như trên web.
- **🌐 Mở trên web**: mở đúng cuộc trò chuyện.
- Gõ tiếp là hỏi tiếp trong cùng cuộc trò chuyện. **🆕 Cuộc trò chuyện mới** để bắt đầu lại.

### 4.4. Chọn kênh

Bấm **📁 Hỏi trong kênh**:

```
Bot: Chọn kênh để hỏi (gõ tên để tìm):
     Gần đây:  [#test-bd2] [#test-kpi]
     [#osint]  [#av]
     [#test5]  [#test02]
     [‹ Trước]  1/3  [Sau ›]
```

- Kênh dùng gần đây lên đầu. Mỗi hàng 2 nút, 6 kênh mỗi trang. Gõ "bd2" thì danh sách lọc theo tên.
- Chỉ hiện kênh người đó được hỏi trên web: kênh có quyền ghi, kênh nhóm / tin nhắn trực tiếp là thành viên. Bỏ các kênh quản trị viên chặn.

Chọn xong, bàn phím đổi theo kênh:

```
Bot: 📁 Đang hỏi trong #test-bd2
     Câu hỏi sẽ được đăng vào kênh dưới tên bạn (thành viên kênh thấy được), giống gọi @Knight trên web.

Bàn phím:
┌───────────────────┬───────────────────┐
│ 📊 Tiến độ tổng quan │ ⏰ Việc quá hạn     │
├───────────────────┼───────────────────┤
│ 👤 Việc của tôi      │ 📄 Xuất báo cáo     │
├───────────────────┼───────────────────┤
│ 🔄 Đổi kênh          │ ↩️ Về hỏi trực tiếp  │
└───────────────────┴───────────────────┘
```

- Bốn nút đầu gửi câu hỏi soạn sẵn, ví dụ "Tóm tắt tiến độ kênh: việc đã xong, đang làm, trễ hạn, vướng mắc". Câu soạn sẵn quản trị viên sửa được.
- **📄 Xuất báo cáo** hỏi tiếp định dạng: `[📄 PDF] [📝 Word] [📊 Excel]`. Bot nhờ AI tạo báo cáo tiến độ đầy đủ bằng `export_project_report`, có phụ lục mọi hạng mục, rồi gửi file.

Câu trả lời trong kênh:

```
Bot:  📁 #test-bd2 · Knight
      <câu trả lời>
      [📄 PDF] [📝 Word] [💬 Mở trong kênh]
      ↳ Trả lời (reply) vào tin này để hỏi tiếp trong cùng luồng của kênh.
```

### 4.5. Cài đặt

Bấm **⚙️ Cài đặt**:

```
Bot: ⚙️ Cài đặt
     Tài khoản: Nguyễn Văn A (a@ncsgroup.vn)
     Model: Knight
     Nhận báo cáo định kỳ tại đây: 2 lịch
     [🤖 Đổi model]  [📬 Báo cáo nhận tại đây]
     [❓ Hướng dẫn]  [🔓 Hủy liên kết]
```

- **🤖 Đổi model**: danh sách model được phép, có ✓ ở model đang dùng.
- **📬 Báo cáo nhận tại đây**: các lịch đang gửi vào Telegram này, nút **Tạo lịch trên web** mở trang Báo cáo định kỳ.
- **🔓 Hủy liên kết**: hỏi xác nhận `[Hủy liên kết] [Không]`.

### 4.6. Gửi file / ảnh cho bot

- Gửi file (PDF, Word, Excel, ảnh) kèm chú thích là câu hỏi.
- Không có chú thích thì bot hỏi `[📝 Tóm tắt] [🔍 Rà soát] [❓ Tôi sẽ hỏi]`.
- Ở chế độ kênh, file được đăng vào kênh như tải lên trên web.

### 4.7. Menu lệnh (nút Menu cạnh ô nhập)

Đăng ký bằng `setMyCommands`:

| Lệnh | Việc |
|---|---|
| `/start` | Chào / liên kết |
| `/moi` | Cuộc trò chuyện mới |
| `/kenh [tên]` | Chọn kênh |
| `/truc_tiep` | Về hỏi trực tiếp |
| `/model` | Đổi model |
| `/caidat` | Cài đặt |
| `/help` | Hướng dẫn |

## 5. Luồng chính

### 5.1. Liên kết tài khoản

1. Web → **Cài đặt → Tài khoản → Telegram** → **Liên kết Telegram**.
2. Hệ thống tạo mã 1 lần (hết hạn 10 phút) và mở `t.me/<bot>?start=acc_<mã>`.
3. Người dùng bấm Start. Bot gắn `from.id` với tài khoản, gửi tin chào và bàn phím (4.2).
4. Trang web tự hiện "Đã liên kết: @username" kèm nút **Hủy liên kết**.

**Quy tắc:**
- Một Telegram gắn với một tài khoản. Liên kết lại sẽ thay liên kết cũ.
- Mỗi tin đều kiểm tra lại: tài khoản còn hoạt động, có quyền `features.telegram`. Không đạt thì bot báo và hiện nút liên kết lại.
- Cùng chat gửi lại mã đã dùng thì bot báo "đã liên kết rồi", không báo mã sai. Đây là trường hợp mở link 2 lần.

### 5.2. Hỏi trực tiếp

- Dựng request như `automations._build_request`, kèm token ngắn hạn của người dùng (`typ: 'telegram'`, 1 giờ, có `session_stamp`).
- Mỗi chat Telegram có một **cuộc trò chuyện hiện tại**, là chat thật tên "Telegram · <câu hỏi đầu>", mở được trên web.
- Chat có `meta.source = 'telegram'`. Gọi `CHAT_COMPLETION_HANDLER` như `execute_automation`, lịch sử lấy từ chat.

### 5.3. Hỏi trong kênh

- `new_message_handler` đăng câu hỏi vào kênh dưới tên người dùng, có mention model mặc định và `meta.source = 'telegram'`. Hàm này đã kiểm tra quyền kênh.
- Rồi `model_response_handler` trả lời như web. Cần tách hàm này khỏi `Request` thật: nó chỉ dùng `request.app`, nên request dựng sẵn dùng được, theo mẫu `_execute_channel_automation`.
- Reply vào tin bot thì đăng câu hỏi vào cùng luồng (`parent_id`), dựa vào bảng ánh xạ tin.

### 5.4. Gửi câu trả lời

- Gửi ngay "⏳ Đang trả lời…" kèm nút **⏹ Dừng**. Nút Dừng hủy tác vụ như nút dừng trên web.
- Chờ AI xong: kênh thì chờ tin trả lời có `meta.done`, chat thì chờ message `done`. Đọc database mỗi giây, tối đa 5 phút.
- Trong lúc chờ, sửa tin tối đa 1 lần / 1,5 giây (giới hạn Telegram khoảng 1 tin/giây mỗi chat).
- Bản cuối:
  - Markdown chuyển sang HTML Telegram, cắt nhiều tin nếu dài;
  - bỏ khối suy nghĩ, khối gọi tool, chú thích `[1]`; bảng chuyển thành danh sách;
  - thêm nút (4.3 / 4.4).
- Câu trả lời có file gắn kèm thì gửi thêm file (mục 6.1).
- Lỗi hoặc quá thời gian: báo ngắn, kèm nút **🌐 Mở trên web**.

## 6. File PDF / Word / Excel

### 6.1. File AI tạo ra

Khi người dùng nhờ "xuất ra Word / PDF / Excel", AI gọi công cụ như trên web (ví dụ `export_project_report`). Công cụ gọi `attach_to_reply`, file được gắn vào câu trả lời (tin kênh: `data.files`; chat: `files` của message).

Khi câu trả lời xong, `reply.py`:
1. đọc danh sách file gắn kèm của câu trả lời;
2. lấy nội dung từ kho file (`Storage`), kiểm tra người dùng có quyền đọc file;
3. gửi bằng `sendDocument`, chú thích là tên file. Ảnh gửi bằng `sendPhoto`;
4. file lớn hơn **50 MB** (giới hạn bot gửi file) thì gửi nút **⬇️ Tải trên web** thay cho file.

### 6.2. Nút "📄 PDF / 📝 Word" dưới mọi câu trả lời

- Không gọi AI lại. Chuyển chính nội dung câu trả lời bằng `tracker/export.to_pdf` / `to_docx`:
  - tiêu đề là câu hỏi, rút gọn;
  - tên file `NCS-AI_<chủ đề>_<ngày>.pdf`;
  - PDF lỗi (thiếu font trên server) thì gửi Word và báo.
- File đồng thời gắn vào tin trên web (`attach_to_reply`), để người khác trong kênh cũng thấy.
- **Excel** không có nút chung, vì câu trả lời dạng chữ không thành bảng tính tốt. Excel có qua **📄 Xuất báo cáo → 📊 Excel** trong kênh, hoặc khi AI tự tạo bằng công cụ.

Nhấn nút nhiều lần thì chỉ tạo một file: cùng câu trả lời, cùng định dạng thì gửi lại file đã tạo.

## 7. Báo cáo định kỳ và tài khoản đã liên kết

**Trong form lịch báo cáo (web), mục 4. Gửi qua → Telegram:**

```
Telegram                                         [bật]
 ☑ Telegram của tôi: @nguyenvana   (tài khoản đã liên kết)
 ☐ Nhóm: Ban quản lý dự án BD2     (liên kết cũ, nếu có)
 [Liên kết tài khoản Telegram]      ← chỉ hiện khi chưa liên kết
```

- Tài khoản đã liên kết **luôn đứng đầu danh sách**, ghi "Telegram của tôi: @username". Bật Telegram thì mục này được **tích sẵn**.
- Lưu trong lịch dưới dạng `account` (không lưu `chat_id`). Lúc gửi mới tra tài khoản Telegram hiện tại của người tạo lịch:
  - hủy liên kết thì tự ngừng gửi Telegram cho lịch đó (ghi lỗi nhẹ trong lịch, không lỗi cả lịch);
  - liên kết Telegram khác thì báo cáo tự sang Telegram mới.
- **Chưa liên kết tài khoản:**
  - nút **Liên kết tài khoản Telegram** mở đúng luồng 5.1 trong hộp thoại;
  - liên kết xong, mục "Telegram của tôi" tự hiện và được tích, không cần tải lại trang (dùng lại cơ chế chờ liên kết hiện có).
- **Liên kết chat cũ** (`report_telegram_link`):
  - vẫn hiện và vẫn gửi bình thường;
  - chat riêng trùng với tài khoản đã liên kết thì gộp làm một, không hiện 2 dòng;
  - nhóm vẫn bị chặn theo cờ đang tạm tắt.
- **Thêm (tùy chọn):** ô "Gửi cả Telegram cho người nhận đã liên kết". Người nhận email nào là người dùng NCS AI đã liên kết Telegram thì nhận thêm qua Telegram. Mục "Báo cáo bạn đang nhận" có nút bật/tắt Telegram cho từng báo cáo.

**Bot phía Telegram:**
- Tin báo cáo có nút `[📊 Mở bảng tiến độ] [💬 Hỏi về báo cáo này]`.
- Nút "Hỏi về báo cáo này" chuyển sang hỏi trực tiếp, có sẵn nội dung báo cáo làm ngữ cảnh.

## 8. Dữ liệu (migration mới)

```
telegram_account               -- Telegram ↔ tài khoản NCS AI
  telegram_user_id TEXT PK     -- from.id (= chat_id của chat riêng với bot)
  user_id          TEXT UNIQUE NOT NULL
  username, display_name TEXT
  linked_at, last_seen_at BIGINT

telegram_link_code             -- mã liên kết tài khoản, 10 phút, dùng 1 lần
  code TEXT PK, user_id TEXT, created_at BIGINT, used_by TEXT NULL   -- used_by: để báo "đã liên kết" khi mở lại link

telegram_session               -- trạng thái từng chat
  chat_id TEXT PK, telegram_user_id TEXT,
  mode TEXT ('direct'|'channel'), model_id TEXT, chat_ref TEXT, channel_id TEXT,
  recent_channels JSON, updated_at BIGINT

telegram_message_map           -- tin bot ↔ tin trong kênh / chat (reply = hỏi tiếp, nút PDF/Word, Trả lời lại)
  chat_id TEXT, tg_message_id BIGINT, target TEXT, target_id TEXT, message_id TEXT, thread_id TEXT, files JSON
  PK (chat_id, tg_message_id); xóa sau 30 ngày
```

- Lịch báo cáo: `config.telegram.link_ids` nhận thêm giá trị `account`. Không cần đổi bảng.
- `report_telegram_link` giữ nguyên.

## 9. Bảo mật

- **Nội dung đi qua máy chủ Telegram**, kể cả file PDF / Word. Cần lãnh đạo / bộ phận an ninh đồng ý. Có thể:
  - tắt riêng "Gửi file qua Telegram";
  - chặn một số kênh (mục 3.1).
- **Danh tính:** chỉ tin `from.id`. Mã liên kết chỉ người đang đăng nhập web tạo được, 10 phút, 1 lần.
- **Khóa tài khoản / nghỉ việc:**
  - bot từ chối ngay ở tin kế tiếp;
  - quản trị viên gỡ liên kết trong trang Telegram.
- **Chỉ chat riêng:** trong nhóm, bot không trả lời câu hỏi.
- **File:** chỉ gửi file người dùng có quyền đọc, kiểm tra như khi tải trên web.
- **Nhật ký:**
  - tin / chat từ Telegram có `meta.source = 'telegram'`;
  - log mỗi câu hỏi (người, chế độ, kênh, thời gian, có file không), không ghi nội dung.
- **Mỗi môi trường một bot.** Trang Telegram cảnh báo khi phát hiện máy khác dùng chung bot (`409 Conflict`).

## 10. Giai đoạn và ước lượng

| Giai đoạn | Nội dung | Ước lượng |
|---|---|---|
| **0. Cấu hình chung** | Module `telegram/`; khóa `telegram.*` (chuyển từ `reports.telegram.*`); trang Cài đặt quản trị → Telegram + tab monitor; cảnh báo dùng chung bot | 2 ngày |
| **1. Liên kết tài khoản** | Bảng + migration; API mã / trạng thái / hủy; Cài đặt → Tài khoản → Telegram; `/start acc_…`; quyền `features.telegram`; bảng người đã liên kết | 2 ngày |
| **2. Báo cáo dùng tài khoản đã liên kết** | "Telegram của tôi" đứng đầu và tích sẵn; giá trị `account` khi gửi; gộp với liên kết chat cũ; nút liên kết trong form | 1,5 ngày |
| **3. Hỏi trực tiếp + nút** | Dispatcher, task theo người dùng, bàn phím 4.2 / 4.3 / 4.5; chờ và sửa dần; Dừng; Trả lời lại; đổi model; giới hạn | 3,5 ngày |
| **4. Hỏi trong kênh** | Chọn kênh (trang, tìm, gần đây); bàn phím kênh + câu soạn sẵn; tách `model_response_handler`; reply = luồng | 3 ngày |
| **5. File** | Gửi file AI tạo (6.1); nút PDF / Word (6.2); Xuất báo cáo PDF / Word / Excel trong kênh; nhận file / ảnh từ người dùng (4.6) | 3 ngày |
| **6. Hoàn thiện** | Nút trên tin báo cáo ("Hỏi về báo cáo này"); bài hướng dẫn trong /help có ảnh; test | 2 ngày |

Tổng khoảng **17 ngày công**. Thứ tự đề xuất: 0 → 1 → 2 (người dùng thấy ngay lợi ích ở báo cáo) → 3 → 4 → 5 → 6.

**Kiểm thử chính:**
- liên kết / hủy / liên kết lại / mở link 2 lần;
- tài khoản bị khóa;
- không thấy model / kênh không có quyền;
- lịch báo cáo tích "Telegram của tôi" rồi hủy liên kết, rồi liên kết Telegram khác;
- 2 người hỏi cùng lúc; câu trả lời dài hơn 4096 ký tự;
- AI tạo file Word / Excel trong kênh, file được gửi về Telegram;
- nút PDF bấm nhiều lần;
- file lớn hơn 50 MB;
- bấm Dừng giữa chừng;
- worker giữ khóa đọc khởi động lại khi đang trả lời;
- câu hỏi trong kênh hiện đúng trên web cho thành viên khác.

## 11. Cần quyết định

1. **Có được đưa nội dung dự án và file lên Telegram không?** Giới hạn theo kênh hay nhóm người dùng?
2. **Hỏi trong kênh đăng công khai vào kênh** (giống web, đề xuất) hay hỏi riêng có ngữ cảnh kênh?
3. **Câu hỏi soạn sẵn ở bàn phím kênh:** dùng 4 câu đề xuất (Tiến độ tổng quan / Việc quá hạn / Việc của tôi / Xuất báo cáo) hay câu khác?
4. **Báo cáo cho người nhận đã liên kết** (mục 7, ô tùy chọn): làm luôn hay để sau?
5. **Ai được dùng lúc đầu:** nhóm thử nghiệm hay mọi người?
