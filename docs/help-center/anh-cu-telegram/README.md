# Ảnh hướng dẫn Telegram cũ (sao lưu 27/11/2026)

Ảnh và lời hướng dẫn của bot Telegram bản cũ (hỏi AI trong Telegram, nhận báo cáo định kỳ qua Telegram, liên kết không cần mã email).
Đã gỡ khỏi trung tâm trợ giúp khi đổi sang: liên kết bằng mã OTP qua email, chỉ gửi tin một chiều vào kênh, tạm ngừng gửi báo cáo qua Telegram.
Dùng lại khi mở lại các tính năng đó: chép ảnh về `static/help-center/images/` và thêm lại bước vào `static/help-center/articles.json`.

## Liên kết Telegram với tài khoản (`telegram_link`)

### `tgc-w2-lien-ket.png`

- Bước: **Bấm Liên kết Telegram**
- Chú thích: Ô Telegram trong Cài đặt → Tài khoản khi chưa liên kết
- Lời bước cũ: Chọn mục Tài khoản, kéo xuống ô Telegram rồi bấm Liên kết Telegram. Không thấy ô Telegram: quản trị viên chưa bật bot Telegram, hoặc chưa cho tài khoản của bạn dùng Telegram.

### `tgc-w3-mo-telegram.png`

- Bước: **Bấm Mở Telegram**
- Chú thích: Bấm Mở Telegram, cửa sổ chờ liên kết
- Lời bước cũ: Hiện hướng dẫn và nút Mở Telegram. Link chỉ dùng một lần, trong 10 phút. Giữ nguyên cửa sổ NCS AI (đang hiện "Đang chờ liên kết…") cho tới khi liên kết xong.

### `tgc-t1-start.png`

- Bước: **Bấm Start trong chat với bot**
- Chú thích: Bấm START ở cuối khung chat với bot
- Lời bước cũ: Telegram mở cuộc chat với bot NCS AI. Bấm START (BẮT ĐẦU) ở cuối khung chat. Không thấy nút này hoặc bấm không có phản hồi: quay lại NCS AI, bấm Liên kết Telegram lần nữa để lấy link mới.

### `tgc-t2-da-lien-ket.png`

- Bước: **Kiểm tra đã liên kết**
- Chú thích: Bot xác nhận đã liên kết và hiện bàn phím
- Lời bước cũ: Bot trả lời "✅ Đã liên kết với <tên của bạn> (<email>)" và hiện bàn phím dưới ô nhập: Hỏi trực tiếp, Hỏi trong kênh, Cuộc trò chuyện mới, Cài đặt. Trên NCS AI hiện thông báo "Đã liên kết Telegram".

### `tgc-w4-da-lien-ket.png`

- Bước: **Xem hoặc bỏ liên kết**
- Chú thích: Tài khoản đã liên kết Telegram
- Lời bước cũ: Ô Telegram trong Cài đặt → Tài khoản ghi Đã liên kết cùng tên Telegram của bạn và tên bot. Mở Telegram để mở lại chat với bot. Bỏ liên kết khi không dùng Telegram đó nữa: bạn sẽ không hỏi AI và không nhận báo cáo qua Telegram. Liên kết một Telegram khác sẽ thay liên kết cũ.

## Chat với AI qua Telegram (`telegram_chat`)

### `tgc-t3-dang-tra-loi.png`

- Bước: **Hỏi trực tiếp**
- Chú thích: Bot đang trả lời, có nút Dừng
- Lời bước cũ: Liên kết xong (xem bài "Liên kết Telegram với tài khoản"), gõ câu hỏi bất kỳ vào chat với bot, như chat riêng với AI trên web. Bot gửi ngay tin "💬 Trực tiếp · <model> ⏳ Đang trả lời…" rồi sửa dần thành câu trả lời. Bấm ⏹ Dừng để hủy. Mỗi lần bot trả lời một câu: gửi câu mới khi câu trước chưa xong, bot nhắc bạn đợi.

### `tgc-t4-cau-tra-loi.png`

- Bước: **Dùng các nút dưới câu trả lời**
- Chú thích: Cuối câu trả lời có nút PDF, Word, Trả lời lại
- Lời bước cũ: 📄 PDF / 📝 Word: nhận chính câu trả lời này thành file, không hỏi AI lại. 🔁 Trả lời lại: AI trả lời lại câu vừa hỏi. 🌐 Mở trên web (khi có): mở đúng cuộc trò chuyện trên {{name}}, tên là "Telegram · <câu hỏi đầu>". Câu trả lời quá dài thì cuối tin ghi "câu trả lời còn dài, xem tiếp trên web".

### `tgc-t5-chon-kenh.png`

- Bước: **Chọn kênh để hỏi**
- Chú thích: Danh sách kênh để chọn
- Lời bước cũ: Bấm 📁 Hỏi trong kênh (hoặc gõ /kenh). Bot hiện các kênh bạn được hỏi trên web: kênh dùng gần đây có ⭐ ở đầu, mỗi trang 6 kênh. Gõ /kenh <tên> để tìm, ví dụ /kenh bd2. Không thấy kênh: bạn không có quyền trong kênh đó, hoặc quản trị viên không cho hỏi kênh đó qua Telegram.

### `tgc-t6-hoi-trong-kenh.png`

- Bước: **Hỏi trong kênh**
- Chú thích: Đang hỏi trong kênh, bàn phím theo kênh
- Lời bước cũ: Bot báo "📁 Đang hỏi trong #<kênh>". Câu hỏi được đăng vào kênh dưới tên bạn và AI trả lời theo dữ liệu của kênh, giống gọi AI trong kênh trên web: thành viên kênh đều thấy. Bàn phím đổi theo kênh: 📊 Tiến độ tổng quan, ⏰ Việc quá hạn, 👤 Việc của tôi gửi câu hỏi soạn sẵn; 📄 Xuất báo cáo cho chọn PDF, Word hoặc Excel. Trả lời (reply) vào tin của bot để hỏi tiếp trong cùng luồng. 🔄 Đổi kênh để chọn kênh khác; ↩️ Về hỏi trực tiếp (hoặc /truc_tiep) để quay lại chat riêng.

### `tgc-t8-cai-dat.png`

- Bước: **Cài đặt, đổi model, hủy liên kết**
- Chú thích: Menu Cài đặt của bot
- Lời bước cũ: Bấm ⚙️ Cài đặt (hoặc /caidat) để xem tài khoản đang liên kết, model đang dùng và số lịch báo cáo gửi vào đây. 🤖 Đổi model (hoặc /model): chọn trong các model bạn được dùng trên web. 📬 Báo cáo nhận tại đây: các lịch báo cáo đang gửi vào Telegram này. ❓ Hướng dẫn: cách dùng tóm tắt. 🔓 Hủy liên kết: ngắt Telegram khỏi tài khoản, bot hỏi xác nhận trước.

## Đặt lịch báo cáo tiến độ tự động (`reports`)

### `tgr-b1-telegram-cua-toi.png`

- Bước: **Chọn nơi nhận**
- Chú thích: Gửi qua email cho chính mình và vào Telegram của tôi
- Lời bước cũ: Ở mục 4. Gửi qua. Email: bật công tắc, tích Gửi cho tôi; thêm người nhận khác bằng cách gõ email rồi nhấn Enter (nếu quản trị viên giới hạn, chỉ thêm được thành viên các kênh trong báo cáo). Telegram: bật công tắc; Telegram của tôi được tích sẵn nếu bạn đã liên kết Telegram, xem bài "Nhận báo cáo qua Telegram". Đã bật Telegram thì phải tích ít nhất một Telegram, nếu không Xem trước và Lưu lịch sẽ báo lỗi "Telegram: chọn ít nhất một chat đã liên kết".

## Nhận báo cáo qua Telegram (`reports_telegram`)

### `tgr-b1-telegram-cua-toi.png`

- Bước: **Bật gửi qua Telegram**
- Chú thích: Telegram của tôi được tích sẵn khi bật Telegram
- Lời bước cũ: Mở lịch báo cáo (tạo mới hoặc bấm vào lịch có sẵn). Ở mục 4. Gửi qua, bật công tắc Telegram. Đã liên kết Telegram với tài khoản thì dòng "Telegram của tôi: @<tên>" đứng đầu và được tích sẵn. Không thấy ô Telegram: quản trị viên chưa bật gửi báo cáo qua Telegram.

### `tgr-b2-chua-lien-ket.png`

- Bước: **Chưa liên kết: bấm Liên kết Telegram**
- Chú thích: Chưa liên kết Telegram: bấm Liên kết Telegram
- Lời bước cũ: Ô Telegram ghi "Chưa liên kết chat Telegram nào.": bấm Liên kết Telegram, rồi Mở Telegram và bấm Start trong chat với bot, như bài "Liên kết Telegram với tài khoản". Liên kết xong, Telegram của tôi tự hiện và được tích, không cần tải lại trang.

## Cấu hình bot Telegram (`admin_telegram`)

### `tga-1-bot.png`

- Bước: **Nhập token và kiểm tra**
- Chú thích: Bot token, nút Kiểm tra, trạng thái và các tính năng
- Lời bước cũ: Mở Cài đặt (ảnh đại diện → Cài đặt), ở phần Quản trị chọn Telegram. Dán token vào ô Bot token, bấm Kiểm tra: hiện "Bot: @<tên bot>" là đúng, rồi bấm Lưu. Dòng trạng thái cho biết bot có đang đọc tin không và lần cuối đọc được. Báo có ứng dụng khác đang đọc bot (409 Conflict): một môi trường khác đang dùng chung token, hãy đổi sang bot riêng.

### `tga-2-gioi-han.png`

- Bước: **Đặt giới hạn và model**
- Chú thích: Giới hạn và model mặc định cho Telegram
- Lời bước cũ: Số tin mỗi người mỗi phút (mặc định 10). Số câu hỏi trả lời cùng lúc của cả hệ thống (mặc định 5): câu vượt quá phải xếp hàng. Tự sang cuộc trò chuyện mới sau bao nhiêu giờ không hỏi (mặc định 4). Thời gian chờ câu trả lời tối đa (mặc định 30 phút): tóm tắt file lớn có thể mất vài phút, nên đừng đặt quá thấp. Model mặc định cho Telegram: để trống thì dùng model mặc định của người dùng.

### `tga-3-da-lien-ket.png`

- Bước: **Chặn kênh và quản lý liên kết**
- Chú thích: Danh sách tài khoản đã liên kết (dữ liệu minh họa)
- Lời bước cũ: Kênh không cho hỏi qua Telegram: tìm và tích các kênh không được hỏi từ bot. Bảng Tài khoản Telegram đã liên kết cho biết ai đã liên kết, khi nào và lần dùng cuối; bấm Bỏ liên kết để gỡ, ví dụ khi người đó nghỉ việc. Tài khoản bị khóa sẽ bị bot từ chối ngay ở tin kế tiếp.
