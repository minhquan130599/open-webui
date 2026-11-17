"""Who owns a work item read from chat messages: the model says HOW the work was given, this code works out WHO.

The update model returns, per new item or owner change, `giao_cho`:

    ten       named or @tagged people ("@Tuấn Anh làm X", "@admin check hộ em") -> exactly them, admins included
    chuc_vu   a role or team ("các AM", "team SOC") -> its people in the channel's role table; while the channel has
              not declared it, the team itself ("AM") is the owner, and its people replace it once declared
    ca_nhom   everyone ("mọi người", "toàn team") -> channel members except system admins, the channel owner and
              managers, the sender and people marked "không nhận việc chung"; called with an explicit @ ("@all",
              "@mọi người") -> every member but the sender, admins and the owner included
    tu_nhan   the sender takes it on or reports their own work -> the sender
    khong_ro  nobody said -> unassigned

Every name is checked against the source messages: an @tag (its account id), the name written in the text, the
account's e-mail name, or an alias of the channel. A name the messages do not contain is dropped, so "toàn team"
can never become the member list, and a new item without a valid source message is not created.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from open_webui.tracker.people import Person
from open_webui.tracker.schema import normalize_key

_USER_TAG_RE = re.compile(r'<@U:([^|>]+)(?:\|([^>]+))?>')
_BRACKETS_RE = re.compile(r'\s*[\(\[][^)\]]*[\)\]]\s*$')
KINDS = ('ten', 'chuc_vu', 'ca_nhom', 'tu_nhan', 'khong_ro')


def display_name(name: str) -> str:
    """An account name without its department suffix: "Nguyễn Trà My (HN.NCS.SALES-03)" -> "Nguyễn Trà My"."""
    return _BRACKETS_RE.sub('', name or '').strip() or (name or '').strip()


@dataclass
class Source:
    """One message an item cites: readable text (with file summaries), sender, @tagged account ids."""

    text: str
    sender_id: Optional[str]
    tagged: set[str] = field(default_factory=set)


@dataclass
class Context:
    people: list[Person]  # members, owner and authors: names that can be matched to accounts
    member_ids: list[str]  # active members (for "everyone"); falls back to people when the channel has none
    owner_id: Optional[str]
    manager_ids: set[str]
    admin_ids: set[str]
    config: dict  # channel_config: vai_tro, khong_nhan_chung, bi_danh
    by_id: dict = field(default_factory=dict)

    def __post_init__(self):
        self.by_id = {p.id: p for p in self.people}

    def left_out(self) -> set[str]:
        return {*self.admin_ids, *self.manager_ids, *(self.config.get('khong_nhan_chung') or [])} | (
            {self.owner_id} if self.owner_id else set()
        )


@dataclass
class Resolved:
    names: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)  # outside the company (a customer, "PH04")
    group: Optional[str] = None  # role / team the work was given to
    teams: list[str] = field(default_factory=list)  # owners that are a team nobody is declared for yet
    links: dict[str, str] = field(default_factory=dict)  # owner name -> account, when known for sure
    unassigned: bool = False
    rejected: int = 0


def tagged_ids(raw_content: str) -> set[str]:
    return {m.group(1) for m in _USER_TAG_RE.finditer(raw_content or '')}


def _words(text: str) -> str:
    return f' {normalize_key(text)} '


def shown_name(person: Person, people: list[Person]) -> str:
    """The name an item shows: without the department, unless another person here has the same name
    ("Nguyễn Trà My (HN.NCS.SALES-03)" next to "Nguyễn Trà My (NCS.HN.SYS)")."""
    base = display_name(person.name)
    same = [p for p in people if normalize_key(display_name(p.name)) == normalize_key(base)]
    return person.name.strip() if len(same) > 1 else base


def _matching(name: str, ctx: Context) -> list[Person]:
    """People a written name can mean: the full account name, the name without department, else the end of the
    name ("Trà My", "Nghĩa"), without "anh/chị"."""
    from open_webui.tracker.people import _name_key

    tokens = _name_key(name)
    key = ' '.join(tokens)
    if not key:
        return []
    exact = [p for p in ctx.people if normalize_key(p.name) == normalize_key(name) or normalize_key(display_name(p.name)) == key]
    if exact:
        return exact
    return [p for p in ctx.people if normalize_key(display_name(p.name)).split()[-len(tokens):] == tokens]


def verify_person(
    name: str, sources: list[Source], ctx: Context, current: Optional[list[str]] = None
) -> Optional[tuple[str, Optional[str]]]:
    """(name to show, account id or None) of the person meant, when the source messages contain them (or, for an
    update, they already own the item); else None.

    An @tag names an account exactly, so it settles a name two people share. Without a tag, a name two people share
    is not guessed: it is rejected (or kept as written for an owner the item already has)."""
    text = ' '.join(s.text for s in sources)
    words = _words(text)
    tagged = set().union(*(s.tagged for s in sources)) if sources else set()
    key = normalize_key(name)
    already = bool(current) and key in {normalize_key(c) for c in current or []}
    candidates = _matching(name, ctx)
    tagged_ones = [p for p in candidates if p.id in tagged]
    if len(tagged_ones) == 1:
        return shown_name(tagged_ones[0], ctx.people), tagged_ones[0].id
    alias = (ctx.config.get('bi_danh') or {}).get(key)
    if alias in ctx.by_id and len(candidates) <= 1:
        candidates = [ctx.by_id[alias]]
    if not candidates:
        compact = key.replace(' ', '')
        candidates = [p for p in ctx.people if p.email and normalize_key(p.email.split('@')[0]).replace(' ', '') == compact]
    if len(candidates) > 1:
        return (name.strip(), None) if already else None
    person = candidates[0] if candidates else None
    if not person:
        if (key and f' {key} ' in words) or already:
            return name.strip(), None  # someone outside the channel ("Duy"), shown as written
        return None
    found = (shown_name(person, ctx.people), person.id)
    if person.id in tagged or (key and f' {key} ' in words) or already:
        return found
    given = normalize_key(display_name(person.name)).split()
    email = normalize_key((person.email or '').split('@')[0])
    # "PM Việt Anh" for Nguyễn Việt Anh, "vietanh.nguyen" for his e-mail name.
    if (len(given) >= 2 and f' {" ".join(given[-2:])} ' in words) or (email and f' {email} ' in words):
        return found
    # "anh Nghĩa" for Vy Trung Nghĩa, when nobody else in the channel is called Nghĩa.
    same_name = [p for p in ctx.people if normalize_key(display_name(p.name)).split()[-1:] == given[-1:]]
    if given and len(same_name) == 1 and f' {given[-1]} ' in words:
        return found
    return None


def resolve(
    giao_cho: Optional[dict], names: list[str], sources: list[Source], ctx: Context, current: Optional[list[str]] = None
) -> Resolved:
    """Owners of one item from the model's `giao_cho` (or, without it, the names it wrote). current: the owners the
    item has now (an update may keep them without naming them again)."""
    result = Resolved()
    giao_cho = giao_cho if isinstance(giao_cho, dict) else {}
    kind = str(giao_cho.get('kieu') or ('ten' if names else 'khong_ro')).strip().lower()
    kind = kind if kind in KINDS else 'ten'
    proposed = [str(n) for n in (giao_cho.get('ten') or names or []) if str(n).strip()]
    for name in giao_cho.get('ben_ngoai') or []:
        if f' {normalize_key(str(name))} ' in _words(' '.join(s.text for s in sources)):
            result.external.append(str(name).strip())

    if kind == 'ten':
        # "các AM @A, @B": named people, and the role they were named as.
        result.group = str(giao_cho.get('chuc_vu') or '').strip() or None
        for name in proposed:
            person = verify_person(name, sources, ctx, current)
            if person and person[0] not in result.names:
                result.names.append(person[0])
                if person[1]:
                    result.links[person[0]] = person[1]
            elif not person and name not in result.external:
                result.rejected += 1
    elif kind == 'chuc_vu':
        from open_webui.tracker.channel_config import find_role

        role_name = str(giao_cho.get('chuc_vu') or '').strip()
        role = find_role(ctx.config, role_name) if role_name else None
        result.group = (role or {}).get('ten') or role_name or None
        result.names = list((role or {}).get('nguoi') or [])
        result.links = dict((role or {}).get('lien_ket') or {})
        if not result.names and role_name:
            # Not declared in the channel yet: the team owns it until the channel says who is in it.
            result.names, result.teams = [role_name], [role_name]
    elif kind == 'ca_nhom':
        senders = {s.sender_id for s in sources if s.sender_id}
        excluded = senders if giao_cho.get('goi_tat_ca') else ctx.left_out() | senders
        ids = ctx.member_ids or [p.id for p in ctx.people]
        result.group = 'Cả nhóm'
        for i in ids:
            if i not in excluded and i in ctx.by_id:
                shown = shown_name(ctx.by_id[i], ctx.people)
                result.names.append(shown)
                result.links[shown] = i
    elif kind == 'tu_nhan':
        sender = next((s.sender_id for s in sources if s.sender_id in ctx.by_id), None)
        if sender:
            shown = shown_name(ctx.by_id[sender], ctx.people)
            result.names, result.links = [shown], {shown: sender}

    result.names += [e for e in result.external if e not in result.names]
    result.unassigned = not result.names
    return result


def learned_roles(updates: list, ref_sources: dict[str, Source], ref_map: dict[str, str], ctx: Context) -> list[dict]:
    """Roles the model read from messages ("AM: @Tuấn Anh, AM @Nghĩa"), each person checked like an owner."""
    roles = []
    for update in updates or []:
        name = str(getattr(update, 'ten', '') or '').strip()
        refs = [r for r in getattr(update, 'nguon', []) or [] if r in ref_sources]
        if not name or not refs:
            continue
        sources = [ref_sources[r] for r in refs]
        people, links = [], {}
        for person in getattr(update, 'nguoi', []) or []:
            verified = verify_person(person, sources, ctx)
            if verified and verified[0] not in people:
                people.append(verified[0])
                if verified[1]:
                    links[verified[0]] = verified[1]
        if people:
            roles.append({'ten': name, 'nguoi': people, 'ids': list(links.values()), 'lien_ket': links,
                          'thay_the': bool(getattr(update, 'thay_the', False)), 'nguon': [ref_map[r] for r in refs]})
    return roles
