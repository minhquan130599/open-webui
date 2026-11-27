"""Once a day the bot's chat history is deleted: the messages it sent and the ones it received in private chats,
and the local records of them (chat log, message map).

Telegram lets a bot delete messages of a private chat only while they are younger than 48 hours, so this runs
every day and only asks Telegram for the messages of the last ~47 hours; older ones (the app was stopped, a run
failed) cannot be deleted any more and are only forgotten locally. Telegram's answers are not checked one by one:
a message the user already deleted, or one too old, is simply skipped.

One worker runs it (a lease in report_lock, like the bot's reading loop); the day of the last run is kept in the
lease's data, so a restart or several workers never run it twice on one day.
"""

import asyncio
import datetime as dt
import logging
import random
import time

from open_webui.reports.models import Locks
from open_webui.telegram import api
from open_webui.telegram.models import ChatLog, MessageMap
from open_webui.telegram.settings import get_settings

log = logging.getLogger(__name__)

LOCK = 'telegram_cleanup'
LEASE_SECONDS = 3600
CHECK_SECONDS = 600
RUN_HOUR = 2  # local time (Vietnam): at night, when nobody is using the bot
DELETABLE_SECONDS = 47 * 3600  # Telegram's limit is 48 hours; one hour kept as margin
BATCH = 100  # deleteMessages takes at most 100 ids at once

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo('Asia/Ho_Chi_Minh')
except Exception:  # no tz database on the machine: fixed UTC+7 (Vietnam has no daylight saving)
    TZ = dt.timezone(dt.timedelta(hours=7))


def _today() -> tuple[str, int]:
    now = dt.datetime.now(TZ)
    return now.date().isoformat(), now.hour


async def _delete_chat(token: str, chat_id: str, message_ids: list[int]) -> int:
    """Delete messages of one chat in batches; returns how many batches Telegram refused (ignored)."""
    failed = 0
    for start in range(0, len(message_ids), BATCH):
        part = message_ids[start : start + BATCH]
        try:
            await api.call(token, 'deleteMessages', chat_id=chat_id, message_ids=part)
        except api.TelegramError as error:
            failed += 1
            log.debug('Telegram cleanup: chat %s, %s message(s) not deleted: %s', chat_id, len(part), error)
            if api.gone_chat(error):
                break  # the user blocked the bot: nothing more can be deleted in this chat
    return failed


async def run_cleanup(token: str) -> dict:
    """Delete everything logged until now from Telegram (what is still deletable) and forget it locally."""
    started = time.time_ns()
    deletable_after = started - DELETABLE_SECONDS * 10**9
    deleted = forgotten = failed = 0
    while True:
        by_chat = await ChatLog.before(started)
        if not by_chat:
            break
        for chat_id, rows in by_chat.items():
            ids = sorted({message_id for message_id, logged_at in rows if logged_at >= deletable_after})
            if ids and token:
                failed += await _delete_chat(token, chat_id, ids)
                deleted += len(ids)
            all_ids = [message_id for message_id, _ in rows]
            await ChatLog.forget(chat_id, all_ids)
            forgotten += len(all_ids)
    # Bot messages mapped to channel messages: the Telegram messages are gone, so are their mappings.
    mapped = await MessageMap.prune(before=started)
    return {'asked': deleted, 'forgotten': forgotten, 'refused_batches': failed, 'mapped': mapped}


async def cleanup_loop() -> None:
    log.info('Telegram daily cleanup started')
    while True:
        try:
            await asyncio.sleep(CHECK_SECONDS + random.uniform(0, 30))
            today, hour = _today()
            if hour < RUN_HOUR:
                continue
            held = await Locks.acquire(LOCK, LEASE_SECONDS)
            if not held:
                continue  # another worker is running it
            lease_id, data = held['lease_id'], held['data'] or {}
            try:
                if data.get('last_date') == today:
                    continue
                settings = await get_settings()
                result = await run_cleanup(settings['telegram.bot_token'])
                log.info('Telegram daily cleanup: %s', result)
                await Locks.save(LOCK, lease_id, {**data, 'last_date': today, 'last_run': int(time.time()), **result})
            finally:
                await Locks.release(LOCK, lease_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Telegram daily cleanup failed')
