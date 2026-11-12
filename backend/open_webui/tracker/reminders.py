"""Due-date reminders: a personal inbox notification to each owner of an open work item that is due today
or (optionally) tomorrow, once a day at the configured time (tracker timezone).

Each reminder has a unique event key per item, due date and kind, so a restart, a second app instance or a
second run on the same day never notifies twice.
"""

import asyncio
import datetime as dt
import logging
import os
import random
import re

from open_webui.internal.db import get_async_db
from open_webui.models.config import Config
from open_webui.tracker import mywork
from open_webui.tracker.service import TZ

log = logging.getLogger(__name__)

EVENT_TYPE = 'tracker.due'
ENABLE_KEY = 'tracker.reminder.enable'
TIME_KEY = 'tracker.reminder.time'
DAY_BEFORE_KEY = 'tracker.reminder.day_before'
LAST_RUN_KEY = 'tracker.reminder.last_run'
CHECK_SECONDS = 60
_TIME_RE = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')

DEFAULTS = {
    ENABLE_KEY: os.getenv('TRACKER_REMINDER_ENABLE', 'True').lower() == 'true',
    TIME_KEY: os.getenv('TRACKER_REMINDER_TIME', '08:00'),
    DAY_BEFORE_KEY: os.getenv('TRACKER_REMINDER_DAY_BEFORE', 'True').lower() == 'true',
}


def valid_time(value: str) -> bool:
    return bool(_TIME_RE.match(value or ''))


async def get_settings() -> dict:
    values = await Config.get_many(ENABLE_KEY, TIME_KEY, DAY_BEFORE_KEY, LAST_RUN_KEY)
    time_value = values.get(TIME_KEY)
    return {
        'enable': DEFAULTS[ENABLE_KEY] if values.get(ENABLE_KEY) is None else bool(values[ENABLE_KEY]),
        'time': time_value if valid_time(time_value) else DEFAULTS[TIME_KEY],
        'day_before': DEFAULTS[DAY_BEFORE_KEY] if values.get(DAY_BEFORE_KEY) is None else bool(values[DAY_BEFORE_KEY]),
        'last_run': values.get(LAST_RUN_KEY),
        'timezone': str(TZ),
    }


async def set_settings(enable: bool, time_value: str, day_before: bool) -> dict:
    if not valid_time(time_value):
        raise ValueError('Time must be HH:MM')
    hours, minutes = time_value.split(':')
    await Config.upsert(
        {ENABLE_KEY: bool(enable), TIME_KEY: f'{int(hours):02d}:{minutes}', DAY_BEFORE_KEY: bool(day_before)}
    )
    return await get_settings()


def reminder_message(entry: mywork.WorkEntry, kind: str) -> str:
    when = 'đến hạn hôm nay' if kind == 'today' else f'đến hạn ngày mai ({entry.han})'
    return f'Công việc "{entry.ten}" trong kênh "{entry.channel_name}" {when}, cần lưu ý.'


async def send_due_reminders(day_before: bool = True) -> int:
    """Notify owners of items due today (and tomorrow). Returns the number of notifications created."""
    from open_webui.models.channels import Channels
    from open_webui.models.notifications import emit_changes, insert_notifications

    channels = mywork.project_channels(await Channels.get_channels())
    kinds = {0: 'today', 1: 'tomorrow'} if day_before else {0: 'today'}
    created = 0
    notified: set[str] = set()
    for entry in await mywork.collect(channels):
        kind = kinds.get(entry.days_left)
        if not kind or entry.bucket == 'done' or not entry.owner_ids:
            continue
        try:
            async with get_async_db() as db:
                recipients = await insert_notifications(
                    recipients={user_id: EVENT_TYPE for user_id in entry.owner_ids},
                    event_key=f'{EVENT_TYPE}:{entry.channel_id}:{entry.id}:{entry.han}:{kind}',
                    resource_type='channel',
                    resource_id=entry.channel_id,
                    object_type='channel',
                    object_id=entry.channel_id,
                    data={'message': reminder_message(entry, kind), 'item': entry.ten, 'due': entry.han, 'kind': kind},
                    db=db,
                )
                await db.commit()
            created += len(recipients)
            notified |= recipients
        except Exception:
            log.exception('Due reminder failed for item %s in channel %s', entry.id, entry.channel_id)
    if notified:
        await emit_changes(notified)
    return created


async def run_if_due(now: dt.datetime) -> bool:
    """Send today's reminders once the configured time has passed. Returns True when it sent them."""
    settings = await get_settings()
    today = now.date().isoformat()
    if not settings['enable'] or settings['last_run'] == today or now.strftime('%H:%M') < settings['time']:
        return False
    await Config.upsert({LAST_RUN_KEY: today})
    created = await send_due_reminders(settings['day_before'])
    log.info('Due reminders for %s: %d notifications', today, created)
    return True


async def reminder_loop() -> None:
    log.info('Project tracker due reminders started')
    while True:
        try:
            await run_if_due(dt.datetime.now(TZ))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Due reminder run failed')
        await asyncio.sleep(CHECK_SECONDS + random.uniform(0, 5))
