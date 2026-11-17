"""Per-channel settings of the tracker that people edit: roles and teams, people left out of work given to "everyone",
and aliases of names that do not match an account.

Kept apart from the project state (a tracker_item row "cfg:<channel>"), because a tracker run writes the state it
computed from an older snapshot: an edit made meanwhile would be lost. Roles the tracker learns from messages
("AM: @Tuấn Anh, @Nghĩa") are merged in here too, but never over a role a person edited.

    vai_tro           {"AM": {"ten": "AM", "nguoi": [names], "ids": [user ids], "sua_tay": bool, "nguon": [...]}}
                      keyed by normalize_key(role)
    khong_nhan_chung  [user ids] left out of work given to everyone (admins, the owner and managers always are)
    bi_danh           {normalize_key(name): user id} a name in messages that means this account in this channel
"""

import time
from typing import Optional

from open_webui.internal.db import get_async_db_context
from open_webui.tracker.models import TrackerItem
from open_webui.tracker.schema import normalize_key

EMPTY = {'vai_tro': {}, 'khong_nhan_chung': [], 'bi_danh': {}}


def _id(channel_id: str) -> str:
    return f'cfg:{channel_id}'


def _clean(data: Optional[dict]) -> dict:
    data = dict(data or {})
    return {
        'vai_tro': dict(data.get('vai_tro') or {}),
        'khong_nhan_chung': [str(i) for i in data.get('khong_nhan_chung') or [] if i],
        'bi_danh': {str(k): str(v) for k, v in (data.get('bi_danh') or {}).items() if k and v},
    }


async def get(channel_id: str) -> dict:
    async with get_async_db_context() as db:
        row = await db.get(TrackerItem, _id(channel_id))
        return _clean(row.data if row else None)


async def update(channel_id: str, change) -> dict:
    """change(config) edits the config in place; saved in one transaction. Returns the new config."""
    now = time.time_ns()
    async with get_async_db_context() as db:
        row = await db.get(TrackerItem, _id(channel_id), with_for_update=True)
        config = _clean(row.data if row else None)
        change(config)
        if row:
            row.data = config
            row.period_end = now
        else:
            db.add(
                TrackerItem(
                    id=_id(channel_id),
                    channel_id=channel_id,
                    source_type='channel_config',
                    source_ids=[],
                    period_start=now,
                    period_end=now,
                    summary='',
                    data=config,
                    created_at=now,
                )
            )
        await db.commit()
        return config


def role_key(name: str) -> str:
    return normalize_key(name)


def find_role(config: dict, name: str) -> Optional[dict]:
    """A role or team by name, ignoring case, accents and a plural word in front ("các AM", "team SOC")."""
    key = role_key(name)
    roles = config.get('vai_tro') or {}
    if key in roles:
        return roles[key]
    for prefix in ('cac ', 'nhung ', 'team ', 'nhom ', 'doi ', 'ben ', 'phong '):
        if key.startswith(prefix) and key[len(prefix):] in roles:
            return roles[key[len(prefix):]]
    return None


def learn_roles(config: dict, learned: list[dict]) -> int:
    """Merge roles read from messages ({'ten', 'nguoi', 'ids', 'thay_the', 'nguon'}); a role edited by hand is kept.
    Returns how many roles changed."""
    changed = 0
    for role in learned:
        key = role_key(role['ten'])
        if not key or not role.get('nguoi'):
            continue
        current = config['vai_tro'].get(key)
        if current and current.get('sua_tay'):
            continue
        if current and not role.get('thay_the'):
            names = list(dict.fromkeys([*current.get('nguoi', []), *role['nguoi']]))
            ids = list(dict.fromkeys([*current.get('ids', []), *role.get('ids', [])]))
        else:
            names, ids = list(dict.fromkeys(role['nguoi'])), list(dict.fromkeys(role.get('ids', [])))
        new = {'ten': role['ten'], 'nguoi': names, 'ids': ids, 'sua_tay': False,
               'nguon': list(dict.fromkeys([*(current or {}).get('nguon', []), *role.get('nguon', [])]))[-5:]}
        if new != current:
            config['vai_tro'][key] = new
            changed += 1
    return changed
