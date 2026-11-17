"""Work items per person across projects: "my work", a channel owner's view of their team, due reminders.

Owners are the user ids linked to an item (phu_trach_ids). Items stored before owners were linked, or whose
owner joined the channel later, are matched by name at read time with the same unambiguous rule.
"""

import datetime as dt
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from open_webui.models.channels import Channels
from open_webui.models.users import Users
from open_webui.tracker import channel_config, team
from open_webui.tracker.models import Trackers
from open_webui.tracker.people import channel_people, match_person
from open_webui.tracker.schema import normalize_key
from open_webui.tracker.team import LEFT_ROLES
from open_webui.tracker.prompts import format_time
from open_webui.tracker.service import TZ

BUCKETS = ('overdue', 'today', 'week', 'later', 'no_due', 'done')


@dataclass
class WorkEntry:
    channel_id: str
    channel_name: str
    id: str
    ten: str
    phu_trach: list[str]
    owner_ids: list[str]
    trang_thai: str
    tien_do: Optional[int]
    han: Optional[str]
    vuong_mac: Optional[str]
    cap_nhat_luc: Optional[str]
    bucket: str = ''
    days_left: Optional[int] = None
    channel_owner_id: Optional[str] = None
    can_manage: bool = False
    chua_giao: bool = False
    nhom: Optional[str] = None
    ben_ngoai: list = field(default_factory=list)
    # One per owner name: {'name', 'id', 'status'}: linked | outside (not a channel member) | external (another
    # company) | left (account disabled or deleted)
    owners: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def today() -> str:
    return format_time(time.time_ns(), TZ)[:10]


def _days_between(start: str, end: str) -> Optional[int]:
    try:
        return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
    except ValueError:
        return None


def classify(entry: WorkEntry, day: str) -> None:
    entry.days_left = _days_between(day, entry.han) if entry.han else None
    if entry.trang_thai == 'hoan_thanh':
        entry.bucket = 'done'
    elif entry.days_left is None:
        entry.bucket = 'no_due'
    elif entry.days_left < 0:
        entry.bucket = 'overdue'
    elif entry.days_left == 0:
        entry.bucket = 'today'
    elif entry.days_left <= 7:
        entry.bucket = 'week'
    else:
        entry.bucket = 'later'


async def collect(channels: list) -> list[WorkEntry]:
    """Every open (not closed or cancelled) work item of the channels, with resolved owner ids."""
    by_id = {c.id: c for c in channels}
    rows = await Trackers.get_states(list(by_id)) if by_id else []
    # States stored before items had ids get them now, so every listed item can be closed or cancelled.
    missing = [r.channel_id for r in rows if any(not i.get('id') for i in (r.state or {}).get('hang_muc') or [])]
    for channel_id in missing:
        await Trackers.ensure_item_ids(channel_id)
    if missing:
        rows = await Trackers.get_states(list(by_id))
    day = today()
    entries: list[WorkEntry] = []
    for row in rows:
        channel = by_id.get(row.channel_id)
        if not channel or not row.state:
            continue
        people = None
        aliases = (await channel_config.get(channel.id)).get('bi_danh') or {}
        for index, item in enumerate(row.state.get('hang_muc') or []):
            if not isinstance(item, dict) or not item.get('ten'):
                continue
            names = [n for n in item.get('phu_trach') or [] if isinstance(n, str)]
            outside_company = [n for n in item.get('ben_ngoai') or [] if isinstance(n, str)]
            teams = [n for n in item.get('doi_nhom') or [] if isinstance(n, str)]
            owner_ids = [i for i in item.get('phu_trach_ids') or [] if isinstance(i, str)]
            if people is None:
                people = await channel_people(channel)
            owners = []
            for name in names:
                if name in outside_company:
                    owners.append({'name': name, 'id': None, 'status': 'external'})
                    continue
                if name in teams:
                    owners.append({'name': name, 'id': None, 'status': 'team'})
                    continue
                user_id = aliases.get(normalize_key(name)) or match_person(name, people)
                if user_id and user_id not in owner_ids:
                    owner_ids.append(user_id)
                owners.append({'name': name, 'id': user_id, 'status': 'linked' if user_id else 'outside'})
            entry = WorkEntry(
                channel_id=channel.id,
                channel_name=channel.name,
                id=item.get('id') or f'{channel.id}:{index}',
                ten=item['ten'],
                phu_trach=names,
                owner_ids=owner_ids,
                trang_thai=item.get('trang_thai') or 'dang_lam',
                tien_do=item.get('tien_do'),
                han=item.get('han'),
                vuong_mac=item.get('vuong_mac'),
                cap_nhat_luc=item.get('cap_nhat_luc'),
                channel_owner_id=channel.user_id,
                chua_giao=bool(item.get('chua_giao')),
                nhom=item.get('nhom'),
                ben_ngoai=outside_company,
                owners=owners,
            )
            classify(entry, day)
            entries.append(entry)
    # Accounts disabled (or deleted) since they were given the work: shown as "left", reminders skip them.
    linked = {o['id'] for e in entries for o in e.owners if o['id']} | {i for e in entries for i in e.owner_ids}
    if linked:
        active = {u.id for u in await Users.get_users_by_user_ids(list(linked)) if u.role not in LEFT_ROLES}
        for entry in entries:
            for owner in entry.owners:
                if owner['id'] and owner['id'] not in active:
                    owner['status'] = 'left'
            entry.owner_ids = [i for i in entry.owner_ids if i in active]
    return entries


def project_channels(channels: list) -> list:
    return [c for c in channels if c.type != 'dm' and not c.deleted_at]


async def readable_channels(user) -> list:
    """Channels the user is in (member or granted access). Admins get no bypass: a channel an admin has not
    joined stays out of their work pages."""
    return project_channels(await Channels.get_channels_by_user_id(user.id))


async def managed_ids(user, channels: list) -> set[str]:
    """Channels among these that the user runs: admins all, else the ones they own or manage (team lead)."""
    if user.role == 'admin':
        return {c.id for c in channels}
    owned = {c.id for c in channels if c.user_id == user.id}
    return owned | await team.managed_channel_ids(user.id, [c.id for c in channels if c.id not in owned])


async def managed_channels(user) -> list:
    """Channels whose members' work the user may see: for an admin every channel they are in, else the
    channels they own or manage."""
    channels = await readable_channels(user)
    ids = await managed_ids(user, channels)
    return [c for c in channels if c.id in ids]


async def work_of(viewer, target_id: str) -> Optional[list[WorkEntry]]:
    """The target's open items the viewer may see, or None when the viewer may not look at the target."""
    if target_id == viewer.id:
        channels = await readable_channels(viewer)
    else:
        channels = await managed_channels(viewer)
        if not channels:
            return None
    entries = [e for e in await collect(channels) if target_id in e.owner_ids]
    managed = await managed_ids(viewer, channels)
    for entry in entries:
        entry.can_manage = entry.channel_id in managed
    return entries


async def team_of(viewer) -> dict:
    """Members of the viewer's managed channels with counts of their open items, plus unassigned items."""
    channels = await managed_channels(viewer)
    entries = await collect(channels)
    member_ids = set()
    if viewer.role != 'admin' and channels:
        member_ids = {m.user_id for m in await Channels.get_members_by_channel_ids([c.id for c in channels])}
    counts: dict[str, dict] = {}
    unassigned = 0
    for entry in entries:
        if not entry.owner_ids:
            if entry.bucket != 'done':
                unassigned += 1
            continue
        for owner in entry.owner_ids:
            stats = counts.setdefault(owner, {bucket: 0 for bucket in BUCKETS})
            stats[entry.bucket] += 1
    ids = (member_ids | set(counts)) - {None}
    users = await Users.get_users_by_user_ids(list(ids)) if ids else []
    people = [
        {
            'id': u.id,
            'name': u.name,
            'email': u.email,
            'counts': counts.get(u.id, {bucket: 0 for bucket in BUCKETS}),
        }
        for u in users
    ]
    people.sort(key=lambda p: (-p['counts']['overdue'], -sum(p['counts'].values()), (p['name'] or '').lower()))
    return {'channels': [{'id': c.id, 'name': c.name} for c in channels], 'people': people, 'unassigned': unassigned}


async def board(viewer, mode: str) -> dict:
    """Work grouped by channel for the "My work" page.

    mine: the viewer's own items in every channel they are in, plus the unassigned items of the channels they
          manage (so an owner can assign, close or cancel them).
    team: every item of the channels the viewer manages (owners: their channels; admins: channels they are in).
    """
    readable = await readable_channels(viewer)
    managed = await managed_ids(viewer, readable)
    channels = readable if mode == 'mine' else [c for c in readable if c.id in managed]
    entries = await collect(channels)
    if mode == 'mine':
        entries = [e for e in entries if viewer.id in e.owner_ids or (not e.owner_ids and e.channel_id in managed)]
    for entry in entries:
        entry.can_manage = entry.channel_id in managed
    owner_ids = {o for e in entries for o in e.owner_ids}
    users = await Users.get_users_by_user_ids(list(owner_ids)) if owner_ids else []
    used = {e.channel_id for e in entries}
    return {
        'mode': mode,
        'today': today(),
        'can_view_team': bool(managed),
        'channels': [
            {'id': c.id, 'name': c.name, 'can_manage': c.id in managed}
            for c in channels
            if c.id in used or (mode == 'team')
        ],
        'people': {u.id: {'name': u.name, 'email': u.email} for u in users},
        'items': [e.to_dict() for e in entries],
    }
