"""The bot: reads Telegram updates (one worker at a time, a lease in report_lock), handles commands and buttons,
and posts the linked user's messages into the channel they chose.

The bot does not chat with the AI any more. Messages go one way only, Telegram -> channel: they are posted under
the user's name as ordinary channel messages (marked as sent from Telegram), never start an AI answer, and nothing
from the channel is sent back. A message sent while no channel is chosen gets a short note on how to choose one.

Linking: the t.me link opened from the web carries a one-time code; pressing Start emails a 6-digit code to the
NCS AI account, and the link is made only when that code is entered on the web page that is waiting.

Many users at once: each update is handled in its own task; `telegram.rate_per_minute` messages per user per
minute; sends go through one rate limiter (api.py).

Security: the sender is `from.id`; the linked account is checked again on every update (active, allowed);
every id coming back from a button is checked against the user's access before use; groups are not answered
(only the legacy report /link, which stays switched off).
"""

import asyncio
import html
import logging
import time
from typing import Optional

from open_webui.reports.models import Locks
from open_webui.telegram import access, api, ask
from open_webui.telegram import keyboards as kb
from open_webui.telegram.models import Accounts, Sessions
from open_webui.telegram.settings import REPORTS_PAUSED, get_settings

log = logging.getLogger(__name__)

POLL_LOCK = 'telegram_poll'
POLL_SECONDS = 25
TEXT_LIMIT = 4000
COMMANDS = [
    ('start', 'Bắt đầu / menu chính'),
    ('kenh', 'Chọn kênh để gửi tin (/kenh tên để tìm)'),
    ('caidat', 'Cài đặt, hủy liên kết'),
    ('help', 'Hướng dẫn'),
]
ABOUT = 'Bot chỉ chuyển tin nhắn của bạn vào một kênh trên NCS AI, không trò chuyện với AI.'
HELP = (
    '<b>Cách dùng bot NCS AI trên Telegram</b>\n'
    f'• {ABOUT}\n'
    f'• Bấm <b>{kb.CHOOSE_CHANNEL}</b> (hoặc gõ /kenh, /kenh tên để tìm) rồi gửi tin nhắn: tin được đăng vào kênh '
    'dưới tên bạn, có nhãn "Gửi từ Telegram".\n'
    '• Có thể gửi file hoặc ảnh (tối đa 20 MB), chú thích đi kèm thành nội dung tin.\n'
    '• AI không trả lời các tin gửi từ Telegram (kể cả khi có gọi tên AI), và tin trong kênh không được gửi về '
    'Telegram: xem và trả lời trên web.\n'
    '• Lịch sử chat với bot được tự xóa mỗi ngày.\n'
    f'• <b>{kb.SETTINGS}</b> (/caidat) để xem tài khoản hoặc hủy liên kết.\n'
    'Bot chỉ làm được những gì tài khoản NCS AI của bạn được phép.'
)
DIRECT_NOTE = (
    f'{ABOUT}\nBấm <b>{kb.CHOOSE_CHANNEL}</b> (hoặc gõ /kenh) để chọn kênh, rồi gửi lại tin nhắn: tin sẽ được đăng '
    'vào kênh đó dưới tên bạn.'
)
MAIL_MISSING = (
    'Chưa liên kết được: liên kết Telegram cần gửi mã xác nhận qua email, nhưng quản trị viên chưa cấu hình gửi '
    'email. Hãy liên hệ quản trị viên.'
)

STATUS: dict = {}
_tasks: set = set()
_uploading: set = set()  # telegram user ids sending a file


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


UPDATES_PER_READ = 100  # Telegram's getUpdates page size


def _stale(update: dict, reading_since: int, draining: bool) -> bool:
    """Sent while nobody was reading (the app stopped, restarting or being updated): everything is skipped, link
    codes included, and only what is sent once the bot reads again is handled. A message carries its send time;
    a button tap does not, so taps still waiting when reading starts (`draining`) are the old ones."""
    if 'callback_query' in update:
        return draining
    message = update.get('message')
    return bool(message) and (message.get('date') or 0) < reading_since


async def _skip_stale(settings: dict, updates: list) -> None:
    """One note per private chat instead of answering what was sent while the bot was stopped."""
    token = settings['telegram.bot_token']
    counts: dict = {}
    for update in updates:
        callback = update.get('callback_query')
        if callback:
            try:
                await api.answer_callback(token, callback.get('id', ''))
            except Exception:
                pass  # too old to answer: nothing to do
        message = (callback or {}).get('message') or update.get('message') or {}
        chat = message.get('chat') or {}
        if chat.get('type') == 'private' and chat.get('id'):
            counts[chat['id']] = counts.get(chat['id'], 0) + 1
    for chat_id, count in counts.items():
        log.info('Telegram: skipped %s stale update(s) from chat %s', count, chat_id)
        try:
            await api.send_message(
                token, chat_id,
                f'⏸ Bot vừa tạm dừng (cập nhật hoặc khởi động lại) nên đã bỏ qua {count} tin nhắn / thao tác bạn gửi '
                'trong lúc đó. Nếu vẫn cần, hãy gửi hoặc bấm lại.',
            )
        except Exception:
            log.debug('Telegram: could not send the skipped note to %s', chat_id, exc_info=True)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


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
    reading_since = 0  # unix time this worker started reading: messages sent before it are skipped
    draining = False  # still reading what waited while nobody read (more than one page of it may wait)
    skipped: list = []
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
            # First read after starting (or taking over from another worker): what is waiting was sent while
            # nobody read; it is read at once (no waiting) and skipped.
            if lease_id is None:
                reading_since, draining = int(time.time()), True
            lease_id = held['lease_id']
            state = held['data'] or {}
            if commands_for != token:
                await api.set_commands(token, COMMANDS)
                commands_for = token
            # The offset belongs to one bot: after the token changed to another bot, the old bot's offset (far past the
            # new bot's update ids) confirmed nothing, and the same updates came back on every read, without end.
            bot_id = token.split(':', 1)[0]
            if state.get('bot') != bot_id:
                state = {'bot': bot_id}
            saved = int(state.get('offset', 0))
            updates = await api.get_updates(token, saved, 0 if draining else POLL_SECONDS)
            # Telegram returns updates from `saved` on: the next offset follows the newest one returned.
            offset = max((int(update.get('update_id', 0)) + 1 for update in updates), default=saved)
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
                if _stale(update, reading_since, draining):
                    skipped.append(update)
                    continue
                _spawn(_handle_update(app, settings, update))
            if len(updates) < UPDATES_PER_READ:
                draining = False
            if skipped and not draining:  # one note per chat for everything that waited
                _spawn(_skip_stale(settings, skipped))
                skipped = []
        except asyncio.CancelledError:
            # Shutting down: hand the lease back so the next start reads at once instead of waiting it out.
            if lease_id:
                try:
                    await asyncio.shield(Locks.release(POLL_LOCK, lease_id))
                except BaseException:
                    pass
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
            'Chào bạn 👋 Đây là bot NCS AI.\nĐể gửi tin nhắn vào kênh NCS AI từ đây, hãy liên kết với tài khoản NCS AI: '
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
        return  # nothing is posted from groups
    # The user's own messages are deleted from the chat with the bot's ones, once a day (cleanup.py).
    await api.remember(chat['id'], message)
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

    # Anyone who was invited to receive reports here (a link, no account needed) can stop them.
    if text.startswith('/') and text.split()[0].split('@')[0].lower() in STOP_REPORTS:
        await _stop_reports(settings, chat['id'])
        return

    if not await _load(ctx):
        return
    await Accounts.touch(ctx.tg_id, sender.get('username') or '', _display(sender))

    command = text.split()[0].split('@')[0].lower() if text.startswith('/') else ''
    if command == '/help':
        await ctx.send(HELP, _keyboard(ctx))
        return
    if command == '/caidat' or text == kb.SETTINGS:
        await _settings(ctx)
        return
    if not await access.can_ask(ctx.user, settings):
        await ctx.send(
            'Tài khoản của bạn chưa được gửi tin nhắn vào kênh qua Telegram (quản trị viên chưa cấp quyền).',
            kb.remove_keyboard(),
        )
        return
    if command == '/kenh' or text in (kb.CHOOSE_CHANNEL, kb.CHANGE_CHANNEL):
        query = text.split(maxsplit=1)[1] if command == '/kenh' and len(text.split(maxsplit=1)) > 1 else ''
        await _pick_channel(ctx, 0, query)
        return
    if command:
        await ctx.send('Không có lệnh này. Dùng /kenh để chọn kênh, /caidat để cài đặt, /help để xem hướng dẫn.', _keyboard(ctx))
        return
    if text in kb.OLD_BUTTONS:
        # A button of the keyboard from before the bot stopped chatting with the AI: show the new keyboard.
        await ctx.send(
            'Bot không còn trò chuyện với AI. Bot chỉ chuyển tin nhắn của bạn vào kênh đã chọn trên NCS AI.',
            _keyboard(ctx),
        )
        return

    channel = await _current_channel(ctx)
    if not channel:
        await ctx.send(DIRECT_NOTE, _keyboard(ctx))
        return

    files = []
    if message.get('document') or message.get('photo'):
        if ctx.tg_id in _uploading:
            await ctx.send('⏳ Đang nhận file trước của bạn, gửi lại sau ít phút nhé.')
            return
        _uploading.add(ctx.tg_id)
        try:
            entry = await _receive_file(ctx, message, channel.id)
        finally:
            _uploading.discard(ctx.tg_id)
        if not entry:
            return
        files.append(entry)
    if not text and not files:
        await ctx.send('Bot chỉ chuyển được tin nhắn chữ, file hoặc ảnh vào kênh.', _keyboard(ctx))
        return
    if len(text) > TEXT_LIMIT:
        await ctx.send(f'Tin nhắn dài quá ({len(text)} ký tự). Rút gọn dưới {TEXT_LIMIT} ký tự, hoặc gửi nội dung dạng file.')
        return
    try:
        await ask.post_to_channel(await ctx.request(), ctx.user, channel, text, files)
    except Exception as error:
        detail = getattr(error, 'detail', None) or str(error)
        log.warning('Telegram: posting into channel %s failed for %s: %s', channel.id, ctx.user.id, detail)
        await ctx.send(f'⚠️ Không gửi được vào #{html.escape(channel.name)}: {html.escape(str(detail))}', _keyboard(ctx))
        return
    data = dict(ctx.session.data or {})
    data['recent'] = [channel.id, *[i for i in data.get('recent', []) if i != channel.id]][:4]
    ctx.session.data = data
    await Sessions.save(ctx.session)
    await ctx.send(f'✅ Đã gửi vào <b>#{html.escape(channel.name)}</b>.', _keyboard(ctx))


async def _current_channel(ctx: Ctx):
    """The channel chosen in this chat, if the user may still post in it (else the choice is dropped)."""
    if ctx.session.mode != 'channel' or not ctx.session.channel_id:
        return None
    channel = await ask.get_channel_for(ctx.user, ctx.session.channel_id, ctx.settings)
    if not channel:
        ctx.session.mode, ctx.session.channel_id = 'direct', None
        data = dict(ctx.session.data or {})
        data.pop('channel_name', None)
        ctx.session.data = data
        await Sessions.save(ctx.session)
        await ctx.send('Bạn không còn gửi được vào kênh đã chọn. Hãy chọn kênh khác.', kb.main_keyboard())
    return channel


def _display(sender: dict) -> str:
    return ' '.join(x for x in (sender.get('first_name'), sender.get('last_name')) if x) or sender.get('username') or ''


def _keyboard(ctx: Ctx) -> dict:
    if ctx.session and ctx.session.mode == 'channel' and (ctx.session.data or {}).get('channel_name'):
        return kb.channel_keyboard(ctx.session.data['channel_name'])
    return kb.main_keyboard()


def _intro(ctx: Ctx) -> str:
    name = (ctx.session.data or {}).get('channel_name') if ctx.session and ctx.session.mode == 'channel' else ''
    if name:
        return f'Tin nhắn bạn gửi ở đây sẽ được đăng vào <b>#{html.escape(name)}</b>. Bấm <b>{kb.CHANGE_CHANNEL}</b> để đổi kênh.'
    return f'Bấm <b>{kb.CHOOSE_CHANNEL}</b> (hoặc gõ /kenh) để chọn kênh, rồi gửi tin nhắn vào kênh đó.'


async def _welcome(ctx: Ctx) -> None:
    name = html.escape(ctx.user.name or '')
    if not await access.can_ask(ctx.user, ctx.settings):
        await ctx.send(
            f'Chào <b>{name}</b> 👋\nTài khoản của bạn chưa được gửi tin nhắn vào kênh qua Telegram (quản trị viên chưa cấp quyền).',
            kb.remove_keyboard(),
        )
        return
    await ctx.send(f'Chào <b>{name}</b> 👋\n{ABOUT}\n{_intro(ctx)}', _keyboard(ctx))


# ---------------------------------------------------------------- linking


async def _link_account(ctx: Ctx, code: str) -> None:
    """Start pressed with a link code from the web: nothing is linked yet. A 6-digit code is emailed to the NCS AI
    account, and the link is made when it is entered on the web page that is waiting (router: /me/link/verify)."""
    from open_webui.telegram import link_mail
    from open_webui.telegram.models import mask_email

    if not code or len(code) > 64:
        await ctx.send('Mã liên kết không đúng.')
        return
    # Checked first: without email nothing can be linked, and no code is made that could not be delivered.
    ok, mail_settings = await link_mail.ready()
    if not ok:
        await Accounts.link_failed(code, 'mail')
        log.info('Telegram: link of %s stopped: email sending is not configured', ctx.tg_id)
        await ctx.send(MAIL_MISSING)
        return
    status, otp, user_id = await Accounts.request_otp(
        code, ctx.tg_id, str(ctx.chat_id), ctx.sender.get('username') or '', _display(ctx.sender)
    )
    log.info('Telegram: link code from %s: %s', ctx.tg_id, status)
    if status == 'invalid':
        await ctx.send('Mã liên kết không đúng hoặc đã hết hạn (10 phút). Vào web bấm <b>Liên kết Telegram</b> để bắt đầu lại.')
        return
    if status == 'other':
        await ctx.send('Liên kết này đang được xác nhận cho một Telegram khác. Vào web bấm <b>Liên kết Telegram</b> để bắt đầu lại.')
        return
    if status == 'locked':
        await ctx.send('Đã nhập sai mã xác nhận quá nhiều lần. Vào web bấm <b>Liên kết Telegram</b> để bắt đầu lại.')
        return
    if status == 'already':
        # Nothing changed: a link opened again (browser and app both open it, Start pressed twice) is answered once
        # per 10 minutes.
        if time.monotonic() - _already_told.get(ctx.tg_id, -1e9) < 600:
            return
        _already_told[ctx.tg_id] = time.monotonic()
        if await _load(ctx):
            await ctx.send('Telegram này đã liên kết với tài khoản rồi.', _keyboard(ctx))
        return
    user = await access.active_user(user_id) if user_id else None
    if not user:
        await Accounts.link_failed(code, 'account')
        await ctx.send('Tài khoản NCS AI này hiện không dùng được (bị khóa hoặc chưa được duyệt).')
        return
    masked = html.escape(mask_email(user.email or ''))
    if status == 'recent':
        await ctx.send(
            f'Mã xác nhận vừa được gửi tới email <b>{masked}</b>. Kiểm tra hộp thư (cả thư rác) rồi nhập mã trên '
            'trang web NCS AI đang chờ liên kết.'
        )
        return
    telegram_name = f'@{ctx.sender["username"]}' if ctx.sender.get('username') else _display(ctx.sender)
    try:
        await link_mail.send_code(user, otp, telegram_name, mail_settings)
    except Exception as error:
        log.warning('Telegram: link code email to user %s failed: %s', user.id, error)
        await Accounts.link_failed(code, 'send')
        await ctx.send(
            'Không gửi được email mã xác nhận nên chưa liên kết được. Thử lại sau ít phút (vào web bấm '
            '<b>Liên kết Telegram</b>), hoặc liên hệ quản trị viên.'
        )
        return
    await Accounts.otp_sent(code, mask_email(user.email or ''))
    await ctx.send(
        f'📧 Mã xác nhận gồm 6 chữ số đã được gửi tới email <b>{masked}</b> của tài khoản NCS AI.\n'
        'Nhập mã đó trên <b>trang web NCS AI</b> đang chờ liên kết để hoàn tất (không gửi mã vào đây). '
        'Mã có hiệu lực 10 phút.'
    )


async def announce_linked(app, settings: dict, account, chat_id: Optional[str] = None) -> None:
    """The email code was entered on the web: tell the Telegram chat that it is linked now."""
    if not settings['telegram.bot_token']:
        return
    user = await access.active_user(account.user_id)
    if not user:
        return
    ctx = Ctx(app, settings, chat_id or account.telegram_user_id, {'id': account.telegram_user_id})
    ctx.account, ctx.user = account, user
    ctx.session = await Sessions.get(str(ctx.chat_id), ctx.tg_id)
    lines = [f'✅ Đã liên kết với <b>{html.escape(user.name or "")}</b> ({html.escape(user.email or "")}).']
    can_post = await access.can_ask(user, settings)
    if can_post:
        lines += [ABOUT, _intro(ctx)]
    try:
        await ctx.send('\n'.join(lines), _keyboard(ctx) if can_post else kb.remove_keyboard())
    except api.TelegramError as error:
        log.info('Telegram: could not tell chat %s it is linked: %s', ctx.chat_id, error)


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
    if REPORTS_PAUSED:
        await api.send_message(token, chat['id'], 'Tạm thời chưa hỗ trợ gửi báo cáo qua Telegram, nên link nhận báo cáo này chưa dùng được.')
        return
    if chat_type != 'private' and not (await report_settings())['reports.telegram.allow_groups']:
        await api.send_message(token, chat['id'], 'NCS AI tạm thời chưa gửi báo cáo vào nhóm Telegram. Hãy liên kết chat riêng với bot.')
        return
    sender = message.get('from') or {}
    title = chat.get('title') or _display(sender) or str(chat['id'])
    if chat_type == 'private' and sender.get('username'):
        title = f'{title} (@{sender["username"]})'
    status, link = await TelegramLinks.confirm(code.strip(), str(chat['id']), title[:200], chat_type)
    log.info('Telegram: report link code from chat %s: %s', chat.get('id'), status)
    if status == 'invalid':
        await api.send_message(
            token, chat['id'],
            'Link nhận báo cáo này không dùng được: đã có người khác dùng, hoặc đã quá 24 giờ. '
            'Mỗi link chỉ dùng cho một người — hãy xin người gửi một link mới.',
        )
        return
    from open_webui.models.users import Users

    owner = await Users.get_user_by_id(link.user_id)
    who = html.escape(owner.name if owner else 'người dùng NCS AI')
    where = f'nhóm <b>{html.escape(chat.get("title") or "")}</b>' if chat_type != 'private' else 'Telegram này'
    if status == 'already':
        reply = f'✅ {where[0].upper() + where[1:]} đã được thêm vào danh sách nhận báo cáo của <b>{who}</b> rồi.'
    else:
        reply = f'✅ Đã thêm {where} vào danh sách nhận báo cáo định kỳ của <b>{who}</b>. Báo cáo sẽ được gửi tới đây theo lịch.'
    reply += '\n\nMuốn ngừng nhận: gửi /ngungnhan'
    await api.send_message(token, chat['id'], reply)


STOP_REPORTS = ('/ngungnhan', '/stopreports')


async def _stop_reports(settings: dict, chat_id) -> None:
    """Remove this chat from every report it was invited to (with a link). Reports to the user's own linked
    account ("Telegram của tôi") are managed on the web."""
    from open_webui.reports.models import TelegramLinks

    removed = await TelegramLinks.forget_chat(str(chat_id))
    await api.send_message(
        settings['telegram.bot_token'], chat_id,
        'Đã ngừng nhận báo cáo định kỳ được mời qua link. Muốn nhận lại, hãy xin người gửi một link mới.' if removed
        else 'Telegram này không nhận báo cáo nào qua link mời.',
    )


# ---------------------------------------------------------------- channels and menus


async def _pick_channel(ctx: Ctx, page: int, query: str = '', edit_id: Optional[int] = None) -> None:
    channels = await ask.user_channels(ctx.user, ctx.settings)
    if query:
        needle = query.lower().lstrip('#')
        channels = [c for c in channels if needle in (c.name or '').lower()]
    if not channels:
        await ctx.send('Không tìm thấy kênh nào bạn được gửi tin.' if query else 'Bạn chưa ở kênh nào có thể gửi tin.', _keyboard(ctx))
        return
    by_id = {c.id: c for c in channels}
    recent = [by_id[i] for i in (ctx.session.data or {}).get('recent', []) if i in by_id] if not query else []
    text = 'Chọn kênh để gửi tin nhắn' + (f' (lọc "{html.escape(query)}")' if query else ' — gõ <code>/kenh tên</code> để tìm') + ':'
    markup = kb.channel_picker(channels, recent, page)
    if edit_id:
        await ctx.edit(edit_id, text, markup)
    else:
        await ctx.send(text, markup)


async def _choose_channel(ctx: Ctx, channel_id: str) -> None:
    channel = await ask.get_channel_for(ctx.user, channel_id, ctx.settings)
    if not channel:
        await ctx.send('Bạn không còn gửi được tin vào kênh này.', _keyboard(ctx))
        return
    data = dict(ctx.session.data or {})
    data['recent'] = [channel.id, *[i for i in data.get('recent', []) if i != channel.id]][:4]
    data['channel_name'] = channel.name
    ctx.session.data = data
    ctx.session.mode = 'channel'
    ctx.session.channel_id = channel.id
    await Sessions.save(ctx.session)
    await ctx.send(
        f'📁 Đang gửi vào <b>#{html.escape(channel.name)}</b>.\n'
        'Tin nhắn, file và ảnh bạn gửi ở đây sẽ được đăng vào kênh dưới tên bạn (thành viên kênh thấy được, có nhãn '
        '"Gửi từ Telegram"). AI không trả lời các tin này, và tin trong kênh không được gửi ngược về Telegram.',
        kb.channel_keyboard(channel.name),
    )


async def _settings(ctx: Ctx) -> None:
    can_post = await access.can_ask(ctx.user, ctx.settings)
    lines = ['<b>⚙️ Cài đặt</b>', f'Tài khoản: {html.escape(ctx.user.name or "")} ({html.escape(ctx.user.email or "")})']
    if can_post and ctx.session.mode == 'channel' and (ctx.session.data or {}).get('channel_name'):
        lines.append(f'Đang gửi vào: #{html.escape(ctx.session.data["channel_name"])}')
    lines.append('Lịch sử chat với bot được tự xóa mỗi ngày.')
    await ctx.send('\n'.join(lines), kb.settings_menu(can_post))


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
                await ctx.send('Hủy liên kết Telegram với tài khoản NCS AI? Bạn sẽ không gửi được tin vào kênh từ đây nữa.', kb.confirm_unlink())
            elif data == 'unl:yes':
                await Accounts.unlink_user(ctx.user.id)
                log.info('Telegram: account %s unlinked by the user', ctx.tg_id)
                await ctx.send('Đã hủy liên kết. Muốn dùng lại, vào web bấm <b>Liên kết Telegram</b>.', kb.remove_keyboard())
            else:
                await ctx.send('Đã giữ liên kết.', _keyboard(ctx))
            return
        if data.startswith('chp:') or data.startswith('ch:'):
            if not await access.can_ask(ctx.user, settings):
                await note('Tài khoản chưa được gửi tin vào kênh qua Telegram', True)
                return
            await note()
            if data.startswith('chp:'):
                page = data[4:]
                await _pick_channel(ctx, int(page) if page.isdigit() else 0, edit_id=message.get('message_id'))
            else:
                await _choose_channel(ctx, data[3:])
            return
        # Buttons under answers of the AI, sent before the bot stopped chatting with it.
        await note('Nút này không còn dùng được: bot chỉ chuyển tin nhắn vào kênh.', True)
    finally:
        if not answered:
            await api.answer_callback(token, callback.get('id', ''))


# ---------------------------------------------------------------- files


async def _receive_file(ctx: Ctx, message: dict, channel_id: str) -> Optional[dict]:
    """A file or photo sent to the bot, stored like an upload in the channel on the web."""
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
        return await ask.save_upload(await ctx.request(), ctx.user, name, data, content_type, channel_id)
    except Exception as error:
        log.warning('Telegram: receiving a file failed', exc_info=True)
        # HTTP errors of the upload (type not allowed, too large) are written for users; others are internal.
        detail = getattr(error, 'detail', None) or (str(error) if isinstance(error, ValueError) else 'lỗi khi lưu hoặc đọc file')
        await ctx.send(f'Không nhận được file: {html.escape(str(detail))}')
        return None
