# Đánh giá câu trả lời trên W — 26/11 (chiều)

Tài liệu: TL Vận hành_NGFW_ v1.3.261123.19.pdf. Hai chat:

- **Bản cũ** (`7ba2cd7a`, 18:18–18:28): build 17:43 — đã gỡ hướng dẫn trong skill, chưa có nguyên tắc ở system prompt,
  chưa bỏ kết quả tool cũ.
- **Bản mới** (`f9d02244`, từ 18:32): build 18:31 — nguyên tắc ở system prompt, bỏ kết quả tool cũ, ngân sách context
  theo request, skill `preload`, sửa chọn vùng / tóm tắt bị cắt / ngưỡng tóm tắt nền.

Chưa sửa gì; mỗi lỗi có đề xuất ở cuối.

## Từng câu

| Câu | Bản | Thời gian | Tool | Nội dung | Lỗi |
|---|---|---|---|---|---|
| Lọc IPsec | cũ | 174 s | `file_outline` + 15 × `view_file` (mỗi lần 1 trang) + `ask_user` (bị từ chối) | Đúng mục 6.13 | Đọc từng trang một; hỏi lại người dùng (L6) |
| Lọc IPsec | mới | 105 s | `file_outline`, 2 × `query_chat_files`, `summarize_chat_file(pages=138-141)` | Đúng mục 6.13, đủ 3 phần | Nhanh hơn, không ra terminal; vẫn tìm rồi mới đọc theo trang thay vì `summarize_chat_file(section)` |
| IKE phase 1/2 | cũ | 74 s | `file_outline` + 4 × `view_file` | Đúng | — |
| IKE phase 1/2 | mới | 131 s | **`write_note`** (không đọc lại tài liệu) | Nội dung lấy từ câu trước | Tự lưu ghi chú dù nguyên tắc đã cấm (L2); dòng trang gốc lặp (L5) |
| Ví dụ tunnel động | cũ | 33 s | 2 × `view_file` (tr.142, 144) | Dựa ví dụ tr.144 | Bịa chuỗi proposal `"crypto=aes256;enc=…"` (không phải cú pháp strongSwan) nhưng ghi "cú pháp strongSwan"; tự thêm `version 2`, `dpd-delay`, `esp-rekey-time` (L3) |
| Ví dụ tunnel động | mới | 53 s | 2 × `query_chat_files` | Dựa ví dụ tr.144 | Ghi "tất cả từ [tr.141–145]" nhưng `aes256-sha256-modp2048`, `dpd-delay 30`, `dpd-timeout 90`, `local-address`, `version 2` là tự thêm; `ike-rekey-time 4h`, `esp-rekey-time 1h` sai kiểu (tài liệu: số giây, tr.143) nên commit sẽ lỗi; `dpd-timeout` với IKEv2 tự mâu thuẫn (L3); dòng trang gốc lặp (L5) |
| Ví dụ OpenVPN server | cũ | 206 s | `summarize_chat_file(section="OpenVPN")` + 3 × `view_file` | Tốt: mọi dòng từ ví dụ tr.151/157/161, có commit + activate, export-ca nói đúng là sao lưu | Dòng trang gốc lặp (L5) |
| Client truy cập VPN | cũ | 79 s | `file_outline`, 5 × `view_file`, `grep_search` (lỗi — tool terminal, đường dẫn API), `query_chat_files` | Tốt: file `.ovpn`, không nhắc phát CA | Gọi nhầm tool terminal `grep_search` (L6); dòng trang gốc lặp (L5) |
| Gửi gì cho nhân viên | cũ | 63 s | **`write_file`** ("placeholder"), `query_chat_files`, **`delete_calendar_event("dummy")`** (lỗi) | Tốt: chỉ `.ovpn`, export-ca là sao lưu | Gọi tool vô nghĩa, có tool xóa dữ liệu (L6); dòng trang gốc lặp (L5) |
| Client truy cập VPN | mới | — | đang chạy khi kiểm tra | — | — |

Kết quả tốt của bản mới: bỏ kết quả tool cũ chạy đúng (mỗi câu hỏi tiếp bỏ 49–79 nghìn ký tự, log
`Context: … earlier tool results left out`), không còn thu gọn bằng model, không có câu trả lời rỗng, không ra terminal.

## Lỗi và đề xuất sửa

### L1. Skill chon-nguon bị đưa vào hai lần ở câu đầu (do sửa `preload`)

Câu đầu của mỗi chat, hệ thống **đã tự chèn sẵn** kết quả `view_skill` (mục `hidden: true`, hàm `preload_skills`, không
tốn lần gọi model). Bản mới thêm `preload: true` nên nội dung skill nằm cả trong system prompt lẫn kết quả chèn sẵn
(~3,7 nghìn ký tự thừa). Nhận xét "mỗi câu tốn một vòng `view_skill`" chỉ đúng với các câu hỏi tiếp (bản cũ: model tự
gọi lại `view_skill`, mục `chatcmpl-…`).

Đề xuất: giữ `preload` (có ích cho câu hỏi tiếp, nhất là khi kết quả `view_skill` cũ bị bỏ khỏi ngữ cảnh), và cho
`preload_skills` bỏ qua skill đã preload để không chèn lần hai.

### L2. Vẫn tự làm việc không được yêu cầu (`write_note`) dù nguyên tắc đã ở system prompt

Bản mới, câu "Lọc … IKE/IPsec": model không đọc lại tài liệu mà gọi `write_note` lưu ghi chú vào Notes của người dùng.
Nguyên tắc chung không đủ giữ model 27B INT4 khi có sẵn 81 tool.

Đề xuất (không dùng luật nhận diện):
1. Tool có tác dụng ghi / xóa (ghi chú, lịch, file terminal, việc dự án) chuyển sang chế độ **hỏi xác nhận** trước khi
   chạy (hệ thống đã có cơ chế duyệt tool: trạng thái `requires_approval`) — model gọi nhầm thì người dùng thấy và từ
   chối, không có gì bị ghi.
2. Hoặc bỏ các tool đó khỏi bộ tool mặc định của Knight trong chat thường (cấu hình model), chỉ bật khi cần.

### L3. Ví dụ cấu hình: tự thêm giá trị và ghi sai nguồn

Cả hai bản: ví dụ tunnel động lấy khung tr.144 rồi tự thêm tham số / giá trị, và ghi là "từ tài liệu". Bản mới còn
tạo cấu hình **không commit được** (`ike-rekey-time 4h` trong khi tham số là số giây). Nguyên tắc "điều ngoài tài liệu
phải ghi rõ" chưa được làm theo.

Đề xuất:
1. Bản mới tìm bằng `query_chat_files` (đoạn rời) thay vì đọc nguyên mục; khi người dùng xin ví dụ, nguyên văn mục
   (`summarize_chat_file` với chủ đề) giúp model thấy cả bảng tham số (kiểu, đơn vị). Không ép bằng luật; kiểm tra
   lại sau khi sửa L4 (tìm kém) xem model có tự chọn đọc nguyên mục không.
2. Thử bật chế độ suy nghĩ (thinking) của Knight cho câu hỏi về tài liệu, chạy bộ `scripts/doc-eval` so sánh — model
   tự soát "dòng này có trong tài liệu không" tốt hơn khi được suy nghĩ trước.
3. Nếu vẫn sai: thêm một bước tự soát bằng model (đọc câu trả lời + nguyên văn, đánh dấu dòng không có trong tài liệu),
   tốn thêm một lần gọi Knight mỗi câu có khối lệnh — cần cân nhắc tải.

### L4. Tìm kiếm còn yếu (ảnh hưởng chọn mục)

Chưa bật BM25 / reranker trên W. Đề xuất bật hybrid search và reranker (bge-reranker-v2-m3) trong Cài đặt → Tài liệu.

### L5. Dòng "Xem trang gốc …" bị lặp hai lần

5/9 câu trả lời có hai dòng link trang gốc giống nhau (hai mục `message` trong output), từ câu hỏi thứ hai trở đi của
chat, ở cả bản cũ và mới — có từ trước các lần sửa hôm nay.

Đề xuất: tìm chỗ `append_file_verification` được gọi hai lần cho cùng câu trả lời (luồng có tool và luồng kết thúc) và
cho nó bỏ qua khi output đã có dòng này.

### L6. Gọi tool không liên quan (bản cũ)

`ask_user`, `grep_search` (tool terminal với đường dẫn API), `write_file` "placeholder", `delete_calendar_event("dummy")`.
Bản mới chưa thấy lại (mới 3 câu), cần theo dõi thêm. Hướng sửa chung với L2 (giảm / duyệt tool có tác dụng).

## Đã sửa (27/11)

- **L1**: skill có `preload: true` không còn được chèn thêm lần hai dưới dạng kết quả `view_skill`
  (`middleware.py`, `preloaded_skill_ids`).
- **L5**: nguyên nhân thật là model **tự chép** dòng "Xem trang gốc" từ câu trả lời trước trong lịch sử (trên W, mọi câu
  bị lặp đều có dòng này ngay trong câu trả lời của model), rồi hệ thống gắn thêm một dòng. Sửa: dòng do hệ thống gắn
  được đánh dấu (`source: file_verification`) và bỏ khỏi lịch sử gửi cho model (`verify.leave_out_added_notes`); chat cũ
  chưa có dấu thì nhận theo đúng câu chữ hệ thống tự viết. Người dùng vẫn thấy dòng link như cũ.
- **L2 / L6**: tool thay đổi dữ liệu người dùng (ghi / sửa ghi chú, tạo / sửa / xóa lịch, automation, skill, tool,
  `notify`) hỏi người dùng **Cho phép / Từ chối** trên trang chat trước khi chạy (`tools.CHANGES_USER_DATA`,
  `middleware.confirmed_by_user`). Model vẫn tự quyết gọi tool, không có luật đoán ý định; gọi nhầm thì người dùng từ
  chối và không có gì bị ghi. Trong chat ghi chú, Telegram, kênh và gọi qua API thì chạy như cũ (không có chỗ hỏi).
  Tool terminal (`write_file`…) không hỏi vì terminal là vùng làm việc riêng.
- **Chưa sửa bằng code**: L4 (bật hybrid search + reranker là cấu hình trên W, reranker tải ~2 GB) và L3 (cần chạy
  `scripts/doc-eval` trên Knight với thinking tắt / bật).

Kèm theo: trang monitor (Thành phần & tài nguyên → Chỉnh sửa cấu hình) có thêm khối **Kết nối terminal** để sửa URL,
khóa, bật / tắt terminal đã cấu hình, có nút Kiểm tra (tải spec OpenAPI); lưu xong NCS AI nạp lại tool terminal trong
tối đa 15 giây (`config_watch`, nhóm `terminals`).

## Thứ tự đề xuất

1. L1 (nhỏ, lỗi do sửa hôm nay).
2. L5 (lỗi hiển thị, nhỏ).
3. L2 / L6: bật duyệt hoặc tắt tool có tác dụng ghi / xóa cho Knight (cấu hình + kiểm tra cơ chế duyệt).
4. L4: bật BM25 + reranker.
5. L3: chạy `scripts/doc-eval` (có câu ví dụ cấu hình) với thinking tắt / bật để quyết định.
