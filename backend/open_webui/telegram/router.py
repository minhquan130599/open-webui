"""Telegram API (/api/v1/telegram): the user's linked Telegram account, and the bot settings for admins."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.models.users import Users
from open_webui.telegram import access, api
from open_webui.telegram.models import Accounts
from open_webui.telegram.settings import REPORTS_PAUSED, bot_ready, get_settings, public, save_settings
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()


class SettingsForm(BaseModel):
    changes: dict


class CodeForm(BaseModel):
    code: str


class VerifyForm(BaseModel):
    code: str
    otp: str


class TestForm(BaseModel):
    token: str = ''


def _account_view(account) -> dict:
    return {
        'telegram_user_id': account.telegram_user_id,
        'username': account.username,
        'display_name': account.display_name,
        'linked_at': account.linked_at,
        'last_seen_at': account.last_seen_at,
    }


# ---------------------------------------------------------------- user


@router.get('/me')
async def me(user=Depends(get_verified_user)):
    settings = await get_settings()
    account = await Accounts.by_user(user.id)
    return {
        'available': bot_ready(settings) and await access.can_use_bot(user, settings),
        'can_ask': bot_ready(settings) and await access.can_ask(user, settings),
        'reports': settings['telegram.reports'] and not REPORTS_PAUSED,
        'bot': settings['telegram.bot_username'],
        'account': _account_view(account) if account else None,
    }


MAIL_MISSING = (
    'Liên kết Telegram cần gửi mã xác nhận qua email, nhưng quản trị viên chưa cấu hình gửi email. '
    'Hãy liên hệ quản trị viên.'
)


@router.post('/me/link')
async def new_link(user=Depends(get_verified_user)):
    """A one-time code (10 minutes) inside the t.me link that opens the bot. Pressing Start there emails a second
    code to this account; the link is made when that one is entered here (/me/link/verify)."""
    from open_webui.telegram import link_mail

    settings = await get_settings()
    if not bot_ready(settings) or not await access.can_use_bot(user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Telegram chưa được bật cho tài khoản của bạn')
    bot = settings['telegram.bot_username']
    if not bot:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Quản trị viên chưa kiểm tra bot (thiếu tên bot)')
    # Checked here too (the bot checks again on Start): no point opening Telegram when the code cannot be emailed.
    if not (await link_mail.ready())[0]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=MAIL_MISSING)
    if not user.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Tài khoản chưa có email để nhận mã xác nhận')
    code = await Accounts.new_code(user.id)
    return {'code': code, 'url': f'https://t.me/{bot}?start=acc_{code}', 'bot': bot, 'minutes': 10}


@router.post('/me/link/status')
async def link_status(form: CodeForm, user=Depends(get_verified_user)):
    """Polled by the page waiting for the link: waiting for Start, then for the email code (`otp`), then linked."""
    result = await Accounts.code_status(user.id, form.code)
    account = await Accounts.by_user(user.id) if result.get('linked') else None
    return {**result, 'account': _account_view(account) if account else None}


@router.post('/me/link/verify')
async def link_verify(request: Request, form: VerifyForm, user=Depends(get_verified_user)):
    """The code from the email: only the right one, in time, links the Telegram that pressed Start."""
    otp = (form.otp or '').strip()
    if len(otp) != 6 or not otp.isdigit():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Mã xác nhận gồm 6 chữ số')
    chat_id = await Accounts.pending_chat(form.code)
    result, account, left = await Accounts.verify_otp(user.id, form.code, otp)
    if result == 'linked' and account:
        settings = await get_settings()
        try:
            from open_webui.telegram.bot import announce_linked

            await announce_linked(request.app, settings, account, chat_id)
        except Exception:
            pass  # linked anyway; the bot's message is only a courtesy
        return {'status': 'linked', 'account': _account_view(account)}
    return {'status': result, 'attempts_left': left}


@router.delete('/me')
async def unlink(user=Depends(get_verified_user)):
    account = await Accounts.unlink_user(user.id)
    if account:
        settings = await get_settings()
        if settings['telegram.bot_token']:
            try:
                await api.send_message(
                    settings['telegram.bot_token'], account.telegram_user_id,
                    'Đã hủy liên kết Telegram với tài khoản NCS AI (từ trang web).', {'remove_keyboard': True},
                )
            except api.TelegramError:
                pass
    return {'ok': bool(account)}


# ---------------------------------------------------------------- admin


@router.get('/admin/settings')
async def admin_settings(user=Depends(get_admin_user)):
    return public(await get_settings())


@router.post('/admin/settings')
async def admin_save(form: SettingsForm, user=Depends(get_admin_user)):
    try:
        values = await save_settings(form.changes)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))
    if 'telegram.bot_token' in form.changes and values['telegram.bot_token']:
        try:
            info = await api.bot_info(values['telegram.bot_token'])
            if info['username'] != values['telegram.bot_username']:
                values = await save_settings({'telegram.bot_username': info['username']})
        except api.TelegramError:
            pass
    return public(values)


@router.post('/admin/test')
async def admin_test(form: TestForm, user=Depends(get_admin_user)):
    token = form.token or (await get_settings())['telegram.bot_token']
    try:
        info = await api.bot_info(token)
    except api.TelegramError as error:
        return {'ok': False, 'error': str(error)}
    if not form.token:
        await save_settings({'telegram.bot_username': info['username']})
    return {'ok': True, **info}


@router.get('/admin/status')
async def admin_status(user=Depends(get_admin_user)):
    from open_webui.telegram.bot import poll_status

    return await poll_status()


@router.get('/admin/accounts')
async def admin_accounts(user=Depends(get_admin_user)):
    accounts = await Accounts.list()
    users = {u.id: u for u in await Users.get_users_by_user_ids([a.user_id for a in accounts])} if accounts else {}
    return [
        {
            **_account_view(a),
            'user_id': a.user_id,
            'user_name': users[a.user_id].name if a.user_id in users else '',
            'user_email': users[a.user_id].email if a.user_id in users else '',
        }
        for a in accounts
    ]


@router.delete('/admin/accounts/{user_id}')
async def admin_unlink(user_id: str, user=Depends(get_admin_user)):
    return {'ok': bool(await Accounts.unlink_user(user_id))}
