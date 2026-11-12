"""Project state schema and the deterministic merge around the LLM update.

Every work item has a stable id assigned here, never by the LLM. For each batch the LLM returns only what
changed (`StateDelta`: new items, updates and merges by id), and `apply_delta` folds it into the state, so
the output stays small however many items a project has, and an item the LLM does not mention is kept as is.
The LLM cites new evidence with short refs (m1, m2...) that are mapped back to message ids here, so source
lists never depend on the model copying long ids correctly.

Items closed or cancelled by a channel owner live in the archive (`tracker_state.archive`), outside the
state the LLM sees; `reconcile` keeps them out and keeps every open item at commit time.
"""

import re
import unicodedata
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 2

# Open work items are never cut; the archive holds closed/cancelled ones outside the LLM's view.
MAX_LIST_ENTRIES = 20
MAX_DECISIONS = 40
MAX_SOURCES = 5

STATUSES = ('chua_bat_dau', 'dang_lam', 'bi_chan', 'tam_dung', 'hoan_thanh')
STATUS_LABELS = {
    'chua_bat_dau': 'Chưa bắt đầu',
    'dang_lam': 'Đang làm',
    'bi_chan': 'Bị chặn',
    'tam_dung': 'Tạm dừng',
    'hoan_thanh': 'Hoàn thành',
}
RISK_LEVELS = ('thap', 'trung_binh', 'cao')
RISK_LABELS = {'thap': 'Thấp', 'trung_binh': 'Trung bình', 'cao': 'Cao'}

_STATUS_ALIASES = {
    'todo': 'chua_bat_dau',
    'not_started': 'chua_bat_dau',
    'in_progress': 'dang_lam',
    'doing': 'dang_lam',
    'blocked': 'bi_chan',
    'paused': 'tam_dung',
    'on_hold': 'tam_dung',
    'done': 'hoan_thanh',
    'completed': 'hoan_thanh',
}
_RISK_ALIASES = {'low': 'thap', 'medium': 'trung_binh', 'high': 'cao'}
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _clean_text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [text for text in (_clean_text(v) for v in value) if text]


def _clean_date(value) -> Optional[str]:
    text = _clean_text(value)
    if text and _DATE_RE.match(text[:10]):
        return text[:10]
    return None


class _Entry(BaseModel):
    nguon: list[str] = Field(default_factory=list)

    @field_validator('noi_dung', mode='before', check_fields=False)
    @classmethod
    def _content(cls, value):
        return _clean_text(value) or ''

    @field_validator('nguon', mode='before')
    @classmethod
    def _sources(cls, value):
        return _clean_list(value)


class WorkItem(_Entry):
    # Assigned by apply_delta / ensure_item_ids, never trusted from the LLM for new items.
    id: Optional[str] = None
    ten: str = Field(min_length=1)
    phu_trach: list[str] = Field(default_factory=list)
    # User ids resolved from phu_trach against the channel's people; [] when unknown or ambiguous.
    phu_trach_ids: list[str] = Field(default_factory=list)
    trang_thai: Literal['chua_bat_dau', 'dang_lam', 'bi_chan', 'tam_dung', 'hoan_thanh'] = 'dang_lam'
    tien_do: Optional[int] = None
    han: Optional[str] = None
    vuong_mac: Optional[str] = None
    # YYYY-MM-DD in the tracker timezone: first seen, last changed, and when it reached hoan_thanh.
    tao_luc: Optional[str] = None
    cap_nhat_luc: Optional[str] = None
    hoan_thanh_luc: Optional[str] = None

    @field_validator('phu_trach_ids', mode='before')
    @classmethod
    def _owner_ids(cls, value):
        return _clean_list(value)

    @field_validator('tao_luc', 'cap_nhat_luc', 'hoan_thanh_luc', mode='before')
    @classmethod
    def _dates(cls, value):
        return _clean_date(value)

    @field_validator('ten', mode='before')
    @classmethod
    def _name(cls, value):
        return _clean_text(value) or ''

    @field_validator('phu_trach', mode='before')
    @classmethod
    def _owners(cls, value):
        return _clean_list(value)

    @field_validator('trang_thai', mode='before')
    @classmethod
    def _status(cls, value):
        text = (_clean_text(value) or 'dang_lam').lower().replace(' ', '_').replace('-', '_')
        text = _STATUS_ALIASES.get(text, text)
        return text if text in STATUSES else 'dang_lam'

    @field_validator('tien_do', mode='before')
    @classmethod
    def _progress(cls, value):
        if value is None or value == '':
            return None
        match = re.search(r'\d+', str(value))
        return min(max(int(match.group()), 0), 100) if match else None

    @field_validator('han', mode='before')
    @classmethod
    def _due(cls, value):
        return _clean_date(value)

    @field_validator('vuong_mac', mode='before')
    @classmethod
    def _blocker(cls, value):
        return _clean_text(value)


class Decision(_Entry):
    noi_dung: str = Field(min_length=1)
    ngay: Optional[str] = None

    @field_validator('ngay', mode='before')
    @classmethod
    def _date(cls, value):
        return _clean_date(value)


class Risk(_Entry):
    noi_dung: str = Field(min_length=1)
    muc_do: Literal['thap', 'trung_binh', 'cao'] = 'trung_binh'

    @field_validator('muc_do', mode='before')
    @classmethod
    def _level(cls, value):
        text = (_clean_text(value) or 'trung_binh').lower().replace(' ', '_')
        text = _RISK_ALIASES.get(text, text)
        return text if text in RISK_LEVELS else 'trung_binh'


class NextStep(_Entry):
    noi_dung: str = Field(min_length=1)
    phu_trach: list[str] = Field(default_factory=list)
    han: Optional[str] = None

    @field_validator('phu_trach', mode='before')
    @classmethod
    def _owners(cls, value):
        return _clean_list(value)

    @field_validator('han', mode='before')
    @classmethod
    def _due(cls, value):
        return _clean_date(value)


class ProjectState(BaseModel):
    tong_quan: str = ''
    hang_muc: list[WorkItem] = Field(default_factory=list)
    quyet_dinh: list[Decision] = Field(default_factory=list)
    rui_ro: list[Risk] = Field(default_factory=list)
    viec_tiep_theo: list[NextStep] = Field(default_factory=list)

    @field_validator('tong_quan', mode='before')
    @classmethod
    def _summary(cls, value):
        return _clean_text(value) or ''

    @field_validator('hang_muc', 'quyet_dinh', 'rui_ro', 'viec_tiep_theo', mode='before')
    @classmethod
    def _drop_unnamed(cls, value):
        # An entry without a name/content is noise; drop it instead of rejecting the whole update.
        if not isinstance(value, list):
            return []
        return [v for v in value if not isinstance(v, dict) or _clean_text(v.get('ten', v.get('noi_dung')))]


class ItemChange(BaseModel):
    """Changes to an existing item, by id. Omitted fields stay unchanged."""

    id: str
    ten: Optional[str] = None
    phu_trach: Optional[list[str]] = None
    trang_thai: Optional[str] = None
    tien_do: Optional[int] = None
    han: Optional[str] = None
    vuong_mac: Optional[str] = None
    nguon: list[str] = Field(default_factory=list)

    @field_validator('id', mode='before')
    @classmethod
    def _id(cls, value):
        return (_clean_text(value) or '').lstrip('#')

    @field_validator('ten', 'vuong_mac', 'trang_thai', 'han', mode='before')
    @classmethod
    def _text(cls, value):
        return _clean_text(value)

    @field_validator('tien_do', mode='before')
    @classmethod
    def _progress(cls, value):
        return WorkItem._progress(value)

    @field_validator('phu_trach', mode='before')
    @classmethod
    def _owners(cls, value):
        return None if value is None else _clean_list(value)

    @field_validator('nguon', mode='before')
    @classmethod
    def _sources(cls, value):
        return _clean_list(value)


class ItemMerge(BaseModel):
    """Duplicate items `tu` folded into item `vao`."""

    vao: str
    tu: list[str] = Field(default_factory=list)

    @field_validator('vao', mode='before')
    @classmethod
    def _into(cls, value):
        return (_clean_text(value) or '').lstrip('#')

    @field_validator('tu', mode='before')
    @classmethod
    def _from(cls, value):
        return [v.lstrip('#') for v in _clean_list(value)]


class StateDelta(BaseModel):
    """What the LLM returns for one batch: only what changed."""

    tong_quan: Optional[str] = None
    hang_muc_moi: list[WorkItem] = Field(default_factory=list)
    cap_nhat: list[ItemChange] = Field(default_factory=list)
    gop: list[ItemMerge] = Field(default_factory=list)
    quyet_dinh_moi: list[Decision] = Field(default_factory=list)
    # None = unchanged; a list replaces the current one (both stay short).
    rui_ro: Optional[list[Risk]] = None
    viec_tiep_theo: Optional[list[NextStep]] = None
    tom_tat_lo: str = ''

    @field_validator('tong_quan', mode='before')
    @classmethod
    def _summary(cls, value):
        return _clean_text(value)

    @field_validator('tom_tat_lo', mode='before')
    @classmethod
    def _batch_summary(cls, value):
        return _clean_text(value) or ''

    @field_validator('hang_muc_moi', 'quyet_dinh_moi', 'rui_ro', 'viec_tiep_theo', mode='before')
    @classmethod
    def _drop_unnamed(cls, value):
        if value is None:
            return None
        if not isinstance(value, list):
            return []
        return [v for v in value if not isinstance(v, dict) or _clean_text(v.get('ten', v.get('noi_dung')))]

    @field_validator('cap_nhat', 'gop', mode='before')
    @classmethod
    def _drop_invalid(cls, value):
        if not isinstance(value, list):
            return []
        return [v for v in value if isinstance(v, dict) and _clean_text(v.get('id', v.get('vao')))]


def update_json_schema() -> dict:
    return StateDelta.model_json_schema()


def normalize_key(text: str) -> str:
    text = unicodedata.normalize('NFD', text or '').lower().replace('đ', 'd')
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def new_item_id() -> str:
    return uuid.uuid4().hex[:8]


def ensure_item_ids(state: 'ProjectState') -> bool:
    """Give legacy items (stored before ids existed) an id. Returns True if any was added."""
    changed = False
    for item in state.hang_muc:
        if not item.id:
            item.id = new_item_id()
            changed = True
    return changed


def _map_refs(refs: list[str], ref_map: dict[str, str]) -> list[str]:
    # Refs not in this batch are dropped as hallucinated.
    return [ref_map[ref] for ref in refs if ref in ref_map]


def _add_sources(existing: list[str], new: list[str]) -> list[str]:
    return list(dict.fromkeys(existing + new))[-MAX_SOURCES:]


def _set_status(item: WorkItem, status: Optional[str], today: str) -> None:
    if not status:
        return
    new = WorkItem._status(status)
    if new == 'hoan_thanh' and item.trang_thai != 'hoan_thanh':
        item.hoan_thanh_luc = today
    elif new != 'hoan_thanh':
        item.hoan_thanh_luc = None
    item.trang_thai = new


def _merge_entries(new_entries: list, old_entries: list, ref_map: dict[str, str], limit: int) -> list:
    old_sources = {normalize_key(e.noi_dung): e.nguon for e in old_entries}
    return [
        entry.model_copy(
            update={
                'nguon': _add_sources(
                    old_sources.get(normalize_key(entry.noi_dung), []), _map_refs(entry.nguon, ref_map)
                )
            }
        )
        for entry in new_entries[:limit]
    ]


def apply_delta(
    old_state: 'ProjectState',
    delta: StateDelta,
    ref_map: dict[str, str],
    today: str,
    archived_keys: frozenset[str] = frozenset(),
) -> tuple['ProjectState', set[str]]:
    """Fold one batch's changes into the state. Returns (new state, ids merged away).

    Items the delta does not mention are kept unchanged. A "new" item whose name matches an open item updates
    that item instead of duplicating it; one matching an archived (closed/cancelled) item is ignored.
    """
    state = old_state.model_copy(deep=True)
    ensure_item_ids(state)
    by_id = {item.id: item for item in state.hang_muc}
    by_key = {normalize_key(item.ten): item for item in state.hang_muc}

    for change in delta.cap_nhat:
        item = by_id.get(change.id)
        if not item:
            continue  # unknown id: the LLM invented or garbled it
        if change.ten and normalize_key(change.ten) != normalize_key(item.ten):
            by_key.pop(normalize_key(item.ten), None)
            item.ten = change.ten
            by_key[normalize_key(item.ten)] = item
        if change.phu_trach is not None:
            item.phu_trach = change.phu_trach
        _set_status(item, change.trang_thai, today)
        if change.tien_do is not None:
            item.tien_do = change.tien_do
        if change.han:
            item.han = _clean_date(change.han) or item.han
        if change.vuong_mac is not None:
            item.vuong_mac = change.vuong_mac or None
        item.nguon = _add_sources(item.nguon, _map_refs(change.nguon, ref_map))
        item.cap_nhat_luc = today

    merged_away: set[str] = set()
    for merge in delta.gop:
        target = by_id.get(merge.vao)
        if not target:
            continue
        for source_id in merge.tu:
            source = by_id.get(source_id)
            if not source or source is target:
                continue
            target.nguon = _add_sources(source.nguon, target.nguon)
            target.phu_trach = list(dict.fromkeys(target.phu_trach + source.phu_trach))
            merged_away.add(source_id)
            by_id.pop(source_id, None)
            by_key.pop(normalize_key(source.ten), None)

    for new in delta.hang_muc_moi:
        key = normalize_key(new.ten)
        existing = by_key.get(key)
        sources = _map_refs(new.nguon, ref_map)
        if existing:
            if new.phu_trach:
                existing.phu_trach = new.phu_trach
            _set_status(existing, new.trang_thai, today)
            if new.tien_do is not None:
                existing.tien_do = new.tien_do
            existing.han = new.han or existing.han
            existing.vuong_mac = new.vuong_mac or existing.vuong_mac
            existing.nguon = _add_sources(existing.nguon, sources)
            existing.cap_nhat_luc = today
            continue
        if key in archived_keys:
            continue  # closed or cancelled by the channel owner; only a reopen brings it back
        item = new.model_copy(
            update={
                'id': new_item_id(),
                'phu_trach_ids': [],
                'nguon': sources[-MAX_SOURCES:],
                'tao_luc': today,
                'cap_nhat_luc': today,
                'hoan_thanh_luc': today if new.trang_thai == 'hoan_thanh' else None,
            }
        )
        state.hang_muc.append(item)
        by_id[item.id] = item
        by_key[key] = item

    state.hang_muc = [item for item in state.hang_muc if item.id not in merged_away]
    if delta.tong_quan:
        state.tong_quan = delta.tong_quan

    decisions = list(state.quyet_dinh)
    seen = {normalize_key(d.noi_dung) for d in decisions}
    for decision in _merge_entries(delta.quyet_dinh_moi, [], ref_map, MAX_DECISIONS):
        if normalize_key(decision.noi_dung) not in seen:
            decisions.append(decision)
            seen.add(normalize_key(decision.noi_dung))
    state.quyet_dinh = decisions[-MAX_DECISIONS:]
    if delta.rui_ro is not None:
        state.rui_ro = _merge_entries(delta.rui_ro, state.rui_ro, ref_map, MAX_LIST_ENTRIES)
    if delta.viec_tiep_theo is not None:
        state.viec_tiep_theo = _merge_entries(delta.viec_tiep_theo, state.viec_tiep_theo, ref_map, MAX_LIST_ENTRIES)
    return state, merged_away


def reconcile(new_state: dict, current_state: Optional[dict], archive: Optional[list], merged_away: set[str]) -> dict:
    """Commit-time guard against concurrent changes (runs under the state row lock).

    The worker computed `new_state` from an older snapshot. Drop items archived meanwhile, and put back any
    open item present now but missing from the result (reopened meanwhile, or dropped by mistake), unless it
    was merged away in this batch.
    """
    archived_ids = {entry.get('id') for entry in archive or [] if entry.get('id')}
    items = [item for item in new_state.get('hang_muc') or [] if item.get('id') not in archived_ids]
    present = {item.get('id') for item in items}
    for item in (current_state or {}).get('hang_muc') or []:
        item_id = item.get('id')
        if item_id and item_id not in present and item_id not in archived_ids and item_id not in merged_away:
            items.append(item)
            present.add(item_id)
    return {**new_state, 'hang_muc': items}


def load_state(data: Optional[dict]) -> ProjectState:
    try:
        return ProjectState.model_validate(data or {})
    except Exception:
        return ProjectState()


def state_for_prompt(state: ProjectState) -> str:
    """The previous state as the LLM sees it: one compact line per item with its id, no sources or bookkeeping."""
    lines = [f'tong_quan: {state.tong_quan}'] if state.tong_quan else []
    if state.hang_muc:
        lines.append('hang_muc (id | ten | phu_trach | trang_thai | tien_do | han | vuong_mac):')
        for item in state.hang_muc:
            fields = [
                f'#{item.id}',
                item.ten,
                ', '.join(item.phu_trach) or '-',
                item.trang_thai,
                f'{item.tien_do}%' if item.tien_do is not None else '-',
                item.han or '-',
                item.vuong_mac or '-',
            ]
            lines.append('- ' + ' | '.join(fields))
    for title, entries, fmt in (
        ('quyet_dinh', state.quyet_dinh, lambda e: e.noi_dung + (f' ({e.ngay})' if e.ngay else '')),
        ('rui_ro', state.rui_ro, lambda e: f'[{e.muc_do}] {e.noi_dung}'),
        (
            'viec_tiep_theo',
            state.viec_tiep_theo,
            lambda e: (
                e.noi_dung
                + (f' (phụ trách: {", ".join(e.phu_trach)})' if e.phu_trach else '')
                + (f' (hạn: {e.han})' if e.han else '')
            ),
        ),
    ):
        if entries:
            lines.append(f'{title}:')
            lines.extend(f'- {fmt(entry)}' for entry in entries)
    return '\n'.join(lines)


def _format_work_item(item: WorkItem) -> str:
    parts = [STATUS_LABELS.get(item.trang_thai, item.trang_thai)]
    if item.tien_do is not None:
        parts.append(f'{item.tien_do}%')
    if item.phu_trach:
        parts.append('phụ trách: ' + ', '.join(item.phu_trach))
    if item.han:
        parts.append(f'hạn: {item.han}')
    line = f'- {item.ten} ({"; ".join(parts)})'
    if item.vuong_mac:
        line += f' — vướng mắc: {item.vuong_mac}'
    return line


def _format_next_step(step: NextStep) -> str:
    line = f'- {step.noi_dung}'
    if step.phu_trach:
        line += f' (phụ trách: {", ".join(step.phu_trach)})'
    if step.han:
        line += f' (hạn: {step.han})'
    return line


def render_state_markdown(state: ProjectState, max_chars: Optional[int] = None) -> str:
    """Compact human/LLM-readable rendering used in answers and tools."""
    lines = [f'Tổng quan: {state.tong_quan}'] if state.tong_quan else []
    sections = (
        ('Hạng mục:', state.hang_muc, _format_work_item),
        ('Quyết định:', state.quyet_dinh, lambda e: f'- {e.noi_dung}' + (f' ({e.ngay})' if e.ngay else '')),
        ('Rủi ro:', state.rui_ro, lambda e: f'- [{RISK_LABELS.get(e.muc_do, e.muc_do)}] {e.noi_dung}'),
        ('Việc tiếp theo:', state.viec_tiep_theo, _format_next_step),
    )
    for title, entries, fmt in sections:
        if entries:
            lines.append(title)
            for entry in entries:
                sources = f' [nguồn: {", ".join(entry.nguon)}]' if entry.nguon else ''
                lines.append(fmt(entry) + sources)

    text = '\n'.join(lines)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit('\n', 1)[0] + '\n…(đã rút gọn)'
    return text
