# Tiến độ: chọn nguồn, tóm tắt linh hoạt, tracker, nhập bảng công việc, tra cứu bảng tính

Trạng thái: ⬜ chưa làm · 🔄 đang làm · ✅ xong (đã test) · ⚠️ xong nhưng chưa test được đầy đủ

## Nhóm 1. Chọn đúng nguồn (file hay báo cáo dự án)
- ⚠️ 1a. Gỡ quy tắc ẩn tool báo cáo theo từ khóa
- ⚠️ 1b. Skill "chọn nguồn" gắn cố định vào model (admin sửa được)
- ⚠️ 1c. Tool tự chỉ đường hai chiều (báo cáo ↔ đọc file)
- ⚠️ 1d. Báo cáo ghi rõ file chưa được tổng hợp

## Nhóm 2. Tóm tắt file theo độ dài linh hoạt
- ✅ 2a. Lưu tóm tắt 3 tầng (phần, chương, toàn file)
- ✅ 2b. Độ dài mục tiêu tỉ lệ với file (cấu hình được)
- ✅ 2c. Tham số detail (ngắn/thường/đầy đủ) và section
- ✅ 2d. Trả theo nhiều phần + "tiếp"

## Nhóm 3. Tracker trích đầu việc từ văn bản sạch của pipeline
- ⚠️ 3a. Dùng văn bản sạch của pipeline thay cho tóm tắt riêng 40 phần
- ✅ 3b. Trích có cấu trúc, song song (tên, phụ trách, hạn, trạng thái, câu gốc, trang)
- ✅ 3c. Đếm dấu hiệu đầu việc, đánh dấu phần nghi thiếu
- ✅ 3d. Rà soát lần 2 các phần nghi thiếu
- ✅ 3e. Kiểm chứng câu gốc
- ⚠️ 3f. Hiển thị trên monitor

## Nhóm 4. Nhập bảng công việc Excel
- ✅ 4a. Đọc thô mọi sheet, lập hồ sơ sheet
- ✅ 4b. LLM hiểu cấu trúc → bản mô tả JSON (không nhận diện cột cứng)
- ✅ 4c. Code áp dụng bản mô tả cho mọi dòng
- ✅ 4d. Kiểm tra tỉ lệ đọc được, tự sửa; bảng tự do → LLM theo lô
- ⚠️ 4e. Lưu/sửa bản mô tả trên monitor, dùng lại cho phiên bản sau
- ⚠️ 4f. Ghép trùng (embedding + LLM), luật mâu thuẫn, xem lại trên monitor

## Nhóm 5. Báo cáo lọc theo hạn
- ⚠️ 5. due_from / due_to (và người phụ trách) cho get_project_status / build_project_report

## Nhóm 6. Tra cứu bảng tính không qua terminal
- ✅ 6a. Tool query_table chạy trong backend
- ✅ 6b. Hồ sơ bảng trong khối file đính kèm
- ✅ 6c. Hướng dẫn: terminal chỉ để tạo file / điền mẫu
- ⚠️ 6d. run_command chờ kết quả + giới hạn output

## Nhóm 7. Từ tab Xử lý file
- ⚠️ 7a. Bảng tính không tóm tắt văn xuôi (xlsx 1,2 MB mất 4–6 phút)
- ⚠️ 7b. Công cụ xem lại các trang "chưa chắc" của Nghị định 331

## Nhóm 8. Đầu việc từ file: chính xác, đủ ý, ngắn gọn, đúng người đúng hạn
- ⚠️ 8a. Người phụ trách / hạn / trạng thái / % / ngày hoàn thành do code đọc từ cột; model chỉ thu hẹp người trong cột,
  hạn riêng chỉ nhận khi ngày có trong câu, % chỉ từ cột hoặc số ghi rõ
- ⚠️ 8b. Tên ≤ 15 từ + Chi tiết (≤ 4 ý) + mã dòng; mặc định 1 việc / dòng, chỉ tách khi khác người hoặc hạn
- ⚠️ 8c. Độ phủ: mọi ý của ô được xếp (việc / vướng mắc / nhận định); ý sót hỏi lại, rồi code thêm vào Chi tiết
- ⚠️ 8d. Yêu cầu trong cột bối cảnh chưa có hành động → việc "Chưa giao" (không người, không hạn)
- ⚠️ 8e. Văn bản (PDF, DOCX): người / hạn / % chỉ giữ khi có trong văn bản; ảnh: vision chép chữ rồi trích như văn bản
- ⚠️ 8f. Phiên bản mới của bảng: dòng biến mất được ghi vào "Mâu thuẫn" (không xóa); monitor hiện chỉ số chất lượng

## Ghi chú kết quả từng phần
- Nhóm 1 + 5 + 7a (commit 9f63f5d52): quy tắc ẩn tool gỡ khỏi middleware; skill `chon-nguon` (seed) luôn nạp sẵn qua
  tham số `files.routing_skill`; báo cáo/`get_project_status` có `due_from`/`due_to` và ghi "file chưa tổng hợp" kèm gợi ý
  đọc file; khối file trong kênh dự án gợi ý dùng báo cáo cho câu hỏi việc/hạn; bảng tính bỏ bước tóm tắt văn xuôi.
  Đã test: đọc ngày (12/2026 → 01–31/12, năm nhuận), gợi ý chỉ hiện trong kênh. Chưa test trên app thật.
- Nhóm 6: `file_pipeline/tables.py` (đọc xlsx/csv, đoán dòng tiêu đề, bỏ dòng chỉ có công thức, lọc theo ngày/số/chữ,
  đếm theo nhóm) + tool `query_table`; pipeline lưu `table_profile` (sheet, số dòng, cột) đưa vào khối file; hướng dẫn chuyển
  bảng tính sang `query_table`, terminal chỉ để tạo file/điền mẫu; output terminal > 12.000 ký tự được cắt giữ đầu và cuối.
  Đã test trên NCS_CX_Feedback_Hub_MaKH_test.xlsx: 6 sheet đọc trong 2 s, lọc hạn tháng 11 ra 9 việc, đếm theo Owner đúng.
  6d còn thiếu: tham số "chờ kết quả" của run_command chưa xác minh được (cần xem /openapi.json của open-terminal).
- Nhóm 2: `summary.py` giữ 3 tầng (từng phần / từng chương / toàn file), độ dài toàn file = `files.summary.ratio_pct`
  (8%) số ký tự file, trong khoảng `min_chars` (3.000) – `max_chars` (60.000); tool `summarize_chat_file` có `detail`
  (brief/normal/full), `section` ("Chương II", "trang 12") và `part`; kết quả dài hơn `files.summary.page_chars` (12.000)
  trả làm nhiều phần kèm gợi ý gõ "tiếp". Bản tóm tắt cũ đã lưu (chưa có tầng) vẫn trả được. Vòng gộp tối đa 6 lần.
  Đã test với mô hình giả: file 109k ký tự, 5 chương → 5 phần, 5 chương, toàn file 7k; normal chia 2 phần; tìm chương
  sai báo danh sách chương. File đã tóm tắt trước bản này cần chạy lại tóm tắt (monitor) để có đủ 3 tầng.
- Nhóm 3 + 4 (`tracker/extract.py`, `media.py`, `service.py`, `files_admin.py`, monitor tab Tracker):
  - File văn bản: đọc HẾT các phần (12.000 ký tự/phần, song song theo `files.summary.concurrency`) từ văn bản sạch của
    pipeline (bỏ giới hạn 40 phần và bản tóm tắt 6.000 ký tự). Mỗi việc có câu gốc + trang. Phần có ≥ 4 dòng giống đầu việc
    mà trích < 40% được đọc lại lần 2. Câu gốc được dò trong văn bản (bỏ dấu, khoảng trắng): khớp → đưa thẳng vào hạng mục
    bằng code trước khi LLM tổng hợp chạy; chưa khớp → chỉ liệt kê cho LLM cân nhắc. File còn đang sửa OCR → chờ (tối đa 30 phút).
  - Bảng Excel: model nhận mô tả các sheet (dòng tiêu đề, 3 dòng dữ liệu, giá trị các cột ít giá trị) và trả bản mô tả JSON
    (sheet gốc, dòng tiêu đề, cột tên/mã/phụ trách/hạn/trạng thái, ánh xạ trạng thái, ký tự tách người, thứ tự ngày). Không có
    tên cột nào viết cứng trong code. Code đọc mọi dòng; < 80% dòng có tên, < 80% ô hạn đọc được hoặc còn trạng thái chưa ánh xạ →
    model tự sửa bản mô tả 1 lần từ các dòng lỗi; vẫn kém → model đọc dòng theo lô như văn bản. Mỗi dòng có khóa
    `table:<sheet>:<mã>` nên nhập lại phiên bản mới chỉ cập nhật, không tạo trùng.
  - Bản mô tả lưu theo chữ ký (tên sheet + dòng tiêu đề), phiên bản sau của file dùng lại không gọi model; sửa tay trên monitor
    (tab Tracker → Bản mô tả bảng) thì model không ghi đè; nút "Đọc lại" xóa bản trích cũ và cho tracker đọc lại ở lần tổng hợp sau.
  - Ghép trùng: cùng khóa dòng / cùng tên → cùng việc; tên giống (trigram ≥ 0.72) → cùng việc; hơi giống (trigram ≥ 0.35 hoặc
    ≥ 60% từ trùng) → model xác nhận theo lô 1 lần. Giá trị mới thắng, giữ cả hai nguồn; hạn / người phụ trách / trạng thái khác
    nhau được ghi vào "Mâu thuẫn khi ghép" trên monitor. Lưu ý: dùng độ giống trigram + model xác nhận thay cho embedding vì
    worker tracker có thể chạy tách khỏi app (không có hàm embedding).
  - Đã test (model giả lập): NCS_CX_Feedback_Hub_MaKH_test.xlsx: mô tả 6 sheet 17k ký tự trong 2 s; bản mô tả sai (cột hạn sai,
    thiếu ánh xạ "Đang thực hiện") được phát hiện và tự sửa → 9/9 việc, 9/9 hạn đúng, khóa PH-000001…; dùng lại bản mô tả: 0 lần
    gọi model; nhập 2 lần: lần 2 cập nhật 9, thêm 0. Văn bản: việc bịa bị đánh dấu chưa khớp; đọc lại lần 2 tìm thêm việc; trang
    lấy từ vị trí câu gốc; "Cài đặt máy chủ SIEM" ↔ "Cài đặt server SIEM" được model xác nhận, hạn đổi ghi mâu thuẫn.
  - Chưa test: chạy thật với Postgres + model thật (SQL các trang monitor mới, luồng process_channel, nút Đọc lại).
- Nhóm 7b: trang "Chưa chắc" trong tab Xử lý file có nút Dùng chữ OCR gốc / Dùng chữ vision / Chữ đang dùng là đúng / Sửa tay.
  Trang được đánh dấu "Đã kiểm tra" (không bị chấm lại hay sửa lại), file được ghép lại và lập chỉ mục lại (không chạy sửa OCR).
  Cần phiên chỉnh sửa. Chưa test trên app thật.
- Nhóm 8: `extract.summarize_rows` thay cho bước tách ô cũ; `check_against_text` cho văn bản; `_image_extraction` cho
  ảnh; trường `chi_tiet`, `chua_giao` trong hạng mục (schema, báo cáo, ngữ cảnh AI, giao diện "Chi tiết" + nhãn "Chưa gán").
  Đã test (model giả lập, cố tình trả lỗi): file phản hồi CSV 9 dòng → 13 việc + 1 yêu cầu chưa giao; người ngoài cột
  (Presale) bị loại; hạn 01/12 không có trong câu bị loại, hạn 13/11 có trong câu được nhận; 2 việc cùng người cùng hạn
  được gộp; ý bị bỏ sót được hỏi lại rồi thêm vào chi tiết; tên bịa bị thay bằng câu gốc; yêu cầu bịa (Y99) bị bỏ; bản
  mới thiếu PH-000008 → ghi mâu thuẫn "không còn trong bản mới". Kế hoạch dự án (WBS) có dòng tiêu đề ở dòng 4, % dạng
  0.6, ngày xong, không cột trạng thái: đọc đúng người (tách ";"), % 100/60/20/0, trạng thái suy từ %, ngày xong → hoàn
  thành. Biên bản họp: người / hạn / % không có trong văn bản bị loại. Chưa test với model thật và ảnh thật.
