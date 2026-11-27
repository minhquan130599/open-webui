"""Settings of the Telegram bot (config table), shared by scheduled reports and asking the AI.

The bot token used to be stored under reports.telegram.*; those keys are read while the new ones are not set,
so an existing setup keeps working without being entered again.
"""

from typing import Any

from open_webui.models.config import Config

# Scheduled reports are not sent by Telegram for now (email still is): the delivery skips Telegram, the report form
# hides it, and the bot no longer offers it. Schedules keep their Telegram choice, so it can come back by setting
# this to False.
REPORTS_PAUSED = True

# key, type, default
SETTINGS: list[tuple[str, str, Any]] = [
    ('telegram.bot_token', 'secret', ''),
    ('telegram.bot_username', 'text', ''),
    ('telegram.reports', 'bool', False),  # send scheduled reports by Telegram (paused for now: REPORTS_PAUSED)
    # Post messages into channels from Telegram (users with the features.telegram permission). The bot no longer
    # chats with the AI: messages only go one way, Telegram -> channel, as ordinary messages of the user.
    ('telegram.ask', 'bool', False),
    ('telegram.ask_channels', 'bool', True),  # no longer used (channels are the only thing the bot does)
    ('telegram.rate_per_minute', 'int:1-120', 10),
    # The next five were for answers of the AI in Telegram; kept so saved values stay valid, no longer used.
    ('telegram.files', 'bool', True),
    ('telegram.model', 'text', ''),
    ('telegram.max_running', 'int:1-50', 5),
    ('telegram.new_chat_hours', 'int:1-168', 4),
    ('telegram.answer_minutes', 'int:1-30', 30),
    ('telegram.blocked_channels', 'list', []),  # channel ids that cannot receive messages from Telegram
]
BY_KEY = {key: (kind, default) for key, kind, default in SETTINGS}
LEGACY = {
    'telegram.bot_token': 'reports.telegram.bot_token',
    'telegram.bot_username': 'reports.telegram.bot_username',
    'telegram.reports': 'reports.telegram.enable',
}


def _coerce(key: str, value):
    kind, default = BY_KEY[key]
    if value is None:
        return default
    if kind == 'bool':
        return value if isinstance(value, bool) else str(value).lower() in ('1', 'true', 'on', 'yes')
    if kind.startswith('int:'):
        low, high = (int(x) for x in kind[4:].split('-'))
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f'{key}: phải là số nguyên')
        if not low <= number <= high:
            raise ValueError(f'{key}: phải trong khoảng {low}–{high}')
        return number
    if kind == 'list':
        if isinstance(value, str):
            value = [v for v in value.replace('\n', ',').split(',')]
        if not isinstance(value, list):
            raise ValueError(f'{key}: phải là danh sách')
        return [str(v).strip() for v in value if str(v).strip()][:500]
    return str(value).strip()


async def get_settings() -> dict:
    stored = await Config.get_many(*BY_KEY, *LEGACY.values())
    values = {}
    for key, (kind, default) in BY_KEY.items():
        raw = stored.get(key)
        if raw is None and key in LEGACY:
            raw = stored.get(LEGACY[key])
        try:
            values[key] = _coerce(key, raw) if raw is not None else default
        except ValueError:
            values[key] = default
    return values


def public(values: dict) -> dict:
    """Settings without the bot token (shown as set / not set)."""
    out = {k: v for k, v in values.items() if BY_KEY[k][0] != 'secret'}
    out['telegram.bot_token_set'] = bool(values.get('telegram.bot_token'))
    return out


async def save_settings(changes: dict) -> dict:
    """Validated changes saved; an empty secret keeps the saved one."""
    unknown = [k for k in changes if k not in BY_KEY]
    if unknown:
        raise ValueError('Không có cài đặt: ' + ', '.join(unknown))
    updates = {}
    for key, value in changes.items():
        if BY_KEY[key][0] == 'secret' and not value:
            continue
        updates[key] = _coerce(key, value)
    if updates:
        await Config.upsert(updates)
    return await get_settings()


def bot_ready(values: dict) -> bool:
    return bool(values.get('telegram.bot_token'))
