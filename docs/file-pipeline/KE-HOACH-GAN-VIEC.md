# Kế hoạch điều chỉnh: gán việc đúng người, giảm việc chưa gán, quyền quản lý kênh

Trạng thái: **đã triển khai T1–T12** (xem TIEN-DO.md nhóm 9); T13 deploy phía server. Q4 (rà lại yêu cầu chưa giao) chưa làm. Dữ liệu dùng để lập kế hoạch: server thật (kênh bd3_csc_proj_26,
bd3_monthly_kpi_26, poc_cg1018526003_soc, poc_cg1016126079_si, test01) và máy dev 192.168.110.113 (kênh av,
bản mới nhất).

## 1. Kết quả kiểm tra kênh av (máy dev, bản mới)

36 việc từ `NCS_CX_Feedback_Data.csv` = **13 việc đã giao + 23 yêu cầu chưa giao**. Nghị định 331 và file quy tắc AI
đã được nhận là tài liệu tham khảo (0 việc); file mã KH: không có việc. Đúng.

### Vì sao nhiều việc "Chưa gán"

| Nguyên nhân | Số việc | Chi tiết |
|-------------|---------|----------|
| A. Mỗi ý phàn nàn thành 1 yêu cầu chưa giao | 23 | PH-000003 ra 8 yêu cầu, PH-000004 ra 5: model tách từng ý theo từng khách hàng thay vì đánh giá ý đó đã có hành động xử lý chưa |
| B. Báo thừa: yêu cầu đã được hành động xử lý | khoảng 6–8 | PH-000003: hành động "lấy mẫu cảnh báo test-CG10016 làm chuẩn, đủ thông tin truy vết để khách không phải hỏi thêm, ghi rõ lý do nếu thiếu" đã xử lý các yêu cầu thiếu whitelist/IP/domain/user, thiếu source IP, thiếu luồng user, thiếu ngữ cảnh tài sản. PH-000009#yc1 đã được "xây dựng mẫu báo cáo mới sát nhu cầu khách hàng" xử lý |
| C. Yêu cầu chưa giao theo thiết kế không có người phụ trách | 23 | Thiết kế hiện tại để trống người cho chủ kênh phân công, nên mọi yêu cầu đều hiện "Chưa gán" |
| D. Người phụ trách là tên team ("SOC", "TI", "PM", "CX") | 13 việc đã giao | Không phải tài khoản nên không liên kết được; hiện "chưa liên kết" ở trang Công việc |

### Các việc đã giao (13): đúng người, đúng hạn theo cột Owner / Hạn; còn 5 lỗi nhỏ

| Việc | Lỗi |
|------|-----|
| PH-000006#1 | Ý "cần xác minh vì sao test-CG10015 còn lọc tay, cập nhật vào cuối tuần **13/11/2026**" bị gộp vào chi tiết, hạn vẫn 30/11 (model đề xuất hạn nhưng code loại vì đặt sai việc: "2 hạn không có trong câu bị loại") |
| PH-000006#1 | Chi tiết trùng nhau ("Xác minh lỗi lọc tay…" và "Xác minh nguyên nhân… lọc tay") |
| PH-000002#1 | Chi tiết lặp lại đúng tên việc |
| PH-000004#1 | Vướng mắc là câu nhận định "SOC đánh giá không nên chứng minh tuning rule theo MITRE" (không phải khó khăn) |
| PH-000007#yc3 | Tên việc hỏng: "Test-CB10001:" (code rút gọn câu gốc bị cắt ở dấu ":") |

## 2. Danh sách lỗi tổng hợp

| # | Lỗi | Nơi gặp | Trạng thái bản mới |
|---|-----|---------|---------------------|
| L1 | "Toàn team / các nhân sự" nở thành toàn bộ thành viên, gồm admin, chủ kênh, người gửi | bd3_monthly_kpi | Còn |
| L2 | Chức vụ ("các AM") đoán từ danh sách thành viên | bd3_csc_proj | Còn |
| L3 | Tin đính chính danh sách người không được áp dụng | bd3_monthly_kpi 17/11 | Còn |
| L4 | Việc không có tin nguồn | bd3 (3 việc), poc soc ("Duy") | Còn (chờ `/tmp/nguon.txt` để biết mức độ) |
| L5 | Việc giao qua @Knight bị bỏ (lỗi mới của bản mới) | bd3, poc soc | Còn |
| L6 | Tên tài khoản kèm phòng ban → không liên kết | bd3 (Nguyễn Trà My) | Đã sửa |
| L7 | Người ngoài kênh hiện "chưa liên kết" | poc soc ("Duy") | Còn |
| L8 | Yêu cầu chưa giao: tách vụn và báo thừa (nguyên nhân A, B) | av | Còn |
| L9 | Người phụ trách là tên team không liên kết tài khoản (nguyên nhân D) | av | Còn |
| L10 | 5 lỗi nhỏ của việc đã giao (mục 1) | av | Còn |
| L11 | AI bị gán việc; văn bản luật thành việc; việc từ file sai người/hạn/%  | — | Đã sửa |

## 3. Quyết định đã chốt

| # | Câu hỏi | Chốt |
|---|---------|------|
| Q1 | Kế hoạch / mốc không nêu ai làm | **Để "chưa giao"** |
| Q3 | Nhắc việc chưa có người phụ trách mỗi sáng thứ Hai | **Có, chỉ gửi cho chủ kênh (và người quản lý kênh), không gửi admin**; gửi thông báo + email riêng, không đăng vào kênh |
| Q2 | "Không nhận việc chung" là gì, ai cấu hình | Giải thích ở mục 5; **cần bạn chốt** |
| Q4 | Bước rà lại yêu cầu chưa giao (tốn thêm lượt gọi model) | Đề xuất **bật** (mục 4.3); **cần bạn chốt** |

## 4. Thiết kế sửa

### 4.1. Gán việc từ tin nhắn chat (sửa L1–L5, L7)

Model trả thêm cho mỗi việc mới / mỗi cập nhật người phụ trách:

```json
"giao_cho": {"kieu": "ten | chuc_vu | ca_nhom | tu_nhan | khong_ro",
             "ten": ["tên / @tag như trong tin"], "chuc_vu": "AM", "thay_the": false}
```

Code tính danh sách người:

| Kiểu | Ví dụ | Gán cho |
|------|-------|---------|
| `ten` | "@Tuấn Anh làm X", "@admin a check hộ em báo cáo", "remind PM vietanh.nguyen" | Đúng người được nêu, **kể cả admin, chủ kênh**; @tag gắn theo **mã tài khoản trong tag** |
| `chuc_vu` | "các AM", "team SOC" | Người có chức vụ / nhóm đó trong **bảng chức vụ của kênh** (4.4); chưa có trong bảng → "chưa giao" ghi rõ "Các AM" |
| `ca_nhom` | "mọi người", "toàn team", "các nhân sự" | Thành viên kênh **trừ** admin hệ thống, chủ kênh, người quản lý kênh, người gửi, người "không nhận việc chung" |
| `tu_nhan` | "em làm X", "mình sẽ gửi" | Người gửi |
| `khong_ro` | kế hoạch, mốc không nêu ai | **Chưa giao** (Q1) |

Kiểm của code: tên phải có trong tin nguồn (tag / nội dung / tóm tắt file); việc mới phải có tin nguồn hợp lệ;
`thay_the` (đính chính "chỉ… các nhân sự sau") thì thay danh sách; tin @Knight vẫn tạo việc cho người được giao,
phần nhờ AI không thành việc; người không thuộc kênh hiện "**Ngoài kênh**"; bên ngoài công ty (PH04, khách hàng)
hiện "**Bên ngoài**".

### 4.2. Người phụ trách là team / nhóm (sửa L9)

- Bảng chức vụ của kênh (4.4) có cả **nhóm**: "SOC" → [tài khoản…], "TI" → […]. Việc gán "SOC" hiện tên nhóm, và
  người trong nhóm thấy việc đó ở "Công việc của tôi", nhận nhắc hạn.
- Nhóm chưa khai báo → việc hiện "SOC (chưa khai báo thành viên)", nằm trong danh sách nhắc thứ Hai cho chủ kênh.

### 4.3. Yêu cầu chưa có hành động (sửa L8)

1. **Gom theo dòng**: mỗi dòng phản hồi có yêu cầu chưa xử lý tạo **một** việc "Bổ sung hành động cho yêu cầu còn
   thiếu (PH-000003)", phần chi tiết liệt kê các yêu cầu (tối đa 6). 23 việc → khoảng 7 việc.
2. **Gán cho người phụ trách của dòng** (cột Owner, ví dụ SOC), vì người xử lý phản hồi là người phải bổ sung hành
   động; trạng thái "Chưa bắt đầu", không hạn. Không còn "chưa gán" cho loại việc này.
3. **Bước rà lại (Q4)**: một lượt gọi model cho mỗi khoảng 4 dòng, hỏi "yêu cầu này đã được hành động nào xử lý chưa,
   kể cả gián tiếp?"; chỉ giữ yêu cầu bị xác nhận chưa xử lý.

### 4.4. Bảng chức vụ, nhóm và "không nhận việc chung" của kênh (mới)

- Mỗi kênh có bảng: chức vụ / nhóm → thành viên (AM, PM, SOC, TI…), và danh sách **"không nhận việc chung"**.
- **Tự học** từ tin phân công ("AM @Tuấn Anh, AM @Nghĩa, AM @My"), có ghi nguồn; tin sau sửa thì cập nhật.
- **Ai cấu hình**: chủ kênh và người quản lý kênh (4.6), trong bảng tiến độ của kênh, mục "Thành viên & chức vụ".
  Admin hệ thống sửa được mọi kênh.
- **"Không nhận việc chung"** (Q2): những người **không bị gán** khi tin nhắn giao chung ("mọi người", "toàn team");
  vẫn được gán khi bị tag / nêu tên cụ thể. Tự động gồm admin hệ thống, chủ kênh, người quản lý kênh; chủ kênh thêm
  người khác (lãnh đạo không phải admin như anh Nguyễn Hồng Đức – CCO, trợ lý…).

### 4.5. Giảm việc chưa gán / chưa liên kết

1. Gắn tài khoản theo mã trong @tag; so tên bỏ phòng ban, xưng hô, dấu; so phần trước @ của email.
2. **Bí danh của kênh**: chủ kênh gán tay "Duy" = một tài khoản một lần, các lần sau tự gắn.
3. **Gán nhanh** trong mục "Chưa có người phụ trách" / "Chưa liên kết": chọn người hoặc nhóm từ danh sách thành viên.
4. **Nhắc thứ Hai (Q3)**: 08:30 sáng thứ Hai, chủ kênh và người quản lý kênh nhận thông báo + email "Kênh X có N việc
   chưa có người phụ trách / chưa liên kết", có nút mở bảng tiến độ. Không gửi admin, không đăng vào kênh. Bật/tắt và
   giờ gửi chỉnh trên monitor.
5. Monitor: cột % việc chưa gán, % chưa liên kết theo kênh.

### 4.6. Người quản lý kênh, chuyển quyền chủ kênh (yêu cầu của chị Văn Thị Ngọc)

Hiện tại chỉ **chủ kênh** (người tạo) quản lý được kênh; người đó nghỉ việc, tài khoản bị khóa thì không ai quản lý
được kênh, phải tạo kênh mới. Mã nguồn đã có sẵn vai trò thành viên "manager" nhưng chưa có giao diện gán và tracker
chưa dùng.

1. **Người quản lý kênh (team lead)**: chủ kênh / admin gán một hoặc nhiều thành viên làm "quản lý" (trong danh sách
   thành viên kênh). Người quản lý có quyền như chủ kênh với tiến độ: đóng / hủy / gán việc, sửa bảng chức vụ, xem chế
   độ Nhóm, nhận email tổng hợp và nhắc thứ Hai.
2. **Chuyển quyền chủ kênh**: chủ kênh hoặc admin chuyển chủ kênh cho thành viên khác; lịch sử, việc, file giữ nguyên.
3. **Tài khoản bị khóa / nghỉ việc**:
   - Chủ kênh bị khóa: người quản lý kênh lâu nhất tự lên làm chủ kênh; kênh không có người quản lý thì admin nhận
     thông báo để chọn chủ kênh mới.
   - Việc của người bị khóa: hiện "Người phụ trách đã nghỉ", đưa vào nhắc thứ Hai để gán lại; không gửi nhắc hạn tới
     tài khoản đã khóa.
4. Admin hệ thống xem / đổi chủ kênh, người quản lý của mọi kênh trên monitor (tab Tracker → Kênh dự án).

### 4.7. Lỗi nhỏ của việc từ file (sửa L10)

- Hạn đặt sai việc: model đề xuất hạn mà ngày đó nằm ở một ý khác của dòng → code tách ý đó thành việc riêng với hạn
  đó (thay vì loại hạn).
- Bỏ chi tiết trùng nhau và chi tiết lặp lại tên việc.
- Vướng mắc chỉ nhận câu khó khăn / phụ thuộc; câu nhận định đưa vào chi tiết.
- Rút gọn câu gốc không cắt ở dấu ":"; tên dưới 3 từ thì lấy tiếp phần sau.

## 5. Danh sách công việc chi tiết

| Mã | Công việc | File chính | Kiểm thử |
|----|-----------|-----------|----------|
| T1 | Prompt tổng hợp chat: `giao_cho`, @Knight, đính chính, danh sách thành viên chỉ để nhận tên | tracker/prompts.py, schema.py | Tin bd3, poc soc |
| T2 | Code tính người phụ trách theo 5 kiểu; gắn theo mã @tag; kiểm tên có trong tin; bắt buộc nguồn | tracker/service.py, people.py | Model giả lập trả 9 tên, thay My bằng Sơn, thiếu nguồn |
| T3 | Bảng chức vụ / nhóm / "không nhận việc chung": lưu trong trạng thái kênh, tự học từ tin, API đọc / sửa | tracker/schema.py, service.py, router.py | "AM @…" → bảng; "các AM" → 3 người |
| T4 | Giao diện "Thành viên & chức vụ" trong bảng tiến độ kênh | ProjectProgress.svelte, apis/tracker | svelte-check, thử tay |
| T5 | Nhãn "Ngoài kênh" / "Bên ngoài" / "Người phụ trách đã nghỉ"; bí danh kênh; gán nhanh | people.py, mywork.py, ProjectProgress.svelte, my-work page | "Duy" ở poc soc |
| T6 | Yêu cầu chưa giao: gom theo dòng, gán người phụ trách dòng, bước rà lại (Q4) | tracker/extract.py | av: 23 → khoảng 7, không còn báo thừa PH-000003, PH-000009 |
| T7 | Lỗi nhỏ việc từ file (4.7) | tracker/extract.py | PH-000006 có việc riêng hạn 13/11; không còn "Test-CB10001:" |
| T8 | Nhắc thứ Hai cho chủ kênh / người quản lý (thông báo + email), cấu hình trên monitor | tracker/reminders.py, mailer.py, monitoring admin | Email mẫu + gửi thử |
| T9 | Người quản lý kênh: gán / bỏ vai trò manager, quyền tiến độ theo manager | routers/channels.py, tracker/router.py, mywork.py, giao diện thành viên kênh | Manager đóng / gán việc được |
| T10 | Chuyển chủ kênh; xử lý tài khoản bị khóa (tự chuyển, cảnh báo admin, việc "đã nghỉ") | routers/channels.py, tracker/reminders.py | Khóa tài khoản chủ kênh thử |
| T11 | Monitor: % chưa gán / chưa liên kết theo kênh; đổi chủ kênh / người quản lý | monitoring/server | Thử tay |
| T12 | Chạy toàn bộ kiểm thử cũ + mới; cập nhật TIEN-DO.md; commit | — | — |
| T13 | Deploy, **Tổng hợp lại kênh** bd3_csc_proj_26, bd3_monthly_kpi_26, poc_cg1018526003_soc, av và kênh có nhiều việc không nguồn | — | Đối chiếu lại bằng monitor / SQL |

Thứ tự đề xuất: T1–T2 (lỗi gán sai người, ảnh hưởng nặng nhất) → T6–T7 (giảm việc chưa gán ở kênh file) →
T3–T5 → T9–T10 (cần trước cuối tháng, khi trưởng nhóm của chị Ngọc nghỉ) → T8, T11 → T12–T13.

## 6. Kiểm thử bằng tin nhắn thật

| Tình huống | Kết quả đúng |
|------------|--------------|
| bd3_csc_proj 17/11: "@Knight … các AM @Tuấn Anh, AM @Nghĩa và AM @My sẽ báo cáo…" | Tuấn Anh, Nghĩa, My (theo mã tag); bảng chức vụ AM = 3 người; không có Sơn |
| bd3_monthly_kpi 16/11: "các nhân sự…", "Báo toàn bộ team… upload" | Thành viên kênh trừ Sơn (admin), My (chủ kênh, người gửi), người "không nhận việc chung" |
| bd3_monthly_kpi 17/11: "@Knight Chỉ thông báo cho các nhân sự sau: @…" | Thay bằng đúng danh sách được tag |
| poc soc 13/11: "@Knight remind PM vietanh.nguyen update tiến độ hàng ngày" | Việc gán Nguyễn Việt Anh |
| poc soc 16/11: "Hiện tại Duy đang làm việc với Điệp PH04…" | Việc gán "Duy", nhãn "Ngoài kênh", có nguồn |
| poc soc 17/11: kế hoạch 160 đơn vị | Mỗi mốc 1 việc có hạn, chưa giao (Q1); "PH04 phát hành công văn" gán PH04 – Bên ngoài |
| av: file phản hồi | 13 việc đã giao + khoảng 7 việc "bổ sung hành động" gán theo Owner; PH-000006 có việc riêng hạn 13/11 |
| Model không dẫn nguồn / tự thêm tên | Việc không được tạo / tên bị loại |
| Chủ kênh bị khóa tài khoản | Người quản lý lên làm chủ kênh; việc của người bị khóa hiện "đã nghỉ" |
