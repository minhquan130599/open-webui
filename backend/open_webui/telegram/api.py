"""Telegram Bot API calls shared by the bot and scheduled reports.

All calls go through one rate limiter per process (Telegram allows about 30 messages a second per bot and one
a second per chat), and wait when Telegram answers 429 with retry_after.
"""

import asyncio
import json
import logging
import time
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

API = 'https://api.telegram.org'
MESSAGE_CHARS = 3900  # Telegram allows 4096 per message
DOWNLOAD_LIMIT = 20 * 1024 * 1024  # bots can download files up to 20 MB
UPLOAD_LIMIT = 50 * 1024 * 1024  # and send files up to 50 MB


class TelegramError(Exception):
    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


class _Limiter:
    """At most `rate` calls a second for the whole process."""

    def __init__(self, rate: float):
        self.interval = 1 / rate
        self.next_at = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            if self.next_at > now:
                await asyncio.sleep(self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval


_limiter = _Limiter(25)


def gone_chat(error: Exception) -> bool:
    """The chat no longer receives messages from the bot (blocked, removed, deleted)."""
    text = str(error).lower()
    return any(key in text for key in ('chat not found', 'bot was kicked', 'bot was blocked', 'user is deactivated'))


async def _post(token: str, method: str, timeout: int, data=None, json_body=None) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(f'{API}/bot{token}/{method}', data=data, json=json_body) as response:
                body = await response.json(content_type=None)
                return body if isinstance(body, dict) else {'ok': False, 'description': f'HTTP {response.status}'}
    except asyncio.TimeoutError:
        raise TelegramError('Telegram không trả lời (hết thời gian chờ)')
    except aiohttp.ClientError as error:
        raise TelegramError(f'Không kết nối được Telegram: {error}')


async def call(token: str, method: str, timeout: int = 30, limited: bool = True, **params) -> dict:
    if not token:
        raise TelegramError('Chưa cấu hình bot token Telegram')
    body = {}
    for attempt in range(3):
        if limited:
            await _limiter.wait()
        body = await _post(token, method, timeout, json_body=params)
        wait = (body.get('parameters') or {}).get('retry_after') if body.get('error_code') == 429 else None
        if not wait or wait > 60 or attempt == 2:
            break
        await asyncio.sleep(wait + 1)
    if not body.get('ok'):
        raise TelegramError(body.get('description') or 'Telegram trả lỗi', body.get('error_code'))
    return body.get('result')


async def bot_info(token: str) -> dict:
    me = await call(token, 'getMe', timeout=15, limited=False)
    return {'username': me.get('username') or '', 'name': me.get('first_name') or '', 'id': me.get('id')}


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


def _plain(html_text: str) -> str:
    import html
    import re

    return html.unescape(re.sub(r'<[^>]+>', '', html_text))


async def send_message(token: str, chat_id, html_text: str, reply_markup: Optional[dict] = None, **extra) -> dict:
    """One message in Telegram HTML; sent as plain text when Telegram cannot parse the HTML."""
    params = {'chat_id': chat_id, 'text': html_text[:4096], 'parse_mode': 'HTML', 'disable_web_page_preview': True, **extra}
    if reply_markup:
        params['reply_markup'] = reply_markup
    try:
        return await call(token, 'sendMessage', **params)
    except TelegramError as error:
        text = str(error).lower()
        if "can't parse entities" in text or 'unsupported start tag' in text:
            params.pop('parse_mode')
            params['text'] = _plain(html_text)[:4096]
            return await call(token, 'sendMessage', **params)
        if 'reply_markup' in params and 'button' in text:
            params.pop('reply_markup')  # a URL Telegram does not accept: send without the buttons
            return await call(token, 'sendMessage', **params)
        raise


async def edit_message(token: str, chat_id, message_id: int, html_text: str, reply_markup: Optional[dict] = None) -> None:
    params = {
        'chat_id': chat_id,
        'message_id': message_id,
        'text': html_text[:4096],
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if reply_markup is not None:
        params['reply_markup'] = reply_markup
    try:
        await call(token, 'editMessageText', **params)
    except TelegramError as error:
        text = str(error).lower()
        if 'message is not modified' in text:
            return
        if "can't parse entities" in text:
            params.pop('parse_mode')
            params['text'] = _plain(html_text)[:4096]
            await call(token, 'editMessageText', **params)
            return
        raise


async def delete_message(token: str, chat_id, message_id: int) -> None:
    try:
        await call(token, 'deleteMessage', chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


async def answer_callback(token: str, callback_id: str, text: str = '', alert: bool = False) -> None:
    try:
        await call(token, 'answerCallbackQuery', limited=False, callback_query_id=callback_id, text=text[:190], show_alert=alert)
    except TelegramError:
        pass


async def chat_action(token: str, chat_id, action: str = 'typing') -> None:
    try:
        await call(token, 'sendChatAction', limited=False, chat_id=chat_id, action=action)
    except TelegramError:
        pass


async def send_document(token: str, chat_id, name: str, data: bytes, content_type: str, caption: str = '',
                        reply_markup: Optional[dict] = None, photo: bool = False) -> dict:
    """A file (multipart upload); `photo` sends an image shown inline."""
    if len(data) > UPLOAD_LIMIT:
        raise TelegramError('File lớn hơn 50 MB, Telegram không cho bot gửi')
    form = aiohttp.FormData()
    form.add_field('chat_id', str(chat_id))
    if caption:
        form.add_field('caption', caption[:1024])
        form.add_field('parse_mode', 'HTML')
    if reply_markup:
        form.add_field('reply_markup', json.dumps(reply_markup))
    field = 'photo' if photo else 'document'
    form.add_field(field, data, filename=name, content_type=content_type or 'application/octet-stream')
    await _limiter.wait()
    body = await _post(token, 'sendPhoto' if photo else 'sendDocument', 120, data=form)
    if not body.get('ok'):
        raise TelegramError(body.get('description') or 'Telegram không nhận file', body.get('error_code'))
    return body.get('result')


async def download_file(token: str, file_id: str, limit: int = DOWNLOAD_LIMIT) -> bytes:
    info = await call(token, 'getFile', limited=False, file_id=file_id)
    if (info.get('file_size') or 0) > limit:
        raise TelegramError('File lớn hơn 20 MB: Telegram không cho bot tải. Hãy tải lên trên web.')
    path = info.get('file_path')
    if not path:
        raise TelegramError('Telegram không trả đường dẫn file')
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            async with session.get(f'{API}/file/bot{token}/{path}') as response:
                if response.status != 200:
                    raise TelegramError(f'Không tải được file (HTTP {response.status})')
                data = await response.content.read(limit + 1)
    except aiohttp.ClientError as error:
        raise TelegramError(f'Không tải được file: {error}')
    if len(data) > limit:
        raise TelegramError('File lớn hơn 20 MB')
    return data


async def get_updates(token: str, offset: int, timeout: int) -> list:
    body = await _post(
        token,
        'getUpdates',
        timeout + 15,
        json_body={'offset': offset, 'timeout': timeout, 'allowed_updates': ['message', 'callback_query']},
    )
    if not body.get('ok'):
        raise TelegramError(body.get('description') or 'getUpdates lỗi', body.get('error_code'))
    return body.get('result') or []


async def set_commands(token: str, commands: list[tuple[str, str]]) -> None:
    try:
        await call(token, 'setMyCommands', commands=[{'command': c, 'description': d} for c, d in commands])
    except TelegramError as error:
        log.warning('Telegram: setMyCommands failed (%s)', error)
