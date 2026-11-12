---
name: project-reports
description: Tạo báo cáo tiến độ của kênh dự án theo ngày, tuần, tháng hoặc tổng hợp, trình bày trong chat hoặc xuất file PDF, Word, Excel.
---

# Báo cáo dự án

Tạo báo cáo cho một kênh dự án (một kênh = một dự án) từ dữ liệu tiến độ đã được tổng hợp tự động.
Không tự sửa skill này và không tạo công cụ mới khi làm báo cáo.

## When to Use

"báo cáo ngày", "báo cáo hôm nay", "báo cáo tuần", "báo cáo tháng", "báo cáo tổng hợp",
"tình hình dự án", "tiến độ kênh", "xuất báo cáo PDF/Word/Excel".

## Prerequisites

- Dữ liệu: công cụ `get_project_status` và `get_project_timeline`.
- Xuất file: cần Terminal (công cụ `run_command`, `write_file`) và `attach_terminal_file`.
  Không có thì chỉ trình bày báo cáo trong chat.

## Chọn loại báo cáo

| Loại | get_project_timeline | Nội dung chính |
|---|---|---|
| Ngày | days=1 | Việc đã làm hôm nay, việc mới phát sinh, vướng mắc cần xử lý ngay |
| Tuần (mặc định) | days=7 | Tổng quan tuần, bảng hạng mục, vướng mắc, việc tuần tới |
| Tháng | days=30 | Kết quả theo hạng mục, tiến độ so với hạn, rủi ro, quyết định |
| Tổng hợp | không gọi, chỉ dùng get_project_status | Toàn cảnh hiện tại: mọi hạng mục, phụ trách, tiến độ, hạn, rủi ro |

Nếu người dùng nêu khoảng thời gian cụ thể ("3 ngày qua", "từ 01/11"), tính số ngày tương ứng.

## Procedure

1. Gọi `get_project_status` cho kênh được yêu cầu.
2. Gọi `get_project_timeline` với `days` theo bảng trên (trừ loại Tổng hợp).
   Hai công cụ này đã bao quát mọi tin nhắn: KHÔNG mở từng tin nhắn; chỉ dùng
   `view_channel_message` khi cần kiểm chứng một chi tiết cụ thể.
3. Người dùng KHÔNG yêu cầu file: trình bày báo cáo trong chat bằng Markdown. Xong.
4. Cần file: chỉ chạy lệnh bằng `run_command` và ghi file bằng `write_file`.
   Không có `run_command` thì nói "Cần chọn Terminal ở ô nhập tin nhắn để xuất file" và dừng.
   - Chỉ PDF: dùng `write_file` tạo `bao_cao.html` (CSS `font-family: "DejaVu Sans"` để hiển thị tiếng Việt),
     rồi chạy qua `run_command`:
     `python3 -c "from weasyprint import HTML; HTML('bao_cao.html').write_pdf('bao_cao.pdf')"`
   - Word: viết script python-docx bằng `write_file`, chạy bằng `run_command`.
     Cần thêm PDF: `soffice --headless --convert-to pdf --outdir . bao_cao.docx`
   - Excel: dùng openpyxl (một sheet cho bảng hạng mục, cột: Hạng mục, Phụ trách, Tiến độ %, Hạn, Trạng thái, Vướng mắc).
   - Biểu đồ tiến độ: matplotlib, lưu PNG, chèn vào file nếu người dùng muốn.
   - Tên file: `bao_cao_<loai>_<ten-kenh>_<YYYY-MM-DD>.<duoi>` (không dấu, không khoảng trắng).
5. Gọi `attach_terminal_file` cho từng file người dùng yêu cầu.

## Quick Reference

- PDF trực tiếp: HTML + weasyprint.
- DOCX sang PDF: `soffice --headless --convert-to pdf --outdir . <file.docx>`
- Kiểm tra file đã tạo: `run_command` với `ls -la`.

## Pitfalls

- Chỉ nói "đã tạo/đã gửi file" khi `attach_terminal_file` trả về `"status": "success"`.
  Lỗi thì báo đúng lỗi công cụ trả về, không bịa tên file hay đường dẫn.
- Không dùng hay tự đặt tên công cụ khác (`terminal_exec`, `bash`, `terminal`, `run_shell_command`... không tồn tại).
- Chỉ gửi đúng định dạng người dùng yêu cầu (chỉ PDF thì không gửi Word).
- Dữ liệu tiến độ có thể chậm vài phút so với tin nhắn mới nhất; `get_project_status` đã kèm các tin chưa tổng hợp.

## Verification

Sau khi gửi, `attach_terminal_file` trả về `"status": "success"` cho mỗi file và file hiện dưới câu trả lời.
