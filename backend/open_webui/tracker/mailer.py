"""Due-reminder emails through Microsoft Graph (Microsoft 365).

NCS AI sends from one shared mailbox (e.g. ncs-ai@company.com) with an app-only token (client credentials): the
Azure app needs the *application* permission Mail.Send with admin consent. Restrict it to that mailbox with an
Exchange Application Access Policy so the app cannot send as anyone else.

The app registration defaults to the one used for Microsoft login (oauth.microsoft.*); a separate one can be set.
Settings live in the config table and are edited on the monitor page.
"""

import html
import logging
import os
import time
from typing import Optional

import aiohttp

from open_webui.models.config import Config

log = logging.getLogger(__name__)

ENABLE_KEY = 'tracker.reminder.email.enable'
SENDER_KEY = 'tracker.reminder.email.sender'
TENANT_KEY = 'tracker.reminder.email.tenant_id'
CLIENT_ID_KEY = 'tracker.reminder.email.client_id'
CLIENT_SECRET_KEY = 'tracker.reminder.email.client_secret'
KEYS = (ENABLE_KEY, SENDER_KEY, TENANT_KEY, CLIENT_ID_KEY, CLIENT_SECRET_KEY)

DEFAULTS = {
    ENABLE_KEY: os.getenv('TRACKER_REMINDER_EMAIL_ENABLE', 'False').lower() == 'true',
    SENDER_KEY: os.getenv('TRACKER_REMINDER_EMAIL_SENDER', ''),
    TENANT_KEY: os.getenv('TRACKER_REMINDER_EMAIL_TENANT_ID', ''),
    CLIENT_ID_KEY: os.getenv('TRACKER_REMINDER_EMAIL_CLIENT_ID', ''),
    CLIENT_SECRET_KEY: os.getenv('TRACKER_REMINDER_EMAIL_CLIENT_SECRET', ''),
}

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

    return {
        'enable': bool(pick(ENABLE_KEY)),
        'sender': (pick(SENDER_KEY) or '').strip(),
        'tenant_id': (pick(TENANT_KEY, 'oauth.microsoft.tenant_id') or '').strip(),
        'client_id': (pick(CLIENT_ID_KEY, 'oauth.microsoft.client_id') or '').strip(),
        'client_secret': pick(CLIENT_SECRET_KEY, 'oauth.microsoft.client_secret') or '',
        'login_base_url': (values.get('oauth.microsoft.login_base_url') or 'https://login.microsoftonline.com').rstrip(
            '/'
        ),
    }


def missing(s: dict) -> list[str]:
    names = {
        'sender': 'hộp thư gửi',
        'tenant_id': 'Tenant ID',
        'client_id': 'Client ID',
        'client_secret': 'Client secret',
    }
    return [label for key, label in names.items() if not s.get(key)]


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
