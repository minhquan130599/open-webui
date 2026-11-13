# Đăng nhập bằng Google và Microsoft (Teams) cho NCS AI (Knight)

Sau khi làm xong, trang đăng nhập có thêm nút **Tiếp tục với Google** và **Tiếp tục với Microsoft**.
Người dùng đăng nhập bằng Gmail / Google Workspace hoặc tài khoản Microsoft 365 (tài khoản dùng cho Teams)
mà không cần mật khẩu riêng của NCS AI (Knight).

Các file liên quan (cùng thư mục với `docker-compose.yaml` trên server):

| File | Vai trò |
|---|---|
| `docker-compose.oauth.yaml` | Thêm cấu hình đăng nhập cho service `open-webui`, dùng kèm file compose chính |
| `.env` | Điền địa chỉ hệ thống, Client ID / Secret của Google và Microsoft |

Dùng được cho cả bản build từ source (`docker-compose.yaml` + `update.ps1`) và bản triển khai bằng image
có sẵn (`deploy/docker-compose.yaml`).

---

## 1. Điều kiện trước khi bắt đầu

- **Hệ thống phải truy cập được qua một tên miền HTTPS**, ví dụ `https://chat.congty.vn`.
  Google không chấp nhận địa chỉ IP hoặc `http://`; Microsoft chỉ cho `http://` với `localhost`.
- Người dùng mở hệ thống **đúng bằng tên miền đó**. Mở bằng `http://<ip>:8000` sẽ không đăng nhập được,
  vì cookie đăng nhập chỉ được gửi qua HTTPS.
- Có quyền tạo ứng dụng trên **Google Cloud Console** (cho Google) và quyền quản trị
  **Microsoft Entra ID** của công ty (cho Microsoft). Chỉ cần làm phần của nhà cung cấp mình dùng.

Trong tài liệu này, `https://chat.congty.vn` là ví dụ; thay bằng tên miền thật ở mọi chỗ.

### Nếu chưa có HTTPS

Cần một reverse proxy đứng trước cổng `WEBUI_PORT` (mặc định 8000). Cách nhanh nhất là
[Caddy](https://caddyserver.com) — tự lấy chứng chỉ Let's Encrypt, tự hỗ trợ WebSocket:

```
# Caddyfile
chat.congty.vn {
    reverse_proxy localhost:8000
}
```

Yêu cầu: bản ghi DNS `chat.congty.vn` trỏ về IP public của server, mở cổng 80 và 443 trên firewall.
Nếu dùng nginx hoặc IIS, nhớ bật chuyển tiếp WebSocket (`Upgrade` / `Connection`) và header
`X-Forwarded-Proto`. Server không có IP public thì có thể dùng Cloudflare Tunnel.

---

## 2. Tạo ứng dụng đăng nhập Google

1. Vào <https://console.cloud.google.com> → chọn hoặc tạo một project.
2. **APIs & Services → OAuth consent screen** (hoặc **Google Auth Platform → Branding**):
   - **User type**:
     - *Internal* nếu công ty dùng Google Workspace và chỉ cho email công ty đăng nhập.
     - *External* nếu cho cả Gmail cá nhân. Sau khi cấu hình xong, bấm **Publish app**; nếu để ở chế độ
       *Testing* thì chỉ những email trong danh sách *Test users* mới đăng nhập được.
   - App name: `NCS AI (Knight)`; điền email hỗ trợ; Authorized domain: `congty.vn`.
   - Scopes: chỉ cần `openid`, `email`, `profile` (mặc định).
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized JavaScript origins: `https://chat.congty.vn`
   - Authorized redirect URIs:
     ```
     https://chat.congty.vn/oauth/google/login/callback
     ```
4. Bấm **Create**, chép **Client ID** và **Client secret** để điền vào `.env` ở bước 4.

---

## 3. Tạo ứng dụng đăng nhập Microsoft (Teams / Microsoft 365)

1. Vào <https://entra.microsoft.com> (hoặc Azure Portal → Microsoft Entra ID) bằng tài khoản quản trị.
2. **App registrations → New registration**:
   - Name: `NCS AI (Knight)`
   - Supported account types: **Accounts in this organizational directory only** (chỉ tài khoản công ty).
   - Redirect URI: chọn **Web**, nhập:
     ```
     https://chat.congty.vn/oauth/microsoft/login/callback
     ```
3. Bấm **Register**. Ở trang **Overview**, chép:
   - **Application (client) ID** → `MICROSOFT_CLIENT_ID`
   - **Directory (tenant) ID** → `MICROSOFT_CLIENT_TENANT_ID`

   Hai mã này cùng dạng GUID, rất dễ điền đảo. Tenant ID là mã của cả công ty (giống nhau cho mọi ứng
   dụng); Client ID là mã riêng của ứng dụng này.
4. **Certificates & secrets → Client secrets → New client secret**: đặt thời hạn (tối đa 24 tháng),
   chép ngay cột **Value** (không phải *Secret ID*) → `MICROSOFT_CLIENT_SECRET`.
   Giá trị này chỉ hiện một lần. **Ghi lại ngày hết hạn**: hết hạn là mọi người không đăng nhập
   bằng Microsoft được nữa, phải tạo secret mới và cập nhật `.env`.
5. **API permissions**: giữ quyền mặc định `Microsoft Graph → User.Read`. Nếu cột *Status* báo cần
   duyệt, bấm **Grant admin consent for <tên công ty>** để người dùng không bị hỏi xin quyền.
6. **Ai được đăng nhập**: vào **Enterprise applications → (tên ứng dụng) → Properties**. Nếu
   *Assignment required?* là **Yes**, chỉ người/nhóm được gán ở **Users and groups** mới đăng nhập được
   (người khác bị lỗi `AADSTS50105`). Gán nhóm nhân viên cần dùng, hoặc đặt **No** để mọi tài khoản
   công ty đều vào được.
7. Tài khoản đăng nhập phải có **địa chỉ email** (thuộc tính *Mail* trong Entra ID). Tài khoản
   Microsoft 365 có hộp thư đều có sẵn; tài khoản không có email sẽ bị báo đăng nhập thất bại.

---

## 4. Điền cấu hình vào `.env`

Mở file `.env` trên server (cạnh `docker-compose.yaml`) và thêm:

```ini
# Tự nạp docker-compose.oauth.yaml cho mọi lệnh docker compose, kể cả update.ps1 và dev.ps1
COMPOSE_PATH_SEPARATOR=;
COMPOSE_FILE=docker-compose.yaml;docker-compose.oauth.yaml

# Địa chỉ HTTPS của hệ thống, không có dấu / ở cuối
PUBLIC_URL=https://chat.congty.vn

# Chỉ cho email thuộc các tên miền này (cách nhau bằng dấu phẩy); * = mọi email kể cả Gmail cá nhân
OAUTH_ALLOWED_DOMAINS=congty.vn

# pending = người mới phải chờ admin duyệt; user = vào dùng ngay
DEFAULT_USER_ROLE=pending

# Giữ true cho tới khi chắc chắn admin đăng nhập được bằng Google/Microsoft
ENABLE_LOGIN_FORM=true

# Google (để trống cả hai nếu không dùng -> nút Google không hiện)
GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxx

# Microsoft (để trống nếu không dùng -> nút Microsoft không hiện)
MICROSOFT_CLIENT_ID=00000000-0000-0000-0000-000000000000
MICROSOFT_CLIENT_SECRET=xxxxxxxx
MICROSOFT_CLIENT_TENANT_ID=00000000-0000-0000-0000-000000000000
```

Ghi chú:

- Dòng `COMPOSE_FILE` dùng dấu `;` theo `COMPOSE_PATH_SEPARATOR`, chạy được trên cả Windows và Linux.
  Không muốn dùng hai dòng này thì mỗi lệnh phải ghi rõ cả hai file:
  `docker compose -f docker-compose.yaml -f docker-compose.oauth.yaml up -d`.
- Với bản triển khai bằng image (`deploy/`), chép `docker-compose.oauth.yaml` vào thư mục `deploy/`.
- `.env` chứa secret: không commit, không gửi qua chat.

---

## 5. Khởi động lại và kiểm tra

```powershell
cd D:\WebUi\openwenui-docker
docker compose config --quiet      # không in gì = cấu hình hợp lệ
docker compose up -d open-webui    # tạo lại container với cấu hình mới (không cần build lại image)
```

Kiểm tra cấu hình đã vào container:

```powershell
docker exec open-webui printenv | Select-String "WEBUI_URL|GOOGLE_REDIRECT|MICROSOFT_REDIRECT|OAUTH_ALLOWED"
```

Sau đó mở `https://chat.congty.vn`:

1. Trang đăng nhập có nút **Tiếp tục với Google** / **Tiếp tục với Microsoft**.
2. Đăng nhập thử bằng một tài khoản → nếu `DEFAULT_USER_ROLE=pending`, người đó thấy màn hình chờ duyệt.
3. Admin vào **Bảng quản trị → Người dùng**, đổi vai trò người đó sang **user**.

---

## 6. Tài khoản cũ và quản lý người dùng

- **Người đã có tài khoản email/mật khẩu**: đăng nhập bằng Google/Microsoft với **cùng email** sẽ vào
  đúng tài khoản cũ (do `OAUTH_MERGE_ACCOUNTS_BY_EMAIL=true`), giữ nguyên lịch sử chat, kênh, nhóm.
  Email được so khớp không phân biệt hoa thường.
- **Admin hiện tại**: nên đăng nhập thử bằng Google/Microsoft một lần (email trùng với tài khoản admin)
  trước khi đặt `ENABLE_LOGIN_FORM=false`.
- **Chặn một người**: đổi vai trò sang *pending* hoặc xóa tài khoản trong **Bảng quản trị → Người dùng**.
  Với Microsoft, có thể chặn ngay trong Entra ID (vô hiệu hóa tài khoản / gỡ khỏi ứng dụng).
- **Chỉ đăng nhập bằng Google/Microsoft**: đặt `ENABLE_LOGIN_FORM=false` trong `.env`, chạy lại
  `docker compose up -d open-webui`.

---

## 7. Cấu hình được lưu trong database

Ba thiết lập sau, nếu đã từng được lưu trong **Bảng quản trị → Cài đặt**, sẽ **lấy giá trị trong database
thay cho `.env`**:

| Biến trong `.env` | Chỗ chỉnh trên giao diện |
|---|---|
| `PUBLIC_URL` (→ `WEBUI_URL`) | Cài đặt → Chung → **WebUI URL** |
| `DEFAULT_USER_ROLE` | Cài đặt → Xác thực → **Vai trò người dùng mặc định** |
| `ENABLE_LOGIN_FORM` | (không có trên giao diện) |

Nếu đổi trong `.env` mà không thấy tác dụng, xem giá trị đang lưu:

```powershell
docker exec owui-postgres psql -U openwebui -d openwebui -c "select key, value from config where key in ('webui.url','ui.default_user_role','ui.enable_login_form');"
```

Sửa trên giao diện, hoặc xóa dòng tương ứng để hệ thống quay về dùng giá trị trong `.env`
(sau đó chạy lại `docker compose up -d open-webui`):

```powershell
docker exec owui-postgres psql -U openwebui -d openwebui -c "delete from config where key = 'ui.enable_login_form';"
```

Các thiết lập Google/Microsoft (`GOOGLE_*`, `MICROSOFT_*`, `OAUTH_*`) luôn đọc từ `.env`.

---

## 8. Lỗi thường gặp

Xem log khi đăng nhập lỗi:

```powershell
docker compose logs --tail 300 open-webui | Select-String "oauth" -CaseSensitive:$false
```

| Hiện tượng | Nguyên nhân và cách sửa |
|---|---|
| Google báo **Error 400: redirect_uri_mismatch** | Redirect URI trong Google Console khác `PUBLIC_URL/oauth/google/login/callback`. Kiểm tra đúng `https`, đúng tên miền, không thừa dấu `/` |
| Microsoft báo **AADSTS50011** | Redirect URI trong Entra ID khác `PUBLIC_URL/oauth/microsoft/login/callback`, hoặc không chọn loại **Web** |
| Microsoft báo **AADSTS7000215** (invalid client secret) | Dán nhầm *Secret ID* thay vì *Value*, hoặc secret đã hết hạn |
| Microsoft báo **AADSTS50020** (user không thuộc tenant) | Tài khoản ngoài công ty; đúng như thiết lập *organizational directory only* |
| Microsoft báo **AADSTS50105** (*not a direct member of a group with access*) | Ứng dụng bật **Assignment required**. Người quản trị Entra ID vào **Enterprise applications → (tên ứng dụng) → Users and groups → Add user/group**, gán người dùng hoặc nhóm (vd nhóm toàn công ty). Muốn mọi tài khoản công ty đều vào được: **Properties → Assignment required? = No** |
| Bấm nút Microsoft ra **Internal Server Error**, log có `400 Bad Request` cho `.../.well-known/openid-configuration` | Tenant ID sai. Kiểm tra: `curl -s https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration` — báo `AADSTS90002 Tenant not found` là sai. Hay gặp nhất: **điền đảo Tenant ID và Client ID**. Tra tenant đúng theo tên miền: `curl -s https://login.microsoftonline.com/ncsgroup.vn/v2.0/.well-known/openid-configuration` (mã GUID trong `issuer` là Tenant ID) |
| Microsoft báo **AADSTS700016** (application not found in directory) | Client ID sai, hoặc ứng dụng thuộc tenant khác Tenant ID đã điền |
| Google báo **Access blocked / app chưa được xác minh** | App đang ở chế độ *Testing*: thêm email vào *Test users* hoặc bấm *Publish app* |
| Quay lại trang đăng nhập với thông báo **đăng nhập thất bại** | Email không thuộc `OAUTH_ALLOWED_DOMAINS`, hoặc tài khoản không có email. Log có dòng `e-mail domain is not in the list` hoặc `email is missing` |
| Đăng nhập xong lại bị đẩy ra trang đăng nhập | Đang mở bằng `http://` hoặc IP. Mở đúng `https://<tên miền>` |
| Không thấy nút Google/Microsoft | Thiếu Client ID/Secret (Microsoft cần đủ cả Tenant ID), hoặc chưa nạp `docker-compose.oauth.yaml`: kiểm tra `docker exec open-webui printenv` |
| Người mới đăng nhập được nhưng chỉ thấy màn hình chờ | Bình thường khi `DEFAULT_USER_ROLE=pending`: admin duyệt trong Bảng quản trị → Người dùng |

---

## 9. Việc định kỳ

- **Microsoft client secret hết hạn** (tối đa 24 tháng): tạo secret mới trước ngày hết hạn, cập nhật
  `MICROSOFT_CLIENT_SECRET` trong `.env`, chạy `docker compose up -d open-webui`.
- **Đổi tên miền**: cập nhật `PUBLIC_URL`, redirect URI trong Google Console và Entra ID, và mục
  WebUI URL trong Bảng quản trị nếu đã lưu (xem mục 7).
