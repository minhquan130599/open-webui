"""Telegram API (/api/v1/telegram): the user's linked Telegram account, and the bot settings for admins."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from open_webui.models.users import Users
from open_webui.telegram import access, api
from open_webui.telegram.models import Accounts
from open_webui.telegram.settings import bot_ready, get_settings, public, save_settings
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()


class SettingsForm(BaseModel):
    changes: dict


class CodeForm(BaseModel):
    code: str


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
        'reports': settings['telegram.reports'],
        'bot': settings['telegram.bot_username'],
        'account': _account_view(account) if account else None,
    }


@router.post('/me/link')
async def new_link(user=Depends(get_verified_user)):
    """A one-time code (10 minutes) inside the t.me link that opens the bot."""
    settings = await get_settings()
    if not bot_ready(settings) or not await access.can_use_bot(user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Telegram chưa được bật cho tài khoản của bạn')
    bot = settings['telegram.bot_username']
    if not bot:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Quản trị viên chưa kiểm tra bot (thiếu tên bot)')
    code = await Accounts.new_code(user.id)
    return {'code': code, 'url': f'https://t.me/{bot}?start=acc_{code}', 'bot': bot, 'minutes': 10}


@router.post('/me/link/status')
async def link_status(form: CodeForm, user=Depends(get_verified_user)):
    result = await Accounts.code_status(user.id, form.code)
    account = await Accounts.by_user(user.id) if result.get('linked') else None
    return {**result, 'account': _account_view(account) if account else None}


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
