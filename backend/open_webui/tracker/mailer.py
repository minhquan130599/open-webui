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
OWNER_DIGEST_KEY = 'tracker.reminder.email.owner_digest'  # daily digest to channel owners
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
    OWNER_DIGEST_KEY,
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
    OWNER_DIGEST_KEY: True,
}
SMTP_TIMEOUT = 30

GRAPH_URL = 'https://graph.microsoft.com/v1.0'
TIMEOUT = aiohttp.ClientTimeout(total=30)
# User setting (settings.ui) that turns the emails off for one person.
USER_OPT_OUT = 'trackerEmailReminders'

_token: dict = {}  # {'key': (tenant, client_id), 'value': str, 'expires': float}


class MailError(RuntimeError):
    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        # The server is limiting or briefly unavailable: worth sending again a little later.
        self.transient = transient


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
    owner_digest = values.get(OWNER_DIGEST_KEY)
    return {
        'enable': bool(pick(ENABLE_KEY)),
        'owner_digest': DEFAULTS[OWNER_DIGEST_KEY] if owner_digest in (None, '') else bool(owner_digest),
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


def _send_smtp(s: dict, to: list[str], subject: str, html_body: str, bcc: list[str] = ()) -> list[str]:
    """Blocking SMTP send (run in a thread); returns the recipients the server refused while taking the others.
    Raises MailError with a readable reason."""
    message = EmailMessage()
    message['From'] = formataddr((s['sender_name'], s['sender'])) if s.get('sender_name') else s['sender']
    message['To'] = ', '.join(to)
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
            # Bcc addresses are given to the server only, never written in the message.
            refused = server.send_message(message, to_addrs=[*to, *bcc])
            return sorted(refused or {})
    except Exception as e:
        raise _smtp_error(e, s, to, where) from e


def _smtp_text(e: smtplib.SMTPResponseException) -> str:
    return e.smtp_error.decode(errors='replace') if isinstance(e.smtp_error, bytes) else str(e.smtp_error)


def _smtp_error(e: Exception, s: dict, to: list[str], where: str) -> MailError:
    """A readable reason for an SMTP failure; limits and dropped connections are marked worth retrying."""
    if isinstance(e, MailError):
        return e
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return MailError(f'Máy chủ SMTP {where} từ chối đăng nhập: sai tài khoản/mật khẩu, hoặc tài khoản chưa được '
                         f'phép gửi qua SMTP ({e.smtp_code} {_smtp_text(e)})'[:400])
    if isinstance(e, smtplib.SMTPResponseException) and 400 <= e.smtp_code < 500:
        return MailError(f'Máy chủ SMTP {where} tạm từ chối ({e.smtp_code} {_smtp_text(e)})'[:400], transient=True)
    if isinstance(e, smtplib.SMTPSenderRefused):
        return MailError(f'Máy chủ SMTP không cho gửi từ {s["sender"]}: địa chỉ gửi phải là tài khoản đăng nhập '
                         f'hoặc địa chỉ được phép gửi thay ({e.smtp_code})'[:400])
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return MailError(f'Máy chủ SMTP từ chối người nhận {", ".join(to)}: có thể cần đăng nhập để gửi ra ngoài (relay) '
                         f'({", ".join(str(v[0]) for v in e.recipients.values())})'[:400])
    if isinstance(e, smtplib.SMTPNotSupportedError):
        return MailError(f'Máy chủ {where} không hỗ trợ STARTTLS hoặc đăng nhập: đổi kiểu mã hóa (cổng 465 → SSL, '
                         '587/25 → STARTTLS) hoặc bỏ tài khoản nếu máy chủ không cần đăng nhập')
    if isinstance(e, ssl.SSLCertVerificationError):
        return MailError(f'Chứng chỉ TLS của {where} không hợp lệ ({e.verify_message}); máy chủ nội bộ dùng chứng chỉ '
                         'tự ký thì tắt "Kiểm tra chứng chỉ TLS"')
    if isinstance(e, ssl.SSLError):
        return MailError(f'Lỗi TLS với {where} ({e.reason}): thường do sai kiểu mã hóa so với cổng '
                         '(465 → SSL, 587 → STARTTLS, 25 → STARTTLS hoặc không mã hóa)')
    if isinstance(e, (socket.timeout, TimeoutError)):
        return MailError(f'Hết thời gian kết nối {where}: kiểm tra địa chỉ, cổng và firewall từ server NCS AI',
                         transient=True)
    if isinstance(e, smtplib.SMTPServerDisconnected):
        return MailError(f'Máy chủ SMTP {where} ngắt kết nối giữa chừng: {e}'[:400], transient=True)
    if isinstance(e, smtplib.SMTPException):
        return MailError(f'Máy chủ SMTP {where} báo lỗi: {e}'[:400])
    if isinstance(e, OSError):
        return MailError(f'Không kết nối được máy chủ SMTP {where}: {e}')
    return MailError(f'Gửi mail qua {where} lỗi: {e}'[:400])


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


async def send(
    to: str | list[str], subject: str, html_body: str, s: Optional[dict] = None, bcc: Optional[list[str]] = None
) -> list[str]:
    """Send one HTML email from the shared mailbox, to one or several addresses (bcc: hidden from the others).

    Returns the addresses an SMTP server refused while taking the others. Raises MailError with a readable reason.
    """
    s = s or await settings()
    lacking = missing(s)
    if lacking:
        raise MailError(f'Chưa cấu hình: {", ".join(lacking)}')
    to = [to] if isinstance(to, str) else list(to)
    bcc = list(bcc or [])
    if s.get('method') == 'smtp':
        return await asyncio.to_thread(_send_smtp, s, to, subject, html_body, bcc)
    token = await _access_token(s)
    payload = {
        'message': {
            'subject': subject,
            'body': {'contentType': 'HTML', 'content': html_body},
            'toRecipients': [{'emailAddress': {'address': a}} for a in to],
            **({'bccRecipients': [{'emailAddress': {'address': a}} for a in bcc]} if bcc else {}),
        },
        'saveToSentItems': False,
    }
    url = f'{GRAPH_URL}/users/{s["sender"]}/sendMail'
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, json=payload, headers={'Authorization': f'Bearer {token}'}) as response:
                if response.status == 202:
                    return []
                try:
                    error = (await response.json(content_type=None)).get('error') or {}
                except Exception:
                    error = {}
                status = response.status
    except MailError:
        raise
    except Exception as e:
        raise MailError(f'Không kết nối được Microsoft Graph: {e}', transient=True)
    if status == 401:
        _token.clear()
    hint = {
        401: 'token không hợp lệ',
        403: 'ứng dụng chưa được cấp quyền Mail.Send (loại Application, đã admin consent), '
        'hoặc Application Access Policy không cho gửi từ hộp thư này',
        404: f'không tìm thấy hộp thư gửi {s["sender"]} (cần là hộp thư người dùng có license Exchange Online hoặc shared mailbox)',
        429: 'Microsoft đang giới hạn tốc độ gửi',
    }.get(status, 'Microsoft Graph báo lỗi')
    raise MailError(
        f'Gửi mail lỗi HTTP {status} ({hint}): {error.get("code", "")} {error.get("message", "")}'[:400],
        transient=status in (429, 500, 502, 503, 504),
    )


REMINDER_GROUPS = (
    # kind, heading, accent, soft background
    ('overdue', 'Đã quá hạn', '#b42318', '#fef3f2'),
    ('today', 'Đến hạn hôm nay', '#e04f16', '#fff4ed'),
    ('tomorrow', 'Đến hạn ngày mai', '#b54708', '#fffaeb'),
)
STATUS_TEXT = {
    'chua_bat_dau': 'Chưa bắt đầu',
    'dang_lam': 'Đang làm',
    'bi_chan': 'Bị chặn',
    'tam_dung': 'Tạm dừng',
    'hoan_thanh': 'Hoàn thành',
}


def _date_vn(value: Optional[str]) -> str:
    """2026-11-30 -> 30/11/2026."""
    if not value or len(value) < 10:
        return value or ''
    return f'{value[8:10]}/{value[5:7]}/{value[:4]}'


def _due_text(kind: str, entry) -> str:
    if kind == 'overdue':
        late = -(entry.days_left or 0)
        return f'Quá hạn {late} ngày (hạn {_date_vn(entry.han)})'
    return f'{"Hôm nay" if kind == "today" else "Ngày mai"}, {_date_vn(entry.han)}'


def reminder_subject(items: list[tuple], day: str) -> str:
    counts = {kind: sum(1 for k, _ in items if k == kind) for kind, *_ in REMINDER_GROUPS}
    parts = [
        f'{counts["overdue"]} quá hạn' if counts['overdue'] else '',
        f'{counts["today"]} đến hạn hôm nay' if counts['today'] else '',
        f'{counts["tomorrow"]} đến hạn ngày mai' if counts['tomorrow'] else '',
    ]
    return f'[NCS AI] Nhắc việc {_date_vn(day)[:5]}: ' + ', '.join(p for p in parts if p)


def _progress_bar(value: Optional[int], color: str) -> str:
    """A bar made of table cells: Outlook ignores most CSS but renders cell widths."""
    if value is None:
        return ''
    done = max(0, min(int(value), 100))
    filled = f'<td width="{done}%" style="background:{color};height:6px;line-height:6px;font-size:0">&nbsp;</td>' if done else ''
    empty = (
        f'<td width="{100 - done}%" style="background:#eaecf0;height:6px;line-height:6px;font-size:0">&nbsp;</td>'
        if done < 100
        else ''
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px"><tr>'
        f'<td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-radius:3px">'
        f'<tr>{filled}{empty}</tr></table></td>'
        f'<td width="44" style="padding-left:8px;font-size:12px;color:#475467;text-align:right;white-space:nowrap">{done}%</td>'
        '</tr></table>'
    )


def _item_html(
    kind: str, entry, name: str, accent: str, base: str, show_channel: bool = True, flag_unlinked: bool = False
) -> str:
    esc = html.escape
    channel = ''
    if show_channel:
        channel = esc(entry.channel_name)
        if base and entry.channel_id:
            channel = (
                f'<a href="{esc(base)}/channels/{esc(entry.channel_id)}" style="color:#344054;text-decoration:underline">'
                f'#{channel}</a>'
            )
        else:
            channel = f'#{channel}'
    meta = [channel, esc(STATUS_TEXT.get(entry.trang_thai, entry.trang_thai or ''))]
    owners = esc(', '.join(entry.phu_trach)) if entry.phu_trach else ''
    if flag_unlinked:
        if not entry.phu_trach:
            meta.append('<b style="color:#b54708">Chưa có người phụ trách</b>')
        elif not entry.owner_ids:
            meta.append(f'Phụ trách: {owners} <b style="color:#b54708">(chưa gắn tài khoản)</b>')
        else:
            meta.append(f'Phụ trách: <b style="color:#101828">{owners}</b>')
    elif owners:
        meta.append(f'Phụ trách: {owners}')
    blocker = (
        '<div style="margin-top:8px;padding:6px 10px;background:#fef3f2;border-left:3px solid #d92d20;'
        f'color:#b42318;font-size:12.5px">Vướng mắc: {esc(entry.vuong_mac)}</div>'
        if entry.vuong_mac
        else ''
    )
    return f'''<tr><td style="padding:0 0 10px 0">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #eaecf0;border-left:4px solid {accent};border-radius:8px;background:#ffffff">
<tr><td style="padding:12px 14px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="font-size:14.5px;font-weight:600;color:#101828;line-height:1.4">{esc(entry.ten)}</td>
<td style="padding-left:12px;font-size:12px;font-weight:600;color:{accent};white-space:nowrap;text-align:right;vertical-align:top">{esc(_due_text(kind, entry))}</td>
</tr></table>
<div style="margin-top:4px;font-size:12.5px;color:#475467">{' &nbsp;·&nbsp; '.join(m for m in meta if m)}</div>
{_progress_bar(entry.tien_do, accent)}{blocker}
</td></tr></table>
</td></tr>'''


def reminder_html(name: str, items: list[tuple], link: str, base: str = '', day: str = '') -> str:
    """The reminder email: items as (kind, entry), kind 'overdue' / 'today' / 'tomorrow', grouped by urgency.
    Built from tables with inline styles so Outlook, Gmail and phone mail apps show it the same way."""
    esc = html.escape
    counts = {kind: sum(1 for k, _ in items if k == kind) for kind, *_ in REMINDER_GROUPS}
    chips = ''.join(
        f'<td style="padding:0 6px 0 0"><span style="display:inline-block;padding:4px 10px;border-radius:999px;'
        f'background:{soft};color:{accent};font-size:12.5px;font-weight:600">{counts[kind]} {heading.lower()}</span></td>'
        for kind, heading, accent, soft in REMINDER_GROUPS
        if counts[kind]
    )
    sections = []
    for kind, heading, accent, _ in REMINDER_GROUPS:
        group = sorted((e for k, e in items if k == kind), key=lambda e: (e.han or '', e.channel_name, e.ten))
        if not group:
            continue
        sections.append(
            f'<tr><td style="padding:18px 0 8px 0;font-size:13px;font-weight:700;color:{accent};'
            f'text-transform:uppercase;letter-spacing:.04em">{heading} ({len(group)})</td></tr>'
            + ''.join(_item_html(kind, entry, name, accent, base) for entry in group)
        )
    button = (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:20px 0 4px 0"><tr>'
        f'<td style="border-radius:8px;background:#101828"><a href="{esc(link)}" style="display:inline-block;'
        'padding:10px 18px;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none">Mở Công việc của tôi</a>'
        '</td></tr></table>'
        if link
        else ''
    )
    total = len(items)
    preheader = ', '.join(
        f'{counts[kind]} {heading.lower()}' for kind, heading, *_ in REMINDER_GROUPS if counts[kind]
    )
    return f'''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Nhắc việc</title></head>
<body style="margin:0;padding:0;background:#f2f4f7">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{esc(preheader)} – NCS AI nhắc việc.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;font-family:'Segoe UI',Arial,Helvetica,sans-serif;color:#101828">
<tr><td style="padding:0 4px 12px 4px;font-size:13px;color:#475467"><b style="color:#101828">NCS AI</b> &nbsp;·&nbsp; Nhắc việc{f" ngày {esc(_date_vn(day))}" if day else ""}</td></tr>
<tr><td style="background:#ffffff;border:1px solid #eaecf0;border-radius:12px;padding:22px 22px 18px 22px">
<div style="font-size:18px;font-weight:700;margin-bottom:6px">Chào {esc(name)},</div>
<div style="font-size:14px;color:#344054;line-height:1.5">Bạn có <b>{total}</b> công việc cần chú ý. Cập nhật tiến độ bằng cách nhắn trong kênh dự án; NCS AI sẽ tự ghi nhận.</div>
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:12px"><tr>{chips}</tr></table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(sections)}</table>
{button}
</td></tr>
<tr><td style="padding:14px 6px 0 6px;font-size:12px;color:#667085;line-height:1.5">Email tự động từ NCS AI (Knight), gửi cho người phụ trách các việc trên.
Việc sai người phụ trách hoặc sai hạn: nhắn đính chính trong kênh dự án. Tắt email nhắc việc: Cài đặt → Thông báo.</td></tr>
</table></td></tr></table></body></html>'''


def sample_reminders(day: str) -> list[tuple]:
    """Made-up items of every group, for the test email and previews."""
    import datetime as dt

    from open_webui.tracker.mywork import WorkEntry

    def shift(days: int) -> str:
        return (dt.date.fromisoformat(day) + dt.timedelta(days=days)).isoformat()

    def entry(ten, channel, owners, status, progress, due, days_left, blocker=None):
        return WorkEntry('', channel, ten[:8], ten, owners, [], status, progress, due, blocker, None, days_left=days_left)

    return [
        ('overdue', entry('Xác minh vì sao test-CG10015 còn lọc tay, cập nhật kết quả test', 'cx-phan-hoi', ['TI'], 'dang_lam', 40, shift(-4), -4,
                          'Chưa có phương án cho lịch sử portal 6 tháng')),
        ('today', entry('Hoàn thiện báo cáo tuần gửi khách hàng', 'du-an-soc-ngan-hang', ['Long', 'Triển'], 'dang_lam', 70, day, 0)),
        ('tomorrow', entry('Gửi hồ sơ nghiệm thu giai đoạn 1', 'du-an-soc-ngan-hang', ['Long'], 'chua_bat_dau', None, shift(1), 1,
                           'Chờ khách hàng ký biên bản')),
    ]


def owner_subject(channels: list[dict], day: str) -> str:
    items = [pair for channel in channels for pair in channel['items']]
    counts = {kind: sum(1 for k, _ in items if k == kind) for kind, *_ in REMINDER_GROUPS}
    parts = [
        f'{counts["overdue"]} quá hạn' if counts['overdue'] else '',
        f'{counts["today"]} đến hạn hôm nay' if counts['today'] else '',
        f'{counts["tomorrow"]} đến hạn ngày mai' if counts['tomorrow'] else '',
    ]
    where = f'kênh {channels[0]["name"]}' if len(channels) == 1 else f'{len(channels)} kênh bạn quản lý'
    return f'[NCS AI] Tiến độ {where} {_date_vn(day)[:5]}: ' + ', '.join(p for p in parts if p)


def owner_digest_html(name: str, channels: list[dict], link: str, base: str = '', day: str = '') -> str:
    """Email to a channel owner: per channel they manage, the items overdue, due today and due tomorrow, with who
    owns each one; items whose owner has no linked account (so nobody gets their reminder) are flagged."""
    esc = html.escape
    items = [pair for channel in channels for pair in channel['items']]
    counts = {kind: sum(1 for k, _ in items if k == kind) for kind, *_ in REMINDER_GROUPS}
    unlinked = sum(1 for _, e in items if not e.owner_ids)
    chips = ''.join(
        f'<td style="padding:0 6px 0 0"><span style="display:inline-block;padding:4px 10px;border-radius:999px;'
        f'background:{soft};color:{accent};font-size:12.5px;font-weight:600">{counts[kind]} {heading.lower()}</span></td>'
        for kind, heading, accent, soft in REMINDER_GROUPS
        if counts[kind]
    )
    blocks = []
    for channel in sorted(channels, key=lambda c: c['name']):
        title = esc(channel['name'])
        if base and channel.get('id'):
            title = f'<a href="{esc(base)}/channels/{esc(channel["id"])}" style="color:#101828;text-decoration:none">#{title}</a>'
        else:
            title = f'#{title}'
        per_kind = {kind: [e for k, e in channel['items'] if k == kind] for kind, *_ in REMINDER_GROUPS}
        summary = ' · '.join(
            f'<span style="color:{accent}">{len(per_kind[kind])} {heading.lower()}</span>'
            for kind, heading, accent, _ in REMINDER_GROUPS
            if per_kind[kind]
        )
        rows = []
        for kind, heading, accent, _ in REMINDER_GROUPS:
            for entry in sorted(per_kind[kind], key=lambda e: (e.han or '', e.ten)):
                rows.append(_item_html(kind, entry, name, accent, base, show_channel=False, flag_unlinked=True))
        blocks.append(
            '<tr><td style="padding:18px 0 8px 0">'
            f'<div style="font-size:15px;font-weight:700">{title}</div>'
            f'<div style="font-size:12.5px;margin-top:2px">{summary}</div></td></tr>' + ''.join(rows)
        )
    button = (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:20px 0 4px 0"><tr>'
        f'<td style="border-radius:8px;background:#101828"><a href="{esc(link)}" style="display:inline-block;'
        'padding:10px 18px;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none">Mở bảng tiến độ</a>'
        '</td></tr></table>'
        if link
        else ''
    )
    warning = (
        '<div style="margin-top:12px;padding:8px 12px;background:#fffaeb;border:1px solid #fedf89;border-radius:8px;'
        f'font-size:12.5px;color:#93370d"><b>{unlinked} việc</b> chưa gắn được tài khoản người phụ trách nên người đó '
        'không nhận được email nhắc. Hãy nhắc trực tiếp, hoặc ghi đúng tên thành viên kênh trong tin nhắn / file.</div>'
        if unlinked
        else ''
    )
    where = f'kênh <b>#{esc(channels[0]["name"])}</b>' if len(channels) == 1 else f'<b>{len(channels)} kênh</b> bạn quản lý'
    preheader = ', '.join(f'{counts[kind]} {heading.lower()}' for kind, heading, *_ in REMINDER_GROUPS if counts[kind])
    return f'''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Tiến độ kênh</title></head>
<body style="margin:0;padding:0;background:#f2f4f7">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{esc(preheader)} – tiến độ các kênh bạn quản lý.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;font-family:'Segoe UI',Arial,Helvetica,sans-serif;color:#101828">
<tr><td style="padding:0 4px 12px 4px;font-size:13px;color:#475467"><b style="color:#101828">NCS AI</b> &nbsp;·&nbsp; Tiến độ kênh{f" ngày {esc(_date_vn(day))}" if day else ""}</td></tr>
<tr><td style="background:#ffffff;border:1px solid #eaecf0;border-radius:12px;padding:22px 22px 18px 22px">
<div style="font-size:18px;font-weight:700;margin-bottom:6px">Chào {esc(name)},</div>
<div style="font-size:14px;color:#344054;line-height:1.5">{where} có <b>{len(items)}</b> công việc sắp hoặc đã quá hạn. Người phụ trách từng việc cũng nhận email nhắc riêng.</div>
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:12px"><tr>{chips}</tr></table>
{warning}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(blocks)}</table>
{button}
</td></tr>
<tr><td style="padding:14px 6px 0 6px;font-size:12px;color:#667085;line-height:1.5">Email tự động từ NCS AI (Knight), gửi cho chủ kênh dự án mỗi ngày có việc sắp đến hạn.
Tắt email nhắc việc: Cài đặt → Thông báo.</td></tr>
</table></td></tr></table></body></html>'''


def sample_owner_digest(day: str) -> list[dict]:
    """Made-up channels for the test email and previews."""
    items = sample_reminders(day)
    unlinked = items[2][1]
    unlinked.owner_ids = []
    for _, entry in items[:2]:
        entry.owner_ids = ['x']
    return [
        {'id': '', 'name': 'du-an-soc-ngan-hang', 'items': [items[1], items[2]]},
        {'id': '', 'name': 'cx-phan-hoi', 'items': [items[0]]},
    ]


async def _deliver(per_user: dict, build, label: str) -> dict:
    """One email per user: build(user, payload, link, base, day) -> (subject, html). Respects the user's opt-out."""
    from open_webui.models.users import Users
    from open_webui.monitoring import recorder as perf
    from open_webui.tracker.mywork import today as today_iso

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
    day = today_iso()
    for user_id, payload in per_user.items():
        user = await Users.get_user_by_id(user_id)
        if not user or not user.email or '@' not in user.email or user.role == 'pending':
            result['skipped'] += 1
            continue
        user_settings = user.settings.model_dump() if hasattr(user.settings, 'model_dump') else user.settings
        if ((user_settings or {}).get('ui') or {}).get(USER_OPT_OUT) is False:
            result['skipped'] += 1
            continue
        subject, body = build(user, payload, link, base, day)
        started = time.perf_counter()
        try:
            await send(user.email, subject, body, s)
            result['sent'] += 1
            perf.record('tracker', name=label, user_id=user_id, duration_ms=(time.perf_counter() - started) * 1000)
        except Exception as e:
            result['failed'] += 1
            log.warning('%s email to %s failed: %s', label, user.email, e)
            perf.record(
                'tracker', name=label, user_id=user_id, status='error', error=str(e)[:400],
                duration_ms=(time.perf_counter() - started) * 1000,
            )
    log.info('%s emails: %s', label, result)
    return result


async def send_reminders(per_user: dict[str, list[tuple]]) -> dict:
    """One email per person with all their reminders. Returns {'sent': n, 'skipped': n, 'failed': n}."""
    return await _deliver(
        per_user,
        lambda user, items, link, base, day: (
            reminder_subject(items, day),
            reminder_html(user.name or user.email, items, link, base, day),
        ),
        'email',
    )


async def send_owner_digests(per_owner: dict[str, list[dict]]) -> dict:
    """One email per channel owner with the due items of the channels they manage (when enabled)."""
    if not (await settings()).get('owner_digest'):
        return {'sent': 0, 'skipped': 0, 'failed': 0}
    return await _deliver(
        per_owner,
        lambda user, channels, link, base, day: (
            owner_subject(channels, day),
            owner_digest_html(user.name or user.email, channels, f'{base}/my-work?mode=team' if base else link, base, day),
        ),
        'email_owner',
    )


PROBLEM_TEXT = {
    'unassigned': ('Chưa có người phụ trách', '#b54708'),
    'group': ('Giao cho nhóm chưa khai báo người', '#b54708'),
    'outside': ('Người phụ trách không phải thành viên kênh', '#c4320a'),
    'left': ('Người phụ trách đã nghỉ / tài khoản bị khóa', '#b42318'),
}


def unassigned_subject(channels: list[dict]) -> str:
    total = sum(len(c['items']) for c in channels)
    where = f'kênh {channels[0]["name"]}' if len(channels) == 1 else f'{len(channels)} kênh bạn quản lý'
    return f'[NCS AI] {total} việc cần phân công trong {where}'


def unassigned_html(name: str, channels: list[dict], link: str, base: str = '', day: str = '') -> str:
    """Monday email to a channel's owner and managers: open items nobody can be reminded about (no owner, a team
    with nobody declared, an owner outside the channel or who left), so they assign them."""
    esc = html.escape
    total = sum(len(c['items']) for c in channels)
    blocks = []
    for channel in sorted(channels, key=lambda c: c['name']):
        title = esc(channel['name'])
        if base and channel.get('id'):
            title = f'<a href="{esc(base)}/channels/{esc(channel["id"])}" style="color:#101828;text-decoration:none">#{title}</a>'
        else:
            title = f'#{title}'
        rows = []
        for problem, entry, who in channel['items']:
            label, color = PROBLEM_TEXT.get(problem, (problem, '#475467'))
            due = f' · hạn {_date_vn(entry.han)}' if entry.han else ''
            rows.append(
                '<tr><td style="padding:0 0 8px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'style="border:1px solid #eaecf0;border-left:4px solid {color};border-radius:8px;background:#ffffff"><tr>'
                f'<td style="padding:10px 14px"><div style="font-size:14px;font-weight:600;color:#101828">{esc(entry.ten)}</div>'
                f'<div style="margin-top:3px;font-size:12.5px;color:{color}">{esc(label)}{(": " + esc(who)) if who else ""}'
                f'<span style="color:#475467">{esc(due)}</span></div></td></tr></table></td></tr>'
            )
        blocks.append(
            f'<tr><td style="padding:16px 0 8px 0;font-size:15px;font-weight:700">{title} '
            f'<span style="font-size:12.5px;font-weight:400;color:#475467">({len(channel["items"])} việc)</span></td></tr>'
            + ''.join(rows)
        )
    button = (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:18px 0 4px 0"><tr>'
        f'<td style="border-radius:8px;background:#101828"><a href="{esc(link)}" style="display:inline-block;'
        'padding:10px 18px;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none">Mở bảng tiến độ để phân công</a>'
        '</td></tr></table>'
        if link
        else ''
    )
    return f'''<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Việc cần phân công</title></head>
<body style="margin:0;padding:0;background:#f2f4f7">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{total} việc chưa có người phụ trách hoặc cần gán lại.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;font-family:'Segoe UI',Arial,Helvetica,sans-serif;color:#101828">
<tr><td style="padding:0 4px 12px 4px;font-size:13px;color:#475467"><b style="color:#101828">NCS AI</b> &nbsp;·&nbsp; Việc cần phân công{f" – tuần {esc(_date_vn(day))}" if day else ""}</td></tr>
<tr><td style="background:#ffffff;border:1px solid #eaecf0;border-radius:12px;padding:22px 22px 18px 22px">
<div style="font-size:18px;font-weight:700;margin-bottom:6px">Chào {esc(name)},</div>
<div style="font-size:14px;color:#344054;line-height:1.5">Có <b>{total}</b> việc chưa ai nhận được nhắc hạn: chưa có người phụ trách, giao cho nhóm chưa khai báo thành viên, hoặc người phụ trách không thuộc kênh / đã nghỉ. Mở bảng tiến độ của kênh để gán người (một lần bấm), hoặc khai báo thành viên nhóm trong mục "Thành viên &amp; chức vụ".</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{''.join(blocks)}</table>
{button}
</td></tr>
<tr><td style="padding:14px 6px 0 6px;font-size:12px;color:#667085;line-height:1.5">Email tự động từ NCS AI (Knight) mỗi sáng thứ Hai, chỉ gửi chủ kênh và người quản lý kênh khi có việc cần phân công.
Tắt email nhắc việc: Cài đặt → Thông báo.</td></tr>
</table></td></tr></table></body></html>'''


async def send_unassigned(per_user: dict[str, list[dict]]) -> dict:
    return await _deliver(
        per_user,
        lambda user, channels, link, base, day: (
            unassigned_subject(channels),
            unassigned_html(user.name or user.email, channels, f'{base}/my-work?mode=team' if base else link, base, day),
        ),
        'email_unassigned',
    )
