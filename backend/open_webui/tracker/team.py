"""People of a project channel for the tracker: owner, managers (team leads), roles, people left out of work given to
everyone, aliases, quick assignment, owner transfer and accounts that were disabled.

A channel has one owner (channel.user_id) and any number of managers (channel_member.role = 'manager'). Managers
run the channel's progress like the owner: assign, close and cancel items, edit roles, see the team view, get the
owner emails. The owner (or an admin) names managers and can hand the channel over to another member, so a team
lead leaving the company does not mean a new channel. Disabled accounts (role "pending", or deleted) are "left":
their items are flagged for reassignment, and a channel whose owner left passes to its longest-serving manager.
"""

import logging
import time
from typing import Optional

from sqlalchemy import select, update

from open_webui.internal.db import get_async_db_context
from open_webui.models.channels import Channel, ChannelMember
from open_webui.models.users import Users
from open_webui.tracker import channel_config
from open_webui.tracker.assign import display_name
from open_webui.tracker.models import Trackers
from open_webui.tracker.schema import load_state, normalize_key

log = logging.getLogger(__name__)

LEFT_ROLES = ('pending',)  # an account the admin (or SCIM) disabled


async def manager_ids(channel_id: str) -> set[str]:
    async with get_async_db_context() as db:
        rows = await db.execute(
            select(ChannelMember.user_id).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.role == 'manager',
                ChannelMember.is_active.is_(True),
            )
        )
        return {r[0] for r in rows.all() if r[0]}


async def managed_channel_ids(user_id: str, channel_ids: list[str]) -> set[str]:
    """Channels among channel_ids that the user manages as a manager (owners are checked by the caller)."""
    if not channel_ids:
        return set()
    async with get_async_db_context() as db:
        rows = await db.execute(
            select(ChannelMember.channel_id).where(
                ChannelMember.user_id == user_id,
                ChannelMember.role == 'manager',
                ChannelMember.is_active.is_(True),
                ChannelMember.channel_id.in_(channel_ids),
            )
        )
        return {r[0] for r in rows.all()}


async def can_manage(channel, user) -> bool:
    if user.role == 'admin' or channel.user_id == user.id:
        return True
    return channel.id in await managed_channel_ids(user.id, [channel.id])


async def team(channel) -> dict:
    """Members with their place in the channel, the role table and the aliases, for the "Members & roles" panel."""
    async with get_async_db_context() as db:
        members = (
            await db.execute(
                select(ChannelMember).where(ChannelMember.channel_id == channel.id, ChannelMember.user_id.is_not(None))
            )
        ).scalars().all()
    active = [m for m in members if m.is_active]
    ids = list({m.user_id for m in active} | ({channel.user_id} if channel.user_id else set()))
    users = {u.id: u for u in await Users.get_users_by_user_ids(ids)} if ids else {}
    config = await channel_config.get(channel.id)
    managers = {m.user_id for m in active if m.role == 'manager'}
    left_out = set(config['khong_nhan_chung'])
    people = []
    for user_id in ids:
        user = users.get(user_id)
        people.append(
            {
                'id': user_id,
                'name': display_name(user.name) if user else '(tài khoản đã xóa)',
                'email': user.email if user else '',
                'is_owner': user_id == channel.user_id,
                'is_manager': user_id in managers,
                'is_admin': bool(user and user.role == 'admin'),
                'left': not user or user.role in LEFT_ROLES,
                # Never given work meant for everyone: admins, owner, managers always; others when marked.
                'left_out': user_id in left_out,
                'left_out_auto': bool(user and user.role == 'admin') or user_id == channel.user_id or user_id in managers,
            }
        )
    people.sort(key=lambda p: (not p['is_owner'], not p['is_manager'], p['name'].lower()))
    roles = [
        {'key': key, 'ten': r['ten'], 'ids': r.get('ids') or [], 'nguoi': r.get('nguoi') or [], 'sua_tay': bool(r.get('sua_tay'))}
        for key, r in sorted(config['vai_tro'].items())
    ]
    aliases = [{'ten': k, 'user_id': v, 'user': people_name(v, users)} for k, v in sorted(config['bi_danh'].items())]
    return {'members': people, 'roles': roles, 'aliases': aliases}


def people_name(user_id: str, users: dict) -> str:
    user = users.get(user_id)
    return display_name(user.name) if user else ''


async def _names(user_ids: list[str]) -> dict[str, str]:
    users = await Users.get_users_by_user_ids(list(dict.fromkeys(user_ids))) if user_ids else []
    return {u.id: display_name(u.name) for u in users}


async def set_roles(channel_id: str, roles: list[dict]) -> dict:
    """Replace the role table by hand ([{'ten', 'ids'}]); open items given to a role follow its new people."""
    names = await _names([i for r in roles for i in r.get('ids') or []])
    table = {}
    for role in roles:
        title = str(role.get('ten') or '').strip()
        ids = [i for i in role.get('ids') or [] if i in names]
        if title:
            table[channel_config.role_key(title)] = {
                'ten': title, 'ids': ids, 'nguoi': [names[i] for i in ids], 'sua_tay': True, 'nguon': []
            }

    def change(config):
        config['vai_tro'] = table

    config = await channel_config.update(channel_id, change)

    def follow(state) -> bool:
        changed = False
        for item in state.hang_muc:
            role = channel_config.find_role(config, item.nhom) if item.nhom and item.nhom != 'Cả nhóm' else None
            if role is not None and item.phu_trach != role['nguoi']:
                item.phu_trach, item.phu_trach_ids = list(role['nguoi']), list(role['ids'])
                item.chua_giao = not item.phu_trach
                changed = True
        return changed

    await Trackers.edit_state(channel_id, follow)
    return config


async def set_left_out(channel_id: str, user_ids: list[str]) -> dict:
    def change(config):
        config['khong_nhan_chung'] = list(dict.fromkeys(user_ids))

    return await channel_config.update(channel_id, change)


async def assign_item(channel_id: str, item_id: str, user_ids: list[str], remember: Optional[str]) -> bool:
    """Give an item to accounts chosen by hand. With remember (the unlinked name it had, "Duy") and one account,
    that name means this account in the channel from now on."""
    names = await _names(user_ids)
    chosen = [i for i in user_ids if i in names]
    if not chosen:
        return False

    def change(state) -> bool:
        for item in state.hang_muc:
            if item.id == item_id:
                item.phu_trach = [names[i] for i in chosen]
                item.phu_trach_ids = chosen
                item.chua_giao = False
                item.nhom = None
                item.ben_ngoai = []
                return True
        return False

    if not await Trackers.edit_state(channel_id, change):
        return False
    if remember and len(chosen) == 1 and normalize_key(remember):

        def alias(config):
            config['bi_danh'][normalize_key(remember)] = chosen[0]

        await channel_config.update(channel_id, alias)
    return True


async def remove_alias(channel_id: str, name: str) -> None:
    def change(config):
        config['bi_danh'].pop(normalize_key(name), None)

    await channel_config.update(channel_id, change)


async def set_manager(channel_id: str, user_id: str, manager: bool) -> bool:
    async with get_async_db_context() as db:
        result = await db.execute(
            update(ChannelMember)
            .where(ChannelMember.channel_id == channel_id, ChannelMember.user_id == user_id)
            .values(role='manager' if manager else None, updated_at=time.time_ns())
        )
        await db.commit()
        return bool(result.rowcount)


async def transfer_owner(channel_id: str, user_id: str, keep_previous_as_manager: bool = True) -> bool:
    """Make another active member the channel owner; the previous owner stays as a manager (unless they left)."""
    async with get_async_db_context() as db:
        channel = await db.get(Channel, channel_id)
        member = (
            await db.execute(
                select(ChannelMember).where(
                    ChannelMember.channel_id == channel_id, ChannelMember.user_id == user_id, ChannelMember.is_active.is_(True)
                )
            )
        ).scalars().first()
        if not channel or not member:
            return False
        previous = channel.user_id
        channel.user_id = user_id
        channel.updated_at = time.time_ns()
        member.role = None  # the owner needs no manager role
        if previous and previous != user_id and keep_previous_as_manager:
            before = (
                await db.execute(
                    select(ChannelMember).where(ChannelMember.channel_id == channel_id, ChannelMember.user_id == previous)
                )
            ).scalars().first()
            user = await Users.get_user_by_id(previous)
            if before and user and user.role not in LEFT_ROLES:
                before.role = 'manager'
        await db.commit()
    log.info('Channel %s: owner %s -> %s', channel_id, previous, user_id)
    return True


async def handover_left_owners(channel_ids: list[str]) -> list[dict]:
    """Channels whose owner's account is disabled or deleted pass to their longest-serving active manager.
    Returns [{'channel_id', 'from', 'to'}] (to None when the channel has no manager: an admin must choose)."""
    moves = []
    async with get_async_db_context() as db:
        channels = (await db.execute(select(Channel).where(Channel.id.in_(channel_ids)))).scalars().all() if channel_ids else []
    owners = {c.user_id for c in channels if c.user_id}
    users = {u.id: u for u in await Users.get_users_by_user_ids(list(owners))} if owners else {}
    for channel in channels:
        owner = users.get(channel.user_id)
        if owner and owner.role not in LEFT_ROLES:
            continue
        async with get_async_db_context() as db:
            managers = (
                await db.execute(
                    select(ChannelMember)
                    .where(
                        ChannelMember.channel_id == channel.id,
                        ChannelMember.role == 'manager',
                        ChannelMember.is_active.is_(True),
                    )
                    .order_by(ChannelMember.joined_at)
                )
            ).scalars().all()
        candidates = [m.user_id for m in managers if m.user_id]
        active = {u.id for u in await Users.get_users_by_user_ids(candidates) if u.role not in LEFT_ROLES} if candidates else set()
        successor = next((c for c in candidates if c in active), None)
        if successor:
            await transfer_owner(channel.id, successor, keep_previous_as_manager=False)
        moves.append({'channel_id': channel.id, 'channel': channel.name, 'from': channel.user_id, 'to': successor})
    return moves
