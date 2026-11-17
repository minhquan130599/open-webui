"""Monitor actions on the tracker: edit a table description, read a file again, rebuild a channel.

Reading again drops the cached extraction of the file and marks the messages that carry it as edited, so the
next tracker run reads the file again (with the edited description) and folds its items into the state like an
edit: rows already there are updated by their table key, nothing is duplicated.
"""

import time
from typing import Optional

from sqlalchemy import String, cast, delete, select, update

from open_webui.internal.db import get_async_db_context
from open_webui.models.messages import Message
from open_webui.tracker.models import TrackerItem, Trackers, TrackerState


async def save_table_spec(signature: str, spec: dict, edited: bool = True, extra: Optional[dict] = None) -> dict:
    """Store a table description: edited on the monitor (kept as it is), or made by the model."""
    item_id = f'tablespec:{signature}'
    now = time.time_ns()
    values = {**(extra or {}), 'spec': spec, 'edited': edited, **({'edited_at': now} if edited else {'updated_at': now})}
    async with get_async_db_context() as db:
        row = await db.get(TrackerItem, item_id)
        if row:
            row.data = {**(row.data or {}), **values}
        else:
            db.add(
                TrackerItem(
                    id=item_id,
                    channel_id='',
                    source_type='table_spec',
                    source_ids=[],
                    period_start=now,
                    period_end=now,
                    summary='',
                    data=values,
                    created_at=now,
                )
            )
        await db.commit()
    return {'saved': True}


async def reimport_file(file_id: str) -> dict:
    now = time.time_ns()
    async with get_async_db_context() as db:
        cached = (
            await db.execute(select(TrackerItem.id, TrackerItem.source_ids).where(TrackerItem.source_type == 'file_tasks'))
        ).all()
        stale = [row.id for row in cached if file_id in (row.source_ids or [])]
        if stale:
            await db.execute(delete(TrackerItem).where(TrackerItem.id.in_(stale)))
        messages = (
            await db.execute(
                select(Message.id, Message.channel_id).where(
                    Message.channel_id.is_not(None), cast(Message.data, String).like(f'%{file_id}%')
                )
            )
        ).all()
        if messages:
            await db.execute(update(Message).where(Message.id.in_([m.id for m in messages])).values(updated_at=now))
        await db.commit()
    channels = sorted({m.channel_id for m in messages})
    for channel_id in channels:
        await Trackers.mark_dirty(channel_id, force=True)
    return {'dropped': len(stale), 'messages': len(messages), 'channels': len(channels)}


async def _channel_files(db, channel_id: str) -> tuple[int, set[str]]:
    """(messages in the channel, ids of the files they carry)."""
    rows = (await db.execute(select(Message.data).where(Message.channel_id == channel_id))).all()
    files = set()
    for (data,) in rows:
        for item in (data or {}).get('files') or []:
            if isinstance(item, dict) and isinstance(item.get('id'), str):
                files.add(item['id'])
    return len(rows), files


async def rebuild_preview(channel_id: str) -> dict:
    """What a rebuild of the channel would redo, for the confirmation."""
    from open_webui.models.channels import Channels

    channel = await Channels.get_channel_by_id(channel_id)
    if not channel:
        raise LookupError('channel')
    async with get_async_db_context() as db:
        row = await db.get(TrackerState, channel_id)
        messages, files = await _channel_files(db, channel_id)
    state = (row.state or {}) if row else {}
    return {
        'channel': channel.name,
        'messages': messages,
        'files': len(files),
        'open_items': len(state.get('hang_muc') or []),
        'archived_items': len(row.archive or []) if row else 0,
        'running': bool(row and row.lease_until and row.lease_until > time.time_ns()),
    }


async def rebuild_channel(channel_id: str) -> dict:
    """Start the channel's summary over: every message and file is read again with the current code.

    Items closed or cancelled in the channel stay in the archive, so they do not come back. The update history is
    written again as the messages are read again. Refused while the tracker is working on the channel (its result
    would be committed on top).
    """
    now = time.time_ns()
    preview = await rebuild_preview(channel_id)
    if preview['running']:
        raise RuntimeError('Tracker đang tổng hợp kênh này, thử lại sau khi lượt hiện tại xong.')
    async with get_async_db_context() as db:
        row = await db.get(TrackerState, channel_id)
        if row is None:
            raise LookupError('tracker')
        _, files = await _channel_files(db, channel_id)
        # Cached extractions of the channel's files, so they are read with the current code and settings.
        cached = (
            await db.execute(
                select(TrackerItem.id, TrackerItem.source_ids).where(TrackerItem.source_type == 'file_tasks')
            )
        ).all()
        stale = [r.id for r in cached if files & set(r.source_ids or [])]
        if stale:
            await db.execute(delete(TrackerItem).where(TrackerItem.id.in_(stale)))
        # The update history described the old reading: it is written again as the messages are read again.
        history = await db.execute(
            delete(TrackerItem).where(TrackerItem.channel_id == channel_id, TrackerItem.source_type == 'message_batch')
        )
        row.state = None
        row.version = (row.version or 0) + 1
        row.watermark = 0
        row.edit_watermark = 0
        row.pending_deletions = None
        row.pending_count = preview['messages']
        row.dirty_at = now
        row.force = True
        row.fail_count = 0
        row.next_attempt_at = None
        row.last_error = None
        row.updated_at = now
        await db.commit()
    return {**preview, 'dropped_extractions': len(stale), 'dropped_history': history.rowcount or 0, 'started': True}
