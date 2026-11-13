"""Work items per person across projects: "my work", a channel owner's view of their team, due reminders.

Owners are the user ids linked to an item (phu_trach_ids). Items stored before owners were linked, or whose
owner joined the channel later, are matched by name at read time with the same unambiguous rule.
"""

import datetime as dt
import time
from dataclasses import asdict, dataclass
from typing import Optional

from open_webui.models.channels import Channels
from open_webui.models.users import Users
from open_webui.tracker.models import Trackers
from open_webui.tracker.people import channel_people, match_person
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
        for index, item in enumerate(row.state.get('hang_muc') or []):
            if not isinstance(item, dict) or not item.get('ten'):
                continue
            names = [n for n in item.get('phu_trach') or [] if isinstance(n, str)]
            owner_ids = [i for i in item.get('phu_trach_ids') or [] if isinstance(i, str)]
            if names and len(owner_ids) < len(names):
                if people is None:
                    people = await channel_people(channel)
                for name in names:
                    user_id = match_person(name, people)
                    if user_id and user_id not in owner_ids:
                        owner_ids.append(user_id)
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
            )
            classify(entry, day)
            entries.append(entry)
    return entries


def project_channels(channels: list) -> list:
    return [c for c in channels if c.type != 'dm' and not c.deleted_at]


async def readable_channels(user) -> list:
    """Channels the user is in (member or granted access). Admins get no bypass: a channel an admin has not
    joined stays out of their work pages."""
    return project_channels(await Channels.get_channels_by_user_id(user.id))


async def managed_channels(user) -> list:
    """Channels whose members' work the user may see: for an admin every channel they are in, else the
    channels they own."""
    channels = await readable_channels(user)
    return channels if user.role == 'admin' else [c for c in channels if c.user_id == user.id]


async def work_of(viewer, target_id: str) -> Optional[list[WorkEntry]]:
    """The target's open items the viewer may see, or None when the viewer may not look at the target."""
    if target_id == viewer.id:
        channels = await readable_channels(viewer)
    else:
        channels = await managed_channels(viewer)
        if not channels:
            return None
    entries = [e for e in await collect(channels) if target_id in e.owner_ids]
    for entry in entries:
        entry.can_manage = viewer.role == 'admin' or entry.channel_owner_id == viewer.id
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
