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
               jsonb_array_length(coalesce(s.archive::jsonb, '[]'::jsonb)) AS archived_items,
               (SELECT count(*) FROM message m WHERE m.channel_id = s.channel_id) AS messages
           FROM tracker_state s LEFT JOIN channel c ON c.id = s.channel_id
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
