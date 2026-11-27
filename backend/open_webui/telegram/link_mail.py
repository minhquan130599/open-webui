"""The email with the code that confirms linking a Telegram account.

Sent with the app's email sender (tracker.mailer: Microsoft Graph or SMTP, settings tracker.reminder.email.* on the
monitor page) to the email of the NCS AI account the link belongs to: only someone who can read that mailbox can
finish the link, so a t.me link seen or forwarded by someone else is not enough to tie their Telegram to the account.
"""

import html
from typing import Optional

from open_webui.telegram.models import OTP_SECONDS


async def ready() -> tuple[bool, Optional[dict]]:
    """(email can be sent, mail settings). The sender only has to be set up (Graph or SMTP): its on/off switch is for
    task-reminder emails and does not stop the linking code from being sent."""
    from open_webui.tracker import mailer

    try:
        settings = await mailer.settings()
    except Exception:
        return False, None
    return not mailer.missing(settings), settings


def _body(name: str, code: str, telegram_name: str) -> str:
    minutes = OTP_SECONDS // 60
    who = f' (<b>{html.escape(telegram_name)}</b>)' if telegram_name else ''
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111;line-height:1.5">'
        f'<p>Chào {html.escape(name or "bạn")},</p>'
        f'<p>Một tài khoản Telegram{who} vừa yêu cầu liên kết với tài khoản NCS AI của bạn. '
        'Mã xác nhận của bạn là:</p>'
        f'<p style="font-size:28px;font-weight:700;letter-spacing:6px;margin:16px 0">{html.escape(code)}</p>'
        f'<p>Nhập mã này trên trang NCS AI đang chờ liên kết. Mã có hiệu lực {minutes} phút và chỉ dùng được một lần.</p>'
        '<p style="color:#666">Nếu bạn không yêu cầu liên kết Telegram, hãy bỏ qua email này: không nhập mã thì '
        'không có gì được liên kết.</p>'
        '</div>'
    )


async def send_code(user, code: str, telegram_name: str, settings: Optional[dict] = None) -> None:
    """Raises (mailer.MailError or other) when the email could not be sent."""
    from open_webui.tracker import mailer

    if not user.email:
        raise ValueError('Tài khoản chưa có email')
    refused = await mailer.send(user.email, 'Mã xác nhận liên kết Telegram - NCS AI',
                                _body(user.name or '', code, telegram_name), settings)
    if refused:
        raise mailer.MailError('Máy chủ email từ chối địa chỉ nhận')
