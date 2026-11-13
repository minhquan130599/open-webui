"""Create, rotate or reset the Google Authenticator code for changing settings on the monitor page.

Run on the server (inside the monitor container):

    docker compose exec monitor python -m open_webui.monitoring.server.totp init     # first time
    docker compose exec monitor python -m open_webui.monitoring.server.totp rotate   # new QR (needs current code)
    docker compose exec monitor python -m open_webui.monitoring.server.totp status
    docker compose exec monitor python -m open_webui.monitoring.server.totp reset    # lost phone: admin key only

The QR code and the secret are printed here once and never shown on the web page. The secret is stored only in
the database, encrypted with MONITOR_ADMIN_KEY (changing that key requires running `reset` then `init`).
"""

import getpass
import json
import os
import sys
import time

import psycopg

from open_webui.monitoring.server import security


def _connect():
    url = os.getenv('DATABASE_URL', '')
    if not url.startswith(('postgres://', 'postgresql://')):
        sys.exit('DATABASE_URL (PostgreSQL) is not set in this container.')
    return psycopg.connect(url.replace('postgres://', 'postgresql://', 1), autocommit=True)


def _load(conn) -> dict:
    row = conn.execute('SELECT value FROM config WHERE key = %s', (security.TOTP_KEY,)).fetchone()
    value = row[0] if row else None
    if isinstance(value, str):
        value = json.loads(value)
    return value or {}


def _save(conn, value: dict) -> None:
    conn.execute(
        """INSERT INTO config (key, value, updated_at) VALUES (%s, %s::json, %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
        (security.TOTP_KEY, json.dumps(value), int(time.time())),
    )


def _ask_admin_key() -> str:
    if not security.ADMIN_KEY:
        sys.exit('MONITOR_ADMIN_KEY is not set (add it to .env, then: docker compose up -d monitor).')
    key = getpass.getpass('Khóa quản trị (MONITOR_ADMIN_KEY): ')
    if not security.check_admin_key(key):
        sys.exit('Khóa quản trị không đúng.')
    return key


def _show(secret: str) -> None:
    import qrcode

    uri = security.provisioning_uri(secret)
    print('\nQuét mã QR này bằng Google Authenticator (hoặc Microsoft Authenticator, Authy):\n')
    qr = qrcode.QRCode(border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(f'\nNếu không quét được, nhập tay secret: {secret}')
    print(f'Tên tài khoản: {security.ISSUER}')
    print(
        '\nLưu secret này ở nơi an toàn. Nó không hiện lại ở bất kỳ đâu; mất điện thoại thì chạy `reset` rồi `init`.\n'
    )


def _new(conn, previous: dict) -> None:
    secret = security.new_secret()
    _show(secret)
    while True:
        code = input('Nhập mã 6 số đang hiện trên ứng dụng để xác nhận đã thêm (Enter để hủy): ').strip()
        if not code:
            sys.exit('Đã hủy, không thay đổi gì.' if previous else 'Đã hủy, chưa tạo mã.')
        step = security.verify_code(secret, code, -1)
        if step is not None:
            break
        print('Mã không đúng, thử lại.')
    _save(conn, {'enc': security.encrypt_secret(secret), 'created_at': int(time.time()), 'last_step': step})
    print('Đã lưu. Mã cũ (nếu có) hết hiệu lực.' if previous else 'Đã tạo mã xác thực.')


def main(argv: list[str]) -> None:
    command = argv[1] if len(argv) > 1 else 'status'
    with _connect() as conn:
        current = _load(conn)
        if command == 'status':
            if current:
                created = time.strftime('%d/%m/%Y %H:%M', time.localtime(current.get('created_at', 0)))
                readable = security.decrypt_secret(current.get('enc', '')) is not None
                print(
                    f'Đã cài mã xác thực từ {created}.'
                    + ('' if readable else ' KHÔNG giải mã được với MONITOR_ADMIN_KEY hiện tại: chạy reset rồi init.')
                )
            else:
                print('Chưa cài mã xác thực. Chạy: python -m open_webui.monitoring.server.totp init')
        elif command == 'init':
            if current:
                sys.exit('Đã có mã xác thực. Dùng `rotate` để đổi (cần mã hiện tại) hoặc `reset` nếu mất điện thoại.')
            _ask_admin_key()
            _new(conn, current)
        elif command == 'rotate':
            if not current:
                sys.exit('Chưa có mã xác thực. Dùng `init`.')
            _ask_admin_key()
            secret = security.decrypt_secret(current.get('enc', ''))
            if not secret:
                sys.exit('Không giải mã được secret hiện tại (MONITOR_ADMIN_KEY đã đổi?). Dùng `reset` rồi `init`.')
            code = input('Mã 6 số hiện tại trên ứng dụng: ').strip()
            if security.verify_code(secret, code, int(current.get('last_step', -1))) is None:
                sys.exit('Mã hiện tại không đúng hoặc đã dùng. Không thay đổi gì.')
            _new(conn, current)
        elif command == 'reset':
            if not current:
                sys.exit('Chưa có mã xác thực.')
            _ask_admin_key()
            if input('Gõ XOA để xóa mã xác thực hiện tại: ').strip() != 'XOA':
                sys.exit('Đã hủy.')
            conn.execute('DELETE FROM config WHERE key = %s', (security.TOTP_KEY,))
            print('Đã xóa. Chạy `init` để tạo mã mới; trước đó không ai sửa được cấu hình trên trang giám sát.')
        else:
            sys.exit('Lệnh: init | rotate | status | reset')


if __name__ == '__main__':
    main(sys.argv)
