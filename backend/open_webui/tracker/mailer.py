"""Due-reminder emails, sent through Microsoft Graph (Microsoft 365) or a company SMTP server.

Graph: NCS AI sends from one shared mailbox (e.g. ncs-ai@company.com) with an app-only token (client credentials):
the Azure app needs the *application* permission Mail.Send with admin consent, and the tenant needs Exchange Online.
Restrict it to that mailbox with an Exchange Application Access Policy so the app cannot send as anyone else. The app
registration defaults to the one used for Microsoft login (oauth.microsoft.*); a separate one can be set.

SMTP: any mail server the company already sends with (host, port, STARTTLS/SSL, account), no Microsoft 365 needed.

Settings live in the config table and are edited on the monitor page.
"""

import asyncio
import html
import logging
import os
import re
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Optional

import aiohttp

from open_webui.models.config import Config

log = logging.getLogger(__name__)

ENABLE_KEY = 'tracker.reminder.email.enable'
METHOD_KEY = 'tracker.reminder.email.method'  # graph | smtp
SENDER_KEY = 'tracker.reminder.email.sender'
SENDER_NAME_KEY = 'tracker.reminder.email.sender_name'
TENANT_KEY = 'tracker.reminder.email.tenant_id'
CLIENT_ID_KEY = 'tracker.reminder.email.client_id'
CLIENT_SECRET_KEY = 'tracker.reminder.email.client_secret'
SMTP_HOST_KEY = 'tracker.reminder.email.smtp_host'
SMTP_PORT_KEY = 'tracker.reminder.email.smtp_port'
SMTP_SECURITY_KEY = 'tracker.reminder.email.smtp_security'  # starttls | ssl | none
SMTP_USERNAME_KEY = 'tracker.reminder.email.smtp_username'
SMTP_PASSWORD_KEY = 'tracker.reminder.email.smtp_password'
SMTP_VERIFY_KEY = 'tracker.reminder.email.smtp_verify_tls'
KEYS = (
    ENABLE_KEY,
    METHOD_KEY,
    SENDER_KEY,
    SENDER_NAME_KEY,
    TENANT_KEY,
    CLIENT_ID_KEY,
    CLIENT_SECRET_KEY,
    SMTP_HOST_KEY,
    SMTP_PORT_KEY,
    SMTP_SECURITY_KEY,
    SMTP_USERNAME_KEY,
    SMTP_PASSWORD_KEY,
    SMTP_VERIFY_KEY,
)

DEFAULTS = {
    ENABLE_KEY: os.getenv('TRACKER_REMINDER_EMAIL_ENABLE', 'False').lower() == 'true',
    METHOD_KEY: os.getenv('TRACKER_REMINDER_EMAIL_METHOD', 'graph'),
    SENDER_KEY: os.getenv('TRACKER_REMINDER_EMAIL_SENDER', ''),
    SENDER_NAME_KEY: os.getenv('TRACKER_REMINDER_EMAIL_SENDER_NAME', 'NCS AI'),
    TENANT_KEY: os.getenv('TRACKER_REMINDER_EMAIL_TENANT_ID', ''),
    CLIENT_ID_KEY: os.getenv('TRACKER_REMINDER_EMAIL_CLIENT_ID', ''),
    CLIENT_SECRET_KEY: os.getenv('TRACKER_REMINDER_EMAIL_CLIENT_SECRET', ''),
    SMTP_HOST_KEY: os.getenv('TRACKER_REMINDER_SMTP_HOST', ''),
    SMTP_PORT_KEY: os.getenv('TRACKER_REMINDER_SMTP_PORT', '587'),
    SMTP_SECURITY_KEY: os.getenv('TRACKER_REMINDER_SMTP_SECURITY', 'starttls'),
    SMTP_USERNAME_KEY: os.getenv('TRACKER_REMINDER_SMTP_USERNAME', ''),
    SMTP_PASSWORD_KEY: os.getenv('TRACKER_REMINDER_SMTP_PASSWORD', ''),
    SMTP_VERIFY_KEY: os.getenv('TRACKER_REMINDER_SMTP_VERIFY_TLS', 'True').lower() == 'true',
}
SMTP_TIMEOUT = 30

GRAPH_URL = 'https://graph.microsoft.com/v1.0'
TIMEOUT = aiohttp.ClientTimeout(total=30)
# User setting (settings.ui) that turns the emails off for one person.
USER_OPT_OUT = 'trackerEmailReminders'

_token: dict = {}  # {'key': (tenant, client_id), 'value': str, 'expires': float}


class MailError(RuntimeError):
    pass


async def settings() -> dict:
    """Effective settings: monitor-page values, else the Microsoft login app registration."""
    values = await Config.get_many(
        *KEYS,
        'oauth.microsoft.tenant_id',
        'oauth.microsoft.client_id',
        'oauth.microsoft.client_secret',
        'oauth.microsoft.login_base_url',
    )

    def pick(key, fallback=None):
        value = values.get(key)
        if value in (None, ''):
            value = DEFAULTS[key] or (values.get(fallback) if fallback else '')
        return value

    try:
        smtp_port = int(pick(SMTP_PORT_KEY) or 587)
    except (TypeError, ValueError):
        smtp_port = 587
    verify = pick(SMTP_VERIFY_KEY)
    return {
        'enable': bool(pick(ENABLE_KEY)),
        'method': 'smtp' if str(pick(METHOD_KEY) or '').strip().lower() == 'smtp' else 'graph',
        'sender': (pick(SENDER_KEY) or '').strip(),
        'sender_name': (pick(SENDER_NAME_KEY) or '').strip(),
        'smtp_host': (pick(SMTP_HOST_KEY) or '').strip(),
        'smtp_port': smtp_port,
        'smtp_security': str(pick(SMTP_SECURITY_KEY) or 'starttls').strip().lower(),
        'smtp_username': (pick(SMTP_USERNAME_KEY) or '').strip(),
        'smtp_password': pick(SMTP_PASSWORD_KEY) or '',
        'smtp_verify_tls': verify if isinstance(verify, bool) else str(verify).lower() != 'false',
        'tenant_id': (pick(TENANT_KEY, 'oauth.microsoft.tenant_id') or '').strip(),
        'client_id': (pick(CLIENT_ID_KEY, 'oauth.microsoft.client_id') or '').strip(),
        'client_secret': pick(CLIENT_SECRET_KEY, 'oauth.microsoft.client_secret') or '',
        'login_base_url': (values.get('oauth.microsoft.login_base_url') or 'https://login.microsoftonline.com').rstrip(
            '/'
        ),
    }


def missing(s: dict) -> list[str]:
    if s.get('method') == 'smtp':
        # Username/password are optional: an internal relay may accept the server without login.
        names = {'sender': 'địa chỉ gửi', 'smtp_host': 'máy chủ SMTP'}
    else:
        names = {
            'sender': 'hộp thư gửi',
            'tenant_id': 'Tenant ID',
            'client_id': 'Client ID',
            'client_secret': 'Client secret',
        }
    return [label for key, label in names.items() if not s.get(key)]


def _html_to_text(body: str) -> str:
    """Plain-text part for mail clients that do not show HTML."""
    body = re.sub(r'(?is)<(script|style).*?</\1>', '', body)
    body = re.sub(r'(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>', '\n', body)
    text = html.unescape(re.sub(r'<[^>]+>', '', body))
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(line.strip() for line in text.splitlines())).strip()


def _send_smtp(s: dict, to: str, subject: str, html_body: str) -> None:
    """Blocking SMTP send (run in a thread). Raises MailError with a readable reason."""
    message = EmailMessage()
    message['From'] = formataddr((s['sender_name'], s['sender'])) if s.get('sender_name') else s['sender']
    message['To'] = to
    message['Subject'] = subject
    message['Message-ID'] = make_msgid(domain=s['sender'].rsplit('@', 1)[-1] or None)
    message.set_content(_html_to_text(html_body) or subject)
    message.add_alternative(html_body, subtype='html')

    context = ssl.create_default_context()
    if not s.get('smtp_verify_tls', True):
        # Internal servers with a self-signed certificate.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    host, port, security = s['smtp_host'], s['smtp_port'], s.get('smtp_security', 'starttls')
    where = f'{host}:{port}'
    try:
        if security == 'ssl':
            server = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
        with server:
            server.ehlo()
            if security == 'starttls':
                server.starttls(context=context)
                server.ehlo()
            if s.get('smtp_username'):
                server.login(s['smtp_username'], s.get('smtp_password') or '')
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f'Máy chủ SMTP {where} từ chối đăng nhập: sai tài khoản/mật khẩu, hoặc tài khoản chưa được '
                        f'phép gửi qua SMTP ({e.smtp_code} {e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else e.smtp_error})'[:400])
    except smtplib.SMTPSenderRefused as e:
        raise MailError(f'Máy chủ SMTP không cho gửi từ {s["sender"]}: địa chỉ gửi phải là tài khoản đăng nhập '
                        f'hoặc địa chỉ được phép gửi thay ({e.smtp_code})'[:400])
    except smtplib.SMTPRecipientsRefused as e:
        raise MailError(f'Máy chủ SMTP từ chối người nhận {to}: có thể cần đăng nhập để gửi ra ngoài (relay) '
                        f'({", ".join(str(v[0]) for v in e.recipients.values())})'[:400])
    except smtplib.SMTPNotSupportedError:
        raise MailError(f'Máy chủ {where} không hỗ trợ STARTTLS hoặc đăng nhập: đổi kiểu mã hóa (cổng 465 → SSL, '
                        '587/25 → STARTTLS) hoặc bỏ tài khoản nếu máy chủ không cần đăng nhập')
    except ssl.SSLCertVerificationError as e:
        raise MailError(f'Chứng chỉ TLS của {where} không hợp lệ ({e.verify_message}); máy chủ nội bộ dùng chứng chỉ '
                        'tự ký thì tắt "Kiểm tra chứng chỉ TLS"')
    except ssl.SSLError as e:
        raise MailError(f'Lỗi TLS với {where} ({e.reason}): thường do sai kiểu mã hóa so với cổng '
                        '(465 → SSL, 587 → STARTTLS, 25 → STARTTLS hoặc không mã hóa)')
    except (socket.timeout, TimeoutError):
        raise MailError(f'Hết thời gian kết nối {where}: kiểm tra địa chỉ, cổng và firewall từ server NCS AI')
    except (ConnectionRefusedError, socket.gaierror, OSError) as e:
        raise MailError(f'Không kết nối được máy chủ SMTP {where}: {e}')
    except smtplib.SMTPException as e:
        raise MailError(f'Máy chủ SMTP {where} báo lỗi: {e}'[:400])


async def _access_token(s: dict) -> str:
    key = (s['tenant_id'], s['client_id'])
    if _token.get('key') == key and _token.get('expires', 0) > time.time() + 60:
        return _token['value']
    url = f'{s["login_base_url"]}/{s["tenant_id"]}/oauth2/v2.0/token'
    form = {
        'grant_type': 'client_credentials',
        'client_id': s['client_id'],
        'client_secret': s['client_secret'],
        'scope': 'https://graph.microsoft.com/.default',
    }
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, data=form) as response:
                body = await response.json(content_type=None)
    except Exception as e:
        raise MailError(f'Không kết nối được Microsoft ({url}): {e}')
    if 'access_token' not in body:
        detail = (body.get('error_description') or body.get('error') or '').split('\r\n')[0]
        hint = ''
        if 'AADSTS7000215' in detail or 'AADSTS7000222' in detail:
            hint = 'Client secret sai hoặc đã hết hạn. '
        elif 'AADSTS700016' in detail:
            hint = 'Client ID không tồn tại trong tenant này. '
        elif 'AADSTS90002' in detail:
            hint = 'Tenant ID sai. '
        raise MailError(f'Không lấy được token Microsoft: {hint}{detail}'[:400])
    _token.update(key=key, value=body['access_token'], expires=time.time() + int(body.get('expires_in', 3600)))
    return body['access_token']


async def send(to: str, subject: str, html_body: str, s: Optional[dict] = None) -> None:
    """Send one HTML email from the shared mailbox. Raises MailError with a readable reason."""
    s = s or await settings()
    lacking = missing(s)
    if lacking:
        raise MailError(f'Chưa cấu hình: {", ".join(lacking)}')
    if s.get('method') == 'smtp':
        await asyncio.to_thread(_send_smtp, s, to, subject, html_body)
        return
    token = await _access_token(s)
    payload = {
        'message': {
            'subject': subject,
            'body': {'contentType': 'HTML', 'content': html_body},
            'toRecipients': [{'emailAddress': {'address': to}}],
        },
        'saveToSentItems': False,
    }
    url = f'{GRAPH_URL}/users/{s["sender"]}/sendMail'
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, json=payload, headers={'Authorization': f'Bearer {token}'}) as response:
                if response.status == 202:
                    return
                try:
                    error = (await response.json(content_type=None)).get('error') or {}
                except Exception:
                    error = {}
                status = response.status
    except MailError:
        raise
    except Exception as e:
        raise MailError(f'Không kết nối được Microsoft Graph: {e}')
    if status == 401:
        _token.clear()
    hint = {
        401: 'token không hợp lệ',
        403: 'ứng dụng chưa được cấp quyền Mail.Send (loại Application, đã admin consent), '
        'hoặc Application Access Policy không cho gửi từ hộp thư này',
        404: f'không tìm thấy hộp thư gửi {s["sender"]} (cần là hộp thư người dùng có license Exchange Online hoặc shared mailbox)',
        429: 'Microsoft đang giới hạn tốc độ gửi',
    }.get(status, 'Microsoft Graph báo lỗi')
    raise MailError(f'Gửi mail lỗi HTTP {status} ({hint}): {error.get("code", "")} {error.get("message", "")}'[:400])


def reminder_html(name: str, items: list[tuple], link: str) -> str:
    """items: (kind, entry) with kind 'today' / 'tomorrow'."""
    rows = []
    for kind, entry in sorted(items, key=lambda x: (x[0] != 'today', x[1].han or '', x[1].channel_name)):
        label = 'Hôm nay' if kind == 'today' else 'Ngày mai'
        color = '#b42318' if kind == 'today' else '#b54708'
        progress = f'{entry.tien_do}%' if entry.tien_do is not None else '—'
        blocker = (
            f'<div style="color:#b42318;font-size:12px;margin-top:2px">Vướng mắc: {html.escape(entry.vuong_mac)}</div>'
            if entry.vuong_mac
            else ''
        )
        rows.append(
            f'<tr><td style="padding:8px;border-bottom:1px solid #eee"><b>{html.escape(entry.ten)}</b>{blocker}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee">#{html.escape(entry.channel_name)}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;color:{color};white-space:nowrap">'
            f'{label} ({html.escape(entry.han or "")})</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{progress}</td></tr>'
        )
    button = (
        f'<p style="margin:16px 0"><a href="{html.escape(link)}" style="background:#111;color:#fff;padding:9px 16px;'
        f'border-radius:8px;text-decoration:none">Mở Công việc của tôi</a></p>'
        if link
        else ''
    )
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111;max-width:680px">
<p>Chào {html.escape(name)},</p>
<p>Bạn có <b>{len(items)}</b> công việc sắp đến hạn:</p>
<table style="border-collapse:collapse;width:100%;font-size:13px">
<tr style="text-align:left;color:#555"><th style="padding:8px;border-bottom:2px solid #ddd">Công việc</th>
<th style="padding:8px;border-bottom:2px solid #ddd">Kênh</th><th style="padding:8px;border-bottom:2px solid #ddd">Hạn</th>
<th style="padding:8px;border-bottom:2px solid #ddd;text-align:right">Tiến độ</th></tr>
{''.join(rows)}
</table>
{button}
<p style="color:#888;font-size:12px">Email tự động từ NCS AI (Knight). Tắt email nhắc việc trong Cài đặt → Thông báo.</p>
</div>"""


async def send_reminders(per_user: dict[str, list[tuple]]) -> dict:
    """One email per person with all their reminders. Returns {'sent': n, 'skipped': n, 'failed': n}."""
    from open_webui.models.users import Users
    from open_webui.monitoring import recorder as perf

    result = {'sent': 0, 'skipped': 0, 'failed': 0}
    s = await settings()
    if not s['enable'] or not per_user:
        return result
    lacking = missing(s)
    if lacking:
        log.warning('Reminder emails enabled but not configured: %s', ', '.join(lacking))
        perf.record('tracker', name='email', status='error', error=f'Chưa cấu hình: {", ".join(lacking)}')
        return result
    try:
        # A wrong secret or an unreachable Microsoft fails once here instead of once per person.
        if s['method'] == 'graph':
            await _access_token(s)
    except MailError as e:
        log.warning('Reminder emails skipped: %s', e)
        perf.record('tracker', name='email', status='error', error=str(e)[:400])
        return {**result, 'failed': len(per_user)}
    base = ((await Config.get('webui.url')) or os.getenv('OPEN_WEBUI_PUBLIC_URL', '') or '').rstrip('/')
    link = f'{base}/my-work' if base else ''
    for user_id, items in per_user.items():
        user = await Users.get_user_by_id(user_id)
        if not user or not user.email or '@' not in user.email:
            result['skipped'] += 1
            continue
        user_settings = user.settings.model_dump() if hasattr(user.settings, 'model_dump') else user.settings
        if ((user_settings or {}).get('ui') or {}).get(USER_OPT_OUT) is False:
            result['skipped'] += 1
            continue
        today = sum(1 for kind, _ in items if kind == 'today')
        subject = (
            f'[NCS AI] {today} việc đến hạn hôm nay' + (f', {len(items) - today} việc ngày mai' if today < len(items) else '')
            if today
            else f'[NCS AI] {len(items)} việc đến hạn ngày mai'
        )
        started = time.perf_counter()
        try:
            await send(user.email, subject, reminder_html(user.name or user.email, items, link), s)
            result['sent'] += 1
            perf.record(
                'tracker', name='email', user_id=user_id, count=len(items),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as e:
            result['failed'] += 1
            log.warning('Reminder email to %s failed: %s', user.email, e)
            perf.record(
                'tracker', name='email', user_id=user_id, status='error', error=str(e)[:400],
                duration_ms=(time.perf_counter() - started) * 1000,
            )
    log.info('Reminder emails: %s', result)
    return result
