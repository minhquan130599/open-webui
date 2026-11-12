import logging
import time
from dataclasses import replace
from typing import Optional

from open_webui.models.channels import Channels
from open_webui.models.messages import Message
from open_webui.models.users import Users
from open_webui.tracker import llm, media
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
from open_webui.tracker.people import channel_people, link_owners
from open_webui.tracker.schema import SCHEMA_VERSION, apply_delta, load_state, normalize_key, render_state_markdown

log = logging.getLogger(__name__)

TZ = get_tz(TRACKER_TIMEZONE)
MAX_EDITED_PER_BATCH = 20
# How often a channel is retried while a posted file is still being extracted.
FILE_RETRY_NS = 20 * 1_000_000_000


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


async def _summarize_batch_attachments(
    channel_id: str, batch: list[Message]
) -> tuple[list[Message], dict[str, list[str]], Optional[Message], bool]:
    """Summarize attachments in message order.

    Returns (batch, summaries, stop, waiting). The batch is cut before `stop` when its files are still
    being extracted by Open WebUI (waiting=True), or when its summaries would push the batch well past the
    char budget (waiting=False, processed in the next run).
    """
    summaries: dict[str, list[str]] = {}
    used = 0
    for index, message in enumerate(batch):
        used += min(len(message.content or ''), TRACKER_MESSAGE_MAX_CHARS) + 80
        if not _file_names(message):
            continue
        result = await media.summarize_attachments(channel_id, message)
        if result.pending:
            return batch[:index], summaries, message, True
        used += sum(len(line) for line in result.lines)
        if index and used > TRACKER_BATCH_MAX_CHARS * 1.4:
            return batch[:index], summaries, message, False
        summaries[message.id] = result.lines
    return batch, summaries, None, False


def _split_long(messages: list[PromptMessage]) -> list[PromptMessage]:
    """Break a message longer than the per-message limit into parts that are all read, instead of cutting it."""
    limit = TRACKER_MESSAGE_MAX_CHARS
    result = []
    for message in messages:
        content = message.content or ''
        if len(content) <= limit:
            result.append(message)
            continue
        parts = [content[i : i + limit] for i in range(0, len(content), limit)]
        for index, part in enumerate(parts, 1):
            result.append(
                replace(
                    message,
                    content=part,
                    part=f'{index}/{len(parts)}',
                    attachments=message.attachments if index == 1 else [],
                )
            )
    return result


def _prompt_groups(messages: list[PromptMessage]) -> list[list[PromptMessage]]:
    """Consecutive groups that each fit the batch budget (one group unless a long message was split)."""
    groups, current, used = [], [], 0
    for message in messages:
        size = len(message.content or '') + sum(len(a) for a in message.attachments) + 80
        if current and used + size > TRACKER_BATCH_MAX_CHARS:
            groups.append(current)
            current, used = [], 0
        current.append(message)
        used += size
    if current:
        groups.append(current)
    return groups


def _watermark_before(messages: list[Message], stop: Message, fallback: int) -> int:
    earlier = [m.created_at for m in messages if m.created_at < stop.created_at]
    return max(earlier) if earlier else fallback


def _drop_sources(state: dict, source_ids: set[str]) -> dict:
    for key in ('hang_muc', 'quyet_dinh', 'rui_ro', 'viec_tiep_theo'):
        for entry in state.get(key) or []:
            entry['nguon'] = [s for s in entry.get('nguon') or [] if s not in source_ids]
    return state


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

        batch, attachments, stop, waiting = await _summarize_batch_attachments(channel_id, batch)
        if stop:
            watermark = _watermark_before(new_messages, stop, row.watermark)
        retry_after_ns = FILE_RETRY_NS if waiting else 0

        edited = [
            m
            for m in await Trackers.get_edited_messages(
                channel_id, row.watermark, row.edit_watermark, MAX_EDITED_PER_BATCH
            )
            if is_tracked_message(m)
        ]
        edit_watermark = max([row.edit_watermark] + [m.updated_at for m in edited])
        for message in edited:
            if _file_names(message):
                attachments[message.id] = (await media.summarize_attachments(channel_id, message)).lines

        deletions = list(row.pending_deletions or [])
        candidates = edited + batch
        if not candidates and not deletions:
            await Trackers.complete(
                channel_id,
                lease_id,
                state=None,
                watermark=watermark,
                edit_watermark=edit_watermark,
                retry_after_ns=retry_after_ns,
            )
            return

        old_state = load_state(row.state)
        prompt_messages = [
            PromptMessage(
                id=d.get('id', ''),
                created_at=d.get('created_at', 0),
                user_name=d.get('user_name', 'Không rõ'),
                content=d.get('content', ''),
                deleted=True,
            )
            for d in deletions
        ] + await _to_prompt_messages(candidates, {m.id for m in edited})
        for prompt_message in prompt_messages:
            prompt_message.attachments = attachments.get(prompt_message.id, [])

        archived_keys = frozenset(normalize_key(e.get('ten', '')) for e in row.archive or [])
        today = format_time(time.time_ns(), TZ)[:10]
        started = time.monotonic()
        state, merged_away, summaries = old_state, set(), []
        # Usually one call; a message longer than the per-message limit is read in parts over several calls.
        for group in _prompt_groups(_split_long(prompt_messages)):
            chat_messages, ref_map = build_update_messages(
                channel.name, state, group, TZ, TRACKER_MESSAGE_MAX_CHARS, time.time_ns()
            )
            delta = await llm.generate_state_update(chat_messages)
            state, merged = apply_delta(state, delta, ref_map, today, archived_keys)
            merged_away |= merged
            if delta.tom_tat_lo:
                summaries.append(delta.tom_tat_lo)

        link_owners(state, await channel_people(channel))
        stored = {**state.model_dump(), 'schema_version': SCHEMA_VERSION}
        _drop_sources(stored, {f'msg:{d.get("id")}' for d in deletions})
        sources = [m.id for m in candidates]
        item = {
            'source_type': 'message_batch',
            'source_ids': sources,
            'period_start': min([m.created_at for m in candidates] or [time.time_ns()]),
            'period_end': max([m.created_at for m in candidates] or [time.time_ns()]),
            'summary': ' '.join(summaries),
            'data': {
                'edited_ids': [m.id for m in edited],
                'deleted_ids': [d.get('id') for d in deletions],
                'attachments': [line for lines in attachments.values() for line in lines],
            },
        }
        await Trackers.complete(
            channel_id,
            lease_id,
            state=stored,
            watermark=watermark,
            edit_watermark=edit_watermark,
            item=item,
            deletions_done=len(deletions),
            retry_after_ns=retry_after_ns,
            merged_away=merged_away,
        )
        log.info(
            'Tracker updated channel %s with %s messages, %s deletions in %.1fs',
            channel_id,
            len(candidates),
            len(deletions),
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
