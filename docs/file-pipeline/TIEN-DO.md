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

## Nhóm 9. Gán việc đúng người, giảm việc chưa gán, quản lý kênh (kế hoạch: KE-HOACH-GAN-VIEC.md)
Chốt: Q1 kế hoạch không nêu người → chưa giao; Q2 có danh sách "không nhận việc chung"; Q3 nhắc thứ Hai chỉ chủ kênh /
người quản lý; Q4 bước rà lại yêu cầu chưa giao: chưa làm.
- ✅ T1. Prompt tổng hợp chat: `giao_cho`, tin @Knight vẫn giao việc, đính chính thay danh sách
- ✅ T2. Code tính người phụ trách (tên/@tag, chức vụ, cả nhóm, tự nhận, không rõ), kiểm tên có trong tin, bắt buộc nguồn
- ✅ T3. Bảng chức vụ / nhóm / "không nhận việc chung" của kênh: lưu, tự học từ tin, API
- ✅ T4. Giao diện "Thành viên & chức vụ" trong bảng tiến độ kênh
- ✅ T5. Nhãn Ngoài kênh / Bên ngoài / Đã nghỉ; bí danh kênh; gán nhanh việc chưa có người
- ✅ T6. Yêu cầu chưa giao: gom 1 việc / dòng, gán người phụ trách của dòng
- ✅ T7. Lỗi nhỏ việc từ file: hạn đặt sai việc, chi tiết trùng, vướng mắc là nhận định, tên cắt ở ":"
- ✅ T8. Nhắc thứ Hai cho chủ kênh / người quản lý (thông báo + email), cấu hình trên monitor
- ✅ T9. Người quản lý kênh (vai trò manager): gán / bỏ, quyền tiến độ như chủ kênh
- ✅ T10. Chuyển chủ kênh; tài khoản bị khóa (tự chuyển, việc "đã nghỉ")
- ✅ T11. Monitor: % chưa gán / chưa liên kết theo kênh, đổi chủ kênh / người quản lý
- ✅ T12. Kiểm thử cũ + mới, cập nhật tài liệu, commit
- ⬜ T13. Deploy, tổng hợp lại kênh bd3, poc soc, av (phía server)

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
- Tổng hợp lại kênh (monitor → Tracker → Kênh dự án): xóa trạng thái đang mở và bản trích file cũ của kênh, đưa con
  trỏ tin nhắn về 0; tracker đọc lại toàn bộ tin nhắn và file theo lô cho đến hết (cột "Chờ tổng hợp" giảm về 0). Việc đã
  đóng/hủy giữ trong lưu trữ và không bị tạo lại (theo tên, hoặc theo khóa dòng bảng: hủy một việc của dòng chỉ chặn việc
  đó, việc cũ mang khóa cả dòng thì chặn cả dòng). Từ chối khi kênh đang được tổng hợp. Cần phiên chỉnh sửa. Chưa test trên app thật.
- Bỏ regex nhận diện ý nghĩa: người phụ trách do model tách từ ô gốc (code chỉ kiểm tên có nguyên từ trong ô), hạn riêng
  do model chỉ ra theo từng ý (code kiểm ngày có viết trong ý rồi tách việc), đọc lại phần văn bản theo số việc model đếm
  ("tong_so_viec") thay cho đếm từ khóa. Regex còn lại chỉ để kiểm / đọc định dạng: dò câu gốc, đọc ngày, kiểm %, cắt câu
  theo dấu chấm để đánh số ý, mention và link file của hệ thống.
- Nhóm 9 / T1–T2 (`tracker/assign.py`, `channel_config.py`, `service.py`, `prompts.py`, `schema.py`): model trả `giao_cho`
  (ten / chuc_vu / ca_nhom / tu_nhan / khong_ro), code tính người: @tag theo mã tài khoản, tên phải có trong tin (hoặc
  tên email "vietanh.nguyen", 2 chữ cuối họ tên, bí danh kênh); "cả nhóm" = thành viên trừ admin, chủ kênh, người quản
  lý, người gửi, "không nhận việc chung"; chức vụ tra bảng chức vụ kênh (học từ tin "AM: @…"); đính chính thay danh sách;
  việc mới không có tin nguồn bị bỏ; tin @Knight vẫn giao việc. Đã test trên tin thật bd3 / poc soc (model giả lập cố
  tình sai): AM đúng 3 người, bỏ Sơn; "các nhân sự" bỏ Sơn / My / người không nhận việc chung; 9 tên tự nở bị loại;
  đính chính thay đúng 5 người; vietanh.nguyen → Nguyễn Việt Anh; PH04 bên ngoài; việc không nguồn bị bỏ.
- Nhóm 9 / T3, T5, T9, T10 phần backend (`tracker/team.py`, `router.py`, `models.edit_state`, `mywork.py`): API bảng chức vụ
  (sửa tay → việc theo chức vụ đổi người theo), "không nhận việc chung", gán nhanh + nhớ bí danh, người quản lý kênh
  (vai trò manager) có quyền như chủ kênh ở tiến độ / trang Công việc, chuyển chủ kênh (chủ cũ thành người quản lý),
  nhãn người phụ trách: đã liên kết / ngoài kênh / bên ngoài / đã nghỉ (tài khoản pending hoặc đã xóa; không nhắc tới họ).
- Nhóm 9 / T6–T7: yêu cầu chưa giao gom 1 việc / dòng "Bổ sung hành động cho N yêu cầu chưa được xử lý", gán người phụ
  trách của dòng, yêu cầu ở phần chi tiết; hạn model đặt nhầm việc → tách ý chứa ngày đó thành việc riêng (PH-000006 có
  việc hạn 13/11); bỏ chi tiết trùng / lặp tên; tên không cắt ở ":"; dòng tiêu đề "PHẢN ÁNH CHUNG:" không thành ý. Đã test
  trên file phản hồi thật (model giả lập mắc đúng lỗi av).
- Nhóm 9 / T8, T10 (tự động): sáng thứ Hai (cùng giờ nhắc hạn) chủ kênh + người quản lý nhận thông báo + email "việc cần
  phân công" (chưa có người, nhóm chưa khai báo, ngoài kênh, đã nghỉ); bật/tắt trên monitor (mục Email). Email tổng hợp
  hằng ngày gửi cả người quản lý. Không gửi mail tới tài khoản đã khóa. Mỗi ngày, kênh có chủ kênh bị khóa tự chuyển cho
  người quản lý lâu nhất; không có người quản lý thì ghi log để admin chọn (monitor hiện ở T11).
- Nhóm 9 / T4–T5, T9–T10 giao diện: bảng tiến độ kênh có mục "Thành viên & chức vụ" (`ChannelTeam.svelte`): chủ kênh / quản lý /
  admin / đã nghỉ, đánh dấu "không nhận việc chung", đặt / bỏ người quản lý, chuyển chủ kênh (chủ cũ thành quản lý), sửa
  chức vụ / nhóm (chức vụ học từ tin hiện "học từ tin nhắn"), xóa bí danh. Menu mỗi việc có "Gán người phụ trách" (chọn
  thành viên; tên chưa liên kết như "Duy" có ô "từ nay Duy trong kênh này là người này"). Nhãn cạnh người phụ trách:
  Bên ngoài / Ngoài kênh / Đã nghỉ, và nhãn nhóm ("AM"). svelte-check không lỗi mới; chưa thử trên trình duyệt.
- Nhóm 9 / T11: monitor → Tracker → Kênh dự án có cột chủ kênh (báo "đã nghỉ") / người quản lý, số và % việc chưa gán /
  chưa liên kết (≥ 30% tô vàng), nút "Chủ kênh & quản lý" để admin chuyển chủ kênh, đặt / bỏ người quản lý (popup OTP).
  SQL mới chưa chạy trên Postgres thật.
- Nhóm 9 / T12: chạy lại 8 bộ kiểm thử (file phản hồi, bảng kế hoạch, văn bản, ảnh mô phỏng, gán việc bd3 / poc soc,
  yêu cầu chưa giao) đều qua; pyflakes không báo tên chưa định nghĩa; svelte-check không lỗi mới. Chưa chạy trên app thật.
- Còn lại T13 (phía server): deploy (build lại frontend), Tổng hợp lại kênh bd3_csc_proj_26, bd3_monthly_kpi_26,
  poc_cg1018526003_soc, av; chủ kênh kiểm "Thành viên & chức vụ", đánh dấu "không nhận việc chung", đặt người quản lý.
- Nhóm 9 bổ sung: "@all" / "@mọi người" (model báo `goi_tat_ca`, kênh không có kiểu tag này) → mọi thành viên trừ người
  gửi, có cả admin và chủ kênh; "mọi người" viết thường vẫn bỏ admin / chủ kênh / quản lý / người gửi / không nhận việc
  chung. Chức vụ / team chưa khai báo trong kênh ("các AM", ô "SOC, PM" trong file) → người phụ trách là chính team
  ("AM", "SOC"), nhãn "Nhóm chưa khai báo người", không còn "chưa gán"; khi kênh khai báo (tin "AM: @…" hoặc sửa tay)
  các việc đó tự gán cho thành viên. Bỏ danh sách tiền tố cứng ("các", "team"…) khi tra chức vụ: model trả tên chức vụ.
- Nhóm 9 bổ sung: trùng tên trong kênh (2 "Nguyễn Trà My"): việc lưu tài khoản cố định cho từng người phụ trách
  (`phu_trach_links`) khi biết chắc (tag, gán tay, chức vụ), không suy lại từ tên; tên trùng hiện kèm phòng ban; tin không
  tag mà tên trùng thì không gán (chủ kênh chọn); không tạo bí danh cho tên trùng. Đã test: tag My Sales / My SYS gắn đúng
  từng người, "chị Trà My" không tag → không gán, cập nhật và tổng hợp lại vẫn giữ đúng tài khoản.
- Nhóm 9 bổ sung: giao lại việc qua tin nhắn: "chuyển cho / giao lại / thay / bỏ / chỉ còn" (model đánh `thay_the`) thay
  danh sách người; "thêm / cùng / hỗ trợ" và trường hợp model không đánh dấu thì giữ người cũ và thêm người mới. Đã test
  trên việc báo cáo AM của bd3_csc_proj_26: chuyển hết cho Hùng → Hùng; thêm Tuấn Anh → Hùng + Tuấn Anh; bỏ Hùng chỉ còn
  Tuấn Anh → Tuấn Anh; thêm Nghĩa không đánh dấu → Tuấn Anh + Nghĩa.
- Nhóm 9 bổ sung: "Thành viên & chức vụ" chuyển vào Chỉnh sửa kênh (nút "Quản lý", có mục Hướng dẫn chi tiết); bảng tiến
  độ ẩn nút "Nguồn", danh sách chính chỉ hiện việc đã có người phụ trách (người hoặc team), việc chưa gán ở tab "Chưa gán (N)".
  Chủ kênh bị khóa tài khoản: chọn chủ mới theo thứ tự quản lý → admin hệ thống trong kênh (kênh nhóm: thành viên; kênh
  thường: người từng nhắn) → người nhắn nhiều nhất 90 ngày → kênh nhóm: thành viên vào sớm nhất / kênh thường: admin hệ
  thống lâu năm nhất; người được chọn nhận thông báo. Monitor: cột "chưa liên kết" không đếm team (SOC, TI…).
- Nhóm 9 bổ sung: bảng tiến độ mỗi việc chỉ hiện người phụ trách (người / team), hạn, tiến độ, chi tiết, vướng mắc; bỏ các
  nhãn cảnh báo, nhãn nhóm và mã dòng. Xóa tin nhắn → việc chỉ có nguồn là tin đó bị gỡ; sửa tin gỡ file / ảnh → việc chỉ
  đến từ file đó bị gỡ (việc trích từ file ghi nguồn "file:<id>"); việc còn tin khác nhắc thì giữ, chỉ bỏ nguồn đã mất.
- Nhóm 9 bổ sung (kênh av 17/11): việc gom yêu cầu chưa có hành động có tên theo nội dung ("Yêu cầu chưa có hành động:
  rà soát dữ liệu cũ… (+2)"), chi tiết chỉ là các yêu cầu (bỏ câu "còn thiếu" lộ chữ nội bộ); hạn chép đúng như trong ý
  ("han_goc") được ưu tiên, hạn chỉ viết ở một ý thì tách ý đó thành việc riêng (PH-000006 hạn 13/11); bỏ chi tiết trùng
  (kể cả sau khi gộp việc); tên mà bảng gọi là team ở dòng khác (TI) là team ở mọi dòng; tên trùng chức vụ đã khai báo
  tự gán thành viên.
- Nhóm 9 bổ sung (kênh av, đầu việc dạng việc cần làm): yêu cầu của khách mà cột Next step/Action chưa có hành động nào
  đáp ứng không còn gom thành "Yêu cầu chưa có hành động: … (+N)". Model ghi mỗi yêu cầu thành một việc bình thường bắt
  đầu bằng động từ ("Xây dựng dashboard ticket management cho CEO"), chi tiết "Khách yêu cầu: …", giao cho người / team phụ
  trách dòng; gộp yêu cầu trùng, tối đa 3 việc mỗi dòng. Đọc lại file: việc cũ của cùng dòng mà lần đọc mới không còn
  (việc gộp kiểu cũ) được thay, không để song song; lịch sử ghi "thay N". EXTRACT_VERSION 11.
- Nhóm 9 bổ sung (rà các kênh test, test01, test02, av ngày 17/11): bảng kế hoạch có cột "Mô tả chi tiết" trước đây bị
  model xếp vào bối cảnh và gần như mất; nay bản mô tả bảng có vai trò "mo_ta" (cách làm, phạm vi của chính việc), các ý
  của cột này đánh số tiếp sau tên việc và vào chi tiết của việc (ý model bỏ sót được code thêm). Bản mô tả cũ do model
  lập chưa có "mo_ta" thì được lập lại (bản sửa tay giữ nguyên). Cột kết quả và các ý thêm vào chi tiết không còn bị cắt
  ở dấu ";" đầu tiên; dòng "Kết quả" luôn giữ khi chi tiết đủ 6 dòng. Prompt: "Long (BE)" là người Long, không thêm nhóm
  BE; tên viết tắt SOC / TI / CX / PM là nhóm. EXTRACT_VERSION 12.
- Nhóm 9 bổ sung (không ghi cứng nhãn): chi tiết việc chỉ ghi nội dung của file, bỏ nhãn "Khách yêu cầu:" và "Kết quả:"
  do code thêm (file khác có thể là mô tả lỗi, yêu cầu nội bộ…); prompt không giả định nguồn là khách. Ý của cột mô tả
  mà model trích dẫn nhưng không viết vào tên / chi tiết thì code thêm nguyên văn. Model viết tên, chi tiết cùng ngôn
  ngữ với ô (file tiếng Anh không bị thay tên vì kiểm tra trùng từ). Đã thử 3 dạng bảng khác: nhật ký lỗi tiếng Anh
  (ngày mdy, cột Resolution), danh sách việc không mã có % dạng 0.5, bảng không có cột người và hạn. EXTRACT_VERSION 13.
- Nhóm 9 bổ sung (test01: xóa hết tin vẫn còn 16 việc): tin bị xóa trong lúc một lượt tổng hợp đang đọc nó (nhất là ngay
  sau "Tổng hợp lại kênh", mốc đọc về 0) bị bỏ qua, lượt đó vẫn ghi việc vào. Nay lần xóa trong lúc đang chạy được ghi
  nhận; mỗi lượt còn đối chiếu nguồn: việc mà mọi tin nguồn đã không còn trong kênh (kể cả trả lời bị xóa theo tin gốc,
  vốn không qua hook xóa) được gỡ như tin bị xóa. Nút làm mới ↻ trên bảng tiến độ luôn chạy một lượt (không có gì mới thì
  chỉ đối chiếu, không gọi model).
- Monitor, tab Tracker → Kênh dự án: nút "Xem việc" mở danh sách việc của kênh (lọc: đang mở, chưa gán, đề xuất từ
  yêu cầu, từ file, chỉ từ tin nhắn, đã đóng / hủy) và nút "Truy vết" từng việc: vì sao có việc (dòng nào của file, cột
  nào, tách từ dòng vì khác người / hạn, đề xuất từ yêu cầu chưa ai xử lý, tổng hợp từ tin nhắn nào), người phụ trách
  được xác định thế nào (gắn chắc / team chưa khai báo / ngoài công ty / khớp tên / chưa khớp), kết quả đọc file và câu
  gốc, các tin nhắn nguồn (đánh dấu tin đã xóa), các lượt tổng hợp đã đọc tin nguồn. Cần khóa truy xuất hoặc phiên chỉnh
  sửa (nội dung kênh). Bản trích bảng lưu thêm tên tiêu đề của các cột đã dùng ("tieu_de_cot") để truy vết hiện tên cột.
- Sửa lỗi gỡ việc không có tác dụng (av 17/11 20:09: lịch sử "Gỡ 19 đầu việc" nhưng giao diện vẫn còn 19): bước lưu
  (reconcile) thêm lại mọi việc có trong trạng thái hiện tại mà kết quả mới thiếu, để giữ việc được mở lại trong lúc
  chạy; nó cũng thêm lại việc vừa bị gỡ có chủ đích. Nay việc đang mở lúc lượt bắt đầu mà kết quả không còn được coi là
  đã gỡ có chủ đích (tin / file nguồn bị xóa, thay bởi lần đọc lại file, gộp, việc giao cho AI), không bị thêm lại; việc
  được mở lại trong lúc chạy vẫn được giữ. Lỗi này cũng làm phần "thay việc cũ khi đọc lại file" không có tác dụng.
- Hỏi Knight trong kênh về file gửi hôm trước ("Nghị định 331.pdf" 16/11) mà Knight trả lời chỉ thấy 3 file CSV/MD:
  trước đây khi tin hỏi (hoặc luồng / tin được trả lời) đã có file, chỉ các file đó được đưa cho model, file khác của
  kênh bị bỏ; file nằm trong luồng trả lời hoặc cũ hơn 50 tin gần nhất cũng không bao giờ được tìm. Nay thứ tự là: file
  được chỉ tới → file của kênh mà câu hỏi gọi tên (khớp tên file bỏ dấu, quét 1000 tin gần nhất kể cả trong luồng) →
  file gửi gần đây; tối đa CHANNEL_MODEL_MAX_FILES (5).
- Kênh: tin trả lời của AI đang chạy (giữa các lần gọi công cụ, lúc model đang viết) hiện dòng "Đang trả lời…" có
  vòng quay dưới các bước công cụ, tắt khi xong (meta.done). Tin không bao giờ được đánh dấu xong (máy chủ dừng giữa
  chừng) thôi hiện sau 10 phút không thay đổi.
- Kênh, hỏi tiếp về tài liệu (20:40 "trách nhiệm và nghĩa vụ của các tổ chức an toàn thông tin"): câu hỏi không nhắc
  tên file nên PDF không được đưa vào, Knight tự viết nội dung "Điều 23–26" từ trí nhớ (trái với Điều 24 vừa trích đúng),
  rồi tự làm thêm báo cáo dự án, xuất Word, ghi chú giữa các bước bằng tiếng Anh. Sửa: tên file nhắc trong 6 tin gần
  nhất (câu hỏi trước, câu trả lời có trích file, tin trong luồng) giữ file đó cho câu hỏi tiếp theo; prompt kênh thêm
  quy tắc trả lời: cùng ngôn ngữ người hỏi kể cả ghi chú giữa các bước, trả lời đúng câu hỏi một lần, nội dung tài liệu
  chỉ lấy từ việc đọc tài liệu (không đọc được thì nói và xin file, không viết từ trí nhớ, không trái với đoạn đã trích).
- Lỗi "TrackerLLMError: Invalid state after retry: Expecting ',' delimiter … (char 8907)" khi tổng hợp lại kênh trên
  server: khi đọc lại cả kênh, một lượt gọi chứa nhiều tin, câu trả lời JSON vượt TRACKER_MAX_TOKENS (4096) nên bị cắt,
  hỏi lại cùng lượng tin lại bị cắt. Nay đọc finish_reason: bị cắt thì không hỏi lại vô ích; câu trả lời bị cắt hoặc
  JSON hỏng hai lần thì nhóm tin được đọc lại thành hai nửa (đến từng tin), theo đúng thứ tự.
- Monitor: nút "Tổng hợp lại tất cả kênh đang hoạt động" (kênh có tin nhắn trong 30 ngày), xác nhận kèm danh sách kênh,
  số tin, số việc; kênh đang tổng hợp được bỏ qua; worker xử lý lần lượt vài kênh một lúc. Cần phiên chỉnh sửa.
- Nhập bảng mất dòng khi cột "mã" trùng giữa nhiều dòng (KPI_Presale 17 việc → 13 đầu việc, KPI_PM 13 → 11 trên
  server): các dòng cùng mã có cùng khóa nên bị gộp còn một. Nay mã dùng cho nhiều dòng thì khóa thêm số dòng
  ("Doanh số dòng 2"), thống kê "shared_codes" hiện trên monitor; nhãn "yêu cầu chưa giao" đổi thành "đề xuất từ yêu cầu".
  Kênh đã nhập file kiểu này cần "Tổng hợp lại kênh" để lấy lại các dòng bị gộp.
- KPI_PM.xlsx (phiếu giao chỉ tiêu, hcm_pmo_kpi): 13 dòng → 11 đầu việc sai cả hai chiều. Thiếu 2 chỉ tiêu (mã "1"
  lặp lại ở mỗi nhóm nên bị gộp: đã sửa ở ff423acb3); thừa các việc bịa từ dòng tiêu đề nhóm ("TÀI CHÍNH" → "Xử lý
  công việc tài chính"), dòng tổng, dòng chữ ký ("Phối hợp giữa Ban Giám đốc và Trưởng phòng Nhân sự"); chi tiết có "0"
  (cột Kết quả là điểm số) và "1" (câu "1. Tử …" bị cắt ở số thứ tự); cột "Thời gian hoàn thành" ghi chữ ("Hàng tháng")
  bị báo đỏ 0/6 ô hạn. Sửa: model đánh dấu dòng không phải việc (tiêu đề nhóm / tổng / chữ ký), code chỉ bỏ dòng không
  có người và hạn; cột kết quả toàn số không đưa vào chi tiết; mẩu ngắn ghép vào câu sau; cột hạn không có ô nào là
  ngày thì nội dung thành chi tiết kèm tên cột của file ("Thời gian hoàn thành: Hàng tháng"), không còn báo lỗi và gọi
  model sửa bản mô tả. EXTRACT_VERSION 15. Thử với file thật: đủ 6 chỉ tiêu, bỏ 6 dòng không phải việc.

## Đọc bảng tính theo hướng agent (17/11, sau khi rà 63 bản trích trên production)

Lỗi trên production gom về 3 gốc: dòng trùng mã bị gộp (BD2 Timeline 252 việc còn 9, các Masterplan mất một nửa),
code tự cắt / lọc nội dung ô bằng quy tắc cứng (regex tách câu, nhận ngày, bỏ dòng có ":", cột kết quả là số…), ô gộp
không được điền xuống. Thêm: file .xls đọc qua pandas biến ô trống thành chữ "nan" (873 ô ở Masterplan Quảng Ninh), và
bộ lọc dòng cũ bỏ mọi dòng có cột đầu trống mà chưa điền nửa số ô.

Làm lại:
- Đọc file: ô gộp nhiều dòng được điền vào từng dòng nó phủ (xlsx, xls qua xlrd); ô trống của pandas không còn là "nan".
- Code chỉ chép bảng: mọi dòng có chữ dưới dòng tiêu đề (bỏ dòng toàn số / cờ và dòng lặp lại tiêu đề), mọi ô kèm tên
  cột thật của file, phần đầu trang phía trên tiêu đề; khóa dòng là mã nếu mã không trùng, không thì số dòng.
- Model đọc cả dòng và quyết định: có phải việc không (tiêu đề nhóm, tổng, chữ ký…), tên, chi tiết, người phụ trách,
  hạn (kể cả hạn ghi chữ), trạng thái, tiến độ, vướng mắc, tách việc khi khác người / khác hạn, đề xuất từ yêu cầu chưa
  ai xử lý. Bản mô tả cột của model chỉ còn là gợi ý.
- Code chỉ kiểm: người, chữ ghi hạn, chữ ghi tiến độ, ngày hoàn thành phải có nguyên văn trong dòng (không có thì loại và
  đếm); ngày ghi dạng số rõ ràng (30/11/2026) thắng ngày model tự đổi; tên / chi tiết / vướng mắc phải lấy từ chữ của
  dòng; ô có nội dung mà kết quả không phản ánh được chép nguyên văn vào chi tiết; dòng model không trả lời được hỏi lại
  riêng, rồi giữ nguyên chữ. Dòng model bỏ (không phải việc) được liệt kê trong báo cáo để rà.
- Văn bản / ảnh: hạn và tiến độ đi kèm chữ gốc, giữ khi chữ gốc có trong văn bản (bỏ nhận ngày bằng regex).
- Bỏ: tách ý bằng regex, nhận ngày trong câu bằng regex, quy tắc dòng tiêu đề ":", cột kết quả toàn số, cột hạn ghi chữ,
  ghép mẩu ngắn, tách hạn theo ý, gộp lại việc cùng người cùng hạn.
- Monitor: cột Chất lượng theo các chỉ số mới. Lệnh đọc thử trên server với model thật:
  `python -m open_webui.tracker.table_check <file…> [--json out.json]`. EXTRACT_VERSION 16.
- Thử bằng model giả trên 29 file bảng của production: không mất dòng, không trùng khóa, không còn "nan"; 14/14 kiểm tra
  (KPI_PM 6 chỉ tiêu, loại người / hạn / % không có trong dòng, ngày gốc thắng ngày model đổi sai, dòng không trả lời
  được giữ, ô gộp được điền, văn bản giữ hạn có chữ gốc). Chất lượng đọc của model thật cần chạy table_check trên dev.
- Kênh có danh sách việc rất dài (BD2 Timeline 252 việc): lời nhắc tổng hợp tin nhắn đưa toàn bộ danh sách (~62.000 ký
  tự, ~20k token) cộng đợt tin (~8k) và câu trả lời (4k) vượt ngữ cảnh 32k, mọi tin mới của kênh sẽ lỗi. Nay danh sách
  trong lời nhắc giới hạn TRACKER_STATE_PROMPT_CHARS (24.000 ký tự): ưu tiên việc mà tin mới nói tới (trùng chữ ở tên,
  người, chi tiết), rồi việc từ tin nhắn và việc mới cập nhật; ghi rõ "còn N việc khác vẫn giữ nguyên". Với 252 việc và
  một tin về OPP NAC: còn ~8k token, đủ 28 việc NAC. Bảng tiến độ và dữ liệu không đổi.
- Đánh giá bản đọc bảng mới trên dev (17/11 22:5x, model thật): đủ dòng, đúng người / hạn / trạng thái, nhưng hướng dẫn
  cho model còn sai ở 4 chỗ: (1) 49/50 việc của file kế hoạch có "vướng mắc" vì hướng dẫn ghi vướng mắc gồm "phụ thuộc",
  model đưa cột "Phụ thuộc: CV-01" vào; (2) chi tiết đầy ô phân loại (Mã, Giai đoạn, Hạng mục, Mức ưu tiên, Còn lại N
  ngày, Segment, Nhóm phản ánh…); (3) người góp ý (Alpha-X "Nhân viên Sales") thành người phụ trách, phản hồi khen thành
  việc, đề xuất trùng việc của dòng; (4) "BA / FE Lead" không tách, BA / BE Lead / QA Lead không nhận là chức vụ.
  Sửa hướng dẫn: vướng mắc chỉ là khó khăn đang cản trở (việc làm trước ghi vào chi tiết "Làm sau CV-01"); chi tiết tối
  đa 5 ý về phạm vi / bước / đầu ra / tần suất, không ghi ô phân loại, model liệt kê các cột đó ("cot_phan_loai") và code
  không chép ngược chúng vào chi tiết; người phụ trách là người được giao, không phải người góp ý / yêu cầu / duyệt; ô
  nhiều tên được tách; chức vụ là "nhom"; phản hồi chỉ khen là không phải việc. Code bỏ đề xuất trùng tên việc của dòng.
  Monitor ghi các cột phân loại và số đề xuất trùng bị bỏ. EXTRACT_VERSION 17.
- Kênh "đang chạy" rất lâu trên dev (test-kpi, tiến-độ-dự-án-x) khi tổng hợp lại tất cả: mỗi lượt giữ kênh 15 phút
  (TRACKER_LEASE_SECONDS) không gia hạn; đọc file lớn theo cách mới (mỗi lượt gọi trả về dài hơn) có thể quá 15 phút,
  khi đó lượt khác nhận lại kênh và làm lại từ đầu, kết quả lượt đầu bị bỏ; chỉ 2 kênh chạy cùng lúc nên các kênh khác
  chờ. Nay lượt đang chạy tự gia hạn mỗi 60 giây (dừng gia hạn khi xong hoặc khi không còn giữ kênh). Monitor tab Tracker
  hiện cả lượt gọi model để đọc file (cột "Việc": tổng hợp tin nhắn / đọc file), trước đây không thấy nên trông như treo.
- Khởi động lại server giữa lúc một kênh đang tổng hợp: kênh bị giữ "đang chạy" tới hết hạn 15 phút mà không ai làm
  (file chỉ vài chục KB). Vì lượt đang chạy đã tự gia hạn mỗi phút, hạn giữ kênh mặc định giảm còn 3 phút
  (TRACKER_LEASE_SECONDS=180): sau khi khởi động lại, kênh được nhận lại trong vòng 3 phút.
- Chấm bản v17 trên dev (model thật): KPI_PM đủ 6 chỉ tiêu, bỏ đúng 7 dòng tiêu đề / tổng / chữ ký, mục tiêu L2 và tần
  suất vào chi tiết, hạn "Kết thúc năm tài chính" đọc thành 2027-03-31 từ đầu trang; CX Feedback tách việc theo SOC / PM,
  chi tiết gọn, không còn ô phân loại; Project Plan 122 dòng → 115 việc, bỏ đúng 7 dòng giai đoạn, 0 vướng mắc giả, đúng
  hạn / trạng thái. Còn lỗi: Alpha-X người góp ý vẫn là người phụ trách (bản mô tả cột cũ của model gắn nhầm cột "Vai trò
  (Ẩn danh)" là cột phụ trách và gợi ý này được đưa vào mọi dòng); KPI_PM model tự thêm "Bước 1/2", "Phạm vi" và đặt tên
  theo phần việc của PM. Sửa: bỏ gợi ý vai trò cột khỏi lời nhắc đọc dòng (model đọc theo tên cột thật); hướng dẫn tên là
  chính việc / chỉ tiêu của dòng, chi tiết chỉ lấy từ chữ của dòng, không tự thêm bước / nhãn; chi tiết phải có ≥50% chữ
  trong dòng (trước 20%). Lưu ý: kiểm tra chữ không bắt được bước "suy ra" dùng lại chữ của dòng, phần này dựa vào hướng
  dẫn. Lượt đọc file trên dev trung bình 47 giây / lượt (p95 96 giây). EXTRACT_VERSION 18.
- Chấm bằng model thật (Knight dev, Qwen 27B) trên các file production, bằng bộ chấm tự động (mất dòng, vướng mắc, ô phân
  loại trong chi tiết, nhãn tự đặt, chi tiết ít chữ của dòng, người lấy từ cột nào, hạn khác ô ngày). Sửa theo kết quả:
  - Bảng không có dòng tiêu đề (Lịch KS BN): model vẫn chọn dòng dữ liệu đầu làm tiêu đề; code báo "dòng tiêu đề trông
    như dữ liệu" (ô là số hoặc giá trị lặp lại trong cột) để model sửa bản mô tả (dong_tieu_de = 0).
  - Model xử lý không nhất quán giữa các lô (6 dòng đầu bị bỏ, 8 dòng giống hệt sau lại là việc; NCS Project Status bỏ
    dòng Issue/Risk cần CEO): dòng bị bỏ có cùng các cột với ≥2 dòng là việc được hỏi lại, kèm một dòng mẫu.
  - Chi tiết bắt đầu bằng tên cột mà chính model khai là cột phân loại bị bỏ.
  - Excel đảo ngày / tháng khi nhập (NganTT: "2/11" thành 11/02, hạn lệch 9 tháng): model được báo hiện tượng này; code
    nhận ngày model sửa chỉ khi đúng là ngày đó đảo ngày / tháng.
  EXTRACT_VERSION 20.
- Lời nhắc mô tả bảng không còn ghi "đoán tiêu đề ở dòng N" (đoán bằng quy tắc của code; với bảng không tiêu đề nó chọn
  dòng dữ liệu đầu và model làm theo): model nhận 15 dòng đầu nguyên trạng và tự quyết định. EXTRACT_VERSION 21.
- Proxy RunPod trả 405 / 504 chập chờn (7/168 lượt khi chấm), mỗi lần làm cả lượt tổng hợp kênh hoặc đọc file thất bại:
  nay hỏi lại cùng endpoint sau 3 rồi 10 giây với các mã 0 / 405 / 429 / 5xx trước khi báo lỗi.
- Rà quy tắc cứng còn lại trong xử lý file, chuyển sang model quyết định:
  - Ghép việc từ file vào danh sách kênh: không còn tự gộp khi tên giống ≥72%; mọi cặp tên chỉ giống nhau đều do model
    xác nhận (theo lô 40 cặp, không bỏ cặp nào); chỉ tên giống hệt mới gộp ngay. Dòng khác của cùng bảng không bao giờ
    được ghép theo tên (9 OPP cùng tên bước; trước đây có thể gộp lẫn).
  - Bỏ trùng trong một văn bản: không còn tự bỏ khi tên giống ≥90% ("Lắp switch tầng 1 / tầng 2"); tên giống hệt mới
    gộp, tên gần giống do model xác nhận.
  - Ảnh: bỏ ngưỡng 40 ký tự (ghi chú ngắn trong ảnh bị coi là không có chữ); chỉ khi model trả "(không có chữ)".
  - Đọc bảng tự do không có bản mô tả: không đoán dòng tiêu đề, đọc mọi dòng.
  - Công cụ tra bảng của Knight (query_table): bỏ quy tắc "cột đầu trống và chưa điền nửa số ô thì không phải dòng" (làm
    Knight đếm thiếu dòng, vd phiếu KPI bắt đầu từ cột B); dòng có chữ ở bất kỳ ô nào là dòng.
  Giữ lại (kiểm tra, không đoán nghĩa): người / ngày / % phải có trong dòng hoặc văn bản, chi tiết lấy từ chữ của dòng,
  ô bị bỏ sót được chép lại, đọc định dạng ngày / số chuẩn, bỏ dòng chỉ toàn số / TRUE-FALSE. EXTRACT_VERSION 22.

## Đọc bảng tính: gửi file nguyên trạng cho model (18/11)

Bỏ bước "mô tả bảng" (chọn sheet, dòng tiêu đề, vai trò cột) và mọi xử lý theo nó. Cách mới:
- Code chỉ chép file: mỗi dòng "dòng N" với các ô theo chữ cái cột, ô gộp đã điền; dòng chỉ có số / TRUE-FALSE (dòng
  công thức trống của mẫu) không gửi từng dòng, model được báo số các dòng đó.
- File nhỏ (≤ 20.000 ký tự): cả file, mọi sheet, một lượt. File lớn: model xem các dòng đầu của mỗi sheet và cho biết
  sheet nào cần đọc, dòng dữ liệu bắt đầu ở đâu; mỗi phần ~40 dòng, mang kèm các dòng đầu của sheet nguyên trạng.
- Model tự xác định: sheet nào là danh sách việc, dòng tiêu đề (nhiều tầng / không có), thông tin đầu trang, dòng nào
  là việc, mã của dòng, và mọi trường. Mỗi phần có một lượt rà lại: bỏ chi tiết không có trong dòng, thêm thông tin
  quan trọng còn thiếu, bỏ đề xuất không có yêu cầu thật, đọc lại dòng bị coi nhầm là không phải việc (thay ngưỡng
  chép ô bị bỏ sót và kiểm tra cùng dạng dòng trước đây).
- Code chỉ: so người / chữ ghi hạn / chữ ghi tiến độ / ngày hoàn thành với chữ của dòng; hỏi lại các dòng model
  không nhắc tới (10 dòng một lượt); khóa việc = mã model đọc (nếu có trong dòng và không trùng) hoặc số dòng.
- Monitor: cột Chất lượng theo chỉ số mới (lượt gọi, đọc lại, rà lại, dòng chưa đọc được); Truy vết ghi dòng / mã /
  sheet. Bản mô tả bảng đã lưu và màn sửa trên monitor không còn tác dụng. EXTRACT_VERSION 23.
- Thử bằng model thật: KPI_PM 2 lượt gọi, đủ 6 chỉ tiêu, tự hiểu tiêu đề hai tầng ("Mục tiêu (L2): 90%, Tối thiểu (L1):
  85%"), không còn bước tự nghĩ; Lịch KS BN (không có tiêu đề) 15/15 buổi; NganTT tự bỏ sheet tra cứu và sheet tạm.
- Kết quả trên dev (v23, model thật) CX Feedback: đúng người / hạn / trạng thái / vướng mắc, chi tiết bám file, không
  còn ô phân loại; nhưng mất hết đề xuất từ yêu cầu khách (đọc cả file trong một câu trả lời dài, model bỏ phần này) và
  không tách việc theo người. Sửa: lượt rà lại (theo phần, đã có danh sách việc) tìm thêm yêu cầu chưa được đáp ứng
  ("de_xuat_moi") và việc gộp phần của nhiều người / nhiều hạn ("tach", đọc lại các dòng đó); chi tiết viết súc tích.
  Thử lại bằng model thật: có lại 3 đề xuất (dashboard ticket management cho CEO, tư vấn nguồn log, thông tin độc quyền);
  tách theo người vẫn chưa ăn (model đọc lại vẫn giữ một việc). Kế hoạch cá nhân lấy người từ tên file. EXTRACT_VERSION 24.
- Dev quá tải (RunPod 520, CPU 100%) khi tổng hợp lại tất cả kênh bằng bản đọc mới: mỗi phần tới 40 dòng / cả file
  một lượt làm mỗi câu trả lời dài 10-12k token, giữ kết nối 5-7 phút, nhiều lượt song song; file 50 việc còn dễ bị cắt
  ở giới hạn token rồi phải đọc lại. Nay mỗi phần ≤ 20 dòng / 12.000 ký tự, đọc cả file một lượt chỉ khi ≤ 20 dòng
  (câu trả lời ~4-6k token, 1-2 phút). EXTRACT_VERSION 26. Khuyến nghị khi tổng hợp lại nhiều kênh: Tracker
  max_concurrency = 1, files.summary.concurrency = 2.
- Dev (v26, model thật): av CX Feedback 9 việc + 3 đề xuất (đã có lại dashboard cho CEO), đúng người / hạn. test02 (kế
  hoạch 50 việc) ra 100 việc: model đọc cả sheet "Danh sach cong viec" lẫn "Tien do 9 tuan" (biểu đồ tiến độ của cùng 50
  việc) vì hướng dẫn "các sheet có thể là góc nhìn của cùng dữ liệu, chỉ chọn sheet gốc" bị bỏ khi bỏ bước mô tả bảng.
  Sửa: thêm lại hướng dẫn đó ở bước xem tổng quan và khi đọc cả file; thêm lớp an toàn: việc ở sheet khác có tên gần
  giống việc ở sheet có nhiều việc nhất được hỏi model có phải cùng một việc, nếu đúng thì bỏ bản ở sheet phụ.
  EXTRACT_VERSION 27.
- Dev (v27): test02 đúng 50 việc, model bỏ đúng các sheet Tong quan / Tien do 9 tuan (Gantt) / Huong dan; chi tiết gọn,
  0 vướng mắc giả, tách "BA / FE Lead"; nhưng chỉ DevOps được nhận là team, "PM dự án", "BA", "BE Lead", "QA Lead"… bị coi
  là người nên 44/50 việc "chưa liên kết". Sửa: sau khi đọc file, hỏi model một lần với mọi tên người phụ trách của file
  "tên nào là team / phòng ban / chức vụ", áp dụng cho mọi dòng. av: 9 việc + 1 đề xuất (lần trước 3, số đề xuất còn
  dao động giữa các lần đọc). Không còn lỗi 520 / 502 sau 01:47. EXTRACT_VERSION 28.
- Dev (v27) test-kpi: đủ 6 chỉ tiêu (công thức, mục tiêu L1/L2, tần suất), bỏ đúng 15 dòng (đầu trang, tiêu đề 2 tầng,
  nhóm A / B / III, tổng, chữ ký) nhưng thừa 2 việc từ dòng tiêu đề nhóm "I", "II" (file chia 2 phần, model đọc 2 phần
  không nhất quán). Sửa: lượt rà lại được bỏ các việc lập từ dòng không giao việc (tiêu đề nhóm, tổng, chữ ký), xét
  nhất quán với các dòng cùng loại đã bỏ. EXTRACT_VERSION 29.

## Sửa theo rà soát production (18/11, bản v27)

- Bỏ sót sheet có việc thật (KHCV_Tháng 11: 7/8 sheet kế hoạch của từng người bị bỏ vì "trùng cấu trúc"; NganTT bỏ sheet
  kế hoạch tháng 11): hướng dẫn "chỉ đọc MỘT sheet gốc" đổi thành "chỉ bỏ sheet lặp lại ĐÚNG CÁC VIỆC của sheet khác;
  sheet cùng mẫu nhưng chứa việc khác phải đọc; không chắc thì đọc" (việc trùng giữa sheet vẫn do model xác nhận sau).
- Người phụ trách ghi ở tên sheet bị loại: tên sheet được đối chiếu như tên file.
- Thông tin đầu trang không được đối chiếu ở phần đầu (Lịch đào tạo: 16 hạn 20/11/2026 bị loại; KPI_PM: 7 người): việc
  được đối chiếu với các dòng của sheet phía trên dòng đầu tiên model coi là việc (đầu trang, tiêu đề như model thấy).
- Chức vụ (PM1-6, AM1-3, BE Lead) và đơn vị ngoài công ty (Hải quan, các Sở, chủ đầu tư) bị tính "chưa liên kết": sau
  khi đọc MỌI loại file, hỏi model một lần tên nào là team / chức vụ, tên nào là bên ngoài (TRACKER_COMPANY_NAME cho biết
  tổ chức của người dùng); việc mang doi_nhom / ben_ngoai, ghép vào kênh giữ cả hai.
- Dòng vẫn chưa đọc được sau lượt hỏi lại theo nhóm được hỏi lại từng dòng (tối đa 20).
- Tên người phụ trách bỏ danh xưng và mô tả kèm ("anh Việt system onsite" → "Việt"), bảng và văn bản.
- Lượt rà lại xử lý nhất quán các dòng cùng loại (là việc / không phải việc). EXTRACT_VERSION 30.
