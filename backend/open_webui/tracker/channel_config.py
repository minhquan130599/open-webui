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
    """A role or team by its name, ignoring case and accents (the model gives the bare name: "AM", "SOC")."""
    return (config.get('vai_tro') or {}).get(role_key(name or ''))


def apply_roles(state, config: dict) -> bool:
    """Items owned by a team the channel has now declared ("AM" -> its people) get those people; items given to a
    role follow its current people. Returns True when something changed."""
    changed = False
    for item in state.hang_muc:
        for team in list(item.doi_nhom):
            role = find_role(config, team)
            if not role or not role.get('nguoi'):
                continue
            owners = [o for o in item.phu_trach if o != team]
            item.phu_trach = list(dict.fromkeys([*owners, *role['nguoi']]))
            item.phu_trach_links = {**item.phu_trach_links, **(role.get('lien_ket') or {})}
            item.doi_nhom = [t for t in item.doi_nhom if t != team]
            item.nhom = item.nhom or role['ten']
            item.chua_giao = False
            changed = True
        # Given to a role: follows the role's current people. (Assigning by hand clears nhom, so it is kept.)
        if item.nhom and item.nhom != 'Cả nhóm' and not item.doi_nhom:
            role = find_role(config, item.nhom)
            if role and role.get('nguoi') and item.phu_trach != role['nguoi']:
                item.phu_trach = list(role['nguoi'])
                item.phu_trach_links = dict(role.get('lien_ket') or {})
                item.chua_giao = False
                changed = True
    return changed


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
            links = {**(current.get('lien_ket') or {}), **(role.get('lien_ket') or {})}
        else:
            names, links = list(dict.fromkeys(role['nguoi'])), dict(role.get('lien_ket') or {})
        links = {n: i for n, i in links.items() if n in names}
        new = {'ten': role['ten'], 'nguoi': names, 'ids': list(dict.fromkeys(links.values())), 'lien_ket': links, 'sua_tay': False,
               'nguon': list(dict.fromkeys([*(current or {}).get('nguon', []), *role.get('nguon', [])]))[-5:]}
        if new != current:
            config['vai_tro'][key] = new
            changed += 1
    return changed
