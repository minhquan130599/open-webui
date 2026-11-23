---
name: project-reports
description: Báo cáo và phân tích công việc của kênh dự án (ngày, tuần, tháng, tổng hợp, theo người, đánh giá hiệu suất), trình bày trong chat hoặc xuất file PDF, Word, Excel.
---

# Báo cáo dự án

Báo cáo cho một kênh dự án (một kênh = một dự án) hoặc nhiều dự án, từ dữ liệu tiến độ đã được tổng hợp tự động.
Không tự sửa skill này và không tạo công cụ mới khi làm báo cáo.

## When to Use

"báo cáo ngày/tuần/tháng", "báo cáo tổng hợp", "tình hình dự án", "tiến độ kênh", "việc của Long",
"đánh giá hiệu suất từng thành viên", "ai đang quá hạn", "xuất báo cáo PDF/Word/Excel", "tiếp" (phần tiếp theo).

Câu hỏi về việc theo thời hạn ("việc hạn tháng 12", "việc đến hạn tuần sau") dùng skill này với `due_from`/`due_to`,
kể cả khi người dùng nhắc tới bảng công việc họ đã gửi: bảng công việc trong kênh được nhập từng dòng vào dữ liệu dự án.
Kết quả ghi file nào **chưa được tổng hợp**; câu hỏi về file đó, hoặc về nội dung chi tiết của file (đoạn văn, cột khác),
thì đọc file (`query_table` với bảng tính).

## Procedure

1. Gọi `build_project_report`:
   - `project`: tên hoặc ID kênh; để trống khi hỏi về mọi dự án.
   - `period`: `day`, `week` (mặc định), `month`, `quarter`, `all` hoặc số ngày ("3 ngày qua" → `3`).
   - `person`: khi chỉ hỏi về một người.
   - `due_from` / `due_to`: chỉ lấy việc có hạn trong khoảng (YYYY-MM-DD, DD/MM/YYYY hoặc MM/YYYY; "tháng 12" →
     `due_from="12/2026"`, `due_to="12/2026"`). Chỉ cần danh sách việc theo hạn thì `get_project_status` với hai
     tham số này nhanh hơn.
   - `include_closed=true`: khi đánh giá hiệu suất, xem lịch sử hoặc việc đã đóng/hủy.
   - `purpose="analysis"`: khi câu hỏi không cần liệt kê từng việc (thống kê, xếp hạng, đánh giá);
     mặc định `report` thì câu trả lời phải nhắc tới mọi hạng mục.
2. Viết câu trả lời theo đúng yêu cầu người dùng và theo HƯỚNG DẪN VIẾT công cụ trả về. Tự chọn bố cục;
   ưu tiên việc quá hạn, bị chặn, sắp đến hạn, rủi ro; việc bình thường được gộp gọn nhưng không bỏ tên.
3. Dữ liệu lớn được chia nhiều phần: viết phần hiện tại, cuối câu trả lời mời gõ "tiếp". Khi người dùng gõ "tiếp",
   gọi lại `build_project_report` với `report_id` và `part` kế tiếp.
4. Người dùng muốn file (PDF, Word, Excel) hoặc cả báo cáo dài một lần: viết phần nhận xét (Markdown),
   rồi gọi `export_project_report` **một lần** với `report_id`, `content` và `format` (`pdf`, `docx`, `xlsx`, `md`).
   File tự kèm bảng đầy đủ mọi hạng mục và thống kê theo người, và hiện ngay dưới câu trả lời khi công cụ báo
   `"status": "success"`. Sau đó chỉ viết tóm tắt ngắn rồi **kết thúc**: không mở, kiểm tra hay gửi lại file.
   Báo cáo dự án luôn xuất bằng `export_project_report`, kể cả khi có Terminal: không viết script Python, không
   tạo file trong Terminal và không dùng `attach_terminal_file` cho báo cáo.
5. Cần chi tiết hoặc trích nguồn một việc: `search_project_evidence` hoặc `view_channel_message`.

## Pitfalls

- Chỉ dùng số liệu có trong dữ liệu; đánh giá, nhận xét là phần tự phân tích và phải nêu căn cứ.
- Không bỏ sót hạng mục: hệ thống kiểm tra và tự liệt kê các hạng mục chưa được nhắc ở cuối câu trả lời.
- Chỉ nói "đã gửi file" khi `export_project_report` trả về `"status": "success"`; lỗi thì báo đúng lỗi, không
  tự thử lại bằng cách khác.
- File báo cáo lưu trong NCS AI, **không nằm trong Terminal**: `list_files`, `read_file` không thấy nó, và thấy file
  báo cáo cũ trong Terminal không có nghĩa là file vừa xuất bị lỗi. Không dùng hay tự đặt tên công cụ khác.
- Đã trả lời xong thì dừng: không gọi thêm công cụ để "kiểm tra lại", không viết lại câu trả lời lần hai.
- Dữ liệu tiến độ có thể chậm vài phút so với tin nhắn mới nhất.
