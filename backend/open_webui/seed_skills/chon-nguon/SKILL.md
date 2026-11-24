---
name: chon-nguon
description: Chọn đúng nguồn thông tin trước khi trả lời: nội dung file đính kèm, báo cáo tiến độ dự án (tracker), hay tra cứu bảng tính. Luôn được nạp sẵn ở mọi cuộc trò chuyện.
---

# Chọn nguồn thông tin

Trước khi gọi tool, xác định câu hỏi cần nguồn nào. Sai nguồn làm câu trả lời chậm (nhiều lượt) hoặc thiếu.

## Nguồn và khi nào dùng

| Câu hỏi | Nguồn | Tool |
|---|---|---|
| Nội dung một file: tóm tắt, điều khoản, đoạn cụ thể, làm theo quy tắc trong file | File | Chữ file có sẵn trong `<file_content>` thì đọc luôn; nếu không: `file_outline`, `view_file`, `query_chat_files`, `summarize_chat_file` |
| Lọc / đếm / tìm dòng trong bảng tính (xlsx, csv) | Bảng tính | `query_table` (không dùng terminal) |
| Tiến độ, việc của ai, việc quá hạn, việc đến hạn trong khoảng thời gian, báo cáo ngày/tuần/tháng | Báo cáo dự án | `get_project_status` hoặc `build_project_report` (có `due_from`/`due_to` để lọc theo hạn) |
| Tạo file mới, điền vào mẫu Word/Excel, xử lý dữ liệu phức tạp bằng code | Terminal | `open_in_terminal` rồi chạy lệnh; gửi file tạo ra bằng `attach_terminal_file` |
| Báo cáo tiến độ dự án dạng file (PDF/Word/Excel) | Báo cáo dự án | `export_project_report` (không dùng terminal) |

## Quy tắc

1. Câu hỏi vừa nhắc file vừa hỏi về việc/hạn (ví dụ "lọc task hạn tháng 12 trong bảng tôi gửi"): gọi báo cáo dự án trước. Nếu báo cáo ghi file đó **chưa được tổng hợp**, hoặc cần chi tiết báo cáo không có, thì đọc file (`query_table` với bảng tính).
2. Báo cáo dự án được tổng hợp từ tin nhắn và file của kênh; mỗi hạng mục có nguồn. Khi kết quả báo cáo ghi "file chưa được tổng hợp", câu hỏi liên quan file đó phải đọc file.
3. Không tìm file đính kèm trong terminal: file người dùng gửi nằm trong chat, không có trong thư mục terminal cho tới khi gọi `open_in_terminal`.
4. Kết quả tool có dòng "gợi ý" chỉ sang tool khác thì làm theo gợi ý đó thay vì thử lại cùng tool.
5. Trả lời từ tài liệu thì ghi trang `[tr.N]` hoặc dòng/sheet nguồn; không có trong nguồn thì nói rõ là không có.
6. Terminal là máy riêng của từng người dùng, có giới hạn: 1 CPU, 2 GB RAM, 5 GB dung lượng, không vào được mạng nội
   bộ hay máy chủ của NCS AI (database, Redis, kho file); Internet chỉ qua cổng 80 / 443 và được ghi lại. Không thử kết
   nối hay dò mật khẩu các máy đó. Dữ liệu lớn thì xử lý theo từng phần. Kết quả ghi "vượt giới hạn bộ nhớ" thì chia nhỏ
   dữ liệu, không chạy lại y hệt. File để lâu không dùng (7 ngày) sẽ bị xóa: file cần giữ phải gửi cho người dùng.
