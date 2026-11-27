# Test độ chính xác của tracker trên kênh `org-csc-cx-planning-q4.2026` (W)

Kênh được chép từ S1 sang W ngày 27/11/2026: kênh, 7 thành viên, 80 tin nhắn (cả luồng và trả lời của AI), 9 file đính
kèm, 6 người dùng. Người dùng chép sang **không đăng nhập được** và đều là `user`. Tài khoản admin trên W
(`minhquan130599@gmail.com`, tên hiển thị **Knight**) được thêm làm quản lý kênh.

Tracker của kênh trên W đang **rỗng và đánh dấu đã đọc hết**: không tự chạy cho tới khi bấm "Tổng hợp lại kênh".

Nơi xem kết quả:
- Bảng **Tiến độ** của kênh trên web (nút biểu đồ góc trên kênh).
- Monitor W `http://192.168.110.113:8090` → Tracker → dòng kênh → **Xem việc** (có tab Chờ xác nhận, Đã đóng/hủy)
  và **Truy vết** từng việc (tin nhắn nguồn, cách xác định người phụ trách, các lượt tổng hợp).
- Câu trả lời gốc của model ở mỗi lượt: lịch sử cập nhật (`tracker_item.data.tra_loi_model`).

> Lưu ý khi tự nhắn thử: tài khoản admin W tên hiển thị là **Knight**, trùng tên model AI. Tracker coi "Knight" là trợ
> lý AI, không bao giờ là người làm. Khi giao việc trong test, luôn ghi rõ tên người (Hà Diệu Hương, Nguyễn Thị Uyên,
> Hoàng Thu Hà), đừng dùng "tôi làm".

---

## Phần A. Dựng lại từ tin nhắn cũ (đo độ chính xác trên dữ liệu thật)

**Cách chạy:** Monitor W → Tracker → kênh `org-csc-cx-planning-q4.2026` → **Tổng hợp lại kênh**. Monitor hiện "Đang
tổng hợp lại: đã đọc X/Y tin"; xong thì bảng Tiến độ có danh sách việc.

**Kết quả đúng mong đợi** (đối chiếu tin nhắn gốc; mỗi dòng là một điểm kiểm tra):

| # | Kiểm tra | Mong đợi |
|---|---|---|
| A1 | 20 việc trong `KH_CX_Q4_2026_Kanban.csv` | Có đủ, không trùng nhau |
| A2 | "Phỏng vấn CG10173" | Hoàn thành (tin 26/11 và 27/11), ngày hoàn thành **26/11**, không phải ngày tổng hợp lại |
| A3 | "Gửi feedback mẫu báo cáo TI mới", "Dự buổi đào tạo tại CG10161" | Hoàn thành |
| A4 | "Hoàn thiện kênh phản hồi độc lập", "Xây nội dung truyền thông kênh phản hồi…" | Hoàn thành |
| A5 | "Khảo sát CG10020, CB10021, CE10133" | **Chưa hoàn thành**: đã xong CG10020 và CE10133, CB10021 còn hẹn lịch; hạn **2026-12-02** |
| A6 | "Xây dựng bộ câu hỏi khảo sát định kỳ cho dịch vụ SOC và TI…" (tin 18/11) và dòng CSV "Xây dựng bộ câu hỏi khảo sát SOC và TI" | **Một việc**, hoàn thành ngày 19/11, phụ trách Hoàng Thu Hà |
| A7 | "Chỉ định đầu mối kĩ thuật để xử lý sự cố cho khách CG10115" | Có, phụ trách Nguyễn Thị Uyên |
| A8 | "Khảo sát nhanh lại sau khi xử lý sự cố qua điện thoại" (CG10115) | Có, phụ trách Nguyễn Thị Uyên, hạn **2026-11-23** |
| A9 | "Ghi nhận phản hồi khách hàng CB10055…" | Một việc, 3 người (Hương, Uyên, Hà), hạn **2026-12-24** (tin 20/11 đổi từ 26/12) |
| A10 | "Phỏng vấn và ghi nhận phản hồi … PA05 HN" (nhắn 3 lần) | **Một việc**, Hương và Uyên, hạn 2026-11-27 |
| A11 | "Cập nhật deadline các hạng mục công việc Blue Team" | **Không có** (là yêu cầu đổi hạn, không phải việc) |
| A12 | "Hẹn lịch phỏng vấn khảo sát … CG10020, CB10021, CE10133 (KH-05-T11)" (tin 17/11) | Gộp vào việc ở A5, không thành việc riêng |
| A13 | "Đăng ký và xác thực tài khoản Zalo OA và Telegram NCS Care" (Hoàng Thu Hà, @Knight, không ghi người) | Ở **Chờ xác nhận**, hoặc là việc hoàn thành 25/11 phụ trách Hoàng Thu Hà |
| A14 | Ngày tạo các việc | Theo ngày gửi tin / ngày gửi file (17/11–27/11), không phải ngày bấm tổng hợp lại |
| A15 | Trong lúc đang tổng hợp lại | Bảng Tiến độ vẫn hiện bản cũ (rỗng); đọc xong mới thay một lần |

Chấp nhận được (tuỳ cách model đọc): việc "Cải tiến báo cáo TI … KH-04-T11" từ tin 17/11 còn riêng hoặc gộp vào một
việc KH-04 của file CSV.

**Cách chấm:** đếm số dòng A1–A14 đạt. Dòng nào sai thì mở **Truy vết** của việc đó và câu trả lời gốc của lượt tương
ứng để biết sai do model đọc hay do code áp dụng.

---

## Phần B. Tin nhắn mới (sau khi Phần A xong)

Gửi từng tin trong kênh trên W (không cần @Knight, trừ khi ghi rõ), đợi khoảng 2 phút (tracker gom tin rồi mới chạy;
bấm nút ↻ trên bảng Tiến độ để chạy ngay), rồi kiểm tra.

| # | Tình huống | Tin gửi | Mong đợi |
|---|---|---|---|
| B1 | Việc mới, hạn dạng ngày/tháng | `Bổ sung việc: Gửi báo cáo CX tháng 11 cho ban giám đốc, phụ trách Hà Diệu Hương, hạn 3/12` | Việc mới, phụ trách Hương, hạn **2026-12-03** |
| B2 | Cùng việc, diễn đạt khác | `PV khách PA05 HN xong rồi nhé` | Việc PA05 HN chuyển **Hoàn thành**, không có việc mới |
| B3 | Xong một phần | `Khảo sát CB10021 đã hẹn được lịch 1/12, chưa khảo sát` | Việc A5 vẫn chưa hoàn thành; có thể cập nhật tiến độ / vướng mắc |
| B4 | Đổi hạn việc đã có | `Dời hạn việc Thiết kế quick survey cho Pentest sang 5/12` | Việc đó hạn **2026-12-05**; **không** có việc "Dời hạn…" |
| B5 | Giao theo nhóm | `Nhờ các AM gửi danh sách khách hàng cần khảo sát trước 4/12` | Việc mới giao cho nhóm **AM** (đánh dấu nhóm chưa khai báo người nếu kênh chưa có AM) |
| B6 | Việc lặp lại sau khi đóng | Trên bảng Tiến độ **Đóng** việc "Họp TI thống nhất cải tiến báo cáo", rồi gửi `Tuần sau họp TI thống nhất cải tiến báo cáo lần 2, phụ trách Nguyễn Thị Uyên, hạn 5/12` | **Việc mới**, chi tiết ghi "Lặp lại việc đã đóng ngày …"; việc cũ vẫn ở Đã đóng |
| B7 | Mở lại việc đã đóng | **Đóng** việc "Thu phản hồi CB10064 & CB10159", rồi gửi `Việc thu phản hồi CB10064 & CB10159 chưa xong, làm tiếp, hạn 12/12` | Việc cũ **trở lại danh sách đang mở** (rời Đã đóng), hạn 2026-12-12, không có việc trùng |
| B8 | Trùng tên việc đã hủy | **Hủy** một việc bất kỳ, rồi gửi tin giao lại đúng tên việc đó kèm người phụ trách | Việc mới (ghi "Lặp lại…" hoặc "Trùng tên việc đã đóng ngày …"); việc cũ vẫn ở Đã hủy |
| B9 | Việc đang mở đã hoàn thành (chưa đóng) bị nhắc lại | Gửi `Phỏng vấn CG10173 đã gửi biên bản cho khách` | Cập nhật vào việc "Phỏng vấn CG10173" (ghi đè trạng thái / thời gian), không tạo việc mới |
| B10 | Nhờ AI, không phải việc | `@Knight tóm tắt tiến độ tuần này` | Knight trả lời; **không** có việc mới |
| B11 | Báo việc qua @Knight | `@Knight bổ sung việc rà soát hợp đồng CG10200, phụ trách Nguyễn Thị Uyên, hạn 6/12` | Knight trả lời **ngắn**, không gọi tool, không nói "đã cập nhật"; vài phút sau việc lên bảng |
| B12 | Việc thêm qua @Knight không ghi người | `@Knight thêm việc chuẩn bị tài liệu họp quý` | Việc ở **Chờ xác nhận** (bảng Tiến độ, khung vàng) |
| B13 | Sửa tin | Sửa tin B1 thành hạn `10/12` | Việc B1 hạn **2026-12-10** |
| B14 | Xóa tin | Xóa tin B11 | Việc rà soát hợp đồng CG10200 bị **gỡ** khỏi bảng |
| B15 | Tin từ Telegram | Từ bot Telegram (đã liên kết, chọn kênh này) gửi `Bổ sung việc: gọi lại khách CG10115, phụ trách Nguyễn Thị Uyên, hạn 2/12` | Tin có nhãn "Gửi từ Telegram"; việc được ghi như tin thường; AI không trả lời |

---

## Phần C. Tổng hợp lại lần nữa (kiểm tra không làm mất / không hồi sinh)

Sau Phần B, bấm **Tổng hợp lại kênh** lần nữa.

| # | Mong đợi |
|---|---|
| C1 | Các việc đã đóng / hủy ở B6–B8 **vẫn đóng / hủy**, không sống lại từ tin cũ |
| C2 | Việc mở lại ở B7 vẫn đang mở (tin mở lại gửi sau lúc đóng) |
| C3 | Các trạng thái hoàn thành ở Phần A và B vẫn giữ, ngày hoàn thành vẫn là ngày của tin báo |
| C4 | Không phát sinh việc trùng so với trước khi tổng hợp lại |

Sau mỗi phần, ghi lại số điểm đạt / tổng số, và với mỗi điểm sai: việc nào, mong đợi gì, thực tế ra sao, link Truy vết.
