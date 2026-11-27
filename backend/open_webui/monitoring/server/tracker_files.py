"""Monitor pages for the tracker's file reading: work items extracted from each file, parts that may still miss
items, conflicts with items already in a project, and the table descriptions used to import spreadsheets.

Counts, conflicts and table descriptions need only the page login. The extracted items quote the documents, so
they need the reveal key (or an admin edit session), like page text. Editing a description and reading a file
again need an admin edit session.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.monitoring.server.admin import require_session

router = APIRouter()


@router.get('/api/tracker/extractions')
async def extractions(range: str = '7d'):
    from open_webui.monitoring.server.app import clean, rows, window

    start, _, _ = window(range)
    items = await rows(
        """SELECT t.id, t.channel_id, c.name AS channel, t.source_ids, t.created_at / 1000000 AS created_at,
               t.data::jsonb->>'name' AS name, t.data::jsonb->>'kind' AS kind,
               jsonb_array_length(coalesce(t.data::jsonb->'items', '[]'::jsonb)) AS items,
               (SELECT count(*) FROM jsonb_array_elements(coalesce(t.data::jsonb->'items', '[]'::jsonb)) AS e(value)
                 WHERE coalesce((e.value->>'xac_minh')::boolean, false) = false) AS unverified,
               (t.data::jsonb->'report') - 'spec' AS report,
               t.data::jsonb->'report'->>'signature' AS signature
           FROM tracker_item t LEFT JOIN channel c ON c.id = t.channel_id
           WHERE t.source_type = 'file_tasks' AND t.created_at >= :start
           ORDER BY t.created_at DESC LIMIT 200""",
        start=start * 1_000_000,
    )
    conflicts = await rows(
        """SELECT t.id AS batch_id, t.channel_id, c.name AS channel, t.period_end / 1000000 AS at, e.value AS entry
           FROM tracker_item t LEFT JOIN channel c ON c.id = t.channel_id,
                jsonb_array_elements(coalesce(t.data::jsonb->'file_tasks', '[]'::jsonb)) AS e(value)
           WHERE t.source_type = 'message_batch' AND t.created_at >= :start
             AND jsonb_array_length(coalesce(e.value->'conflicts', '[]'::jsonb)) > 0
           ORDER BY t.period_end DESC LIMIT 100""",
        start=start * 1_000_000,
    )
    specs = await rows(
        """SELECT id, created_at / 1000000 AS created_at, data::jsonb AS data
           FROM tracker_item WHERE source_type = 'table_spec' ORDER BY created_at DESC LIMIT 100"""
    )
    return {'items': clean(items), 'conflicts': clean(conflicts), 'specs': clean(specs)}


async def _extraction(item_id: str) -> dict:
    from open_webui.monitoring.server.app import clean, rows

    found = await rows(
        """SELECT t.id, c.name AS channel, t.source_ids, t.created_at / 1000000 AS created_at, t.data::jsonb AS data
           FROM tracker_item t LEFT JOIN channel c ON c.id = t.channel_id
           WHERE t.id = :id AND t.source_type = 'file_tasks'""",
        id=item_id,
    )
    if not found:
        raise HTTPException(404, 'Không có bản trích này')
    return clean(found)[0]


@router.get('/api/tracker/extractions/{item_id}')
async def extraction_reveal(item_id: str, request: Request):
    from open_webui.monitoring.server.app import check_reveal

    check_reveal(request, f'tracker extraction {item_id}')
    return await _extraction(item_id)


@router.get('/api/admin/tracker/extractions/{item_id}')
async def extraction_admin(item_id: str, token: str = Depends(require_session)):
    return await _extraction(item_id)


class SpecForm(BaseModel):
    spec: dict


@router.post('/api/admin/tracker/table-specs/{signature}')
async def save_spec(signature: str, form: SpecForm, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/tracker/table-specs/{signature}', method='POST', json_body={'spec': form.spec})
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


@router.post('/api/admin/tracker/files/{file_id}/reimport')
async def reimport(file_id: str, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/tracker/files/{file_id}/reimport', method='POST', json_body={})
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


@router.get('/api/tracker/channels')
async def tracked_channels():
    """Channels followed by the tracker: items, messages still to read, last run, errors."""
    from open_webui.monitoring.server.app import clean, rows

    items = await rows(
        """SELECT s.channel_id, c.name AS channel, s.pending_count, s.processed_at / 1000000 AS processed_at,
               s.fail_count, s.last_error, (s.lease_until > :now) AS running, s.force,
               jsonb_array_length(coalesce(s.state::jsonb->'hang_muc', '[]'::jsonb)) AS open_items,
               (SELECT count(*) FROM jsonb_array_elements(coalesce(s.state::jsonb->'hang_muc', '[]'::jsonb)) i
                 WHERE jsonb_array_length(coalesce(i->'phu_trach', '[]'::jsonb)) = 0) AS unassigned_items,
               (SELECT count(*) FROM jsonb_array_elements(coalesce(s.state::jsonb->'hang_muc', '[]'::jsonb)) i
                 WHERE jsonb_array_length(coalesce(i->'phu_trach_ids', '[]'::jsonb))
                     < jsonb_array_length(coalesce(i->'phu_trach', '[]'::jsonb))
                       - jsonb_array_length(coalesce(i->'ben_ngoai', '[]'::jsonb))
                       - jsonb_array_length(coalesce(i->'doi_nhom', '[]'::jsonb))) AS unlinked_items,
               (SELECT count(*) FROM jsonb_array_elements(coalesce(s.state::jsonb->'hang_muc', '[]'::jsonb)) i
                 WHERE jsonb_array_length(coalesce(i->'doi_nhom', '[]'::jsonb)) > 0) AS team_items,
               o.name AS owner_name, (o.role = 'pending' OR o.id IS NULL) AS owner_left,
               (SELECT string_agg(mu.name, ', ') FROM channel_member cm JOIN "user" mu ON mu.id = cm.user_id
                 WHERE cm.channel_id = s.channel_id AND cm.role = 'manager' AND cm.is_active) AS managers,
               jsonb_array_length(coalesce(s.archive::jsonb, '[]'::jsonb)) AS archived_items,
               (SELECT count(*) FROM message m WHERE m.channel_id = s.channel_id) AS messages,
               -- A rebuild under way: messages read so far (up to its own watermark) out of those in the channel.
               (s.rebuild IS NOT NULL) AS rebuilding,
               (s.rebuild::jsonb->>'started_at')::bigint / 1000000 AS rebuild_started_at,
               CASE WHEN s.rebuild IS NOT NULL THEN
                 (SELECT count(*) FROM message m WHERE m.channel_id = s.channel_id
                    AND m.created_at <= coalesce((s.rebuild::jsonb->>'watermark')::bigint, 0)) END AS rebuild_read
           FROM tracker_state s LEFT JOIN channel c ON c.id = s.channel_id LEFT JOIN "user" o ON o.id = c.user_id
           ORDER BY s.processed_at DESC NULLS LAST LIMIT 200""",
        now=time.time_ns(),
    )
    return {'items': clean(items)}


@router.get('/api/admin/tracker/channels/{channel_id}/rebuild')
async def rebuild_preview(channel_id: str, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/tracker/channels/{channel_id}/rebuild')
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


@router.post('/api/admin/tracker/channels/{channel_id}/rebuild')
async def rebuild(channel_id: str, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/tracker/channels/{channel_id}/rebuild', method='POST', json_body={})
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


@router.get('/api/admin/tracker/channels/{channel_id}/team')
async def channel_team(channel_id: str, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/tracker/channels/{channel_id}/team')
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


class OwnerForm(BaseModel):
    user_id: str


class ManagerForm(BaseModel):
    user_id: str
    manager: bool


@router.post('/api/admin/tracker/channels/{channel_id}/owner')
async def channel_owner(channel_id: str, form: OwnerForm, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/tracker/channels/{channel_id}/owner', method='POST', json_body=form.model_dump())
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


@router.post('/api/admin/tracker/channels/{channel_id}/managers')
async def channel_manager(channel_id: str, form: ManagerForm, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui(f'/tracker/channels/{channel_id}/managers', method='POST', json_body=form.model_dump())
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return result


# ---------- work items of a channel, and why each one exists ----------
# Item names, details and the messages behind them are channel content: reveal key or admin edit session.

STATUS_TEXT = {
    'chua_bat_dau': 'chưa bắt đầu', 'dang_lam': 'đang làm', 'bi_chan': 'bị chặn', 'tam_dung': 'tạm dừng',
    'hoan_thanh': 'hoàn thành',
}


async def _channel_state(channel_id: str) -> dict:
    from open_webui.monitoring.server.app import rows

    found = await rows(
        """SELECT c.name AS channel, s.state::jsonb AS state, s.archive::jsonb AS archive,
               s.processed_at / 1000000 AS processed_at
           FROM tracker_state s LEFT JOIN channel c ON c.id = s.channel_id WHERE s.channel_id = :id""",
        id=channel_id,
    )
    if not found:
        raise HTTPException(404, 'Kênh chưa được theo dõi tiến độ')
    return found[0]


def _source_kinds(sources: list) -> dict:
    return {
        'tin_nhan': sum(1 for s in sources if s.startswith('msg:')),
        'file': sum(1 for s in sources if s.startswith('file:')),
        'dong_bang': sum(1 for s in sources if s.startswith('table:')),
    }


async def _channel_items(channel_id: str) -> dict:
    found = await _channel_state(channel_id)
    items = []
    for entry in (found['state'] or {}).get('hang_muc') or []:
        items.append({**entry, 'da_dong': False, 'nguon_dem': _source_kinds(entry.get('nguon') or [])})
    for entry in found['archive'] or []:
        items.append({**entry, 'da_dong': True, 'nguon_dem': _source_kinds(entry.get('nguon') or [])})
    # Read without a source message: waiting for a channel manager, not in the progress.
    for entry in (found['state'] or {}).get('cho_xac_nhan') or []:
        items.append({**entry, 'da_dong': False, 'cho_xac_nhan': True, 'nguon_dem': _source_kinds([])})
    return {'channel': found['channel'], 'processed_at': found['processed_at'], 'items': items}


async def _trace(channel_id: str, item_id: str) -> dict:
    """Everything behind one item: the messages, file rows and document quotes it came from, how its owners were
    resolved, and the summary runs that read its messages."""
    from open_webui.monitoring.server.app import rows

    found = await _channel_state(channel_id)
    state = found['state'] or {}
    entries = [*(state.get('hang_muc') or []), *(found['archive'] or []), *(state.get('cho_xac_nhan') or [])]
    item = next((e for e in entries if e.get('id') == item_id), None)
    if not item:
        raise HTTPException(404, 'Không có việc này trong kênh (có thể đã bị gỡ ở lần tổng hợp sau)')
    # A waiting item has no source of its own: the messages read in its update are shown instead.
    sources = item.get('nguon') or item.get('tin_lien_quan') or []
    message_ids = [s[4:] for s in sources if s.startswith('msg:')]
    file_ids = [s[5:] for s in sources if s.startswith('file:')]
    table_keys = [s for s in sources if s.startswith('table:')]

    messages = []
    if message_ids:
        present = {
            r['id']: r
            for r in await rows(
                """SELECT m.id, m.created_at / 1000000 AS created_at, m.content, m.parent_id, u.name AS user_name,
                       (SELECT string_agg(coalesce(f->>'name', f->'file'->>'filename', ''), ', ')
                          FROM jsonb_array_elements(coalesce(m.data::jsonb->'files', '[]'::jsonb)) f) AS files
                   FROM message m LEFT JOIN "user" u ON u.id = m.user_id
                   WHERE m.channel_id = :channel AND m.id = ANY(:ids)""",
                channel=channel_id,
                ids=message_ids,
            )
        }
        for message_id in message_ids:
            row = present.get(message_id)
            messages.append(
                {**row, 'content': (row['content'] or '')[:4000], 'con': True} if row else {'id': message_id, 'con': False}
            )

    files = []
    if file_ids:
        names = {r['id']: r['filename'] for r in await rows('SELECT id, filename FROM file WHERE id = ANY(:ids)', ids=file_ids)}
        files = [{'id': i, 'ten': names.get(i), 'con': i in names} for i in file_ids]

    # The file reading that produced the item: the newest cached extraction of its files holding its row key (a
    # document item has no row key: matched by name).
    readings = []
    if file_ids:
        extracted = await rows(
            """SELECT t.id, t.created_at / 1000000 AS created_at, t.data::jsonb->>'name' AS name,
                   t.data::jsonb->>'kind' AS kind, t.data::jsonb->'report'->'spec' AS spec,
                   t.data::jsonb->'report'->'tieu_de_cot' AS titles, e.value AS entry
               FROM tracker_item t, jsonb_array_elements(coalesce(t.data::jsonb->'items', '[]'::jsonb)) e(value)
               WHERE t.source_type = 'file_tasks' AND t.source_ids::jsonb ?| CAST(:files AS text[])
                 AND (e.value->>'khoa' = ANY(CAST(:keys AS text[]))
                      OR (cardinality(CAST(:keys AS text[])) = 0 AND e.value->>'ten' = :name))
               ORDER BY t.created_at DESC LIMIT 20""",
            files=file_ids,
            keys=table_keys,
            name=item.get('ten') or '',
        )
        seen = set()
        for r in extracted:
            key = r['entry'].get('khoa') or r['entry'].get('ten')
            if key not in seen:
                seen.add(key)
                readings.append(r)

    # Summary runs that read one of its messages (or a file posted in one).
    history = []
    if message_ids:
        wanted = set(message_ids)
        for r in await rows(
            """SELECT id, created_at / 1000000 AS created_at, source_ids::jsonb AS source_ids, summary,
                   data::jsonb->'giao_viec' AS giao_viec, data::jsonb->'viec_bi_go' AS removed,
                   data::jsonb->'deleted_ids' AS deleted_ids, data::jsonb->'edited_ids' AS edited_ids,
                   data::jsonb->'file_tasks' AS file_tasks
               FROM tracker_item WHERE channel_id = :channel AND source_type = 'message_batch'
               ORDER BY created_at DESC LIMIT 500""",
            channel=channel_id,
        ):
            touched = set(r['source_ids'] or []) | set(r['edited_ids'] or []) | set(r['deleted_ids'] or [])
            touched |= {f.get('message_id') for f in r['file_tasks'] or [] if isinstance(f, dict)}
            if touched & wanted:
                history.append(
                    {'id': r['id'], 'created_at': r['created_at'], 'summary': r['summary'], 'giao_viec': r['giao_viec'],
                     'removed': r['removed']}
                )

    return {
        'channel': found['channel'],
        'item': item,
        'why': _why(item, messages, files, readings),
        'owners': _owner_trace(item),
        'messages': messages,
        'files': files,
        'readings': [
            {'id': r['id'], 'created_at': r['created_at'], 'name': r['name'], 'kind': r['kind'], 'entry': r['entry'],
             'columns': _spec_columns(r)}
            for r in readings
        ],
        'history': history[:30],
    }


def _spec_columns(reading: dict) -> dict:
    """Columns by role: their header names when the reading kept them, else the letters of the description."""
    spec = reading.get('spec')
    letters = (spec.get('cot') or {}) if isinstance(spec, dict) else {}
    titles = reading.get('titles') if isinstance(reading.get('titles'), dict) else {}
    return {role: [f'"{t}"' for t in titles[role]] if titles.get(role) else value for role, value in letters.items()}


def _columns(cols: dict, role: str) -> str:
    value = cols.get(role)
    if not value:
        return ''
    return ' + '.join(value) if isinstance(value, list) else str(value)


def _why(item: dict, messages: list, files: list, readings: list) -> list:
    """Plain sentences saying where the item comes from, built from its sources (no model involved)."""
    lines = []
    for r in readings:
        entry, cols = r['entry'], _spec_columns(r)
        key = entry.get('khoa') or ''
        where = key.split(':', 2)[-1] if key else ''
        code, _, part = where.partition('#')
        sheet = key.split(':', 2)[1] if key.count(':') >= 2 else ''
        place = f'dòng {entry.get("dong")}' + (f' (mã {code})' if code and code != str(entry.get('dong')) else '') + (
            f', sheet "{sheet}"' if sheet else '')
        if part.startswith('yc'):
            asked = (f'cột bối cảnh {_columns(cols, "ngu_canh")} ' if _columns(cols, 'ngu_canh') else 'dòng ')
            lines.append(
                f'Đề xuất từ yêu cầu chưa có ai xử lý ở {place} của file "{r["name"]}": {asked}nêu yêu cầu này nhưng '
                'việc của dòng không đáp ứng. Tên việc do model đặt; chi tiết là câu gốc trong file.'
            )
        elif key:
            lines.append(
                f'Model đọc {place} của file "{r["name"]}"'
                + (f', việc thứ {part} tách từ dòng này (khác người hoặc khác hạn)' if part and part != '1' else '')
                + (f'. Tên việc tóm tắt từ cột {_columns(cols, "ten")}' if _columns(cols, 'ten') else '')
                + (f'; phụ trách từ cột {_columns(cols, "phu_trach")}' if _columns(cols, 'phu_trach') else '')
                + '; người, hạn, tiến độ đã được đối chiếu có trong dòng.'
            )
        else:
            page = f', trang {entry.get("trang")}' if entry.get('trang') else ''
            lines.append(
                f'Trích từ văn bản "{r["name"]}"{page}; câu gốc '
                + ('khớp văn bản.' if entry.get('xac_minh') else 'chưa khớp hẳn văn bản (model tổng hợp cân nhắc).')
            )
    if any(s.startswith('table:') for s in item.get('nguon') or []) and not readings:
        lines.append('Từ một dòng bảng tính, nhưng bản trích của file không còn trong bộ nhớ (đã đọc lại hoặc file bị gỡ).')
    alive = [m for m in messages if m.get('con')]
    gone = [m for m in messages if not m.get('con')]
    if alive and not readings:
        names = ', '.join(dict.fromkeys(m.get('user_name') or '?' for m in alive))
        lines.append(f'Model tổng hợp từ {len(alive)} tin nhắn của {names}; người và hạn đã được code đối chiếu với tin.')
    elif alive:
        lines.append(f'File được gửi trong {len(alive)} tin nhắn của kênh.')
    if gone:
        lines.append(f'{len(gone)} tin nguồn đã bị xóa khỏi kênh: việc sẽ bị gỡ ở lượt tổng hợp tới nếu không còn nguồn khác.')
    if any(not f.get('con') for f in files):
        lines.append('File nguồn không còn trên hệ thống.')
    if not item.get('nguon'):
        lines.append('Việc không còn nguồn nào (thêm tay hoặc nguồn đã bị bỏ).')
    if item.get('tao_luc'):
        lines.append(
            f'Tạo ngày {item["tao_luc"]}'
            + (f', cập nhật lần cuối {item["cap_nhat_luc"]}' if item.get('cap_nhat_luc') else '')
            + f'; trạng thái {STATUS_TEXT.get(item.get("trang_thai"), item.get("trang_thai"))}.'
        )
    return lines


def _owner_trace(item: dict) -> list:
    """How each owner name stands: an account fixed for sure, a team still to declare, someone outside the
    company, or a name matched (or not) by name."""
    links = item.get('phu_trach_links') or {}
    teams = set(item.get('doi_nhom') or [])
    outside = set(item.get('ben_ngoai') or [])
    linked = item.get('phu_trach_ids') or []
    out = []
    for name in item.get('phu_trach') or []:
        if name in links:
            state = 'tài khoản đã gắn chắc (tag @, gán tay hoặc chức vụ kênh)'
        elif name in teams:
            state = 'team / chức vụ chưa khai báo thành viên trong kênh'
        elif name in outside:
            state = 'người / đơn vị ngoài công ty'
        elif linked:
            state = 'khớp theo tên với thành viên kênh'
        else:
            state = 'chưa khớp tài khoản nào (tên trùng hoặc không có trong kênh)'
        out.append({'ten': name, 'trang_thai': state, 'tai_khoan': links.get(name)})
    if item.get('nhom'):
        out.append({'ten': item['nhom'], 'trang_thai': 'giao cho cả nhóm / chức vụ', 'tai_khoan': None})
    if item.get('chua_giao'):
        out.append({'ten': '—', 'trang_thai': 'chưa giao: nguồn không nêu ai làm', 'tai_khoan': None})
    return out


@router.get('/api/tracker/channels/{channel_id}/items')
async def channel_items_reveal(channel_id: str, request: Request):
    from open_webui.monitoring.server.app import check_reveal

    check_reveal(request, f'tracker items {channel_id}')
    return await _channel_items(channel_id)


@router.get('/api/admin/tracker/channels/{channel_id}/items')
async def channel_items_admin(channel_id: str, token: str = Depends(require_session)):
    return await _channel_items(channel_id)


@router.get('/api/tracker/channels/{channel_id}/items/{item_id}/trace')
async def item_trace_reveal(channel_id: str, item_id: str, request: Request):
    from open_webui.monitoring.server.app import check_reveal

    check_reveal(request, f'tracker trace {channel_id}/{item_id}')
    return await _trace(channel_id, item_id)


@router.get('/api/admin/tracker/channels/{channel_id}/items/{item_id}/trace')
async def item_trace_admin(channel_id: str, item_id: str, token: str = Depends(require_session)):
    return await _trace(channel_id, item_id)


# ---------- rebuild every channel still in use ----------


async def _active_channels(days: int) -> list:
    """Followed channels with a message in the last `days` days, and how much a rebuild would read."""
    from open_webui.monitoring.server.app import clean, rows

    since = time.time_ns() - max(days, 1) * 86_400 * 1_000_000_000
    return clean(
        await rows(
            """SELECT s.channel_id, c.name AS channel, (s.lease_until > :now) AS running,
                   jsonb_array_length(coalesce(s.state::jsonb->'hang_muc', '[]'::jsonb)) AS open_items,
                   (SELECT count(*) FROM message m WHERE m.channel_id = s.channel_id) AS messages,
                   (SELECT max(m.created_at) FROM message m WHERE m.channel_id = s.channel_id) / 1000000 AS last_message
               FROM tracker_state s JOIN channel c ON c.id = s.channel_id
               WHERE EXISTS (SELECT 1 FROM message m WHERE m.channel_id = s.channel_id AND m.created_at >= :since)
               ORDER BY c.name""",
            now=time.time_ns(),
            since=since,
        )
    )


@router.get('/api/admin/tracker/channels-rebuild-all')
async def rebuild_all_preview(days: int = 30, token: str = Depends(require_session)):
    return {'days': days, 'channels': await _active_channels(days)}


class RebuildAllForm(BaseModel):
    days: int = 30


@router.post('/api/admin/tracker/channels-rebuild-all')
async def rebuild_all(form: RebuildAllForm, token: str = Depends(require_session)):
    """Start a rebuild of every channel with a message in the last `days` days. Channels being summarized are
    skipped (their result would be committed on top); the tracker worker then reads them a few at a time."""
    from open_webui.monitoring.server.app import webui

    started, skipped, failed = [], [], []
    for channel in await _active_channels(form.days):
        name = channel['channel'] or channel['channel_id']
        if channel['running']:
            skipped.append(name)
            continue
        result = await webui(f'/tracker/channels/{channel["channel_id"]}/rebuild', method='POST', json_body={})
        if isinstance(result, dict) and result.get('error'):
            failed.append({'channel': name, 'error': result['error']})
        else:
            started.append(name)
    return {'started': started, 'skipped': skipped, 'failed': failed}
