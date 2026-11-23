"""The bot: reads Telegram updates (one worker at a time, a lease in report_lock), handles commands and buttons,
and answers questions as the linked user.

Many users at once:
- each update is handled in its own task, so a long answer never blocks the others;
- one question at a time per Telegram user (a second one is refused politely, or the first can be stopped);
- at most `telegram.max_running` answers at once for the whole app; others wait their turn (told so);
- `telegram.rate_per_minute` messages per user per minute;
- sends go through one rate limiter (api.py) and the progress message is edited at most every 2 seconds.

Security: the sender is `from.id`; the linked account is checked again on every update (active, allowed);
every id coming back from a button or a reply is checked against the user's access before use; groups are not
answered (only the legacy report /link, which stays switched off).
"""

import asyncio
import html
import logging
import time
import uuid
from typing import Optional

from open_webui.reports.models import Locks
from open_webui.telegram import access, api, ask
from open_webui.telegram import format as fmt
from open_webui.telegram import keyboards as kb
from open_webui.telegram.models import Accounts, MessageMap, Sessions
from open_webui.telegram.settings import get_settings

log = logging.getLogger(__name__)

POLL_LOCK = 'telegram_poll'
POLL_SECONDS = 25
TEXT_LIMIT = 4000
COMMANDS = [
    ('start', 'Bắt đầu / menu chính'),
    ('moi', 'Cuộc trò chuyện mới'),
    ('kenh', 'Chọn kênh để hỏi (/kenh tên để tìm)'),
    ('truc_tiep', 'Về hỏi trực tiếp'),
    ('model', 'Đổi model'),
    ('caidat', 'Cài đặt'),
    ('help', 'Hướng dẫn'),
]
HELP = (
    '<b>Cách dùng trợ lý NCS AI trên Telegram</b>\n'
    '• Gõ câu hỏi bất kỳ để hỏi AI (như chat riêng trên web).\n'
    '• <b>📁 Hỏi trong kênh</b>: chọn kênh, câu hỏi được đăng vào kênh dưới tên bạn và AI trả lời theo dữ liệu kênh '
    '(như gọi @Knight trên web). Trả lời (reply) vào tin của bot để hỏi tiếp trong cùng luồng.\n'
    '• Gửi file / ảnh kèm câu hỏi để AI đọc file.\n'
    '• Dưới mỗi câu trả lời: <b>📄 PDF</b> / <b>📝 Word</b> để nhận file, <b>🔁 Trả lời lại</b>.\n'
    '• <b>🆕 Cuộc trò chuyện mới</b> để bắt đầu lại; <b>⚙️ Cài đặt</b> để đổi model hoặc hủy liên kết.\n'
    'Bot chỉ làm được những gì tài khoản NCS AI của bạn được phép.'
)

STATUS: dict = {}
_tasks: set = set()
_running: dict[str, dict] = {}  # telegram user id -> running answer
_uploading: set = set()  # telegram user ids receiving a file
_semaphore: Optional[asyncio.Semaphore] = None
_semaphore_size = 0
_waiting = 0


_recent_updates: dict[int, None] = {}
_already_told: dict[str, float] = {}


def _seen(update_id) -> bool:
    """True when this update was already handled by this worker (kept for the last 5000 updates)."""
    if update_id is None:
        return False
    if update_id in _recent_updates:
        return True
    _recent_updates[update_id] = None
    while len(_recent_updates) > 5000:
        _recent_updates.pop(next(iter(_recent_updates)))
    return False


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _gate(size: int) -> asyncio.Semaphore:
    global _semaphore, _semaphore_size
    if _semaphore is None or size != _semaphore_size:
        _semaphore, _semaphore_size = asyncio.Semaphore(size), size
    return _semaphore


async def _base() -> str:
    from open_webui.reports.build import public_url

    return await public_url()


class Ctx:
    """One update: the bot token, the chat, the sender, the linked user and the chat session."""

    def __init__(self, app, settings: dict, chat_id, sender: dict):
        self.app = app
        self.settings = settings
        self.token = settings['telegram.bot_token']
        self.chat_id = chat_id
        self.sender = sender
        self.tg_id = str(sender.get('id'))
        self.account = None
        self.user = None
        self.session = None
        self._request = None

    async def request(self):
        if self._request is None:
            self._request = await access.user_request(self.app, self.user)
        return self._request

    async def send(self, text: str, markup: Optional[dict] = None, **extra) -> Optional[dict]:
        try:
            return await api.send_message(self.token, self.chat_id, text, markup, **extra)
        except api.TelegramError as error:
            if markup and ('url' in str(error).lower() or 'button' in str(error).lower()):
                return await api.send_message(self.token, self.chat_id, text, kb.strip_urls(markup), **extra)
            if api.gone_chat(error):
                log.info('Telegram: chat %s no longer reachable', self.chat_id)
                return None
            raise

    async def edit(self, message_id: int, text: str, markup: Optional[dict] = None) -> None:
        try:
            await api.edit_message(self.token, self.chat_id, message_id, text, markup)
        except api.TelegramError as error:
            if markup and ('url' in str(error).lower() or 'button' in str(error).lower()):
                await api.edit_message(self.token, self.chat_id, message_id, text, kb.strip_urls(markup))
            else:
                raise


# ---------------------------------------------------------------- polling


async def bot_loop(app) -> None:
    """Read the bot's updates while a token is set; one worker at a time across the app."""
    lease_id = None
    commands_for = None
    state: dict = {}
    log.info('Telegram bot loop started')
    while True:
        try:
            state = {}
            settings = await get_settings()
            token = settings['telegram.bot_token']
            if not token or not (settings['telegram.reports'] or settings['telegram.ask']):
                lease_id = None
                await asyncio.sleep(30)
                continue
            held = await Locks.acquire(POLL_LOCK, POLL_SECONDS * 3, lease_id)
            if not held:
                lease_id = None
                await asyncio.sleep(20)
                continue
            lease_id = held['lease_id']
            state = held['data'] or {}
            if commands_for != token:
                await api.set_commands(token, COMMANDS)
                commands_for = token
            updates = await api.get_updates(token, int(state.get('offset', 0)), POLL_SECONDS)
            offset = int(state.get('offset', 0))
            for update in updates:
                offset = max(offset, int(update.get('update_id', 0)) + 1)
            # Saved before handling: an update is handled at most once even if this worker stops. When the save
            # does not go through (the lease moved to another worker meanwhile), nothing is handled here: the
            # worker holding the lease reads the same updates and handles them once.
            state.update({'offset': offset, 'last_ok': int(time.time()), 'error': None})
            if not await Locks.save(POLL_LOCK, lease_id, state):
                log.warning('Telegram: lost the reading lease before saving; updates left to the other worker')
                lease_id = None
                continue
            STATUS.update(state)
            for update in updates:
                if _seen(update.get('update_id')):
                    log.warning('Telegram: update %s came again; ignored', update.get('update_id'))
                    continue
                _spawn(_handle_update(app, settings, update))
        except asyncio.CancelledError:
            raise
        except api.TelegramError as error:
            note = {'error': str(error), 'error_at': int(time.time())}
            if error.code == 409:
                note['conflict_at'] = int(time.time())
                log.warning('Telegram: another app reads this bot (409 Conflict). Use one bot per environment.')
            else:
                log.warning('Telegram poll: %s', error)
            STATUS.update(note)
            if lease_id and state.get('offset') is not None:
                try:  # the offset read with this lease, never an older one kept in memory
                    await Locks.save(POLL_LOCK, lease_id, {**state, **note})
                except Exception:
                    pass
            await asyncio.sleep(10 if error.code == 409 else 30)
        except Exception:
            log.exception('Telegram poll failed')
            await asyncio.sleep(30)


async def poll_status() -> dict:
    """Last state saved by the worker reading the updates (for the admin page)."""
    from open_webui.internal.db import get_async_db_context
    from open_webui.reports.models import ReportLock

    async with get_async_db_context() as db:
        row = await db.get(ReportLock, POLL_LOCK)
        data = dict(row.data or {}) if row else {}
        data['reading'] = bool(row and row.lease_until and row.lease_until > time.time_ns())
        return data


# ---------------------------------------------------------------- updates


async def _handle_update(app, settings: dict, update: dict) -> None:
    message = update.get('message') or {}
    log.debug('Telegram: update %s (%s)', update.get('update_id'), 'button' if 'callback_query' in update else (message.get('text') or '')[:20])
    try:
        if 'callback_query' in update:
            await _on_callback(app, settings, update['callback_query'])
        elif 'message' in update:
            await _on_message(app, settings, update['message'])
    except Exception:
        log.exception('Telegram: update %s not handled', update.get('update_id'))


async def _load(ctx: Ctx) -> bool:
    """Resolve the linked user; tells the sender what to do when not linked / not allowed."""
    ctx.account, ctx.user = await access.resolve(ctx.tg_id)
    if not ctx.account:
        base = await _base()
        await ctx.send(
            'Chào bạn 👋 Đây là trợ lý NCS AI.\nĐể hỏi AI và nhận báo cáo tại đây, hãy liên kết với tài khoản NCS AI: '
            'đăng nhập web → <b>Cài đặt → Tài khoản → Telegram</b> → <b>Liên kết Telegram</b>.',
            kb.link_account(f'{base}/?settings=account' if base else ''),
        )
        return False
    if not ctx.user:
        await ctx.send('Tài khoản NCS AI đã liên kết hiện không dùng được (bị khóa hoặc chưa được duyệt). Liên hệ quản trị viên.')
        return False
    ctx.session = await Sessions.get(str(ctx.chat_id), ctx.tg_id)
    return True


async def _on_message(app, settings: dict, message: dict) -> None:
    chat = message.get('chat') or {}
    sender = message.get('from') or {}
    if not chat.get('id') or not sender.get('id') or sender.get('is_bot'):
        return
    text = (message.get('text') or message.get('caption') or '').strip()
    if chat.get('type') != 'private':
        if text.startswith('/link'):
            await _legacy_report_link(settings, message, text)
        return  # questions in groups are not answered
    ctx = Ctx(app, settings, chat['id'], sender)
    # Counted before anything else (also for unlinked senders and /start): nobody can flood the bot.
    if not access.rate_ok(ctx.tg_id, settings['telegram.rate_per_minute']):
        if access.rate_ok(f'{ctx.tg_id}:warned', 1):
            await ctx.send('Bạn gửi hơi nhanh, đợi một chút rồi gửi lại nhé.')
        return

    if text.startswith('/start'):
        code = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ''
        if code.startswith('acc_'):
            await _link_account(ctx, code[4:])
            return
        if code:
            await _legacy_report_link(settings, message, f'/start {code}')
            return
        if await _load(ctx):
            await _welcome(ctx)
        return

    if not await _load(ctx):
        return
    await Accounts.touch(ctx.tg_id, sender.get('username') or '', _display(sender))

    command = text.split()[0].split('@')[0].lower() if text.startswith('/') else ''
    if command in ('/help',):
        await ctx.send(HELP, _keyboard(ctx))
        return
    if command == '/caidat' or text == kb.SETTINGS:
        await _settings(ctx)
        return
    can_ask = await access.can_ask(ctx.user, settings)
    if not can_ask:
        await ctx.send(
            'Tài khoản của bạn chưa được dùng hỏi đáp AI qua Telegram (quản trị viên chưa cấp quyền). '
            'Bạn vẫn nhận được báo cáo định kỳ tại đây.',
            kb.remove_keyboard(),
        )
        return
    if command == '/moi' or text == kb.NEW_CHAT:
        await _new_chat(ctx)
        return
    if command == '/truc_tiep' or text == kb.ASK_DIRECT or text == kb.BACK_DIRECT:
        await _to_direct(ctx)
        return
    if command == '/kenh' or text == kb.ASK_CHANNEL or text == kb.CHANGE_CHANNEL:
        query = text.split(maxsplit=1)[1] if command == '/kenh' and len(text.split(maxsplit=1)) > 1 else ''
        await _pick_channel(ctx, 0, query)
        return
    if command == '/model':
        await _pick_model(ctx)
        return
    if command:
        await ctx.send('Không có lệnh này. Gõ câu hỏi hoặc dùng các nút bên dưới.', _keyboard(ctx))
        return
    if text == kb.EXPORT:
        if ctx.session.mode != 'channel':
            await ctx.send('Chọn kênh trước khi xuất báo cáo.', _keyboard(ctx))
            return
        await ctx.send('Chọn định dạng báo cáo:', kb.export_formats())
        return
    if text in kb.PRESETS:
        if ctx.session.mode != 'channel':
            await ctx.send('Chọn kênh trước.', _keyboard(ctx))
            return
        text = kb.PRESETS[text]

    files = []
    if message.get('document') or message.get('photo'):
        if ctx.tg_id in _uploading or ctx.tg_id in _running:
            await ctx.send('⏳ Đang xử lý việc trước của bạn, gửi file lại sau ít phút nhé.')
            return
        _uploading.add(ctx.tg_id)
        try:
            entry = await _receive_file(ctx, message)
        finally:
            _uploading.discard(ctx.tg_id)
        if not entry:
            return
        files.append(entry)
        if not text:
            ctx.session = await Sessions.get(str(ctx.chat_id), ctx.tg_id)  # fresh: another file may have arrived
            data = dict(ctx.session.data or {})
            data['pending_files'] = [*(data.get('pending_files') or []), entry][-5:]
            ctx.session.data = data
            await Sessions.save(ctx.session)
            await ctx.send(f'Đã nhận file <b>{html.escape(entry["name"])}</b>. Bạn muốn làm gì?', kb.file_actions())
            return
    if not text:
        return
    if len(text) > TEXT_LIMIT:
        await ctx.send(f'Câu hỏi dài quá ({len(text)} ký tự). Rút gọn dưới {TEXT_LIMIT} ký tự, hoặc gửi nội dung dạng file.')
        return
    pending = (ctx.session.data or {}).get('pending_files') or []
    if pending:
        files = [*pending, *files]
        data = dict(ctx.session.data or {})
        data.pop('pending_files', None)
        ctx.session.data = data

    target = None
    reply = message.get('reply_to_message')
    if reply and (reply.get('from') or {}).get('is_bot'):
        target = await MessageMap.get(str(ctx.chat_id), reply.get('message_id'))
    await _ask(ctx, text, files, target)


def _display(sender: dict) -> str:
    return ' '.join(x for x in (sender.get('first_name'), sender.get('last_name')) if x) or sender.get('username') or ''


def _keyboard(ctx: Ctx) -> dict:
    if ctx.session and ctx.session.mode == 'channel' and (ctx.session.data or {}).get('channel_name'):
        return kb.channel_keyboard(ctx.session.data['channel_name'])
    return kb.main_keyboard()


async def _welcome(ctx: Ctx) -> None:
    name = html.escape(ctx.user.name or '')
    can_ask = await access.can_ask(ctx.user, ctx.settings)
    lines = [f'Chào <b>{name}</b> 👋']
    if can_ask:
        lines.append('Gõ câu hỏi bất kỳ để hỏi AI, hoặc chọn bên dưới.')
    if ctx.settings['telegram.reports']:
        lines.append('Báo cáo định kỳ chọn "Telegram của tôi" sẽ được gửi vào đây.')
    await ctx.send('\n'.join(lines), _keyboard(ctx) if can_ask else kb.remove_keyboard())


# ---------------------------------------------------------------- linking


async def _link_account(ctx: Ctx, code: str) -> None:
    if not code or len(code) > 64:
        await ctx.send('Mã liên kết không đúng.')
        return
    status, account = await Accounts.confirm(code, ctx.tg_id, ctx.sender.get('username') or '', _display(ctx.sender))
    log.info('Telegram: link code from %s: %s', ctx.tg_id, status)
    if status == 'already':
        # The same link opened again (browser and app both open it, or Start pressed twice): say it once.
        key = f'{ctx.tg_id}:{code}'
        if time.monotonic() - _already_told.get(key, -1e9) < 600:
            return
        _already_told[key] = time.monotonic()
    if status == 'invalid':
        await ctx.send('Mã liên kết không đúng hoặc đã hết hạn (10 phút). Vào web bấm <b>Liên kết Telegram</b> để lấy mã mới.')
        return
    ctx.account, ctx.user = account, await access.active_user(account.user_id)
    if not ctx.user:
        await Accounts.unlink_user(account.user_id)
        await ctx.send('Tài khoản NCS AI này hiện không dùng được (bị khóa hoặc chưa được duyệt).')
        return
    ctx.session = await Sessions.get(str(ctx.chat_id), ctx.tg_id)
    if status == 'already':
        await ctx.send('Telegram này đã liên kết với tài khoản rồi.', _keyboard(ctx))
        return
    log.info('Telegram: account %s linked to user %s', ctx.tg_id, account.user_id)
    can_ask = await access.can_ask(ctx.user, ctx.settings)
    lines = [f'✅ Đã liên kết với <b>{html.escape(ctx.user.name or "")}</b> ({html.escape(ctx.user.email or "")}).']
    if can_ask:
        lines.append('Gõ câu hỏi bất kỳ để hỏi AI, hoặc chọn bên dưới.')
    if ctx.settings['telegram.reports']:
        lines.append('Báo cáo định kỳ chọn "Telegram của tôi" sẽ được gửi vào đây.')
    await ctx.send('\n'.join(lines), _keyboard(ctx) if can_ask else kb.remove_keyboard())


async def _legacy_report_link(settings: dict, message: dict, text: str) -> None:
    """Report chats linked before accounts existed: "/start <code>" (private) or "/link <code>" (group)."""
    from open_webui.reports.models import TelegramLinks
    from open_webui.reports.settings import get_settings as report_settings

    chat = message.get('chat') or {}
    token = settings['telegram.bot_token']
    words = text.split()
    code = words[1] if len(words) > 1 else ''
    chat_type = chat.get('type') or 'private'
    if not code:
        return
    if chat_type != 'private' and not (await report_settings())['reports.telegram.allow_groups']:
        await api.send_message(token, chat['id'], 'NCS AI tạm thời chưa gửi báo cáo vào nhóm Telegram. Hãy liên kết chat riêng với bot.')
        return
    title = chat.get('title') or _display(message.get('from') or {}) or str(chat['id'])
    link = await TelegramLinks.confirm(code.strip(), str(chat['id']), title, chat_type)
    reply = (
        f'Đã liên kết {"nhóm " + html.escape(title) if chat_type != "private" else "chat này"} với NCS AI. '
        'Báo cáo định kỳ chọn chat này sẽ được gửi tới đây.'
        if link
        else 'Mã liên kết không đúng hoặc đã hết hạn. Tạo mã mới trong NCS AI → Báo cáo định kỳ.'
    )
    await api.send_message(token, chat['id'], reply)


# ---------------------------------------------------------------- modes and menus


async def _new_chat(ctx: Ctx) -> None:
    ctx.session.chat_ref = None
    ctx.session.last_message_id = None
    if ctx.session.mode == 'channel':
        ctx.session.mode = 'direct'
        ctx.session.channel_id = None
    await Sessions.save(ctx.session)
    await ctx.send('🆕 Đã bắt đầu cuộc trò chuyện mới. Gõ câu hỏi nhé.', kb.main_keyboard())


async def _to_direct(ctx: Ctx) -> None:
    ctx.session.mode = 'direct'
    ctx.session.channel_id = None
    await Sessions.save(ctx.session)
    await ctx.send('💬 Đang hỏi trực tiếp. Gõ câu hỏi nhé.', kb.main_keyboard())


async def _pick_channel(ctx: Ctx, page: int, query: str = '', edit_id: Optional[int] = None) -> None:
    if not ctx.settings['telegram.ask_channels']:
        await ctx.send('Quản trị viên chưa bật hỏi trong kênh qua Telegram.', _keyboard(ctx))
        return
    channels = await ask.user_channels(ctx.user, ctx.settings)
    if query:
        needle = query.lower().lstrip('#')
        channels = [c for c in channels if needle in (c.name or '').lower()]
    if not channels:
        await ctx.send('Không tìm thấy kênh nào bạn được hỏi.' if query else 'Bạn chưa ở kênh nào có thể hỏi.', _keyboard(ctx))
        return
    by_id = {c.id: c for c in channels}
    recent = [by_id[i] for i in (ctx.session.data or {}).get('recent', []) if i in by_id] if not query else []
    text = 'Chọn kênh để hỏi' + (f' (lọc "{html.escape(query)}")' if query else ' — gõ <code>/kenh tên</code> để tìm') + ':'
    markup = kb.channel_picker(channels, recent, page)
    if edit_id:
        await ctx.edit(edit_id, text, markup)
    else:
        await ctx.send(text, markup)


async def _choose_channel(ctx: Ctx, channel_id: str) -> None:
    channel = await ask.get_channel_for(ctx.user, channel_id, ctx.settings)
    if not channel:
        await ctx.send('Bạn không còn hỏi được trong kênh này.', _keyboard(ctx))
        return
    data = dict(ctx.session.data or {})
    data['recent'] = [channel.id, *[i for i in data.get('recent', []) if i != channel.id]][:4]
    data['channel_name'] = channel.name
    ctx.session.data = data
    ctx.session.mode = 'channel'
    ctx.session.channel_id = channel.id
    await Sessions.save(ctx.session)
    await ctx.send(
        f'📁 Đang hỏi trong <b>#{html.escape(channel.name)}</b>.\n'
        'Câu hỏi sẽ được đăng vào kênh dưới tên bạn (thành viên kênh thấy được), giống gọi AI trên web.',
        kb.channel_keyboard(channel.name),
    )


async def _pick_model(ctx: Ctx) -> None:
    request = await ctx.request()
    models = await ask.allowed_models(request, ctx.user)
    if not models:
        await ctx.send('Bạn chưa được dùng model nào.')
        return
    current = ask.default_model_id(ctx.user, ctx.settings, models, ctx.session.model_id)
    data = dict(ctx.session.data or {})
    data['models'] = [m['id'] for m in models[:40]]
    ctx.session.data = data
    await Sessions.save(ctx.session)
    await ctx.send('Chọn model:', kb.model_picker(models, current))


async def _settings(ctx: Ctx) -> None:
    can_ask = await access.can_ask(ctx.user, ctx.settings)
    lines = ['<b>⚙️ Cài đặt</b>', f'Tài khoản: {html.escape(ctx.user.name or "")} ({html.escape(ctx.user.email or "")})']
    if can_ask:
        models = await ask.allowed_models(await ctx.request(), ctx.user)
        current = ask.default_model_id(ctx.user, ctx.settings, models, ctx.session.model_id)
        lines.append(f'Model: {html.escape(ask.model_name(models, current) if current else "chưa có")}')
        if ctx.session.mode == 'channel' and (ctx.session.data or {}).get('channel_name'):
            lines.append(f'Đang hỏi trong: #{html.escape(ctx.session.data["channel_name"])}')
    if ctx.settings['telegram.reports']:
        lines.append(f'Báo cáo định kỳ nhận tại đây: {len(await _schedules_here(ctx))} lịch')
    await ctx.send('\n'.join(lines), kb.settings_menu(ctx.settings['telegram.reports'], can_ask))


async def _schedules_here(ctx: Ctx) -> list:
    from open_webui.reports.models import Schedules, TelegramLinks

    mine = {l.id for l in await TelegramLinks.list(ctx.user.id) if l.chat_id == str(ctx.chat_id)}
    out = []
    for schedule in await Schedules.list(ctx.user.id):
        tg = (schedule.config or {}).get('telegram') or {}
        ids = set(tg.get('link_ids') or [])
        if tg.get('enabled') and ('account' in ids or ids & mine):
            out.append(schedule)
    return out


# ---------------------------------------------------------------- callbacks


async def _on_callback(app, settings: dict, callback: dict) -> None:
    sender = callback.get('from') or {}
    message = callback.get('message') or {}
    chat = message.get('chat') or {}
    data = callback.get('data') or ''
    token = settings['telegram.bot_token']
    if chat.get('type') != 'private' or not sender.get('id'):
        await api.answer_callback(token, callback.get('id', ''))
        return
    ctx = Ctx(app, settings, chat['id'], sender)
    if not access.rate_ok(f'{ctx.tg_id}:buttons', 60):
        await api.answer_callback(token, callback.get('id', ''), 'Bạn bấm hơi nhanh, đợi một chút nhé.')
        return
    if not await _load(ctx):
        await api.answer_callback(token, callback.get('id', ''))
        return
    answered = False

    async def note(text: str = '', alert: bool = False):
        nonlocal answered
        if not answered:
            answered = True
            await api.answer_callback(token, callback.get('id', ''), text, alert)

    try:
        if data == 'noop':
            return
        if data == 'help':
            await note()
            await ctx.send(HELP, _keyboard(ctx))
            return
        if data.startswith('unl'):
            await note()
            if data == 'unl':
                await ctx.send('Hủy liên kết Telegram với tài khoản NCS AI? Bạn sẽ không hỏi AI và không nhận báo cáo tại đây nữa.', kb.confirm_unlink())
            elif data == 'unl:yes':
                await Accounts.unlink_user(ctx.user.id)
                log.info('Telegram: account %s unlinked by the user', ctx.tg_id)
                await ctx.send('Đã hủy liên kết. Muốn dùng lại, vào web bấm <b>Liên kết Telegram</b>.', kb.remove_keyboard())
            else:
                await ctx.send('Đã giữ liên kết.', _keyboard(ctx))
            return
        if data == 'rcv':
            await note()
            schedules = await _schedules_here(ctx)
            base = await _base()
            lines = ['<b>📬 Báo cáo định kỳ nhận tại đây</b>']
            lines += [f'• {html.escape(s.name)}' for s in schedules] or ['Chưa có lịch nào gửi vào Telegram này.']
            button = kb.url_button('🗓️ Tạo / sửa lịch trên web', f'{base}/reports' if base else '')
            await ctx.send('\n'.join(lines), kb.inline([[button]] if button else []))
            return

        if not await access.can_ask(ctx.user, settings):
            await note('Tài khoản chưa được dùng hỏi đáp AI qua Telegram', True)
            return
        if data.startswith('stop:'):
            running = _running.get(ctx.tg_id)
            if running and running.get('key') == data[5:]:
                running['stopped'] = True
                if running.get('handle'):
                    await ask.stop(app, running['handle'])
                await note('Đã dừng')
            else:
                await note('Câu trả lời đã xong')
            return
        if data.startswith('chp:'):
            await note()
            await _pick_channel(ctx, int(data[4:] or 0), edit_id=message.get('message_id'))
            return
        if data.startswith('ch:'):
            await note()
            await _choose_channel(ctx, data[3:])
            return
        if data == 'mdl':
            await note()
            await _pick_model(ctx)
            return
        if data.startswith('m:'):
            ids = (ctx.session.data or {}).get('models') or []
            index = int(data[2:]) if data[2:].isdigit() else -1
            models = await ask.allowed_models(await ctx.request(), ctx.user)
            allowed = {m['id']: m for m in models}
            if not 0 <= index < len(ids) or ids[index] not in allowed:
                await note('Model này không còn dùng được', True)
                return
            ctx.session.model_id = ids[index]
            await Sessions.save(ctx.session)
            await note('Đã đổi model')
            await ctx.send(f'🤖 Model: <b>{html.escape(allowed[ids[index]].get("name") or ids[index])}</b>', _keyboard(ctx))
            return
        if data.startswith('rep:'):
            kind = data[4:]
            if kind not in kb.EXPORT_FORMATS or ctx.session.mode != 'channel':
                await note('Chọn kênh trước', True)
                return
            await note('Đang lập báo cáo…')
            await _ask(ctx, kb.EXPORT_PROMPT.format(label=kb.EXPORT_FORMATS[kind], fmt=kind), [], None)
            return
        if data.startswith('fa:'):
            action = data[3:]
            pending = (ctx.session.data or {}).get('pending_files') or []
            if not pending:
                await note('Không còn file đang chờ', True)
                return
            if action == 'wait':
                await note()
                await ctx.send('Gõ câu hỏi về file, mình sẽ dùng file vừa gửi.')
                return
            if action in kb.FILE_ACTIONS:
                await note()
                session_data = dict(ctx.session.data or {})
                session_data.pop('pending_files', None)
                ctx.session.data = session_data
                await _ask(ctx, kb.FILE_ACTIONS[action][1], pending, None)
            return

        # Buttons under an answer: the mapped answer, checked against the user's access again.
        target = await MessageMap.get(str(ctx.chat_id), message.get('message_id'))
        if data in ('x:pdf', 'x:docx', 'rg') and not target:
            await note('Tin này đã cũ, không còn thao tác được', True)
            return
        if data in ('x:pdf', 'x:docx'):
            await _export(ctx, target, data[2:], note)
            return
        if data == 'rg':
            if not target.question:
                await note('Không còn câu hỏi gốc', True)
                return
            await note()
            await _ask(ctx, target.question, [], target, regenerate=True)
            return
        await note()
    finally:
        if not answered:
            await api.answer_callback(token, callback.get('id', ''))


# ---------------------------------------------------------------- files


async def _receive_file(ctx: Ctx, message: dict) -> Optional[dict]:
    if message.get('photo'):
        photo = max(message['photo'], key=lambda p: p.get('file_size') or 0)
        file_id, name, content_type, size = photo['file_id'], f'anh-{int(time.time())}.jpg', 'image/jpeg', photo.get('file_size') or 0
    else:
        document = message['document']
        file_id = document['file_id']
        name = (document.get('file_name') or f'file-{int(time.time())}')[:200]
        content_type = document.get('mime_type') or 'application/octet-stream'
        size = document.get('file_size') or 0
    if size > api.DOWNLOAD_LIMIT:
        await ctx.send('File lớn hơn 20 MB: Telegram không cho bot tải. Hãy tải lên trên web.')
        return None
    await api.chat_action(ctx.token, ctx.chat_id, 'upload_document')
    try:
        data = await api.download_file(ctx.token, file_id)
        channel_id = ctx.session.channel_id if ctx.session.mode == 'channel' else None
        if channel_id and not await ask.get_channel_for(ctx.user, channel_id, ctx.settings):
            channel_id = None
        return await ask.save_upload(await ctx.request(), ctx.user, name, data, content_type, channel_id)
    except Exception as error:
        log.warning('Telegram: receiving a file failed: %s', error)
        await ctx.send(f'Không nhận được file: {html.escape(str(error))}')
        return None


async def _send_files(ctx: Ctx, entries: list) -> None:
    if not entries or not ctx.settings['telegram.files']:
        return
    base = await _base()
    for entry in entries[:10]:
        try:
            got = await ask.readable_file(ctx.user, entry)
            if not got:
                continue
            name, data, content_type = got
            if len(data) > api.UPLOAD_LIMIT:
                url = f'{base}/api/v1/files/{entry.get("id")}/content' if base else ''
                await ctx.send(f'📎 {html.escape(name)} lớn hơn 50 MB, tải trên web.', kb.inline([[kb.url_button('⬇️ Tải trên web', url)]] if url else []))
                continue
            await api.chat_action(ctx.token, ctx.chat_id, 'upload_document')
            await api.send_document(ctx.token, ctx.chat_id, name, data, content_type, f'📎 {html.escape(name)}',
                                    photo=content_type in ('image/jpeg', 'image/png', 'image/webp') and len(data) < 10 * 1024 * 1024)
        except Exception as error:
            log.warning('Telegram: sending a file failed: %s', error)
            await ctx.send(f'Không gửi được file {html.escape(entry.get("name") or "")}: {html.escape(str(error))}')


async def _export(ctx: Ctx, target, kind: str, note) -> None:
    if not ctx.settings['telegram.files']:
        await note('Quản trị viên đã tắt gửi file qua Telegram', True)
        return
    cached = ((target.data or {}).get('exports') or {}).get(kind)
    if cached:
        await note()
        await api.call(ctx.token, 'sendDocument', chat_id=ctx.chat_id, document=cached)
        return
    if target.target == 'channel':
        channel = await ask.get_channel_for(ctx.user, target.target_id, ctx.settings)
        if not channel:
            await note('Bạn không còn quyền trong kênh này', True)
            return
    else:
        from open_webui.models.chats import Chats

        if not await Chats.get_chat_by_id_and_user_id(target.target_id, ctx.user.id):
            await note('Cuộc trò chuyện không còn', True)
            return
    await note('Đang tạo file…')
    handle = {'kind': target.target, 'channel_id': target.target_id, 'chat_id': target.target_id, 'message_id': target.message_id}
    answer = await ask.fetch(handle)
    if not answer['content'].strip():
        await ctx.send('Câu trả lời trống, không có gì để xuất.')
        return
    await api.chat_action(ctx.token, ctx.chat_id, 'upload_document')
    try:
        name, data, content_type = await ask.export_answer(
            ctx.app, await ctx.request(), ctx.user, {'target': target.target, 'target_id': target.target_id, 'message_id': target.message_id},
            answer['content'], target.question or '', kind,
        )
        sent = await api.send_document(ctx.token, ctx.chat_id, name, data, content_type, f'📎 {html.escape(name)}')
        file_id = ((sent or {}).get('document') or {}).get('file_id')
        if file_id:
            data_map = dict(target.data or {})
            data_map['exports'] = {**(data_map.get('exports') or {}), kind: file_id}
            await MessageMap.set_data(str(ctx.chat_id), target.tg_message_id, data_map)
    except Exception as error:
        log.warning('Telegram: export failed: %s', error)
        await ctx.send(f'Không tạo được file: {html.escape(str(error))}')


# ---------------------------------------------------------------- asking


async def _ask(ctx: Ctx, text: str, files: list, target=None, regenerate: bool = False) -> None:
    """Answer one question (one at a time per user, a limited number at once for the app)."""
    global _waiting
    if ctx.tg_id in _running:
        await ctx.send('⏳ Đang trả lời câu trước. Đợi xong, hoặc bấm <b>⏹ Dừng</b> ở tin đang trả lời.')
        return
    run = {'key': uuid.uuid4().hex[:12], 'stopped': False, 'handle': None}
    _running[ctx.tg_id] = run
    queued = False
    try:
        gate = _gate(ctx.settings['telegram.max_running'])
        waiting_note = None
        if gate.locked():
            _waiting += 1
            queued = True
            waiting_note = await ctx.send(
                f'⏳ Hệ thống đang bận, câu hỏi của bạn đang chờ đến lượt (trước bạn {_waiting - 1} câu)…',
                kb.stop(run['key']),
            )
        async with gate:
            if queued:
                _waiting -= 1
                queued = False
                if waiting_note:
                    await api.delete_message(ctx.token, ctx.chat_id, waiting_note['message_id'])
            if run['stopped']:
                await ctx.send('⏹ Đã hủy câu hỏi đang chờ.')
                return
            await _answer(ctx, run, text, files, target, regenerate)
    finally:
        if queued:
            _waiting -= 1
        _running.pop(ctx.tg_id, None)


async def _answer(ctx: Ctx, run: dict, text: str, files: list, target, regenerate: bool) -> None:
    request = await ctx.request()
    models = await ask.allowed_models(request, ctx.user)
    model_id = ask.default_model_id(ctx.user, ctx.settings, models, ctx.session.model_id)
    if not model_id:
        await ctx.send('Bạn chưa được dùng model nào. Liên hệ quản trị viên.')
        return
    model = next(m for m in models if m['id'] == model_id)
    base = await _base()

    # Where the question goes: the answer replied to, else the chat's mode.
    channel, thread_id = None, None
    if target and target.target == 'channel':
        channel = await ask.get_channel_for(ctx.user, target.target_id, ctx.settings)
        if not channel:
            await ctx.send('Bạn không còn hỏi được trong kênh của tin này.')
            return
        thread_id = target.thread_id
    elif target and target.target == 'chat':
        from open_webui.models.chats import Chats

        if not await Chats.get_chat_by_id_and_user_id(target.target_id, ctx.user.id):
            await ctx.send('Cuộc trò chuyện của tin này không còn.')
            return
        # Replying to an older answer continues that conversation, however long ago it was.
        ctx.session.chat_ref, ctx.session.last_message_id = target.target_id, target.message_id
        ctx.session.updated_at = time.time_ns()
    elif ctx.session.mode == 'channel':
        channel = await ask.get_channel_for(ctx.user, ctx.session.channel_id, ctx.settings)
        if not channel:
            ctx.session.mode, ctx.session.channel_id = 'direct', None
            await Sessions.save(ctx.session)
            await ctx.send('Bạn không còn hỏi được trong kênh đã chọn. Đã chuyển về hỏi trực tiếp.', kb.main_keyboard())
            return
    if channel and not ctx.settings['telegram.ask_channels']:
        await ctx.send('Quản trị viên chưa bật hỏi trong kênh qua Telegram.')
        return

    header = (f'📁 <b>#{html.escape(channel.name)}</b> · {html.escape(model.get("name") or model_id)}' if channel
              else f'💬 <b>Trực tiếp</b> · {html.escape(model.get("name") or model_id)}')
    progress = await ctx.send(f'{header}\n⏳ Đang trả lời…', kb.stop(run['key']))
    if not progress:
        return
    progress_id = progress['message_id']
    try:
        if channel:
            handle = await ask.ask_channel(ctx.app, request, ctx.user, channel, model, text, thread_id, files)
        else:
            handle = await ask.ask_direct(ctx.app, request, ctx.user, ctx.session, model_id, text, ctx.settings, files)
    except Exception as error:
        detail = getattr(error, 'detail', None) or str(error)
        log.warning('Telegram: asking failed for %s: %s', ctx.user.id, detail)
        await ctx.edit(progress_id, f'{header}\n⚠️ Không gửi được câu hỏi: {html.escape(str(detail))}')
        return
    run['handle'] = handle
    if handle['kind'] == 'chat':
        ctx.session.chat_ref, ctx.session.last_message_id = handle['chat_id'], handle['message_id']
        await Sessions.save(ctx.session)

    answer = await _follow(ctx, run, handle, header, progress_id)
    url = (f'{base}/channels/{handle["channel_id"]}' if handle['kind'] == 'channel' else f'{base}/c/{handle["chat_id"]}') if base else ''
    if run['stopped']:
        content = answer['content'].strip()
        body = fmt.preview(content, header) if content else header
        await ctx.edit(progress_id, f'{body}\n\n⏹ <i>Đã dừng.</i>', kb.inline([[kb.url_button('🌐 Mở trên web', url)]] if url else []))
        return
    if not answer['content'].strip():
        reason = answer['error'] or ('AI chưa trả lời xong trong thời gian cho phép' if not answer['done'] else 'AI không trả lời')
        await ctx.edit(progress_id, f'{header}\n⚠️ {html.escape(str(reason))}', kb.error_buttons(url))
        await MessageMap.add(str(ctx.chat_id), progress_id, handle['kind'], handle.get('channel_id') or handle.get('chat_id'),
                             handle['message_id'], handle.get('thread_id'), text)
        return

    parts = fmt.messages(answer['content'], header)
    if not answer['done']:
        parts[-1] += '\n\n<i>… câu trả lời còn dài, xem tiếp trên web.</i>'
    markup = kb.answer(handle['kind'], url, ctx.settings['telegram.files'])
    tg_ids = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if index == 0:
            await ctx.edit(progress_id, part, markup if last else kb.inline([]))
            tg_ids.append(progress_id)
        else:
            sent = await ctx.send(part, markup if last else None)
            if sent:
                tg_ids.append(sent['message_id'])
    target_id = handle.get('channel_id') if handle['kind'] == 'channel' else handle['chat_id']
    for tg_id in tg_ids:
        await MessageMap.add(str(ctx.chat_id), tg_id, handle['kind'], target_id, handle['message_id'], handle.get('thread_id'), text)
    await _send_files(ctx, answer['files'])
    if handle['kind'] == 'channel':
        data = dict(ctx.session.data or {})
        data['recent'] = [target_id, *[i for i in data.get('recent', []) if i != target_id]][:4]
        ctx.session.data = data
    await Sessions.save(ctx.session)


async def _follow(ctx: Ctx, run: dict, handle: dict, header: str, progress_id: int) -> dict:
    """Wait for the answer, showing it as it is written (channel) or a progress line (chat)."""
    deadline = time.monotonic() + ctx.settings['telegram.answer_minutes'] * 60
    started = time.monotonic()
    last_edit = last_action = 0.0
    shown = ''
    answer = {'content': '', 'done': False, 'error': None, 'files': []}
    while time.monotonic() < deadline and not run['stopped']:
        await asyncio.sleep(1.2)
        try:
            answer = await ask.fetch(handle)
        except Exception:
            log.debug('Telegram: fetching the answer failed', exc_info=True)
            continue
        if answer['done']:
            break
        now = time.monotonic()
        if now - last_action > 5:
            last_action = now
            await api.chat_action(ctx.token, ctx.chat_id)
        if now - last_edit < 2.5:
            continue
        content = fmt.clean(answer['content'])
        if content and content != shown:
            text = fmt.preview(content, header) + '\n\n⏳ <i>đang viết…</i>'
        elif not content and now - last_edit > 10:
            text = f'{header}\n⏳ Đang trả lời… {int(now - started)} giây'
        else:
            continue
        try:
            await ctx.edit(progress_id, text, kb.stop(run['key']))
            shown = content
            last_edit = now
        except api.TelegramError as error:
            log.debug('Telegram: progress edit failed: %s', error)
            last_edit = now
    return answer
