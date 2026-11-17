"""Map the owner names the LLM read from messages ("Long", "anh Long") to user accounts.

Only a single unambiguous match is linked: two people who could both be "Long" leave the owner unlinked
rather than guessed, so "my work" never shows someone else's task.
"""

import re
from dataclasses import dataclass
from typing import Optional

from open_webui.models.channels import Channels
from open_webui.models.users import Users
from open_webui.tracker.schema import ProjectState, normalize_key

# Forms of address that precede a name in Vietnamese chats ("anh Long", "chị Lan", "sếp Hùng").
_HONORIFICS = {
    'anh', 'chi', 'em', 'a', 'c', 'e', 'ban', 'bac', 'co', 'chu', 'thay', 'sep', 'ong', 'ba', 'dong', 'mr', 'ms',
    'mrs', 'miss', 'sir',
}  # fmt: skip


@dataclass
class Person:
    id: str
    name: str
    email: str = ''
    role: str = ''  # system role: admin | user | pending


def _name_key(name: str) -> list[str]:
    # A role or team in brackets is not part of the name: "Long (BE)" is Long.
    tokens = normalize_key(re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', name or '')).split()
    while len(tokens) > 1 and tokens[0] in _HONORIFICS:
        tokens = tokens[1:]
    return tokens


def match_person(name: str, people: list[Person]) -> Optional[str]:
    """User id for an owner name, or None when nobody or more than one person matches."""
    tokens = _name_key(name)
    if not tokens:
        return None
    key = ' '.join(tokens)

    exact = [p for p in people if ' '.join(_name_key(p.name)) == key]
    if exact:
        return exact[0].id if len(exact) == 1 else None

    # "Long" or "Văn Long" against "Nguyễn Văn Long": the given name(s) end the full name.
    suffix = [p for p in people if _name_key(p.name)[-len(tokens) :] == tokens]
    if len(suffix) == 1:
        return suffix[0].id
    if len(suffix) > 1:
        return None

    compact = key.replace(' ', '')
    by_email = [p for p in people if p.email and normalize_key(p.email.split('@')[0]).replace(' ', '') == compact]
    return by_email[0].id if len(by_email) == 1 else None


async def channel_people(channel) -> list[Person]:
    """People who can own work in a channel: its members, its owner and everyone who has posted in it."""
    from open_webui.tracker.models import Trackers

    ids = set(await Trackers.get_author_ids(channel.id))
    if channel.user_id:
        ids.add(channel.user_id)
    try:
        ids.update(m.user_id for m in await Channels.get_members_by_channel_id(channel.id) if m.user_id)
    except Exception:
        pass  # channel types without a member list
    if not ids:
        return []
    return [
        Person(id=u.id, name=u.name or '', email=u.email or '', role=u.role or '')
        for u in await Users.get_users_by_user_ids(list(ids))
    ]


async def channel_roles(channel) -> tuple[list[str], set[str]]:
    """(active member ids, manager ids) of a channel; empty for channel types without a member list."""
    try:
        members = await Channels.get_members_by_channel_id(channel.id)
    except Exception:
        return [], set()
    active = [m for m in members if m.user_id and getattr(m, 'is_active', True)]
    return [m.user_id for m in active], {m.user_id for m in active if getattr(m, 'role', None) == 'manager'}


def link_owners(state: ProjectState, people: list[Person], aliases: Optional[dict] = None) -> None:
    """Fill phu_trach_ids from phu_trach for every open item; a channel alias ("Duy" -> an account) wins."""
    aliases = aliases or {}
    for item in state.hang_muc:
        ids = []
        for name in item.phu_trach:
            if name in item.ben_ngoai or name in item.doi_nhom:
                continue
            user_id = aliases.get(normalize_key(name)) or match_person(name, people)
            if user_id and user_id not in ids:
                ids.append(user_id)
        item.phu_trach_ids = ids
