import logging
import time
from typing import Optional

from open_webui.models.channels import Channels
from open_webui.models.messages import Message
from open_webui.models.users import Users
from open_webui.tracker import llm
from open_webui.tracker.config import (
    TRACKER_BATCH_MAX_CHARS,
    TRACKER_BATCH_MAX_MESSAGES,
    TRACKER_MESSAGE_MAX_CHARS,
    TRACKER_STATE_MAX_CHARS,
    TRACKER_TAIL_LIMIT,
    TRACKER_TAIL_MAX_CHARS,
    TRACKER_TIMEZONE,
)
from open_webui.tracker.models import Trackers
from open_webui.tracker.prompts import (
    PromptMessage,
    build_update_messages,
    format_message_line,
    format_time,
    get_tz,
    is_trivial,
)
from open_webui.tracker.schema import SCHEMA_VERSION, load_state, merge_sources, render_state_markdown

log = logging.getLogger(__name__)

TZ = get_tz(TRACKER_TIMEZONE)
MAX_EDITED_PER_BATCH = 20


def is_tracked_message(message: Message) -> bool:
    """Model replies and automation prompts are not project evidence."""
    meta = message.meta or {}
    return not (meta.get('model_id') or meta.get('automation_id'))


def _file_names(message: Message) -> list[str]:
    files = (message.data or {}).get('files') or []
    return [str(f.get('name') or f.get('filename') or 'tệp') for f in files if isinstance(f, dict)]


async def _to_prompt_messages(messages: list[Message], edited_ids: set[str]) -> list[PromptMessage]:
    user_ids = list({m.user_id for m in messages if m.user_id})
    names = {u.id: u.name for u in await Users.get_users_by_user_ids(user_ids)} if user_ids else {}
    result = []
    for m in messages:
        meta = m.meta or {}
        if meta.get('webhook'):
            name = 'Webhook'
        elif m.app_id:
            name = 'Ứng dụng'
        else:
            name = names.get(m.user_id, 'Không rõ')
        result.append(
            PromptMessage(
                id=m.id,
                created_at=m.created_at,
                user_name=name,
                content=m.content or '',
                parent_id=m.parent_id,
                edited=m.id in edited_ids,
                files=_file_names(m),
            )
        )
    return result


def _select_batch(messages: list[Message]) -> tuple[list[Message], int]:
    """Pick relevant messages within the char budget; returns them and the new watermark.

    The watermark only advances past messages that were either included or skipped as irrelevant,
    so nothing is lost when the budget cuts a batch short.
    """
    selected, used = [], 0
    watermark = None
    for m in messages:
        if not is_tracked_message(m) or is_trivial(m.content or '', _file_names(m)):
            watermark = m.created_at
            continue
        size = min(len(m.content or ''), TRACKER_MESSAGE_MAX_CHARS) + 80
        if selected and used + size > TRACKER_BATCH_MAX_CHARS:
            break
        selected.append(m)
        used += size
        watermark = m.created_at
    return selected, watermark


async def process_channel(channel_id: str, lease_id: str) -> None:
    try:
        row = await Trackers.get_state(channel_id)
        channel = await Channels.get_channel_by_id(channel_id)
        if not row or not channel:
            await Trackers.complete(
                channel_id,
                lease_id,
                state=None,
                watermark=row.watermark if row else 0,
                edit_watermark=row.edit_watermark if row else 0,
            )
            return

        new_messages = await Trackers.get_new_messages(channel_id, row.watermark, TRACKER_BATCH_MAX_MESSAGES)
        batch, watermark = _select_batch(new_messages)
        watermark = watermark if watermark is not None else row.watermark

        edited = [
            m
            for m in await Trackers.get_edited_messages(
                channel_id, row.watermark, row.edit_watermark, MAX_EDITED_PER_BATCH
            )
            if is_tracked_message(m)
        ]
        edit_watermark = max([row.edit_watermark] + [m.updated_at for m in edited])

        candidates = edited + batch
        if not candidates:
            await Trackers.complete(
                channel_id, lease_id, state=None, watermark=watermark, edit_watermark=edit_watermark
            )
            return

        old_state = load_state(row.state)
        prompt_messages = await _to_prompt_messages(candidates, {m.id for m in edited})
        chat_messages, ref_map = build_update_messages(
            channel.name, old_state, prompt_messages, TZ, TRACKER_MESSAGE_MAX_CHARS, time.time_ns()
        )

        started = time.monotonic()
        update = await llm.generate_state_update(chat_messages)
        new_state = merge_sources(update.trang_thai, old_state, ref_map)

        stored = {**new_state.model_dump(), 'schema_version': SCHEMA_VERSION}
        item = {
            'source_type': 'message_batch',
            'source_ids': [m.id for m in candidates],
            'period_start': min(m.created_at for m in candidates),
            'period_end': max(m.created_at for m in candidates),
            'summary': update.tom_tat_lo,
            'data': {'edited_ids': [m.id for m in edited]},
        }
        await Trackers.complete(
            channel_id, lease_id, state=stored, watermark=watermark, edit_watermark=edit_watermark, item=item
        )
        log.info(
            'Tracker updated channel %s with %s messages in %.1fs',
            channel_id,
            len(candidates),
            time.monotonic() - started,
        )
    except Exception as e:
        log.exception('Tracker failed for channel %s', channel_id)
        await Trackers.fail(channel_id, lease_id, f'{type(e).__name__}: {e}')


async def build_channel_context(channel_id: str, exclude_message_ids: Optional[set[str]] = None) -> str:
    """Project state plus the unprocessed tail, for injection into a model prompt."""
    row = await Trackers.get_state(channel_id)
    watermark = row.watermark if row else 0
    state = load_state(row.state if row else None)

    parts = []
    if row and row.state:
        updated = format_time(row.processed_at, TZ) if row.processed_at else 'không rõ'
        parts.append(
            f'TRẠNG THÁI DỰ ÁN (tổng hợp tự động, cập nhật lúc {updated}):\n'
            + render_state_markdown(state, TRACKER_STATE_MAX_CHARS)
        )
    else:
        parts.append('TRẠNG THÁI DỰ ÁN: chưa có bản tổng hợp.')

    exclude = exclude_message_ids or set()
    tail = [
        m
        for m in await Trackers.get_tail_messages(channel_id, watermark, TRACKER_TAIL_LIMIT)
        if is_tracked_message(m) and m.id not in exclude and (m.content or _file_names(m))
    ]
    if tail:
        prompt_messages = await _to_prompt_messages(tail, set())
        lines, used = [], 0
        for i, m in enumerate(reversed(prompt_messages)):
            line = format_message_line(f't{len(prompt_messages) - i}', m, TZ, None, 500)
            if used + len(line) > TRACKER_TAIL_MAX_CHARS:
                break
            lines.append(line)
            used += len(line)
        lines.reverse()
        omitted = len(prompt_messages) - len(lines)
        header = 'TIN NHẮN MỚI CHƯA ĐƯỢC TỔNG HỢP (mới hơn trạng thái trên, ưu tiên khi trả lời):'
        if omitted:
            header += f' ({omitted} tin cũ hơn đã lược bỏ)'
        parts.append(header + '\n<du_lieu>\n' + '\n'.join(lines) + '\n</du_lieu>')

    if row and row.dirty_at:
        await Trackers.request_refresh(channel_id)

    return '\n\n'.join(parts)
