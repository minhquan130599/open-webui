"""A schedule's settings, checked and completed, and its next send time.

frequency  daily (optionally workdays only) | weekly (weekdays) | monthly (month_day, -1 = last day) |
           custom (several times a day, on the chosen weekdays)
times      ["HH:MM", ...] in the tracker timezone (only the first is used except for custom)
"""

import datetime as dt
import re
from typing import Optional

from open_webui.tracker.service import TZ

FREQUENCIES = ('daily', 'weekly', 'monthly', 'custom')
PERIODS = ('since_last', '7d', 'last_month')
SECTIONS = ('overview', 'due', 'done', 'blockers', 'people', 'decisions')
DEFAULT_SECTIONS = ['overview', 'due', 'done', 'blockers']
_TIME = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')
_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def normalize_config(raw: dict) -> dict:
    """The schedule settings with defaults filled in; ValueError with a readable message when invalid."""
    raw = raw or {}
    frequency = raw.get('frequency') or 'weekly'
    if frequency not in FREQUENCIES:
        raise ValueError('Tần suất không hợp lệ')
    times = [t.strip() for t in (raw.get('times') or ['08:00']) if isinstance(t, str) and t.strip()]
    if not times or any(not _TIME.match(t) for t in times):
        raise ValueError('Giờ gửi phải có dạng HH:MM')
    times = sorted({f'{int(t.split(":")[0]):02d}:{t.split(":")[1]}' for t in times})
    if frequency != 'custom':
        times = times[:1]
    weekdays = sorted({int(d) for d in raw.get('weekdays') or [] if str(d).isdigit() and 0 <= int(d) <= 6})
    if frequency in ('weekly',) and not weekdays:
        raise ValueError('Chọn ít nhất một ngày trong tuần')
    month_day = int(raw.get('month_day') or 1)
    if month_day != -1 and not 1 <= month_day <= 28:
        raise ValueError('Ngày trong tháng phải từ 1 đến 28, hoặc ngày cuối tháng')
    scope = raw.get('scope') if raw.get('scope') in ('all', 'pick') else 'all'
    channel_ids = [str(c) for c in raw.get('channel_ids') or [] if c]
    if scope == 'pick' and not channel_ids:
        raise ValueError('Chọn ít nhất một kênh')
    period = raw.get('period') if raw.get('period') in PERIODS else 'since_last'
    sections = [s for s in raw.get('sections') or DEFAULT_SECTIONS if s in SECTIONS] or list(DEFAULT_SECTIONS)
    email = raw.get('email') or {}
    to = []
    for address in email.get('to') or []:
        address = str(address).strip().lower()
        if not address:
            continue
        if not _EMAIL.match(address):
            raise ValueError(f'Địa chỉ email không hợp lệ: {address}')
        if address not in to:
            to.append(address)
    telegram = raw.get('telegram') or {}
    config = {
        'scope': scope,
        'channel_ids': channel_ids if scope == 'pick' else [],
        'frequency': frequency,
        'weekdays': weekdays,
        'workdays_only': bool(raw.get('workdays_only')),
        'month_day': month_day,
        'times': times,
        'period': period,
        'sections': sections,
        'prompt': str(raw.get('prompt') or '').strip()[:4000],
        'skip_unchanged': bool(raw.get('skip_unchanged', True)),
        'email': {'enabled': bool(email.get('enabled')), 'self': bool(email.get('self', True)), 'to': to},
        'telegram': {
            'enabled': bool(telegram.get('enabled')),
            'link_ids': [str(i) for i in telegram.get('link_ids') or [] if i],
        },
    }
    if not config['email']['enabled'] and not config['telegram']['enabled']:
        raise ValueError('Chọn ít nhất một cách gửi: email hoặc Telegram')
    if config['email']['enabled'] and not config['email']['self'] and not to:
        raise ValueError('Email: chọn gửi cho mình hoặc thêm người nhận')
    if config['telegram']['enabled'] and not config['telegram']['link_ids']:
        raise ValueError('Telegram: chọn ít nhất một chat đã liên kết')
    return config


def _days_in_month(year: int, month: int) -> int:
    first_next = dt.date(year + (month == 12), month % 12 + 1, 1)
    return (first_next - dt.timedelta(days=1)).day


def _day_matches(config: dict, day: dt.date) -> bool:
    frequency = config['frequency']
    if frequency == 'daily':
        return not config.get('workdays_only') or day.weekday() < 5
    if frequency == 'weekly':
        return day.weekday() in config['weekdays']
    if frequency == 'monthly':
        target = config['month_day']
        return day.day == (_days_in_month(day.year, day.month) if target == -1 else target)
    # custom: chosen weekdays, every day when none chosen
    return not config['weekdays'] or day.weekday() in config['weekdays']


def next_run(config: dict, after: Optional[dt.datetime] = None) -> Optional[int]:
    """The next send time (ns) strictly after `after` (default: now), in the tracker timezone."""
    after = (after or dt.datetime.now(TZ)).astimezone(TZ)
    for offset in range(0, 400):
        day = after.date() + dt.timedelta(days=offset)
        if not _day_matches(config, day):
            continue
        for value in config['times']:
            hour, minute = (int(x) for x in value.split(':'))
            moment = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=TZ)
            if moment > after:
                return int(moment.timestamp() * 1_000_000_000)
    return None


WEEKDAY_NAMES = ['Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7', 'Chủ nhật']


def describe(config: dict) -> str:
    """"Thứ 2 hằng tuần · 08:00"."""
    times = ', '.join(config.get('times') or [])
    frequency = config.get('frequency')
    if frequency == 'daily':
        return f'Hằng ngày{" (T2–T6)" if config.get("workdays_only") else ""} · {times}'
    if frequency == 'weekly':
        days = ', '.join(WEEKDAY_NAMES[d] for d in config.get('weekdays') or [])
        return f'{days} hằng tuần · {times}'
    if frequency == 'monthly':
        day = 'ngày cuối' if config.get('month_day') == -1 else f'ngày {config.get("month_day")}'
        return f'{day.capitalize()} hằng tháng · {times}'
    days = ', '.join(WEEKDAY_NAMES[d] for d in config.get('weekdays') or []) or 'Mỗi ngày'
    return f'{days} · {times}'
