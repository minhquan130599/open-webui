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
from open_webui.tracker.schema import normalize_key

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
    shared: dict[str, int] = {}
    for user in users.values():
        key = normalize_key(display_name(user.name))
        shared[key] = shared.get(key, 0) + 1
    people = []
    for user_id in ids:
        user = users.get(user_id)
        people.append(
            {
                'id': user_id,
                'name': (user.name.strip() if shared.get(normalize_key(display_name(user.name)), 0) > 1 else display_name(user.name))
                if user
                else '(tài khoản đã xóa)',
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


async def _channel_people(channel_id: str) -> list:
    from open_webui.tracker.people import Person

    async with get_async_db_context() as db:
        ids = [
            r[0]
            for r in (
                await db.execute(
                    select(ChannelMember.user_id).where(
                        ChannelMember.channel_id == channel_id, ChannelMember.user_id.is_not(None)
                    )
                )
            ).all()
        ]
        channel = await db.get(Channel, channel_id)
    if channel and channel.user_id:
        ids.append(channel.user_id)
    users = await Users.get_users_by_user_ids(list(dict.fromkeys(ids))) if ids else []
    return [Person(id=u.id, name=u.name or '', email=u.email or '', role=u.role or '') for u in users]


async def _names(user_ids: list[str], channel_id: Optional[str] = None) -> dict[str, str]:
    """Names to show for these accounts: with the department when another member has the same name."""
    from open_webui.tracker.assign import shown_name

    people = await _channel_people(channel_id) if channel_id else []
    users = await Users.get_users_by_user_ids(list(dict.fromkeys(user_ids))) if user_ids else []
    from open_webui.tracker.people import Person

    known = {p.id: p for p in people}
    return {
        u.id: shown_name(known.get(u.id) or Person(id=u.id, name=u.name or ''), people or [Person(id=u.id, name=u.name or '')])
        for u in users
    }


async def set_roles(channel_id: str, roles: list[dict]) -> dict:
    """Replace the role table by hand ([{'ten', 'ids'}]); open items given to a role follow its new people."""
    names = await _names([i for r in roles for i in r.get('ids') or []], channel_id)
    table = {}
    for role in roles:
        title = str(role.get('ten') or '').strip()
        ids = [i for i in role.get('ids') or [] if i in names]
        if title:
            table[channel_config.role_key(title)] = {
                'ten': title, 'ids': ids, 'nguoi': [names[i] for i in ids], 'lien_ket': {names[i]: i for i in ids},
                'sua_tay': True, 'nguon': [],
            }

    def change(config):
        config['vai_tro'] = table

    config = await channel_config.update(channel_id, change)

    def follow(state) -> bool:
        changed = channel_config.apply_roles(state, config)
        for item in state.hang_muc:
            role = channel_config.find_role(config, item.nhom) if item.nhom and item.nhom != 'Cả nhóm' else None
            if role is not None and not item.doi_nhom and item.phu_trach != role['nguoi']:
                item.phu_trach, item.phu_trach_ids = list(role['nguoi']), list(role['ids'])
                item.phu_trach_links = dict(role.get('lien_ket') or {})
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
    names = await _names(user_ids, channel_id)
    chosen = [i for i in user_ids if i in names]
    if not chosen:
        return False

    def change(state) -> bool:
        for item in state.hang_muc:
            if item.id == item_id:
                item.phu_trach = [names[i] for i in chosen]
                item.phu_trach_ids = chosen
                item.phu_trach_links = {names[i]: i for i in chosen}
                item.chua_giao = False
                item.nhom = None
                item.ben_ngoai = []
                return True
        return False

    if not await Trackers.edit_state(channel_id, change):
        return False
    # Remembered only for a name nobody in the channel shares: "Trà My" when two members are called Trà My would
    # send every later "Trà My" to one of them.
    if remember and len(chosen) == 1 and normalize_key(remember) and not await _shared_name(channel_id, remember):

        def alias(config):
            config['bi_danh'][normalize_key(remember)] = chosen[0]

        await channel_config.update(channel_id, alias)
    return True


async def _shared_name(channel_id: str, name: str) -> bool:
    tokens = normalize_key(name).split()
    people = await _channel_people(channel_id)
    same = [p for p in people if normalize_key(display_name(p.name)).split()[-len(tokens):] == tokens] if tokens else []
    return len(same) > 1


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


async def transfer_owner(
    channel_id: str, user_id: str, keep_previous_as_manager: bool = True, require_member: bool = True
) -> bool:
    """Make another active member the channel owner; the previous owner stays as a manager (unless they left).
    A standard channel (access by groups, no member list) may also go to someone who wrote in it."""
    async with get_async_db_context() as db:
        channel = await db.get(Channel, channel_id)
        member = (
            await db.execute(
                select(ChannelMember).where(
                    ChannelMember.channel_id == channel_id, ChannelMember.user_id == user_id, ChannelMember.is_active.is_(True)
                )
            )
        ).scalars().first()
        group = bool(channel and channel.type in ('group', 'dm'))
        if not channel or (not member and (require_member or group)):
            return False
        previous = channel.user_id
        channel.user_id = user_id
        channel.updated_at = time.time_ns()
        if member:
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


SUCCESSION_DAYS = 90
SUCCESSION_REASONS = {
    'manager': 'người quản lý kênh được bổ nhiệm sớm nhất',
    'admin': 'admin hệ thống trong kênh',
    'active': f'người nhắn nhiều nhất trong kênh {SUCCESSION_DAYS} ngày qua',
    'member': 'thành viên vào kênh sớm nhất',
    'system_admin': 'admin hệ thống lâu năm nhất',
}


async def pick_successor(channel) -> tuple[Optional[str], Optional[str]]:
    """Who takes over a channel whose owner left: (user id, reason) or (None, None).

    1. its managers (team leads), the earliest named first;
    2. a system admin among its members (group channel) or among the people who wrote in it (standard channel);
    3. whoever wrote the most in it over the last 90 days;
    4. group channel: the earliest member; standard channel: the longest-standing system admin.
    Only active accounts count; in a group channel only active members."""
    from open_webui.models.messages import Message

    group = channel.type in ('group', 'dm')
    async with get_async_db_context() as db:
        members = (
            await db.execute(
                select(ChannelMember)
                .where(ChannelMember.channel_id == channel.id, ChannelMember.user_id.is_not(None), ChannelMember.is_active.is_(True))
                .order_by(ChannelMember.joined_at)
            )
        ).scalars().all()
        since = time.time_ns() - SUCCESSION_DAYS * 86400 * 1_000_000_000
        rows = (
            await db.execute(
                select(Message.user_id, Message.meta)
                .where(Message.channel_id == channel.id, Message.created_at >= since, Message.user_id.is_not(None))
                .limit(5000)
            )
        ).all()
    posts: dict[str, int] = {}
    for user_id, meta in rows:
        if (meta or {}).get('model_id') or (meta or {}).get('automation_id'):
            continue  # assistant replies and automations are not people
        posts[user_id] = posts.get(user_id, 0) + 1
    member_ids = [m.user_id for m in members if m.user_id != channel.user_id]
    ids = list(dict.fromkeys([*member_ids, *posts]))
    users = {u.id: u for u in await Users.get_users_by_user_ids(ids)} if ids else {}

    def eligible(user_id: str) -> bool:
        user = users.get(user_id)
        if not user or user.role in LEFT_ROLES or user_id == channel.user_id:
            return False
        return not group or user_id in member_ids

    managers = [m.user_id for m in members if m.role == 'manager' and eligible(m.user_id)]
    if managers:
        return managers[0], 'manager'
    pool = member_ids if group else list(posts)
    admins = [i for i in pool if eligible(i) and users[i].role == 'admin']
    if admins:
        return max(admins, key=lambda i: posts.get(i, 0)), 'admin'
    writers = sorted((i for i in posts if eligible(i)), key=lambda i: -posts[i])
    if writers:
        return writers[0], 'active'
    if group:
        rest = [i for i in member_ids if eligible(i)]
        return (rest[0], 'member') if rest else (None, None)
    from open_webui.models.users import User

    async with get_async_db_context() as db:
        admin_ids = (
            await db.execute(select(User.id).where(User.role == 'admin', User.id != channel.user_id).order_by(User.created_at))
        ).scalars().all()
    return (admin_ids[0], 'system_admin') if admin_ids else (None, None)


async def handover_left_owners(channel_ids: list[str]) -> list[dict]:
    """Channels whose owner's account is disabled or deleted pass to a successor (pick_successor), who is told.
    Returns [{'channel_id', 'channel', 'from', 'to', 'reason'}] (to None when nobody qualifies: an admin must choose)."""
    moves = []
    async with get_async_db_context() as db:
        channels = (await db.execute(select(Channel).where(Channel.id.in_(channel_ids)))).scalars().all() if channel_ids else []
    owners = {c.user_id for c in channels if c.user_id}
    users = {u.id: u for u in await Users.get_users_by_user_ids(list(owners))} if owners else {}
    for channel in channels:
        owner = users.get(channel.user_id)
        if owner and owner.role not in LEFT_ROLES:
            continue
        successor, reason = await pick_successor(channel)
        if successor and await transfer_owner(channel.id, successor, keep_previous_as_manager=False, require_member=False):
            await _tell_new_owner(channel, successor, reason)
        else:
            successor = None
        moves.append({'channel_id': channel.id, 'channel': channel.name, 'from': channel.user_id, 'to': successor,
                      'reason': SUCCESSION_REASONS.get(reason or '', '')})
    return moves


async def _tell_new_owner(channel, user_id: str, reason: Optional[str]) -> None:
    try:
        from open_webui.internal.db import get_async_db
        from open_webui.models.notifications import emit_changes, insert_notifications

        async with get_async_db() as db:
            await insert_notifications(
                recipients={user_id: 'tracker.due'},
                event_key=f'tracker.owner:{channel.id}:{user_id}',
                resource_type='channel',
                resource_id=channel.id,
                object_type='channel',
                object_id=channel.id,
                data={'message': f'Bạn đã trở thành chủ kênh "{channel.name}" vì tài khoản chủ kênh cũ đã bị khóa '
                                 f'({SUCCESSION_REASONS.get(reason or "", "")}). Có thể chuyển cho người khác trong Chỉnh sửa kênh.',
                      'kind': 'owner_handover'},
                db=db,
            )
            await db.commit()
        await emit_changes({user_id})
    except Exception:
        log.exception('Could not tell %s about owning channel %s', user_id, channel.id)
