import logging
import re
import time
from dataclasses import replace
from typing import Optional

from open_webui.models.channels import Channels
from open_webui.models.messages import Message
from open_webui.models.users import Users
from open_webui.tracker import llm, media, relevance
from open_webui.tracker.config import (
    TRACKER_BATCH_MAX_CHARS,
    TRACKER_BATCH_MAX_MESSAGES,
    TRACKER_GROUP_MAX_MESSAGES,
    TRACKER_MAX_TOKENS,
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
    state_prompt_chars,
    format_time,
    get_tz,
    is_trivial,
)
from open_webui.tracker import assign, channel_config
from open_webui.tracker.people import channel_people, channel_roles, link_owners
from open_webui.tracker.schema import (
    SCHEMA_VERSION,
    PendingItem,
    apply_delta,
    drop_marked_files,
    item_text,
    items_prompt_chars,
    load_state,
    new_item_id,
    normalize_key,
    render_state_markdown,
)

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


async def unprocessed_files(channel_id: str) -> list[str]:
    """Files posted after the last tracker update ("name (gửi HH:MM dd/mm)"): not in the project status yet."""
    row = await Trackers.get_state(channel_id)
    watermark = row.watermark if row else 0
    lines = []
    for message in await Trackers.get_tail_messages(channel_id, watermark, TRACKER_TAIL_LIMIT):
        if is_tracked_message(message):
            lines += [f'{name} (gửi {format_time(message.created_at, TZ)})' for name in _file_names(message)]
    return lines


_MODEL_MENTION_RE = re.compile(r'<@M:([^|>]+)(?:\|([^>]+))?>')
_OTHER_MENTION_RE = re.compile(r'<@[A-Z]:([^|>]+)(?:\|([^>]+))?>')


def ai_mentions(content: str) -> list[str]:
    """Names (label and id) of the AI models mentioned in a message."""
    names = []
    for model_id, label in _MODEL_MENTION_RE.findall(content or ''):
        names += [model_id] + ([label] if label else [])
    return names


def _readable(content: str) -> str:
    """Mentions as the reader sees them: an AI model as a request to the assistant, a person as @Name."""
    content = _MODEL_MENTION_RE.sub(lambda m: f'[gọi trợ lý AI "{m.group(2) or m.group(1)}"]', content or '')
    return _OTHER_MENTION_RE.sub(lambda m: '@' + (m.group(2) or m.group(1)), content)


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
                content=_readable(m.content or ''),
                parent_id=m.parent_id,
                edited=m.id in edited_ids,
                files=_file_names(m),
                asks_ai=bool(ai_mentions(m.content or '')),
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
    channel_id: str, batch: list[Message], extractions: dict[str, list[dict]]
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
        if result.extractions:
            extractions[message.id] = result.extractions
    return batch, summaries, None, False


async def _merge_extractions(
    state,
    extractions: dict[str, list[dict]],
    today: str,
    archived_keys: frozenset,
    archived_rows: frozenset = frozenset(),
    marked: frozenset = frozenset(),
) -> tuple[object, list[dict]]:
    """Fold the verified items extracted from files into the state before the update LLM runs, so no item
    depends on the LLM copying it. Files marked as reference material (marked: file ids) are not read.
    Returns (state, one log entry per file for the batch record)."""
    from open_webui.tracker import extract
    from open_webui.tracker.schema import ensure_item_ids

    if not extractions:
        return state, []
    state = state.model_copy(deep=True)
    ensure_item_ids(state)
    entries = []
    for message_id, results in extractions.items():
        for result in results:
            if result.get('file_id') in marked:
                continue
            # Closed or cancelled items stay closed: matched by name, or by their table row key, which survives
            # a rebuild where the model names the row differently.
            items = [
                i
                for i in result.get('items') or []
                if normalize_key(i['ten']) not in archived_keys
                and not (i.get('khoa') and ({i['khoa'], i['khoa'].split('#')[0]} & archived_rows))
            ]
            matches = await extract.match_state(items, state.hang_muc)
            outcome = extract.merge_into_state(state, items, f'msg:{message_id}', today, matches, result.get('file_id'))
            report = result.get('report') or {}
            entries.append(
                {
                    'message_id': message_id,
                    'file_id': result.get('file_id'),
                    'name': result.get('name'),
                    'cache_id': result.get('cache_id'),
                    'kind': result.get('kind'),
                    'items': len(items),
                    'unverified': sum(1 for i in items if not i.get('xac_minh')),
                    'suspect': len(report.get('suspect') or []),
                    **outcome,
                }
            )
    return state, entries


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
    """Consecutive groups, in order, that each fit the batch budget and hold at most TRACKER_GROUP_MAX_MESSAGES."""
    groups, current, used = [], [], 0
    for message in messages:
        size = len(message.content or '') + sum(len(a) for a in message.attachments) + 80
        if current and (used + size > TRACKER_BATCH_MAX_CHARS or len(current) >= TRACKER_GROUP_MAX_MESSAGES):
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


async def _ai_names(messages: list[Message]) -> set[str]:
    """Names an AI model goes by here: mentioned in the batch, or any model configured in Open WebUI."""
    names = {normalize_key(n) for m in messages for n in ai_mentions(m.content or '')}
    try:
        from open_webui.models.models import Models

        for model in await Models.get_all_models():
            names |= {normalize_key(model.id), normalize_key(model.name or '')}
    except Exception as error:
        log.warning('Tracker: model names unavailable (%s)', error)
    return {n for n in names if n} | {'tro ly ai'}


def _drop_ai_tasks(state, old_state, messages: list[Message], names: set[str]) -> None:
    """Guard after the update LLM: an AI model is never an owner, and a new item that only comes from requests
    to the assistant ("@Knight lọc các việc hạn tháng 11") is a question, not a project task."""
    asks = {f'msg:{m.id}' for m in messages if ai_mentions(m.content or '') and not _file_names(m)}
    known = {item.id for item in old_state.hang_muc}
    kept = []
    for item in state.hang_muc:
        owners = [o for o in item.phu_trach if normalize_key(o) not in names]
        had_ai = len(owners) != len(item.phu_trach)
        item.phu_trach = owners
        # Given to a role or team ("các AM") through the assistant is real work, even with nobody resolved yet.
        if item.id not in known and item.nguon and set(item.nguon) <= asks and (had_ai or not owners) and not item.nhom:
            log.info('Tracker: dropped "%s", a request to the AI assistant', item.ten)
            continue
        kept.append(item)
    state.hang_muc = kept
    for step in state.viec_tiep_theo:
        step.phu_trach = [o for o in step.phu_trach if normalize_key(o) not in names]


def _group_time(group: list[PromptMessage]) -> int:
    """When the messages of a call were sent (the latest). An edit or a deletion happens now, whatever the date of
    the message it changes."""
    if any(m.edited or m.deleted for m in group):
        return time.time_ns()
    return min(max(m.created_at for m in group), time.time_ns())


ANSWERS_MAX_CHARS = 20_000


def _clip_answers(answers: list[dict]) -> list[dict]:
    """The model's answers kept with a batch record, newest dropped first past ANSWERS_MAX_CHARS (a rebuild)."""
    import json

    kept, used = [], 0
    for answer in answers:
        size = len(json.dumps(answer, ensure_ascii=False))
        if kept and used + size > ANSWERS_MAX_CHARS:
            kept.append({'luoc_bo': len(answers) - len(kept)})
            break
        kept.append(answer)
        used += size
    return kept


def _pending_entry(new, group: list[PromptMessage], today: str) -> dict:
    """A waiting-list entry for a new item read without a source (schema.PendingItem)."""
    owners = list(new.phu_trach) or (list(new.giao_cho.ten) if new.giao_cho else [])
    entry = PendingItem(
        id=new_item_id(),
        ten=new.ten,
        chi_tiet=list(new.chi_tiet),
        trang_thai=new.trang_thai,
        tien_do=new.tien_do,
        han=new.han,
        vuong_mac=new.vuong_mac,
        chua_giao=True,
        tao_luc=today,
        cap_nhat_luc=today,
        tin_lien_quan=list(dict.fromkeys(f'msg:{m.id}' for m in group))[:20],
        phu_trach_doc_duoc=owners,
    )
    return entry.model_dump()


def _assign_owners(delta, ref_sources: dict, ctx, counts: dict, state=None, unsourced: Optional[list] = None) -> dict:
    """Owners of the delta's new items and owner changes, worked out by code from how the model says the work was
    given (assign.py). A new item without a valid source message cannot be traced nor given owners: it goes to
    `unsourced` (the waiting list a manager confirms) instead of the progress. Returns {item id: Resolved} for the
    changes, to set their group after the delta is applied."""
    kept = []
    for new in delta.hang_muc_moi:
        refs = [r for r in new.nguon if r in ref_sources]
        if not refs:
            counts['khong_nguon'] = counts.get('khong_nguon', 0) + 1
            log.info('Tracker: new item "%s" has no source message (nguon: %s); waiting for a manager', new.ten, new.nguon)
            if unsourced is not None:
                unsourced.append(new)
            continue
        resolved = assign.resolve(
            new.giao_cho.model_dump() if new.giao_cho else None, new.phu_trach, [ref_sources[r] for r in refs], ctx
        )
        counts['ten_bi_loai'] = counts.get('ten_bi_loai', 0) + resolved.rejected
        new.phu_trach, new.nhom, new.ben_ngoai = resolved.names, resolved.group, resolved.external
        new.doi_nhom = resolved.teams
        new.phu_trach_links = resolved.links
        new.chua_giao = resolved.unassigned
        kept.append(new)
    delta.hang_muc_moi = kept
    changes = {}
    current_owners = {item.id: list(item.phu_trach) for item in (state.hang_muc if state else [])}
    for change in delta.cap_nhat:
        if change.giao_cho is None and change.phu_trach is None:
            continue
        refs = [r for r in change.nguon if r in ref_sources] or list(ref_sources)
        resolved = assign.resolve(
            change.giao_cho.model_dump() if change.giao_cho else None,
            change.phu_trach or [],
            [ref_sources[r] for r in refs],
            ctx,
            current_owners.get(change.id),
        )
        counts['ten_bi_loai'] = counts.get('ten_bi_loai', 0) + resolved.rejected
        # Never replaced by nobody: when no name checks out, the item keeps its owners.
        if not resolved.names:
            change.phu_trach = None
            continue
        kind = change.giao_cho.kieu if change.giao_cho else 'ten'
        replace = kind in ('chuc_vu', 'ca_nhom', 'tu_nhan') or bool(change.giao_cho and change.giao_cho.thay_the)
        if not replace:
            # Adding people ("thêm @X hỗ trợ"), or a change the model did not mark as a handover: nobody is dropped.
            resolved.names = list(dict.fromkeys([*current_owners.get(change.id, []), *resolved.names]))
        change.phu_trach = resolved.names
        changes[change.id] = resolved
    return changes


def _attached_file_ids(message: Message) -> set[str]:
    ids = set()
    for item in (message.data or {}).get('files') or []:
        if isinstance(item, dict):
            file_id = media._file_id(item)
            if file_id:
                ids.add(file_id)
    return ids


def _retract(state, deleted: set[str], edited: list[Message]) -> list[str]:
    """Items that only came from a deleted message, or from a file or image removed from an edited message, are
    removed; an item other messages also back keeps them and only loses the source that is gone. Returns the names
    of the removed items. (The update model has no way to delete an item, so this is done by code.)"""
    gone_files: dict[str, set[str]] = {}  # message source -> file sources no longer attached to it
    current = {f'msg:{m.id}': {f'file:{i}' for i in _attached_file_ids(m)} for m in edited}
    removed, kept = [], []
    for item in state.hang_muc:
        messages = [s for s in item.nguon if s.startswith('msg:')]
        files = [s for s in item.nguon if s.startswith('file:')]
        lost_files = {f for f in files for m in messages if m in current and f not in current[m]}
        if lost_files:
            gone_files.setdefault(item.id, set()).update(lost_files)
        # An item backed by a removed file and by no other message than the one the file was removed from.
        from_lost_file = bool(lost_files) and set(files) <= lost_files and all(m in current for m in messages)
        if messages and (set(messages) <= deleted or from_lost_file):
            removed.append(item.ten)
            continue
        if lost_files:
            item.nguon = [s for s in item.nguon if s not in lost_files]
        kept.append(item)
    state.hang_muc = kept
    return removed


def _file_summary(file_tasks: list[dict], retracted: list[str], marked_removed: list[str] = ()) -> list[str]:
    """The history line for what code did with files (the update model only sees the result, and must not be the one
    describing it): "Thêm 12, cập nhật 3 đầu việc từ file X."."""
    lines = []
    for entry in file_tasks or []:
        parts = [f'thêm {entry["added"]}' if entry.get('added') else '', f'cập nhật {entry["updated"]}' if entry.get('updated') else '',
                 f'thay {len(entry["superseded"])}' if entry.get('superseded') else '']
        parts = [p for p in parts if p]
        if parts:
            lines.append(f'{", ".join(parts).capitalize()} đầu việc từ file {entry.get("name") or "đính kèm"}.')
    if retracted:
        lines.append(f'Gỡ {len(retracted)} đầu việc do tin nhắn / file nguồn đã bị xóa.')
    if marked_removed:
        lines.append(f'Gỡ {len(marked_removed)} đầu việc lấy từ tệp đánh dấu không cần cập nhật đầu việc.')
    return lines


def _drop_sources(state: dict, source_ids: set[str]) -> dict:
    for key in ('hang_muc', 'quyet_dinh', 'rui_ro', 'viec_tiep_theo'):
        for entry in state.get(key) or []:
            entry['nguon'] = [s for s in entry.get('nguon') or [] if s not in source_ids]
    return state


_FILE_NAME_STOP = {'va', 'and', 'file', 'tep'}
FILE_SEARCH_LIMIT = 1000


def _name_words(text: str) -> set[str]:
    return set(normalize_key(text).split()) - _FILE_NAME_STOP


def _name_matches(words: set[str], said: str, file_words: set[str], file_name: str) -> bool:
    """Every word said is in the file name, or the name said is in it once spaces and punctuation are dropped
    ("kế hoạch T11" matches "KeHoach_T11.xlsx")."""
    if words <= file_words:
        return True
    compact = ''.join(w for w in normalize_key(said).split() if w not in _FILE_NAME_STOP)
    return len(compact) >= 4 and compact in normalize_key(file_name).replace(' ', '')


async def _marks_from_chat(channel_id: str, names: list[str], now: int) -> tuple[dict, list[str]]:
    """Files the channel named as reference material ("file X không phải đầu việc"), matched by name among the
    channel's recent files. A name matching no file or several files is not guessed at: it is returned unmatched."""
    files = []
    for message in await Trackers.get_file_messages(channel_id, FILE_SEARCH_LIMIT):
        for item in (message.data or {}).get('files') or []:
            file_id = media._file_id(item) if isinstance(item, dict) else None
            if file_id:
                name = str(item.get('name') or item.get('filename') or 'tệp')
                files.append((file_id, message.id, name, _name_words(name)))
    marks, unmatched = {}, []
    for name in names:
        words = _name_words(name)
        if not words:
            continue
        hits = {
            file_id: (message_id, file_name)
            for file_id, message_id, file_name, found in files
            if _name_matches(words, name, found, file_name)
        }
        if len(hits) != 1:
            unmatched.append(name)
            continue
        file_id, (message_id, file_name) = next(iter(hits.items()))
        marks[file_id] = {'msg': message_id, 'ten': file_name, 'boi': None, 'boi_ten': None, 'tu': 'chat', 'luc': now}
    return marks, unmatched


async def process_channel(channel_id: str, lease_id: str) -> None:
    try:
        row = await Trackers.get_state(channel_id)
        channel = await Channels.get_channel_by_id(channel_id)
        if row is not None and row.rebuild is not None:
            # A rebuild works on its own copy (Trackers.complete stores the result there); deletions recorded meanwhile
            # do not concern it, the deleted messages are simply not read again.
            row = row.model_copy(
                update={
                    'state': row.rebuild.get('state'),
                    'watermark': row.rebuild.get('watermark') or 0,
                    'edit_watermark': row.rebuild.get('edit_watermark') or 0,
                    'pending_deletions': None,
                }
            )
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

        extractions: dict[str, list[dict]] = {}
        batch, attachments, stop, waiting = await _summarize_batch_attachments(channel_id, batch, extractions)
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
                result = await media.summarize_attachments(channel_id, message)
                attachments[message.id] = result.lines
                if result.extractions:
                    extractions[message.id] = result.extractions

        # Files unmarked since they were last read are read again; a message already read in this run is not.
        marks = row.file_marks or {}
        skip_marks = dict(marks.get('skip') or {})
        reread = dict(marks.get('reread') or {})
        reread_done: set[str] = set()
        if reread:
            rereads = {m.id: m for m in await Trackers.get_messages_by_ids(channel_id, set(reread.values()))}
            for file_id, message_id in reread.items():
                message = rereads.get(message_id)
                files = [f for f in ((message.data or {}).get('files') or []) if isinstance(f, dict)] if message else []
                if not any(media._file_id(f) == file_id for f in files) or message.id in extractions:
                    reread_done.add(file_id)
                    continue
                result = await media.summarize_attachments(channel_id, message)
                if result.pending:
                    retry_after_ns = FILE_RETRY_NS
                    continue
                if result.extractions:
                    extractions[message.id] = result.extractions
                reread_done.add(file_id)

        deletions = list(row.pending_deletions or [])
        # Items open when this run started: any of them missing from the result was removed on purpose by this run
        # (retracted, superseded by a new reading of its file, merged, an AI-owned task dropped), so the commit-time
        # guard must not put it back.
        started_ids = {e.get('id') for e in (row.state or {}).get('hang_muc') or [] if e.get('id')}
        # Sources whose message is gone without a recorded deletion (deleted while a run was reading it, a reply
        # removed with its thread): their items are retracted the same way.
        referenced = {
            source[4:] for entry in (row.state or {}).get('hang_muc') or [] for source in entry.get('nguon') or []
            if source.startswith('msg:')
        }
        vanished = {
            f'msg:{i}'
            for i in await Trackers.missing_message_ids(channel_id, referenced - {d.get('id') for d in deletions})
        }
        candidates = edited + batch
        if not candidates and not deletions and not vanished and not extractions:
            await Trackers.complete(
                channel_id,
                lease_id,
                state=None,
                watermark=watermark,
                edit_watermark=edit_watermark,
                retry_after_ns=retry_after_ns,
                reread_done=reread_done,
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
        # Table rows of closed items (exact keys); an item archived with a whole-row key closes that row's tasks.
        archived_rows = frozenset(
            source
            for entry in row.archive or []
            for source in entry.get('nguon') or []
            if source.startswith('table:')
        )
        # A file's items join the state at the file's place among the messages (just before the group holding the
        # message that carried it), so a later message that reports one done is applied after it, and an earlier one
        # does not overwrite what the file says. Files read again for a message outside this run go first.
        in_run = {m.id for m in prompt_messages}
        folded = {message_id for message_id in extractions if message_id not in in_run}
        state, file_tasks = await _merge_extractions(
            old_state,
            {k: v for k, v in extractions.items() if k in folded},
            today,
            archived_keys,
            archived_rows,
            frozenset(skip_marks),
        )
        merged_away, summaries = set(), []
        people = await channel_people(channel)
        member_ids, manager_ids = await channel_roles(channel)
        config = await channel_config.get(channel_id)
        ctx = assign.Context(
            people=people,
            member_ids=member_ids,
            owner_id=channel.user_id,
            manager_ids=manager_ids,
            admin_ids={p.id for p in people if p.role == 'admin'},
            config=config,
        )
        members = sorted({assign.display_name(p.name) for p in people if p.name})
        raw = {m.id: m for m in candidates}
        counts: dict = {}
        learned: list[dict] = []
        # Usually one call; a message longer than the per-message limit is read in parts over several calls.
        # When the model cannot give a whole valid answer for a group (cut at the token limit, invalid JSON),
        # the group is read again in two halves, down to one message.
        groups = _prompt_groups(_split_long(prompt_messages))
        file_drops: list[str] = []
        pending: list[dict] = []
        answers: list[dict] = []
        while groups:
            group = groups.pop(0)
            # The day these messages were sent: "today" for the model (relative dates such as "thứ 6 tuần này") and
            # the date written on what they change (done on, updated on), so reading old messages again (a rebuild)
            # dates them as they happened, not on the day of the rebuild.
            group_ns = _group_time(group)
            day = format_time(group_ns, TZ)[:10]
            here = {m.id: extractions[m.id] for m in group if m.id in extractions and m.id not in folded}
            if here:
                folded |= set(here)
                state, entries = await _merge_extractions(
                    state, here, day, archived_keys, archived_rows, frozenset(skip_marks)
                )
                file_tasks += entries
            roles = [f'{r["ten"]}: {", ".join(r["nguoi"])}' for r in ctx.config['vai_tro'].values()]
            texts = ['\n'.join([m.content or '', *m.attachments]) for m in group]
            # Only a list that does not fit is cut down, and only then is it worth matching items by meaning.
            scores = (
                await relevance.item_scores([item_text(item) for item in state.hang_muc], texts)
                if items_prompt_chars(state.hang_muc) > state_prompt_chars('\n'.join(texts))
                else None
            )
            chat_messages, ref_map = build_update_messages(
                channel.name, state, group, TZ, TRACKER_MESSAGE_MAX_CHARS, group_ns, members, roles, scores
            )
            refs = list(ref_map)
            try:
                delta = await llm.generate_state_update(chat_messages, refs=refs)
            except llm.TrackerOutputError as e:
                if len(group) >= 2:
                    half = len(group) // 2
                    log.warning('Tracker: %s for %d messages in %s; reading them in two halves', e, len(group), channel_id)
                    groups[0:0] = [group[:half], group[half:]]
                    continue
                # One message whose update does not fit: once more with twice the room, then it is left out so the
                # messages after it are still read (otherwise the channel would stop updating for good).
                try:
                    delta = await llm.generate_state_update(chat_messages, max_tokens=TRACKER_MAX_TOKENS * 2, refs=refs)
                except llm.TrackerOutputError as again:
                    log.error(
                        'Tracker: message %s in %s left out, its update does not fit (%s)', group[0].id, channel_id, again
                    )
                    continue
            # What the model asked to change, as it wrote it (refs m1.. of this call), kept with the batch record:
            # without it, an update that was understood but not applied could only be guessed at afterwards.
            answers.append({'tin': [f'{ref}={source[4:]}' for ref, source in ref_map.items()],
                            'thay_doi': delta.model_dump(mode='json', exclude_defaults=True)})
            by_message = {f'msg:{m.id}': m for m in group}
            ref_sources = {}
            for ref, source in ref_map.items():
                message = by_message.get(source)
                if message is None:
                    continue
                original = raw.get(message.id)
                ref_sources[ref] = assign.Source(
                    text='\n'.join([message.content or '', *message.attachments]),
                    sender_id=original.user_id if original else None,
                    tagged=assign.tagged_ids(original.content if original else ''),
                )
            if len(ref_sources) == 1:
                # One message in this call: it is the source of every new item, whatever the model wrote.
                only = next(iter(ref_sources))
                for new in delta.hang_muc_moi:
                    if not any(r in ref_sources for r in new.nguon):
                        new.nguon = [only]
            roles_now = assign.learned_roles(delta.vai_tro_moi, ref_sources, ref_map, ctx)
            if roles_now:
                ctx.config = await channel_config.update(channel_id, lambda c: channel_config.learn_roles(c, roles_now))
                learned += roles_now
            unsourced: list = []
            changed_groups = _assign_owners(delta, ref_sources, ctx, counts, state, unsourced)
            pending += [_pending_entry(new, group, day) for new in unsourced]
            state, merged = apply_delta(state, delta, ref_map, day, archived_keys)
            for item in state.hang_muc:
                if item.id in changed_groups:
                    item.nhom = changed_groups[item.id].group
                    item.ben_ngoai = changed_groups[item.id].external
                    item.doi_nhom = changed_groups[item.id].teams
                    kept = {n: i for n, i in item.phu_trach_links.items() if n in item.phu_trach}
                    item.phu_trach_links = {**kept, **changed_groups[item.id].links}
            merged_away |= merged
            file_drops += delta.bo_tep
            if delta.tom_tat_lo:
                summaries.append(delta.tom_tat_lo)

        _drop_ai_tasks(state, old_state, candidates, await _ai_names(candidates))
        chat_marks, unmatched = await _marks_from_chat(channel_id, file_drops, time.time_ns()) if file_drops else ({}, [])
        for file_id, mark in chat_marks.items():
            await Trackers.set_file_mark(channel_id, file_id, mark, mark['msg'])
        marked_removed = drop_marked_files(state, {**skip_marks, **chat_marks})
        retracted = _retract(state, {f'msg:{d.get("id")}' for d in deletions} | vanished, edited)
        # Teams declared in the channel ("AM: @…", now or before) replace the team names that owned items.
        channel_config.apply_roles(state, ctx.config)
        link_owners(state, people, ctx.config.get('bi_danh'))
        stored = {**state.model_dump(), 'schema_version': SCHEMA_VERSION}
        _drop_sources(stored, {f'msg:{d.get("id")}' for d in deletions} | vanished)
        sources = [m.id for m in candidates]
        item = {
            'source_type': 'message_batch',
            'source_ids': sources,
            'period_start': min([m.created_at for m in candidates] or [time.time_ns()]),
            'period_end': max([m.created_at for m in candidates] or [time.time_ns()]),
            'summary': ' '.join(
                [
                    *_file_summary(file_tasks, retracted, marked_removed),
                    *(f'Đánh dấu tệp "{m["ten"]}" không cần cập nhật đầu việc theo tin nhắn.' for m in chat_marks.values()),
                    *(f'Chưa rõ tệp nào khớp "{name}", chưa đánh dấu tệp nào.' for name in unmatched),
                    *summaries,
                ]
            ),
            'data': {
                'edited_ids': [m.id for m in edited],
                'deleted_ids': [d.get('id') for d in deletions] + sorted(v[4:] for v in vanished),
                'attachments': [line for lines in attachments.values() for line in lines],
                **({'file_tasks': file_tasks} if file_tasks else {}),
                **({'giao_viec': counts} if counts else {}),
                **({'viec_bi_go': retracted} if retracted else {}),
                **({'tep_danh_dau': [m['ten'] for m in chat_marks.values()]} if chat_marks else {}),
                **({'viec_bo_theo_tep': marked_removed} if marked_removed else {}),
                **({'vai_tro_moi': learned} if learned else {}),
                **({'tra_loi_model': _clip_answers(answers)} if answers else {}),
                **({'cho_xac_nhan': [p['ten'] for p in pending]} if pending else {}),
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
            merged_away=merged_away | (started_ids - {i.id for i in state.hang_muc}),
            reread_done=reread_done,
            pending=pending,
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
