# Xử lý file sau khi tải lên: chấm OCR, sửa bằng vision, lập chỉ mục, tóm tắt sẵn

Tài liệu cho người vận hành NCS AI. Mục đích: hỏi đáp trên file lớn và PDF scan vừa nhanh vừa đúng, không bịa số liệu.

## Luồng xử lý

```
Tải file lên ─→ trích xuất (như cũ, người dùng hỏi được ngay)
  PDF có sẵn lớp chữ: trang đạt điểm đọc thẳng, chỉ trang lỗi gửi OCR   (files.ocr.text_layer_first)
             │
             └─→ hàng đợi nền (bảng file_job), chạy trong container open-webui:
                 1. Chấm điểm OCR từng trang (bảng file_page giữ chữ gốc)
                 2. Trang dưới ngưỡng → model vision đọc lại từ ảnh trang
                 3. Ghép lại văn bản từ các trang (chữ đã sửa thay chữ lỗi)
                 4. Lập chỉ mục theo Chương/Mục/Điều, mỗi đoạn kèm "› Điều 12 | tr.27"
                 5. File dài: tóm tắt sẵn, lưu vào bộ nhớ đệm của file
```

Khi trả lời về file, model trích dẫn trang dạng `[tr.12]`. Dưới câu trả lời, hệ thống liệt kê **con số và ngày tháng không tìm thấy trong phần tài liệu đã đọc**. Với PDF, câu trả lời có thêm link mở ảnh trang gốc.

## Điểm OCR của một trang (0–100)

- **Tài liệu tiếng Việt:** điểm là tỉ lệ từ là âm tiết tiếng Việt hợp lệ. Ví dụ "Đièu" sai vần, "bằt" sai dấu (âm tiết kết thúc bằng p/t/c/ch chỉ mang dấu sắc hoặc nặng), chữ Trung/Hy Lạp lẫn vào: đều là từ không hợp lệ. Chữ viết tắt in hoa (HTTT, NĐ, CP) không bị tính.
- **Tài liệu khác:** điểm là tỉ lệ từ có nguyên âm.
- **Trừ điểm** khi có ký tự rác (`�`, ký hiệu lạ) hoặc nhiều từ dính liền nhau.
- **Trang dưới 40 ký tự** được 0 điểm (trang scan không đọc ra chữ).

**Giới hạn:** sai dấu thanh mà vẫn ra một chữ có nghĩa ("của" thành "cửa") thì bộ chấm không phát hiện được. Muốn kiểm tra kỹ hơn, tăng ngưỡng để nhiều trang được model vision đọc lại.

## Theo dõi trên trang monitor

Tab **Xử lý file** có:
- **Danh sách file:** số trang, số trang OCR kém, số trang đã sửa, số trang còn "chưa chắc", điểm trung bình (gốc → đang dùng), thời gian xử lý.
- **Bấm một file** để xem từng trang: đọc bằng cách nào (lớp chữ PDF / OCR / model vision), điểm gốc, điểm đang dùng, lỗi phát hiện, kết quả sửa (model, thời gian, điểm trước → sau; "không dùng" nghĩa là bản sửa không tốt hơn bản gốc).
- **Bấm "Xem"** để đặt **ảnh trang gốc** cạnh **chữ OCR gốc**, **chữ đang dùng**, hoặc **so sánh gốc / sửa**. Cần khóa truy xuất, hoặc phiên Chỉnh sửa cấu hình.
- **Chọn trang rồi bấm "Sửa lại các trang đã chọn"**: model vision đọc lại các trang đó, kể cả trang được chấm "Tốt". Dùng khi thấy bộ chấm điểm bỏ sót lỗi.
- **"Chạy lại toàn bộ"** chạy lại cả 5 bước. **"Xử lý file cũ (30 ngày)"** đưa các file tải lên trước khi có tính năng này vào hàng đợi.

Hai thao tác chạy lại cần phiên chỉnh sửa (khóa admin và mã Google Authenticator). Các lần gọi model của pipeline được ghi trong bảng `perf_event` với kind `file_llm`.

**Cách hiệu chỉnh ngưỡng:**
1. Mở vài file có trang được chấm khoảng 70–85 điểm.
2. So ảnh trang với chữ OCR.
3. Nếu trang trên ngưỡng mà chữ vẫn sai nhiều, tăng `Điểm OCR tối thiểu`. Nếu trang dưới ngưỡng mà chữ đúng, giảm ngưỡng.

## Tham số

Chỉnh ở **Cài đặt → Tài liệu → Xử lý file** hoặc trên monitor (**Thành phần & tài nguyên → Chỉnh sửa cấu hình → Xử lý file**). Tham số có hiệu lực trong vài giây, không cần khởi động lại.

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| Xử lý nâng cao sau khi tải file lên | bật | Tắt thì upload chạy như trước |
| Số file xử lý nền cùng lúc | 2 | |
| PDF có sẵn chữ: đọc trực tiếp, chỉ OCR trang lỗi | bật | Chỉ có tác dụng khi engine trích xuất là OCR |
| Điểm OCR tối thiểu (%) | 80 | Ngưỡng trang "OCR kém" |
| Sửa trang OCR kém bằng model vision | bật | |
| Model vision sửa OCR | trống = model của tracker | Model phải nhận được ảnh |
| Số trang sửa song song / tối đa mỗi file | 4 / 300 | |
| Lập chỉ mục theo Chương/Mục/Điều; độ dài tối đa một đoạn | bật; 2500 | |
| Tóm tắt sẵn file lớn; khi file dài từ (ký tự); model tóm tắt nền | bật; 30000; trống = tracker | |
| Model tóm tắt khi chat | trống = model đang trả lời | ID model trong NCS AI |
| Số ký tự mỗi phần / số phần song song / độ dài bản tóm tắt | 40000 / 6 / 16000 | |
| Tắt thinking khi tóm tắt / sửa OCR | bật | `enable_thinking=false` (Qwen3 trên vLLM) |
| Đưa nguyên văn file đính kèm tới (ký tự) | 45000 | |
| Giới hạn đọc file trong một câu trả lời | 60000 | 0 = không giới hạn |
| Ký tự tối đa mỗi lần tìm trong file | 12000 | |
| Kiểm tra số liệu trong câu trả lời | bật | |
| Cạnh dài ảnh trang PDF gửi model (px) | 1600 | |

Các tham số này **chỉ chỉnh trong giao diện**, không có biến môi trường tương ứng. Giá trị trên trang Cài đặt luôn đúng là giá trị đang chạy. Chưa ai lưu thì dùng mặc định ở bảng trên, khai báo trong `backend/open_webui/file_pipeline/settings.py`.

## Tài nguyên

- **GPU:** sửa OCR và tóm tắt sẵn dùng chung GPU với chat. Một PDF scan 40 trang, trong đó 30 trang lỗi, mất khoảng 30 lần gọi model vision, chạy 4 trang song song. Nếu chat bị chậm khi nhiều người tải file cùng lúc, giảm số trang sửa song song, hoặc đặt **Model vision sửa OCR** sang một model vision riêng, nhỏ hơn.
- **Xóa file** thì xóa luôn chữ từng trang và bản ghi xử lý của file đó.
