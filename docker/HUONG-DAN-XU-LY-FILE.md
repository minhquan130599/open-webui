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

**Người dùng hỏi ngay sau khi tải file lên:**
- Upload xong, file vẫn cần thêm vài giây đến vài phút để **trích xuất chữ**. Tin nhắn gửi ngay trong lúc đó được giữ lại tới khi trích xuất xong, tối đa 180 giây, với mọi loại file. Bước này luôn chạy, kể cả khi đặt thời gian chờ sửa OCR bằng 0.
- Sau đó hệ thống chờ tiếp tối đa `Chờ sửa OCR xong trước khi trả lời` (mặc định 120 giây; chỉ áp dụng cho PDF có trang OCR kém) để chữ được sửa xong. File được chờ được đưa lên đầu hàng đợi.
- Trong lúc chờ, người dùng thấy dòng trạng thái *"Đang sửa OCR 3/5 trang…"*. Trong channel, dòng này hiện ngay trong tin trả lời và được thay bằng câu trả lời khi xong.
- Trang nào sửa xong thì chữ của file được cập nhật ngay, không đợi xong cả file.
- Hết thời gian chờ mà file chưa xong: model được báo file còn đang xử lý và đọc ảnh trang cho các trang lỗi. Dưới câu trả lời có dòng *"ℹ️ … vẫn đang được sửa OCR khi trả lời"*.
- Yêu cầu tóm tắt trong lúc bước tóm tắt nền đang chạy: hệ thống chờ lấy kết quả đó, không tóm tắt lại lần nữa.

Khi trả lời về file, model trích dẫn trang dạng `[tr.12]`. Dưới câu trả lời, hệ thống liệt kê **con số và ngày tháng không tìm thấy trong phần tài liệu đã đọc**. Với PDF, câu trả lời có thêm link mở ảnh trang gốc.

## Điểm OCR của một trang (0–100)

Điểm là tỉ lệ từ đọc được trên trang, sau khi bỏ mã HTML của bảng (`<td style=...>`).

- **Từ đọc được:** âm tiết tiếng Việt hợp lệ, hoặc từ Latin không dấu có nguyên âm (tiếng Anh, tên riêng...). Văn bản Việt–Anh, phụ lục tiếng Anh và văn bản tiếng Anh vì vậy không bị chấm oan.
- **Từ lỗi:** chữ có dấu tiếng Việt nhưng sai vần ("Đièu"), sai dấu theo âm cuối ("bằt", vì âm cuối p/t/c/ch chỉ mang dấu sắc hoặc nặng), hoặc không có nguyên âm. Chữ viết tắt in hoa (HTTT, NĐ, CP) không bị tính.
- **Mất dấu:** trang mà phần lớn là âm tiết tiếng Việt nhưng gần như không có dấu ("trach nhiem") bị chấm tối đa 70.
- **Chữ Trung/Nhật/Hàn/Thái...:** nếu chiếm từ 2% chữ cái của cả tài liệu (văn bản tiếng Trung, song ngữ) thì là nội dung thật. Nếu chỉ lác đác trong tài liệu chữ Latin thì coi là OCR bịa, và trang bị chấm tối đa 60. Chữ Hy Lạp/Kirin chỉ bị tính khi tạo thành từ từ 3 chữ trở lên, nên ký hiệu α, β, Ω, µ vẫn hợp lệ.
- **Trừ điểm** khi có ký tự rác (`�`, ký hiệu lạ) hoặc nhiều từ dính liền nhau. Trang dưới 40 ký tự được 0 điểm.

Bản model vision sửa được chấm theo đúng ngôn ngữ của cả tài liệu. Prompt sửa chỉ dặn "không xuất chữ Trung" khi tài liệu là chữ Latin.

**Giới hạn:** sai dấu thanh mà vẫn ra một chữ có nghĩa ("của" thành "cửa"), hoặc sai số ("2026" thành "2028"), thì bộ chấm không phát hiện được. Bước kiểm tra số liệu trong câu trả lời bù một phần cho chỗ này.

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
| Chờ sửa OCR xong trước khi trả lời (giây) | 120 | 0 = trả lời ngay bằng chữ OCR gốc |
| Chờ bản tóm tắt nền khi được hỏi (giây) | 300 | |
| Gửi kèm chữ OCR khi sửa bằng vision | tắt | Bật dễ khiến model chép lỗi OCR |
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
