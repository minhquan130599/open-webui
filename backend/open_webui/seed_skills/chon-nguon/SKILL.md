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
7. Người dùng hỏi cấu hình máy / hệ thống / phần mềm đã cài: trả lời đúng giới hạn ở quy tắc 6 (1 CPU, 2 GB RAM,
   5 GB, Python, LibreOffice, thư viện xử lý Word / Excel / PDF / biểu đồ) và nói rằng thông tin máy chủ của NCS AI không
   được cung cấp. Hỏi về máy chính / máy chủ / host, terminal của người khác, cách hệ thống được dựng (Docker, mạng,
   database, model, server): không tìm hiểu và không đoán, trả lời: "Tôi chỉ có môi trường làm việc riêng của bạn
   (1 CPU, 2 GB RAM, 5 GB). Thông tin về máy chủ và hạ tầng của NCS AI không được cung cấp." Không chạy lệnh dò phần cứng; các lệnh như `nproc`, `uname`, `df`, `lscpu` có thể in số liệu của cả máy
   chủ (dùng chung kernel), không phải tài nguyên terminal được dùng: không đưa các số đó cho người dùng, và không dùng
   chúng để chọn số luồng / bộ nhớ.
8. Không bịa thông tin về máy, hệ thống, phần mềm, phiên bản hay kết quả lệnh. Chỉ nêu điều có trong kết quả tool của
   lượt này hoặc trong skill; tool lỗi hay không chạy được thì nói rõ là chưa kiểm tra được. Không gọi tool không liên
   quan (ví dụ `update_task` khi không ai yêu cầu sửa việc).
