"""Who may make reports, which channels a report reads, and the report itself: figures from the progress
tables, comments written by the AI (custom request or the admin's default), rendered as an email and as
Telegram messages.

The figures (counts, due dates, progress) always come from the progress tables; the AI only writes the
comments, so a custom request cannot change the numbers.
"""

import datetime as dt
import hashlib
import html
import logging
import os
import re
from typing import Optional

from open_webui.models.config import Config
from open_webui.models.users import Users
from open_webui.reports.schedule import describe
from open_webui.tracker import mywork
from open_webui.tracker.models import Trackers
from open_webui.tracker.service import TZ

log = logging.getLogger(__name__)

DATA_CHARS = 24_000  # data handed to the AI for its comments
LIST_LIMIT = 8  # items listed per channel in the email


# ---------------------------------------------------------------- access


async def can_create(user, settings: dict) -> bool:
    if not settings['reports.enable']:
        return False
    who = settings['reports.who']
    if who == 'admins':
        return user.role == 'admin'
    if who == 'owners' and user.role != 'admin':
        channels = await mywork.readable_channels(user)
        return bool(await mywork.managed_ids(user, channels))
    return True


async def eligible_channels(user, settings: dict) -> list:
    """Channels this user's reports may read: the project channels they are in (owners mode: the ones they run)."""
    channels = await mywork.readable_channels(user)
    if settings['reports.who'] == 'owners' and user.role != 'admin':
        ids = await mywork.managed_ids(user, channels)
        channels = [c for c in channels if c.id in ids]
    return channels


async def report_channels(user, config: dict, settings: dict) -> list:
    channels = await eligible_channels(user, settings)
    if config.get('scope') == 'pick':
        wanted = set(config.get('channel_ids') or [])
        channels = [c for c in channels if c.id in wanted]
    return channels[: settings['reports.max_channels']]


async def public_url() -> str:
    return ((await Config.get('webui.url')) or os.getenv('OPEN_WEBUI_PUBLIC_URL', '') or '').rstrip('/')


# ---------------------------------------------------------------- data


def _period_start(config: dict, last_run_at: Optional[int], now: dt.datetime) -> dt.date:
    period = config.get('period')
    if period == 'last_month':
        first = now.date().replace(day=1)
        return (first - dt.timedelta(days=1)).replace(day=1)
    if period == 'since_last' and last_run_at:
        return dt.datetime.fromtimestamp(last_run_at / 1e9, TZ).date()
    return now.date() - dt.timedelta(days=7)


async def gather(user, config: dict, settings: dict, last_run_at: Optional[int] = None) -> dict:
    now = dt.datetime.now(TZ)
    since = _period_start(config, last_run_at, now)
    channels = await report_channels(user, config, settings)
    entries = await mywork.collect(channels) if channels else []
    states = {row.channel_id: row for row in await Trackers.get_states([c.id for c in channels])}
    since_text = since.isoformat()
    since_ns = int(dt.datetime.combine(since, dt.time(), tzinfo=TZ).timestamp() * 1e9)
    projects, totals = [], {'open': 0, 'overdue': 0, 'due_soon': 0, 'done': 0, 'blocked': 0}
    for channel in channels:
        row = states.get(channel.id)
        state = (row.state if row else None) or {}
        items = [e for e in entries if e.channel_id == channel.id]
        open_items = [e for e in items if e.bucket != 'done']
        overdue = sorted((e for e in open_items if e.bucket == 'overdue'), key=lambda e: e.han or '')
        due_soon = sorted((e for e in open_items if e.bucket in ('today', 'week')), key=lambda e: e.han or '')
        blocked = [e for e in open_items if e.vuong_mac or e.trang_thai == 'bi_chan']
        done = [
            i for i in state.get('hang_muc') or []
            if isinstance(i, dict) and i.get('trang_thai') == 'hoan_thanh'
            and (i.get('hoan_thanh_luc') or i.get('cap_nhat_luc') or '') >= since_text
        ]
        done += [
            i for i in (row.archive if row else None) or []
            if isinstance(i, dict) and i.get('archive_kind') == 'dong' and (i.get('archived_at') or 0) >= since_ns
        ]
        decisions = [
            d for d in state.get('quyet_dinh') or [] if isinstance(d, dict) and (d.get('ngay') or '') >= since_text
        ]
        progress = [e.tien_do for e in open_items if isinstance(e.tien_do, int)]
        people: dict[str, dict] = {}
        for e in open_items:
            for name in e.phu_trach or ['(chưa giao)']:
                p = people.setdefault(name, {'open': 0, 'overdue': 0})
                p['open'] += 1
                p['overdue'] += e.bucket == 'overdue'
        project = {
            'id': channel.id,
            'name': channel.name,
            'has_data': bool(row and state),
            'overview': (state.get('tong_quan') or '').strip(),
            'open': len(open_items),
            'overdue': overdue,
            'due_soon': due_soon,
            'blocked': blocked,
            'done': done,
            'decisions': decisions,
            'risks': [r for r in state.get('rui_ro') or [] if isinstance(r, dict)],
            'progress': round(sum(progress) / len(progress)) if progress else None,
            'people': people,
        }
        projects.append(project)
        totals['open'] += project['open']
        totals['overdue'] += len(overdue)
        totals['due_soon'] += len(due_soon)
        totals['done'] += len(done)
        totals['blocked'] += len(blocked)
    return {
        'today': now.date().isoformat(),
        'since': since_text,
        'projects': projects,
        'totals': totals,
    }


def _vn(day: Optional[str]) -> str:
    try:
        return dt.date.fromisoformat(day).strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return day or ''


def _owners(e) -> str:
    return ', '.join(e.phu_trach) or 'chưa giao'


def data_text(data: dict, sections: list[str]) -> str:
    """The report data as plain text for the AI (and the digest that tells whether anything changed)."""
    lines = [f'Kỳ báo cáo: {_vn(data["since"])} – {_vn(data["today"])}']
    for p in data['projects']:
        lines.append(f'\n## Kênh {p["name"]}')
        if not p['has_data']:
            lines.append('(chưa có dữ liệu tiến độ)')
            continue
        lines.append(
            f'Đang mở {p["open"]}, quá hạn {len(p["overdue"])}, đến hạn trong 7 ngày {len(p["due_soon"])}, '
            f'hoàn thành trong kỳ {len(p["done"])}, có vướng mắc {len(p["blocked"])}'
            + (f', tiến độ trung bình {p["progress"]}%' if p['progress'] is not None else '')
        )
        if 'overview' in sections and p['overview']:
            lines.append(f'Tổng quan: {p["overview"]}')
        if 'due' in sections:
            for e in p['overdue']:
                lines.append(f'- QUÁ HẠN {-(e.days_left or 0)} ngày (hạn {_vn(e.han)}): {e.ten} – {_owners(e)}')
            for e in p['due_soon']:
                lines.append(f'- Sắp đến hạn {_vn(e.han)}: {e.ten} – {_owners(e)}')
        if 'done' in sections:
            for i in p['done']:
                lines.append(f'- Hoàn thành: {i.get("ten")}')
        if 'blockers' in sections:
            for e in p['blocked']:
                lines.append(f'- Vướng mắc: {e.ten} – {e.vuong_mac or "bị chặn"}')
            for r in p['risks'][:5]:
                lines.append(f'- Rủi ro: {r.get("noi_dung")}')
        if 'decisions' in sections:
            for d in p['decisions']:
                lines.append(f'- Quyết định ({_vn(d.get("ngay"))}): {d.get("noi_dung")}')
        if 'people' in sections:
            for name, c in sorted(p['people'].items(), key=lambda x: -x[1]['overdue']):
                lines.append(f'- {name}: {c["open"]} việc mở, {c["overdue"]} quá hạn')
    text = '\n'.join(lines)
    return text[:DATA_CHARS]


def digest(text: str) -> str:
    # The period line changes every run; whether anything else changed is what matters.
    return hashlib.sha256(text.split('\n', 1)[-1].encode()).hexdigest()[:24]


COMMENT_PROMPT = """Bạn viết phần NHẬN XÉT cho báo cáo tiến độ định kỳ "{name}" ({when}).
Yêu cầu của người đặt báo cáo:
{request}

Quy tắc:
- Chỉ dùng dữ liệu bên dưới; không bịa số liệu, tên người, ngày tháng.
- Viết bằng ngôn ngữ của yêu cầu (mặc định tiếng Việt), tối đa khoảng {max_chars} ký tự.
- Mỗi ý một dòng bắt đầu bằng "- ". Có thể dùng **in đậm** cho tên kênh / việc quan trọng. Không dùng bảng, không tiêu đề.
- Không lặp lại toàn bộ danh sách việc (bảng số liệu đã có trong báo cáo); nêu điều đáng chú ý.

<du_lieu>
{data}
</du_lieu>"""


async def comments(name: str, config: dict, settings: dict, text: str) -> str:
    from open_webui.tracker.llm import generate_text

    request = (config.get('prompt') if settings['reports.allow_prompt'] else '') or settings['reports.default_prompt']
    prompt = COMMENT_PROMPT.format(
        name=name, when=describe(config), request=request, max_chars=settings['reports.max_chars'], data=text
    )
    answer = await generate_text(
        [{'role': 'user', 'content': prompt}],
        max_tokens=2500,
        model=settings['reports.model'] or None,
        purpose='report_schedule',
        disable_thinking=True,
    )
    answer = re.sub(r'<think>.*?</think>', '', answer or '', flags=re.DOTALL).strip()
    return answer[: settings['reports.max_chars'] * 2]


# ---------------------------------------------------------------- rendering


def _inline(text: str) -> str:
    """Escaped text with **bold** kept."""
    return re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html.escape(text))


def _comment_lines(text: str) -> list[str]:
    return [re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*', '', line).strip() for line in (text or '').splitlines() if line.strip()]


def email_html(name: str, owner_name: str, config: dict, data: dict, comment: str, link: str, note: str = '') -> str:
    t = data['totals']
    stat = lambda value, label, bg, fg: (  # noqa: E731
        f'<td style="padding:4px"><div style="background:{bg};border-radius:8px;padding:10px">'
        f'<div style="font-size:20px;font-weight:700;color:{fg}">{value}</div>'
        f'<div style="font-size:12px;color:#475467">{label}</div></div></td>'
    )
    rows = []
    for p in data['projects']:
        pct = p['progress']
        bar = (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td width="{pct}%" style="background:#2563eb;height:6px;font-size:0">&nbsp;</td>'
            f'<td width="{100 - pct}%" style="background:#eaecf0;height:6px;font-size:0">&nbsp;</td></tr></table>'
            if pct is not None else '<span style="color:#98a2b3">—</span>'
        )
        rows.append(
            f'<tr><td style="padding:8px 6px;border-top:1px solid #f2f4f7;font-weight:600">{html.escape(p["name"])}</td>'
            f'<td style="padding:8px 6px;border-top:1px solid #f2f4f7">{p["open"]}</td>'
            f'<td style="padding:8px 6px;border-top:1px solid #f2f4f7;color:#b42318">{len(p["overdue"])}</td>'
            f'<td style="padding:8px 6px;border-top:1px solid #f2f4f7;color:#067647">{len(p["done"])}</td>'
            f'<td style="padding:8px 6px;border-top:1px solid #f2f4f7;width:120px">{bar}</td></tr>'
        )
    overdue = [(p['name'], e) for p in data['projects'] for e in p['overdue']]
    overdue_html = ''
    if 'due' in config.get('sections', []) and overdue:
        items = ''.join(
            f'<li style="margin:3px 0">{html.escape(e.ten)} – {html.escape(_owners(e))} '
            f'<span style="color:#b42318">(trễ {-(e.days_left or 0)} ngày, #{html.escape(n)})</span></li>'
            for n, e in overdue[:LIST_LIMIT * 2]
        )
        more = f'<div style="color:#475467;font-size:12px">… và {len(overdue) - LIST_LIMIT * 2} việc khác</div>' if len(overdue) > LIST_LIMIT * 2 else ''
        overdue_html = f'<div style="font-size:13px;font-weight:700;margin-top:14px">Việc quá hạn</div><ul style="margin:6px 0;padding-left:18px;font-size:13px;color:#344054">{items}</ul>{more}'
    comment_html = ''
    lines = _comment_lines(comment)
    if lines:
        comment_html = (
            '<div style="border:1px solid #e0eaff;background:#f5f8ff;border-radius:10px;padding:12px 14px;margin-top:14px">'
            '<div style="font-size:13px;font-weight:700;color:#1d2939;margin-bottom:4px">Nhận xét của AI</div>'
            '<ul style="margin:0;padding-left:18px;font-size:13.5px;line-height:1.55;color:#344054">'
            + ''.join(f'<li>{_inline(line)}</li>' for line in lines)
            + '</ul></div>'
        )
    elif note:
        comment_html = f'<div style="font-size:12px;color:#667085;margin-top:12px">{html.escape(note)}</div>'
    button = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:18px 0 2px"><tr><td style="border-radius:8px;background:#101828">'
        f'<a href="{html.escape(link)}" style="display:inline-block;padding:10px 18px;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none">Mở bảng tiến độ</a></td></tr></table>'
        if link else ''
    )
    period = f'{_vn(data["since"])} – {_vn(data["today"])}'
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(name)}</title></head>
<body style="margin:0;padding:0;background:#f2f4f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;font-family:'Segoe UI',Arial,Helvetica,sans-serif;color:#101828">
<tr><td style="padding:0 4px 12px;font-size:13px;color:#475467"><b style="color:#101828">NCS AI</b> &nbsp;·&nbsp; {html.escape(name)} &nbsp;·&nbsp; {period}</td></tr>
<tr><td style="background:#ffffff;border:1px solid #eaecf0;border-radius:12px;padding:22px">
<div style="font-size:18px;font-weight:700;margin-bottom:10px">{html.escape(name)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
{stat(t['open'], 'việc đang mở', '#f9fafb', '#101828')}{stat(t['overdue'], 'quá hạn', '#fef3f2', '#b42318')}{stat(t['done'], 'hoàn thành trong kỳ', '#ecfdf3', '#067647')}{stat(t['blocked'], 'đang vướng mắc', '#fffaeb', '#b54708')}
</tr></table>
{comment_html}
<div style="font-size:13px;font-weight:700;margin-top:16px">Theo kênh</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;margin-top:4px">
<tr style="color:#475467"><td style="padding:6px">Kênh</td><td style="padding:6px">Mở</td><td style="padding:6px">Trễ</td><td style="padding:6px">Xong</td><td style="padding:6px">Tiến độ</td></tr>
{''.join(rows) or '<tr><td style="padding:8px 6px;color:#475467" colspan="5">Không có kênh nào trong báo cáo.</td></tr>'}
</table>
{overdue_html}
{button}
</td></tr>
<tr><td style="padding:14px 6px 0;font-size:12px;color:#667085;line-height:1.5">Báo cáo tự động từ NCS AI theo lịch "{html.escape(name)}" ({html.escape(describe(config))}) do {html.escape(owner_name)} tạo. Số liệu lấy từ bảng tiến độ các kênh; phần nhận xét do AI viết.</td></tr>
</table></td></tr></table></body></html>"""


def telegram_text(name: str, data: dict, comment: str, note: str = '') -> str:
    t = data['totals']
    out = [
        f'<b>{html.escape(name)}</b> ({_vn(data["since"])} – {_vn(data["today"])})',
        f'{t["open"]} việc mở · <b>{t["overdue"]} quá hạn</b> · {t["done"]} hoàn thành · {t["blocked"]} vướng mắc',
    ]
    lines = _comment_lines(comment)
    if lines:
        out.append('\n<b>Nhận xét</b>')
        out += [f'• {_inline(line)}' for line in lines]
    elif note:
        out.append(f'<i>{html.escape(note)}</i>')
    out.append('\n<b>Theo kênh</b>')
    for p in data['projects']:
        pct = f' · {p["progress"]}%' if p['progress'] is not None else ''
        out.append(f'• #{html.escape(p["name"])}: {p["open"]} mở, {len(p["overdue"])} trễ, {len(p["done"])} xong{pct}')
    overdue = [(p['name'], e) for p in data['projects'] for e in p['overdue']]
    if overdue:
        out.append('\n<b>Việc quá hạn</b>')
        for index, (channel, e) in enumerate(overdue[:15], 1):
            out.append(f'{index}. {html.escape(e.ten)} – {html.escape(_owners(e))} – trễ {-(e.days_left or 0)} ngày')
        if len(overdue) > 15:
            out.append(f'… và {len(overdue) - 15} việc khác')
    return '\n'.join(out)


async def figures(owner, config: dict, settings: dict, last_run_at: Optional[int] = None) -> tuple[dict, str]:
    """The data counted now and its text for the AI (no AI call)."""
    data = await gather(owner, config, settings, last_run_at)
    return data, data_text(data, config.get('sections') or [])


async def render(schedule_name: str, owner, config: dict, data: dict, text: str, comment: str, note: str = '') -> dict:
    """Email and Telegram renderings of the figures with the comments (written now or ahead of time)."""
    base = await public_url()
    link = f'{base}/my-work?mode=team' if base else ''
    subject = f'{schedule_name} – {_vn(data["since"])} đến {_vn(data["today"])}'
    return {
        'subject': subject,
        'html': email_html(schedule_name, owner.name, config, data, comment, link, note),
        'telegram': telegram_text(schedule_name, data, comment, note),
        'link': link,
        'digest': digest(text),
        'totals': data['totals'],
        'channels': len(data['projects']),
        'comment': comment,
    }


async def build(schedule_name: str, owner, config: dict, settings: dict, last_run_at: Optional[int] = None) -> dict:
    """Everything one send needs, the comments written now (preview, test, send now)."""
    data, text = await figures(owner, config, settings, last_run_at)
    try:
        comment = await comments(schedule_name, config, settings, text) if data['projects'] else ''
    except Exception as error:
        log.warning('Report "%s": AI comments failed (%s)', schedule_name, error)
        comment = ''
    return await render(schedule_name, owner, config, data, text, comment)


async def owner_of(schedule) -> Optional[object]:
    return await Users.get_user_by_id(schedule.user_id)
