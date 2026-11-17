"""Monitor actions on the tracker's file extractions: edit a table description, read a file again.

Reading again drops the cached extraction of the file and marks the messages that carry it as edited, so the
next tracker run reads the file again (with the edited description) and folds its items into the state like an
edit: rows already there are updated by their table key, nothing is duplicated.
"""

import time
from typing import Optional

from sqlalchemy import String, cast, delete, select, update

from open_webui.internal.db import get_async_db_context
from open_webui.models.messages import Message
from open_webui.tracker.models import TrackerItem, Trackers


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
