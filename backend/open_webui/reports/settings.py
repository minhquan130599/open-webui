"""Admin settings of scheduled reports (config table, editable from Admin settings and the monitor)."""

from typing import Any

from open_webui.models.config import Config

DEFAULT_PROMPT = (
    'Tóm tắt tiến độ các kênh trong kỳ cho người quản lý: mỗi kênh 2-3 câu về việc đã xong, việc đang làm và việc '
    'trễ hạn; nêu các vướng mắc cần người khác gỡ; kết thúc bằng tối đa 3 việc cần chú ý kỳ tới. Viết bằng tiếng '
    'Việt, ngắn gọn, không bịa số liệu.'
)

# key, type, default
SETTINGS: list[tuple[str, str, Any]] = [
    ('reports.enable', 'bool', True),
    ('reports.who', 'enum:all|owners|admins', 'all'),
    ('reports.hidden_mode', 'enum:receiving|hidden', 'receiving'),
    ('reports.allow_prompt', 'bool', True),
    ('reports.allow_outside', 'bool', True),
    ('reports.email.enable', 'bool', True),
    ('reports.email.internal_only', 'bool', False),
    ('reports.telegram.enable', 'bool', False),
    ('reports.telegram.bot_token', 'secret', ''),
    ('reports.telegram.bot_username', 'text', ''),
    ('reports.telegram.allow_groups', 'bool', True),
    ('reports.max_schedules', 'int:1-100', 5),
    ('reports.max_channels', 'int:1-500', 20),
    ('reports.default_prompt', 'text', DEFAULT_PROMPT),
    ('reports.model', 'text', ''),
    ('reports.max_chars', 'int:300-10000', 1500),
    # Queue: comments are written ahead of the send time, only while the model is not busy.
    ('reports.prepare_minutes', 'int:5-720', 60),
    ('reports.workers', 'int:1-4', 1),
    ('reports.busy_waiting', 'int:0-100', 0),
    ('reports.busy_seconds', 'int:3-120', 15),
    ('reports.late_minutes', 'int:0-240', 10),
    ('reports.email.mode', 'enum:bcc|to|separate', 'bcc'),
]
BY_KEY = {key: (kind, default) for key, kind, default in SETTINGS}

# Telegram groups are switched off for now, whatever the admin setting says: groups cannot be linked
# and reports are not sent to groups linked earlier. Set to True to give the admin setting back.
TELEGRAM_GROUPS_ENABLED = False


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
    if kind.startswith('enum:'):
        value = str(value)
        if value not in kind[5:].split('|'):
            raise ValueError(f'{key}: giá trị không hợp lệ')
        return value
    return '' if value is None else str(value).strip()


async def get_settings() -> dict:
    stored = await Config.get_many(*BY_KEY)
    values = {}
    for key, (kind, default) in BY_KEY.items():
        try:
            values[key] = _coerce(key, stored.get(key)) if stored.get(key) is not None else default
        except ValueError:
            values[key] = default
    if not values['reports.default_prompt']:
        values['reports.default_prompt'] = DEFAULT_PROMPT
    if not TELEGRAM_GROUPS_ENABLED:
        values['reports.telegram.allow_groups'] = False
    return values


def public(values: dict) -> dict:
    """Settings without the bot token (shown as set / not set)."""
    out = {k: v for k, v in values.items() if BY_KEY[k][0] != 'secret'}
    out['reports.telegram.bot_token_set'] = bool(values.get('reports.telegram.bot_token'))
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
