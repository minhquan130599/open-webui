"""Project reports: complete data for the model to write from, and a check that nothing was left out.

The model writes the report in its own words and layout. This module only (1) gathers every open work item
(and, on request, closed/cancelled ones) with per-person statistics, splitting into parts when the data is
too large for one answer, and (2) after the answer is written, lists any work item it never mentioned, so a
report can be short and free-form without silently dropping work. Closed and cancelled items are the only
ones a report may leave out.
"""

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from open_webui.tracker.models import Trackers
from open_webui.tracker.prompts import format_time
from open_webui.tracker.schema import STATUS_LABELS, ProjectState, load_state, normalize_key
from open_webui.tracker.service import TZ

log = logging.getLogger(__name__)

# Data handed to the model per call; larger reports are written part by part ("tiếp").
PART_CHARS = 28_000
TIMELINE_DAY_CHARS = 700
PERIOD_DAYS = {'day': 1, 'today': 1, 'week': 7, 'month': 30, 'quarter': 90}
_TTL_SECONDS = 4 * 3600


@dataclass
class ReportItem:
    ref: str
    project: str
    ten: str
    phu_trach: list[str]
    trang_thai: str
    tien_do: Optional[int]
    han: Optional[str]
    vuong_mac: Optional[str]
    tao_luc: Optional[str]
    cap_nhat_luc: Optional[str]
    hoan_thanh_luc: Optional[str]
    archived: Optional[str] = None  # 'dong' | 'huy' when taken from the archive

    def overdue(self, today: str) -> bool:
        return bool(self.han and self.han < today and self.trang_thai != 'hoan_thanh' and not self.archived)


@dataclass
class ProjectData:
    id: str
    name: str
    tong_quan: str
    items: list[ReportItem]
    timeline: list[tuple[str, str]]
    decisions: list[str]
    risks: list[str]
    next_steps: list[str]
    closed_count: int
    processed_at: Optional[int]
    pending: int


@dataclass
class Report:
    id: str
    title: str
    period_label: str
    today: str
    projects: list[ProjectData]
    person: Optional[str] = None
    created: float = field(default_factory=time.time)

    @property
    def items(self) -> list[ReportItem]:
        return [item for project in self.projects for item in project.items]


# Reports by id (for export and later parts) and the items each answer must mention, keyed by message id.
_reports: dict[str, Report] = {}
_coverage: dict[str, dict[str, ReportItem]] = {}


def _expire() -> None:
    cutoff = time.time() - _TTL_SECONDS
    for key in [k for k, r in _reports.items() if r.created < cutoff]:
        _reports.pop(key, None)


def get_report(report_id: str) -> Optional[Report]:
    _expire()
    return _reports.get(report_id)


def _owner_matches(person_key: str, names: list[str]) -> bool:
    tokens = person_key.split()
    for name in names:
        key = normalize_key(name)
        if key == person_key or key.split()[-len(tokens) :] == tokens or person_key.split()[-1:] == key.split()[-1:]:
            return True
    return False


async def _project_data(channel, days: Optional[int], include_closed: bool, person: Optional[str], counter: list):
    row = await Trackers.get_state(channel.id)
    state = load_state(row.state if row else None) if row else ProjectState()
    archive = (row.archive or []) if row else []
    now = time.time_ns()

    def make(source: dict, archived: Optional[str] = None) -> ReportItem:
        counter[0] += 1
        return ReportItem(
            ref=f'R{counter[0]}',
            project=channel.name,
            ten=source.get('ten', ''),
            phu_trach=list(source.get('phu_trach') or []),
            trang_thai=source.get('trang_thai') or 'dang_lam',
            tien_do=source.get('tien_do'),
            han=source.get('han'),
            vuong_mac=source.get('vuong_mac'),
            tao_luc=source.get('tao_luc'),
            cap_nhat_luc=source.get('cap_nhat_luc'),
            hoan_thanh_luc=source.get('hoan_thanh_luc'),
            archived=archived,
        )

    sources = [(item.model_dump(), None) for item in state.hang_muc]
    if include_closed:
        sources += [(entry, entry.get('archive_kind') or 'dong') for entry in archive]
    if person:
        person_key = ' '.join(normalize_key(person).split())
        sources = [(s, a) for s, a in sources if _owner_matches(person_key, s.get('phu_trach') or [])]
    items = [make(source, archived) for source, archived in sources]

    timeline: list[tuple[str, str]] = []
    if days:
        by_day: dict[str, list[str]] = {}
        for entry in await Trackers.get_items(channel.id, now - days * 86_400 * 1_000_000_000, now, limit=1000):
            if entry.summary:
                by_day.setdefault(format_time(entry.period_end, TZ)[:10], []).append(entry.summary)
        for day, summaries in sorted(by_day.items()):
            text = ' '.join(summaries)
            if len(text) > TIMELINE_DAY_CHARS:
                text = text[:TIMELINE_DAY_CHARS].rsplit(' ', 1)[0] + '…'
            timeline.append((day, text))

    return ProjectData(
        id=channel.id,
        name=channel.name,
        tong_quan=state.tong_quan,
        items=items,
        timeline=timeline,
        decisions=[d.noi_dung + (f' ({d.ngay})' if d.ngay else '') for d in state.quyet_dinh],
        risks=[f'[{r.muc_do}] {r.noi_dung}' for r in state.rui_ro],
        next_steps=[
            s.noi_dung + (f' — {", ".join(s.phu_trach)}' if s.phu_trach else '') + (f' (hạn {s.han})' if s.han else '')
            for s in state.viec_tiep_theo
        ],
        closed_count=0 if include_closed else len(archive),
        processed_at=row.processed_at if row else None,
        pending=row.pending_count if row else 0,
    )


async def build_report(
    channels: list, period: str, include_closed: bool, person: Optional[str], report_id: str
) -> Report:
    period_key = (period or 'week').strip().lower()
    if period_key.isdigit():
        days: Optional[int] = min(max(int(period_key), 1), 365)
    else:
        days = PERIOD_DAYS.get(period_key, None if period_key in ('all', 'overall', 'tong_hop') else 7)
    today = format_time(time.time_ns(), TZ)[:10]
    counter = [0]
    projects = [await _project_data(channel, days, include_closed, person, counter) for channel in channels]
    report = Report(
        id=report_id,
        title=', '.join(p.name for p in projects),
        period_label=f'{days} ngày gần nhất (đến {today})' if days else f'toàn bộ dự án (đến {today})',
        today=today,
        projects=projects,
        person=person,
    )
    _expire()
    _reports[report_id] = report
    return report


def person_stats(items: list[ReportItem], today: str) -> dict[str, dict]:
    """Per owner: assigned, done (on time / late / no due date), overdue, blocked, cancelled, average progress."""
    stats: dict[str, dict] = {}
    for item in items:
        for owner in item.phu_trach or ['(chưa giao)']:
            s = stats.setdefault(
                owner,
                {
                    'tong': 0,
                    'xong': 0,
                    'xong_dung_han': 0,
                    'xong_tre_han': 0,
                    'dang_mo': 0,
                    'qua_han': 0,
                    'bi_chan': 0,
                    'da_huy': 0,
                    '_progress': [],
                },
            )
            s['tong'] += 1
            if item.archived == 'huy':
                s['da_huy'] += 1
                continue
            if item.trang_thai == 'hoan_thanh':
                s['xong'] += 1
                if item.han and item.hoan_thanh_luc:
                    s['xong_dung_han' if item.hoan_thanh_luc <= item.han else 'xong_tre_han'] += 1
                continue
            if item.archived:
                continue  # closed without being marked done
            s['dang_mo'] += 1
            if item.overdue(today):
                s['qua_han'] += 1
            if item.trang_thai == 'bi_chan':
                s['bi_chan'] += 1
            if item.tien_do is not None:
                s['_progress'].append(item.tien_do)
    for s in stats.values():
        progress = s.pop('_progress')
        s['tien_do_tb_viec_dang_mo'] = round(sum(progress) / len(progress)) if progress else None
    return stats


def _item_line(item: ReportItem, today: str) -> str:
    status = STATUS_LABELS.get(item.trang_thai, item.trang_thai)
    if item.archived:
        status = ('Đã hủy' if item.archived == 'huy' else 'Đã đóng') + f' ({status})'
    fields = [
        item.ref,
        item.ten,
        ', '.join(item.phu_trach) or '-',
        status,
        f'{item.tien_do}%' if item.tien_do is not None else '-',
        (item.han or '-') + (' QUÁ HẠN' if item.overdue(today) else ''),
        item.vuong_mac or '-',
        f'{item.tao_luc or "?"}/{item.cap_nhat_luc or "?"}/{item.hoan_thanh_luc or "-"}',
    ]
    return '- ' + ' | '.join(fields)


def _project_header(project: ProjectData, today: str) -> list[str]:
    open_items = [i for i in project.items if not i.archived]
    lines = [f'## Dự án: {project.name}']
    counts = [f'{len(open_items)} việc đang mở', f'{sum(i.overdue(today) for i in open_items)} quá hạn']
    if project.closed_count:
        counts.append(f'{project.closed_count} việc đã đóng/hủy không đưa vào')
    lines.append('(' + ', '.join(counts) + ')')
    if project.processed_at:
        updated = f'Dữ liệu tổng hợp lúc {format_time(project.processed_at, TZ)}'
        if project.pending:
            updated += f'; còn {project.pending} tin nhắn mới chưa tổng hợp'
        lines.append(updated)
    if project.tong_quan:
        lines.append(f'Tổng quan: {project.tong_quan}')
    return lines


def _project_tail(project: ProjectData) -> list[str]:
    lines = []
    for title, entries in (
        ('Diễn biến theo ngày', [f'{day}: {text}' for day, text in project.timeline]),
        ('Quyết định', project.decisions),
        ('Rủi ro', project.risks),
        ('Việc tiếp theo', project.next_steps),
    ):
        if entries:
            lines.append(f'{title}:')
            lines.extend(f'- {entry}' for entry in entries)
    return lines


def render_parts(report: Report) -> list[tuple[str, list[ReportItem]]]:
    """Report data as text parts that each fit one answer, with the items each part must cover."""
    header = 'hạng mục (ref | tên | phụ trách | trạng thái | tiến độ | hạn | vướng mắc | tạo/cập nhật/xong):'
    parts: list[tuple[list[str], list[ReportItem]]] = []
    lines: list[str] = []
    covered: list[ReportItem] = []

    def flush():
        nonlocal lines, covered
        if lines:
            parts.append((lines, covered))
        lines, covered = [], []

    for project in report.projects:
        block = _project_header(project, report.today)
        tail = _project_tail(project)
        if sum(len(x) for x in lines + block + tail) > PART_CHARS:
            flush()
        lines += block + [header]
        for item in project.items:
            line = _item_line(item, report.today)
            if sum(len(x) for x in lines) + len(line) > PART_CHARS:
                lines.append('(danh sách hạng mục của dự án này tiếp tục ở phần sau)')
                flush()
                lines = [f'## Dự án: {project.name} (tiếp)', header]
            lines.append(line)
            covered.append(item)
        if sum(len(x) for x in lines + tail) > PART_CHARS:
            flush()
            lines = [f'## Dự án: {project.name} (tiếp)']
        lines += tail + ['']
    flush()

    stats = person_stats(report.items, report.today)
    if stats:
        stat_lines = ['## Thống kê theo người (tính trên các hạng mục trong dữ liệu này)']
        for owner, s in sorted(stats.items()):
            stat_lines.append(
                f'- {owner}: {s["tong"]} việc; xong {s["xong"]} (đúng hạn {s["xong_dung_han"]}, trễ hạn '
                f'{s["xong_tre_han"]}); đang mở {s["dang_mo"]} (quá hạn {s["qua_han"]}, bị chặn {s["bi_chan"]}); '
                f'đã hủy {s["da_huy"]}; tiến độ TB việc đang mở '
                + (f'{s["tien_do_tb_viec_dang_mo"]}%' if s['tien_do_tb_viec_dang_mo'] is not None else '-')
            )
        if parts and sum(len(x) for x in parts[-1][0] + stat_lines) <= PART_CHARS:
            parts[-1] = (parts[-1][0] + stat_lines, parts[-1][1])
        else:
            parts.append((stat_lines, []))
    if not parts:
        parts = [(['(Không có dữ liệu hạng mục nào trong phạm vi này.)'], [])]
    return [('\n'.join(text), items) for text, items in parts]


def expect_coverage(message_id: Optional[str], items: list[ReportItem]) -> None:
    """Remember the items the answer to this message must mention."""
    if not message_id:
        return
    pending = _coverage.setdefault(message_id, {})
    for item in items:
        if not item.archived:
            pending[f'{item.project}\x00{item.ten}'] = item


def waive_coverage(message_id: Optional[str]) -> None:
    """The full list went into an exported file, so the chat answer may stay short."""
    if message_id:
        _coverage.pop(message_id, None)


def _mentioned(item: ReportItem, text_key: str) -> bool:
    if item.ref.lower() in text_key.split():
        return True
    name_key = normalize_key(item.ten)
    if name_key and name_key in text_key:
        return True
    # Paraphrased names: most of the meaningful words appear.
    # Numbers tell items apart ("Đầu việc 5" vs "Đầu việc 0"), so they always count.
    words = [w for w in name_key.split() if len(w) > 2 or w.isdigit()]
    if not words:
        return False
    found = sum(1 for w in words if f' {w} ' in f' {text_key} ')
    return found / len(words) >= 0.75


def coverage_note(message_id: Optional[str], answer: str) -> str:
    """A short note listing open work items the finished answer never mentioned ('' when complete)."""
    if not message_id:
        return ''
    pending = _coverage.pop(message_id, None)
    if not pending:
        return ''
    text_key = normalize_key(answer)
    missing = [item for item in pending.values() if not _mentioned(item, text_key)]
    if not missing:
        return ''
    log.info('Report for message %s missed %s work items; appending them', message_id, len(missing))
    entries = []
    for item in missing:
        details = [', '.join(item.phu_trach)] if item.phu_trach else []
        if item.han:
            details.append('hạn ' + dt.date.fromisoformat(item.han).strftime('%d/%m'))
        details.append(STATUS_LABELS.get(item.trang_thai, item.trang_thai).lower())
        entries.append(f'- {item.ten} ({"; ".join(details)})')
    projects = {item.project for item in missing}
    scope = f' — {next(iter(projects))}' if len(projects) == 1 else ''
    return f'\n\n---\n**Các hạng mục khác chưa được nhắc trong báo cáo ({len(missing)}){scope}:**\n' + '\n'.join(
        entries
    )


def export_markdown(report: Report, narrative: str) -> str:
    """The model's narrative followed by a complete appendix of every work item and per-person statistics."""
    lines = [narrative.strip(), '', '## Phụ lục: danh sách hạng mục', f'Phạm vi: {report.period_label}', '']
    for project in report.projects:
        lines.append(f'### {project.name}')
        if not project.items:
            lines += ['(không có hạng mục)', '']
            continue
        lines.append('| # | Hạng mục | Phụ trách | Trạng thái | Tiến độ | Hạn | Vướng mắc |')
        lines.append('|---|---|---|---|---|---|---|')
        for index, item in enumerate(project.items, 1):
            status = STATUS_LABELS.get(item.trang_thai, item.trang_thai)
            if item.archived:
                status = 'Đã hủy' if item.archived == 'huy' else 'Đã đóng'
            due = item.han or ''
            if item.overdue(report.today):
                due += ' (quá hạn)'
            cells = [
                str(index),
                item.ten,
                ', '.join(item.phu_trach),
                status,
                f'{item.tien_do}%' if item.tien_do is not None else '',
                due,
                item.vuong_mac or '',
            ]
            lines.append('| ' + ' | '.join(c.replace('|', '/') for c in cells) + ' |')
        lines.append('')
    stats = person_stats(report.items, report.today)
    if stats:
        lines += [
            '## Phụ lục: thống kê theo người',
            '',
            '| Người | Tổng | Xong | Đúng hạn | Trễ hạn | Đang mở | Quá hạn | Bị chặn | Đã hủy | Tiến độ TB |',
            '|---|---|---|---|---|---|---|---|---|---|',
        ]
        for owner, s in sorted(stats.items()):
            average = s['tien_do_tb_viec_dang_mo']
            lines.append(
                f'| {owner} | {s["tong"]} | {s["xong"]} | {s["xong_dung_han"]} | {s["xong_tre_han"]} | '
                f'{s["dang_mo"]} | {s["qua_han"]} | {s["bi_chan"]} | {s["da_huy"]} | '
                + (f'{average}%' if average is not None else '')
                + ' |'
            )
    return '\n'.join(lines).strip() + '\n'
