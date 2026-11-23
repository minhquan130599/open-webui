"""Telegram Bot API: check the bot, send report messages, and link chats.

A user links a chat by sending the bot a one-time code: privately ("/start <code>", which the t.me link does)
or in a group the bot was added to ("/link <code>"). One worker at a time reads the bot's updates (a lease in
report_lock), so several app workers never compete for them.
"""

import asyncio
import html
import logging
from typing import Optional

import aiohttp

from open_webui.reports.models import Locks, TelegramLinks

log = logging.getLogger(__name__)

API = 'https://api.telegram.org'
MESSAGE_CHARS = 3900  # Telegram allows 4096 per message
POLL_LOCK = 'telegram_poll'
POLL_SECONDS = 25


class TelegramError(Exception):
    pass


async def call(token: str, method: str, timeout: int = 30, **params) -> dict:
    if not token:
        raise TelegramError('Chưa cấu hình bot token Telegram')
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(f'{API}/bot{token}/{method}', json=params) as response:
                    body = await response.json(content_type=None)
        except asyncio.TimeoutError:
            raise TelegramError('Telegram không trả lời (hết thời gian chờ)')
        except aiohttp.ClientError as error:
            raise TelegramError(f'Không kết nối được Telegram: {error}')
        # Too many messages: Telegram says how long to wait before sending again.
        wait = (body.get('parameters') or {}).get('retry_after') if body.get('error_code') == 429 else None
        if not wait or wait > 60 or attempt == 2:
            break
        await asyncio.sleep(wait + 1)
    if not body.get('ok'):
        raise TelegramError(body.get('description') or f'Telegram trả lỗi {response.status}')
    return body.get('result')


async def bot_info(token: str) -> dict:
    me = await call(token, 'getMe', timeout=15)
    return {'username': me.get('username') or '', 'name': me.get('first_name') or ''}


def split_message(text: str, limit: int = MESSAGE_CHARS) -> list[str]:
    """Pieces of at most `limit` characters, cut at line breaks."""
    parts, current = [], ''
    for line in text.split('\n'):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ''
            parts.append(line[:limit])
            line = line[limit:]
        if current and len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = ''
        current = f'{current}\n{line}' if current else line
    if current.strip():
        parts.append(current)
    return parts


async def send(token: str, chat_id: str, html_text: str, button: Optional[tuple[str, str]] = None) -> None:
    """Send a report (Telegram HTML), split into several messages when long; the button goes on the last one."""
    parts = split_message(html_text)
    for index, part in enumerate(parts):
        params = {'chat_id': chat_id, 'text': part, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
        if button and index == len(parts) - 1 and button[1].startswith(('http://', 'https://')):
            params['reply_markup'] = {'inline_keyboard': [[{'text': button[0], 'url': button[1]}]]}
        try:
            await call(token, 'sendMessage', **params)
        except TelegramError as error:
            if 'reply_markup' in params and 'button' in str(error).lower():
                params.pop('reply_markup')  # a URL Telegram does not accept: send without the button
                await call(token, 'sendMessage', **params)
            elif any(key in str(error).lower() for key in ('chat not found', 'bot was kicked', 'bot was blocked')):
                await TelegramLinks.forget_chat(str(chat_id))
                raise TelegramError(f'Chat Telegram không còn nhận được tin (bot đã bị xóa / chặn): {error}')
            else:
                raise


async def _handle(token: str, update: dict, allow_groups: bool) -> None:
    message = update.get('message') or update.get('channel_post') or {}
    text = (message.get('text') or '').strip()
    chat = message.get('chat') or {}
    if not text.startswith(('/start', '/link')) or not chat.get('id'):
        return
    words = text.split()
    code = words[1] if len(words) > 1 else ''
    chat_type = chat.get('type') or 'private'
    if not code:
        if chat_type == 'private':
            await call(token, 'sendMessage', chat_id=chat['id'], text='Chào bạn! Vào NCS AI → Báo cáo định kỳ → Liên kết Telegram để nhận báo cáo tại đây.')
        return
    if chat_type != 'private' and not allow_groups:
        await call(token, 'sendMessage', chat_id=chat['id'], text='NCS AI tạm thời chưa gửi báo cáo vào nhóm Telegram. Hãy liên kết chat riêng với bot.')
        return
    title = chat.get('title') or ' '.join(x for x in (chat.get('first_name'), chat.get('last_name')) if x) or str(chat['id'])
    link = await TelegramLinks.confirm(code.strip(), str(chat['id']), title, chat_type)
    reply = (
        f'Đã liên kết {"nhóm " + html.escape(title) if chat_type != "private" else "chat này"} với NCS AI. '
        'Báo cáo định kỳ chọn chat này sẽ được gửi tới đây.'
        if link
        else 'Mã liên kết không đúng hoặc đã hết hạn. Tạo mã mới trong NCS AI → Báo cáo định kỳ.'
    )
    await call(token, 'sendMessage', chat_id=chat['id'], text=reply)


async def poll_once(settings: dict, state: dict) -> dict:
    """Read new updates once (long poll); returns the state to keep (the next update offset)."""
    token = settings['reports.telegram.bot_token']
    updates = await _get_updates(token, state.get('offset', 0))
    for update in updates or []:
        state['offset'] = max(state.get('offset', 0), int(update.get('update_id', 0)) + 1)
        try:
            await _handle(token, update, settings['reports.telegram.allow_groups'])
        except Exception as error:
            log.warning('Telegram: update not handled (%s)', error)
    return state


async def _get_updates(token: str, offset: int) -> list:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=POLL_SECONDS + 15)) as session:
            async with session.post(
                f'{API}/bot{token}/getUpdates',
                json={'offset': offset, 'timeout': POLL_SECONDS, 'allowed_updates': ['message', 'channel_post']},
            ) as response:
                body = await response.json(content_type=None)
    except (asyncio.TimeoutError, aiohttp.ClientError) as error:
        raise TelegramError(f'Không đọc được tin Telegram: {error}')
    if not body.get('ok'):
        raise TelegramError(body.get('description') or 'getUpdates lỗi')
    return body.get('result') or []


async def poll_loop(get_settings) -> None:
    """Keep reading the bot's updates while Telegram is enabled; one worker at a time."""
    lease_id = None
    while True:
        try:
            settings = await get_settings()
            if not (settings['reports.enable'] and settings['reports.telegram.enable'] and settings['reports.telegram.bot_token']):
                lease_id = None
                await asyncio.sleep(30)
                continue
            held = await Locks.acquire(POLL_LOCK, POLL_SECONDS * 3, lease_id)
            if not held:
                lease_id = None
                await asyncio.sleep(20)
                continue
            lease_id = held['lease_id']
            state = await poll_once(settings, held['data'])
            await Locks.save(POLL_LOCK, lease_id, state)
        except asyncio.CancelledError:
            raise
        except TelegramError as error:
            log.warning('Telegram poll: %s', error)
            await asyncio.sleep(30)
        except Exception:
            log.exception('Telegram poll failed')
            await asyncio.sleep(30)
