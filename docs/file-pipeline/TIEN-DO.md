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
