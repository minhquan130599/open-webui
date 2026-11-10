"""Project state schema and the deterministic merge around the LLM update.

The LLM sees the previous state without sources and cites new evidence with short refs (m1, m2...).
`merge_sources` maps those refs back to message ids and carries over the sources of entries the LLM
kept, so source lists never depend on the model copying long ids correctly.
"""

import re
import unicodedata
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 1

MAX_ITEMS = 40
MAX_LIST_ENTRIES = 20
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
    ten: str = Field(min_length=1)
    phu_trach: list[str] = Field(default_factory=list)
    trang_thai: Literal['chua_bat_dau', 'dang_lam', 'bi_chan', 'tam_dung', 'hoan_thanh'] = 'dang_lam'
    tien_do: Optional[int] = None
    han: Optional[str] = None
    vuong_mac: Optional[str] = None

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


class StateUpdate(BaseModel):
    """What the LLM returns for one batch."""

    trang_thai: ProjectState
    tom_tat_lo: str = ''

    @field_validator('tom_tat_lo', mode='before')
    @classmethod
    def _batch_summary(cls, value):
        return _clean_text(value) or ''


def update_json_schema() -> dict:
    return StateUpdate.model_json_schema()


def normalize_key(text: str) -> str:
    text = unicodedata.normalize('NFD', text or '').lower().replace('đ', 'd')
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def _merge_list(new_entries: list, old_entries: list, key_field: str, ref_map: dict[str, str], limit: int) -> list:
    old_sources = {normalize_key(getattr(e, key_field)): e.nguon for e in old_entries}
    merged = []
    for entry in new_entries[:limit]:
        new_sources = [ref_map[ref] for ref in entry.nguon if ref in ref_map]
        sources = old_sources.get(normalize_key(getattr(entry, key_field)), [])
        combined = list(dict.fromkeys(sources + new_sources))[-MAX_SOURCES:]
        merged.append(entry.model_copy(update={'nguon': combined}))
    return merged


def merge_sources(new_state: ProjectState, old_state: ProjectState, ref_map: dict[str, str]) -> ProjectState:
    """Replace LLM refs with stored source ids; refs not in this batch are dropped as hallucinated."""
    return ProjectState(
        tong_quan=new_state.tong_quan,
        hang_muc=_merge_list(new_state.hang_muc, old_state.hang_muc, 'ten', ref_map, MAX_ITEMS),
        quyet_dinh=_merge_list(new_state.quyet_dinh, old_state.quyet_dinh, 'noi_dung', ref_map, MAX_LIST_ENTRIES),
        rui_ro=_merge_list(new_state.rui_ro, old_state.rui_ro, 'noi_dung', ref_map, MAX_LIST_ENTRIES),
        viec_tiep_theo=_merge_list(
            new_state.viec_tiep_theo, old_state.viec_tiep_theo, 'noi_dung', ref_map, MAX_LIST_ENTRIES
        ),
    )


def load_state(data: Optional[dict]) -> ProjectState:
    try:
        return ProjectState.model_validate(data or {})
    except Exception:
        return ProjectState()


def state_for_prompt(state: ProjectState) -> dict:
    """The previous state as the LLM sees it: no sources, no empty fields."""
    return state.model_dump(
        exclude={
            'hang_muc': {'__all__': {'nguon'}},
            'quyet_dinh': {'__all__': {'nguon'}},
            'rui_ro': {'__all__': {'nguon'}},
            'viec_tiep_theo': {'__all__': {'nguon'}},
        },
        exclude_none=True,
    )


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
