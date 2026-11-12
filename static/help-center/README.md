# Nội dung trang Hướng dẫn sử dụng

Trang **Hướng dẫn sử dụng** (`/help`, mục ngay dưới "Không gian làm việc" ở thanh bên) đọc toàn bộ nội dung
từ file `articles.json` trong thư mục này. Sửa file là sửa trang, không cần sửa code.

## Ngôn ngữ

- `articles.json`: bản tiếng Việt, cũng là bản mặc định.
- `articles.en-US.json`: bản tiếng Anh, dùng khi giao diện để English (US).
- Thêm ngôn ngữ khác: tạo `articles.<mã ngôn ngữ>.json` (mã như trong Cài đặt → Ngôn ngữ, ví dụ `articles.ja-JP.json`).
  Ngôn ngữ chưa có file riêng sẽ hiện bản tiếng Việt.
- Các file dùng chung thư mục `images/`; muốn ảnh riêng cho một ngôn ngữ thì trỏ `src` sang file ảnh khác.
- Nên giữ cùng danh sách `id` giữa các file để `featured` hoạt động giống nhau.

## Cấu trúc `articles.json`

| Trường | Ý nghĩa |
|---|---|
| `heading`, `subheading`, `searchPlaceholder` | Tiêu đề, dòng mô tả và gợi ý ô tìm kiếm ở đầu trang |
| `updated` | Dòng "Cập nhật: ..." cuối mỗi bài |
| `featured` | Danh sách `id` bài hiện ở "Chủ đề nổi bật" (người dùng thường) |
| `featuredAdmin` | Như trên, khi xem ở chế độ Quản trị viên |
| `articles` | Danh sách bài viết, hiển thị theo đúng thứ tự trong file |

Mỗi bài viết:

```json
{
	"id": "ten-khong-dau",
	"category": "Trợ lý AI",
	"roles": ["user", "admin"],
	"title": "Tiêu đề bài",
	"desc": "Mô tả ngắn",
	"minutes": 3,
	"icon": "✨",
	"steps": [
		{ "title": "Bước 1", "text": "Nội dung bước 1" },
		{ "title": "Bước 2", "text": "Nội dung bước 2" }
	],
	"example": "@ai-assistant câu lệnh mẫu (không bắt buộc)",
	"tip": "Lưu ý (không bắt buộc)",
	"images": [
		{ "src": "/help-center/images/ten-anh.png", "caption": "Chú thích ảnh" }
	]
}
```

- `id`: duy nhất, không dấu, không khoảng trắng.
- `category`: các bài cùng `category` được gom thành một nhóm trong danh mục bên trái.
- `roles`: `["user", "admin"]` = mọi người thấy; `["admin"]` = chỉ quản trị viên thấy.
- `icon`: một emoji bất kỳ.
- `{{name}}` trong nội dung được thay bằng tên hệ thống (biến `BRAND_NAME` / `WEBUI_NAME`).
- `example`, `tip`, `images`: bỏ trường đi nếu không dùng.
- Ảnh minh họa: đặt file vào thư mục `images/` cạnh file này rồi thêm vào `images`. Một bài có thể có nhiều ảnh,
  hiện theo đúng thứ tự; bấm vào ảnh để xem cỡ lớn. `caption` có thể bỏ.

## Lưu ý khi sửa tay

- File phải là JSON hợp lệ: chuỗi trong dấu nháy kép `"`, các phần tử cách nhau bằng dấu phẩy, **không** có dấu phẩy
  sau phần tử cuối. Muốn dùng dấu nháy kép trong nội dung, viết `\"`.
- Nếu file lỗi, trang Hướng dẫn hiện thông báo lỗi kèm vị trí; kiểm tra nhanh bằng https://jsonlint.com hoặc VS Code.

## Áp dụng thay đổi

- **Sửa trong repo** (`static/help-center/`): build lại image (`update.ps1`).
- **Sửa trực tiếp trên server, không build lại**: gắn thư mục này vào container trong `docker-compose.yaml`
  (service `open-webui`):

  ```yaml
      volumes:
        - ./help-center:/app/build/help-center
  ```

  Chép thư mục `help-center` cạnh `docker-compose.yaml`, chạy `docker compose up -d`. Từ đó sửa file
  `help-center/articles.json` trên server rồi tải lại trang là thấy ngay.
