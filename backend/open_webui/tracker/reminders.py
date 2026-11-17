"""Due-date reminders: a personal inbox notification to each owner of an open work item that is due today
or (optionally) tomorrow, once a day at the configured time (tracker timezone), plus one email per person with
all their reminders when reminder emails are enabled (see mailer.py).

Each reminder has a unique event key per item, due date and kind, so a restart, a second app instance or a
second run on the same day never notifies twice.
"""

import asyncio
import datetime as dt
import logging
import os
import random
import re
from typing import Optional

from open_webui.internal.db import get_async_db
from open_webui.models.config import Config
from open_webui.tracker import mywork, team
from open_webui.tracker.service import TZ

log = logging.getLogger(__name__)

EVENT_TYPE = 'tracker.due'
ENABLE_KEY = 'tracker.reminder.enable'
TIME_KEY = 'tracker.reminder.time'
DAY_BEFORE_KEY = 'tracker.reminder.day_before'
LAST_RUN_KEY = 'tracker.reminder.last_run'
# Monday reminder to channel owners and managers about items nobody can be reminded about.
WEEKLY_KEY = 'tracker.reminder.weekly_unassigned'
WEEKLY_LAST_KEY = 'tracker.reminder.weekly_last_run'
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
    from open_webui.tracker import mailer

    channels = mywork.project_channels(await Channels.get_channels())
    kinds = {0: 'today', 1: 'tomorrow'} if day_before else {0: 'today'}
    inbox_enabled = bool(await Config.get('notifications.enable'))
    created = 0
    notified: set[str] = set()
    # Emails follow the inbox: only people newly notified for an item get it in their email, so a second run
    # on the same day sends nothing again. Without the inbox, the once-a-day run is the only guard.
    emails: dict[str, list] = {}
    # Overdue items are not notified again every day; they ride along in the email of someone reminded today.
    overdue: dict[str, list] = {}
    # Channel owners: every open item due soon or overdue in their channels, owner linked or not.
    by_channel: dict[str, list] = {}
    for entry in await mywork.collect(channels):
        if entry.bucket != 'done' and entry.channel_owner_id:
            soon = kinds.get(entry.days_left)
            if soon or entry.bucket == 'overdue':
                by_channel.setdefault(entry.channel_id, []).append((soon or 'overdue', entry))
        if entry.bucket == 'overdue':
            for user_id in entry.owner_ids:
                overdue.setdefault(user_id, []).append(('overdue', entry))
            continue
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
            for user_id in recipients if inbox_enabled else entry.owner_ids:
                emails.setdefault(user_id, []).append((kind, entry))
        except Exception:
            log.exception('Due reminder failed for item %s in channel %s', entry.id, entry.channel_id)
    if notified:
        await emit_changes(notified)
    for user_id in emails:
        emails[user_id] = overdue.get(user_id, []) + emails[user_id]
    if emails:
        try:
            await mailer.send_reminders(emails)
        except Exception:
            log.exception('Reminder emails failed')
    try:
        await _owner_digests(by_channel)
    except Exception:
        log.exception('Channel owner digests failed')
    return created


async def _owner_digests(by_channel: dict[str, list]) -> None:
    """Tell each channel owner, once a day per channel, what is due soon or overdue in the channels they own.
    Only channels with something due today or tomorrow are reported; overdue items ride along."""
    from open_webui.models.notifications import emit_changes, insert_notifications
    from open_webui.tracker import mailer

    day = mywork.today()
    per_owner: dict[str, list[dict]] = {}
    notified: set[str] = set()
    for channel_id, items in by_channel.items():
        due = [entry for kind, entry in items if kind != 'overdue']
        if not due:
            continue
        first = items[0][1]
        owner_id = first.channel_owner_id
        recipients_of_channel = {owner_id, *await team.manager_ids(channel_id)} - {None}
        message = (
            f'Kênh "{first.channel_name}" có {len(due)} việc đến hạn hôm nay hoặc ngày mai'
            + (f' và {len(items) - len(due)} việc quá hạn' if len(items) > len(due) else '')
            + '.'
        )
        async with get_async_db() as db:
            recipients = await insert_notifications(
                recipients={user_id: EVENT_TYPE for user_id in recipients_of_channel},
                event_key=f'{EVENT_TYPE}:owner:{channel_id}:{day}',
                resource_type='channel',
                resource_id=channel_id,
                object_type='channel',
                object_id=channel_id,
                data={'message': message, 'kind': 'owner_digest'},
                db=db,
            )
            await db.commit()
        for user_id in recipients:
            notified.add(user_id)
            per_owner.setdefault(user_id, []).append({'id': channel_id, 'name': first.channel_name, 'items': items})
    if notified:
        await emit_changes(notified)
    if per_owner:
        await mailer.send_owner_digests(per_owner)


async def run_if_due(now: dt.datetime) -> bool:
    """Send today's reminders once the configured time has passed. Returns True when it sent them.
    The same run hands over channels whose owner left, and on Mondays sends the "to assign" reminder."""
    settings = await get_settings()
    today = now.date().isoformat()
    if not settings['enable'] or settings['last_run'] == today or now.strftime('%H:%M') < settings['time']:
        return False
    await Config.upsert({LAST_RUN_KEY: today})
    try:
        await handover_channels()
    except Exception:
        log.exception('Channel handover failed')
    created = await send_due_reminders(settings['day_before'])
    log.info('Due reminders for %s: %d notifications', today, created)
    if now.weekday() == 0:
        values = await Config.get_many(WEEKLY_KEY, WEEKLY_LAST_KEY)
        enabled = True if values.get(WEEKLY_KEY) is None else bool(values.get(WEEKLY_KEY))
        if enabled and values.get(WEEKLY_LAST_KEY) != today:
            await Config.upsert({WEEKLY_LAST_KEY: today})
            try:
                await send_unassigned_reminders()
            except Exception:
                log.exception('Unassigned reminders failed')
    return True


async def handover_channels() -> list[dict]:
    """Channels whose owner's account was disabled pass to a manager (team lead); logged when there is none."""
    from open_webui.models.channels import Channels

    channels = mywork.project_channels(await Channels.get_channels())
    moves = await team.handover_left_owners([c.id for c in channels])
    for move in moves:
        if move['to']:
            log.info('Channel %s: owner %s left, now owned by manager %s', move['channel'], move['from'], move['to'])
        else:
            log.warning('Channel %s: owner %s left and the channel has no manager; an admin must choose', move['channel'], move['from'])
    return moves


def problem_of(entry) -> tuple[Optional[str], str]:
    """Why nobody can be reminded about an open item: (problem, who) or (None, '')."""
    if entry.bucket == 'done':
        return None, ''
    left = [o['name'] for o in entry.owners if o['status'] == 'left']
    outside = [o['name'] for o in entry.owners if o['status'] == 'outside']
    if left:
        return 'left', ', '.join(left)
    if not entry.owner_ids and not entry.ben_ngoai:
        if entry.nhom and entry.nhom != 'Cả nhóm':
            return 'group', entry.nhom
        if outside:
            return 'outside', ', '.join(outside)
        return 'unassigned', ''
    return None, ''


async def send_unassigned_reminders() -> int:
    """Monday: each channel's owner and managers (never other admins) get one notification and email listing the open
    items nobody can be reminded about. Returns the number of people notified."""
    from open_webui.models.channels import Channels
    from open_webui.models.notifications import emit_changes, insert_notifications
    from open_webui.tracker import mailer

    channels = mywork.project_channels(await Channels.get_channels())
    week = mywork.today()
    by_channel: dict[str, list] = {}
    names: dict[str, str] = {}
    owners: dict[str, Optional[str]] = {}
    for entry in await mywork.collect(channels):
        problem, who = problem_of(entry)
        if problem:
            by_channel.setdefault(entry.channel_id, []).append((problem, entry, who))
            names[entry.channel_id] = entry.channel_name
            owners[entry.channel_id] = entry.channel_owner_id
    per_user: dict[str, list[dict]] = {}
    notified: set[str] = set()
    for channel_id, items in by_channel.items():
        recipients_of_channel = {owners[channel_id], *await team.manager_ids(channel_id)} - {None}
        async with get_async_db() as db:
            recipients = await insert_notifications(
                recipients={user_id: EVENT_TYPE for user_id in recipients_of_channel},
                event_key=f'{EVENT_TYPE}:unassigned:{channel_id}:{week}',
                resource_type='channel',
                resource_id=channel_id,
                object_type='channel',
                object_id=channel_id,
                data={'message': f'Kênh "{names[channel_id]}" có {len(items)} việc cần phân công người phụ trách.',
                      'kind': 'unassigned'},
                db=db,
            )
            await db.commit()
        for user_id in recipients:
            notified.add(user_id)
            per_user.setdefault(user_id, []).append({'id': channel_id, 'name': names[channel_id], 'items': items})
    if notified:
        await emit_changes(notified)
    if per_user:
        await mailer.send_unassigned(per_user)
    return len(notified)


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
