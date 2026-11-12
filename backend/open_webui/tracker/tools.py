"""Builtin tools exposing tracker state. Access is limited to channels the user can read.

When the model runs inside a channel (chat id "channel:<id>"), the tools only see that channel: the
answer is visible to every channel member, so it must not reveal other projects of the asking user.
"""

import logging
import time
from typing import Optional

from fastapi import Request

from open_webui.models.channels import Channels
from open_webui.tracker.models import Trackers
from open_webui.tracker.prompts import format_time
from open_webui.tracker.schema import load_state, normalize_key
from open_webui.tracker.service import TZ, build_channel_context
from open_webui.utils.chat_id import CHANNEL_CHAT_ID_PREFIX
from open_webui.utils.json_codec import JSONCodec

log = logging.getLogger(__name__)


async def _accessible_channels(user_id: str, chat_id: Optional[str] = None) -> list:
    if isinstance(chat_id, str) and chat_id.startswith(CHANNEL_CHAT_ID_PREFIX):
        # The channel chat id is only accepted after the chat completion membership/write gate.
        channel = await Channels.get_channel_by_id(chat_id.removeprefix(CHANNEL_CHAT_ID_PREFIX))
        return [channel] if channel else []
    return [c for c in await Channels.get_channels_by_user_id(user_id) if c.type != 'dm']


async def get_project_status(
    project: str,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Get the current status of a project (one channel = one project): summary, work items with owner,
    progress, due date and blockers, decisions, risks, next steps, plus messages not yet summarized.

    :param project: Channel ID or channel name of the project
    :return: The project status as text
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        match, error = await _resolve_project(project, __user__.get('id'), __chat_id__)
        if error:
            return error
        return f'Dự án: {match.name} (id: {match.id})\n\n' + await build_channel_context(match.id)
    except Exception as e:
        log.exception(f'get_project_status error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def _resolve_project(project: str, user_id: str, chat_id: Optional[str] = None):
    """Find an accessible channel by id or name. Returns (channel, None) or (None, error JSON)."""
    channels = await _accessible_channels(user_id, chat_id)
    if len(channels) == 1 and chat_id and chat_id.startswith(CHANNEL_CHAT_ID_PREFIX):
        return channels[0], None  # inside a channel, "the project" is always this channel
    key = normalize_key(project)
    match = next((c for c in channels if c.id == project), None) or next(
        (c for c in channels if normalize_key(c.name) == key), None
    )
    if match:
        return match, None
    candidates = [c for c in channels if key and key in normalize_key(c.name)]
    if len(candidates) == 1:
        return candidates[0], None
    if candidates:
        return None, JSONCodec.dumps(
            {'error': 'Ambiguous project', 'candidates': [{'id': c.id, 'name': c.name} for c in candidates[:10]]},
            ensure_ascii=False,
        )
    return None, JSONCodec.dumps({'error': 'Project not found'})


async def get_project_timeline(
    project: str,
    days: int = 7,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Get what happened in a project over the last N days, as a chronological list of update summaries.
    Use it for questions like "what was done last week".

    :param project: Channel ID or channel name of the project
    :param days: Number of past days to cover (default: 7, max: 90)
    :return: JSON list of {time, summary, message_ids}
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        match, error = await _resolve_project(project, __user__.get('id'), __chat_id__)
        if error:
            return error
        days = min(max(int(days or 7), 1), 90)
        now = time.time_ns()
        items = await Trackers.get_items(match.id, now - days * 86_400 * 1_000_000_000, now, limit=200)
        return JSONCodec.dumps(
            {
                'project': match.name,
                'days': days,
                'updates': [
                    {
                        'time': format_time(i.period_end, TZ),
                        'summary': i.summary,
                        'sources': [f'msg:{s}' for s in i.source_ids[:10]],
                    }
                    for i in items
                    if i.summary
                ],
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'get_project_timeline error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def search_project_evidence(
    project: str,
    query: str,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    Search a project's history (update summaries, including file and image summaries) for a topic.
    Matching ignores Vietnamese accents. Returns the best matching updates with their source message ids,
    which can be opened with view_channel_message.

    :param project: Channel ID or channel name of the project
    :param query: Words to look for, e.g. "thanh toán" or "server lỗi"
    :param count: Maximum number of results (default: 10)
    :return: JSON list of matching updates with time, summary and sources
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        match, error = await _resolve_project(project, __user__.get('id'), __chat_id__)
        if error:
            return error
        terms = [t for t in normalize_key(query).split() if len(t) > 1]
        if not terms:
            return JSONCodec.dumps({'error': 'Query is empty'})

        scored = []
        for item in await Trackers.search_items(match.id):
            attachments = (item.data or {}).get('attachments') or []
            text = normalize_key(' '.join([item.summary or '', *attachments]))
            score = sum(text.count(term) for term in terms) + (5 if normalize_key(query) in text else 0)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], pair[1].period_end), reverse=True)

        return JSONCodec.dumps(
            [
                {
                    'time': format_time(item.period_end, TZ),
                    'summary': item.summary,
                    'attachments': ((item.data or {}).get('attachments') or [])[:5],
                    'sources': [f'msg:{s}' for s in item.source_ids[:10]],
                }
                for _, item in scored[: min(max(int(count or 10), 1), 30)]
            ],
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'search_project_evidence error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def list_my_projects(
    query: Optional[str] = None,
    only_with_issues: bool = False,
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
) -> str:
    """
    List projects (channels) the user can access with a short status: overview, number of work items,
    blocked items, overdue items and risks. Use it for questions spanning several projects.

    :param query: Optional text to filter project names
    :param only_with_issues: If true, only return projects with blocked/overdue items or high risks
    :return: JSON list of projects with their short status
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        channels = await _accessible_channels(__user__.get('id'), __chat_id__)
        if query:
            key = normalize_key(query)
            channels = [c for c in channels if key in normalize_key(c.name)]

        states = {s.channel_id: s for s in await Trackers.get_states([c.id for c in channels])}
        today = format_time(time.time_ns(), TZ)[:10]

        projects = []
        for channel in channels:
            row = states.get(channel.id)
            state = load_state(row.state if row else None)
            blocked = [i.ten for i in state.hang_muc if i.trang_thai == 'bi_chan']
            overdue = [i.ten for i in state.hang_muc if i.han and i.han < today and i.trang_thai != 'hoan_thanh']
            high_risks = [r.noi_dung for r in state.rui_ro if r.muc_do == 'cao']
            if only_with_issues and not (blocked or overdue or high_risks):
                continue
            projects.append(
                {
                    'id': channel.id,
                    'name': channel.name,
                    'overview': state.tong_quan,
                    'work_items': len(state.hang_muc),
                    'done': sum(1 for i in state.hang_muc if i.trang_thai == 'hoan_thanh'),
                    'blocked': blocked,
                    'overdue': overdue,
                    'high_risks': high_risks,
                    'updated_at': format_time(row.processed_at, TZ) if row and row.processed_at else None,
                    'pending_messages': row.pending_count if row else 0,
                }
            )
        return JSONCodec.dumps(projects[:100], ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_my_projects error: {e}')
        return JSONCodec.dumps({'error': str(e)})


REPORT_GUIDE = """HƯỚNG DẪN VIẾT (không hiển thị phần này cho người dùng):
- Viết theo đúng yêu cầu của người dùng, tự chọn bố cục, giọng văn và mức chi tiết. Mặc định ngắn gọn: nêu điều cần chú ý trước (quá hạn, bị chặn, sắp đến hạn, rủi ro), việc bình thường thì gộp gọn.
- Bắt buộc nhắc tới MỌI hạng mục trong dữ liệu ít nhất một lần, dùng đúng tên hạng mục. Được gộp nhiều việc vào một dòng, ví dụ "Đúng tiến độ (5 việc): A, B, C, D, E". Không cần ghi mã R1, R2.
- Chỉ dùng số liệu có trong dữ liệu. Có thể gọi search_project_evidence hoặc view_channel_message để xem chi tiết hoặc trích nguồn.
- Đánh giá, nhận xét, so sánh, xếp hạng... (ví dụ hiệu suất từng người) là phần bạn tự phân tích từ dữ liệu và thống kê theo người; nêu rõ căn cứ.
- Người dùng muốn file (PDF, Word, Excel) hoặc báo cáo chi tiết dài: viết phần nhận xét rồi gọi export_project_report với report_id này; bảng đầy đủ mọi hạng mục được tự thêm vào file, nên câu trả lời trong chat chỉ cần tóm tắt và báo đã gửi file."""


async def build_project_report(
    project: str = '',
    period: str = 'week',
    person: str = '',
    include_closed: bool = False,
    purpose: str = 'report',
    part: int = 1,
    report_id: str = '',
    __request__: Request = None,
    __user__: dict = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Get the complete data for a project report (one channel = one project) or a cross-project report:
    every open work item with owner, status, progress, due date, blocker and dates, the day-by-day activity
    of the period, decisions, risks, next steps and per-person statistics. Use it for any work report or
    progress summary (daily, weekly, monthly, overall, per person, several projects), and for analysis such
    as evaluating each member's performance. Then write the answer yourself following the guide it returns.
    Closed and cancelled items are left out unless include_closed is true.

    :param project: Channel ID or name of the project; empty for every project the user can access
    :param period: day, week, month, quarter, all, or a number of days, for the activity timeline
    :param person: Optional owner name to limit the report to that person's work
    :param include_closed: Also include closed/cancelled items (use for performance evaluation and history)
    :param purpose: "report" when writing a report that must mention every work item; "analysis" for evaluations, statistics or questions that do not list items
    :param part: Part number when a large report is delivered in several parts (answer "tiếp" with the next part)
    :param report_id: The report_id of an earlier call, to get its next part
    :return: The report data as text, with a writing guide
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})
    try:
        import uuid

        from open_webui.tracker import report as reports

        existing = reports.get_report(report_id) if report_id else None
        if existing:
            data = existing
        else:
            if project and project.strip().lower() not in ('all', '*', 'tat ca', 'tất cả'):
                match, error = await _resolve_project(project, __user__.get('id'), __chat_id__)
                if error:
                    return error
                channels = [match]
            else:
                channels = await _accessible_channels(__user__.get('id'), __chat_id__)
            if not channels:
                return JSONCodec.dumps({'error': 'No accessible projects'})
            data = await reports.build_report(
                channels, period, bool(include_closed), (person or '').strip() or None, uuid.uuid4().hex[:10]
            )

        parts = reports.render_parts(data)
        index = min(max(int(part or 1), 1), len(parts))
        text, items = parts[index - 1]
        if purpose != 'analysis':
            reports.expect_coverage(__message_id__, items)

        header = f'report_id: {data.id} | phần {index}/{len(parts)} | phạm vi: {data.period_label}'
        if data.person:
            header += f' | người: {data.person}'
        guide = REPORT_GUIDE
        if len(parts) > 1:
            guide += f'\n- Dữ liệu lớn nên được chia làm {len(parts)} phần; đây là phần {index}. Chỉ viết phần này'
            if index < len(parts):
                guide += (
                    f', và cuối câu trả lời mời người dùng gõ "tiếp" để xem phần {index + 1}/{len(parts)} '
                    f'(khi đó gọi lại build_project_report với report_id="{data.id}", part={index + 1}). '
                    'Nếu người dùng muốn nhận cả báo cáo một lần, dùng export_project_report.'
                )
            else:
                guide += ' (phần cuối).'
        return f'{header}\n\n{text}\n\n{guide}'
    except Exception as e:
        log.exception(f'build_project_report error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def export_project_report(
    report_id: str,
    content: str,
    format: str = 'pdf',
    __request__: Request = None,
    __user__: dict = None,
    __event_emitter__: callable = None,
    __chat_id__: str = None,
    __message_id__: str = None,
) -> str:
    """
    Deliver a report built with build_project_report as a downloadable file attached to your reply. A complete
    appendix (every work item and per-person statistics) is added automatically after your text, so the file
    is complete even when your text is a summary.

    :param report_id: The report_id returned by build_project_report
    :param content: The report text you wrote, in Markdown (headings, bullet lists, tables, **bold**)
    :param format: pdf, docx (Word), xlsx (Excel) or md
    :return: JSON with the attached file name, or an error
    """
    if __request__ is None or not __user__:
        return JSONCodec.dumps({'error': 'Request context not available'})
    try:
        from open_webui.models.users import UserModel
        from open_webui.tools.builtin import attach_to_reply
        from open_webui.tracker import report as reports
        from open_webui.tracker.export import build_file

        data = reports.get_report(report_id)
        if not data:
            return JSONCodec.dumps({'error': 'Report expired or unknown; call build_project_report again'})
        note = ''
        try:
            name, payload, content_type = build_file(data, content or '', format)
        except Exception as e:
            if (format or '').lower() != 'pdf':
                raise
            log.warning('PDF export failed, sending Word instead: %s', e)
            name, payload, content_type = build_file(data, content or '', 'docx')
            note = ' PDF could not be created on this server, so a Word file was sent instead; tell the user.'
        await attach_to_reply(
            __request__,
            UserModel(**__user__),
            __chat_id__,
            __message_id__,
            __event_emitter__,
            name,
            payload,
            content_type,
        )
        reports.waive_coverage(__message_id__)
        return JSONCodec.dumps(
            {
                'status': 'success',
                'file': name,
                'message': f'{name} is attached to your reply with the full appendix. Summarize briefly; do not '
                'paste the whole report or a link.' + note,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'export_project_report error: {e}')
        return JSONCodec.dumps({'error': str(e)})
