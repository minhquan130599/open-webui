"""Asking the AI as the linked user, through the same code paths as the web:

- directly: a real chat of the user (chat pipeline saves it; it can be opened on the web);
- in a channel: the question is posted in the channel under the user's name with the model mentioned, and the
  channel's model handler answers it (channel context, progress table, channel files), exactly like @Knight.

Permissions are the web's: models filtered for the user, channel write access checked by new_message_handler.
"""

import io
import logging
import time
import uuid
from typing import Optional

from open_webui.models.users import UserModel
from open_webui.telegram import format as fmt

log = logging.getLogger(__name__)

MIME = {
    'pdf': 'application/pdf',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}


# ---------------------------------------------------------------- models


async def allowed_models(request, user: UserModel) -> list[dict]:
    """Models the user may chat with (the web's filter), without hidden ones and terminal agents."""
    from open_webui.utils.models import get_all_models, get_filtered_models

    models = await get_filtered_models(await get_all_models(request, user=user), user)
    out = []
    for model in models:
        meta = (model.get('info') or {}).get('meta') or {}
        if meta.get('hidden') or str(model.get('id', '')).startswith('agent:') or model.get('arena'):
            continue
        out.append(model)
    return out


def default_model_id(user: UserModel, settings: dict, models: list[dict], chosen: Optional[str]) -> Optional[str]:
    ids = [m['id'] for m in models]
    ui = (user.settings.ui or {}) if getattr(user, 'settings', None) else {}
    for candidate in (chosen, settings.get('telegram.model'), *(ui.get('models') or [])):
        if candidate and candidate in ids:
            return candidate
    return ids[0] if ids else None


def model_name(models: list[dict], model_id: str) -> str:
    for model in models:
        if model['id'] == model_id:
            return model.get('name') or model_id
    return model_id


# ---------------------------------------------------------------- channels


async def can_post(user: UserModel, channel) -> bool:
    from open_webui.models.channels import Channels
    from open_webui.routers.channels import channel_has_access

    if not channel or channel.archived_at:
        return False
    if channel.type in ('group', 'dm'):
        return await Channels.is_user_channel_member(channel.id, user.id)
    return user.role == 'admin' or await channel_has_access(user.id, channel, permission='write', strict=False)


async def channels_enabled_for(user: UserModel) -> bool:
    from open_webui.models.config import Config
    from open_webui.utils.access_control import has_permission

    if not await Config.get('channels.enable'):
        return False
    return user.role == 'admin' or await has_permission(user.id, 'features.channels', await Config.get('user.permissions'))


async def user_channels(user: UserModel, settings: dict) -> list:
    """Channels the user can ask in (like the sidebar, write access), most recently active first."""
    from open_webui.models.channels import Channels
    from open_webui.models.messages import Messages

    if not await channels_enabled_for(user):
        return []
    blocked = set(settings.get('telegram.blocked_channels') or [])
    out = []
    for channel in await Channels.get_channels_by_user_id(user.id):
        if channel.id in blocked or channel.type == 'dm':
            continue
        if await can_post(user, channel):
            out.append(channel)
    times = await Messages.get_last_message_times_by_channel_ids([c.id for c in out]) if out else {}
    out.sort(key=lambda c: times.get(c.id) or c.updated_at or 0, reverse=True)
    return out


async def get_channel_for(user: UserModel, channel_id: str, settings: dict):
    """The channel if the user may still ask in it from Telegram, else None."""
    from open_webui.models.channels import Channels

    if not channel_id or channel_id in set(settings.get('telegram.blocked_channels') or []):
        return None
    if not await channels_enabled_for(user):
        return None
    channel = await Channels.get_channel_by_id(channel_id)
    return channel if channel and channel.type != 'dm' and await can_post(user, channel) else None


# ---------------------------------------------------------------- files from Telegram


async def save_upload(request, user: UserModel, name: str, data: bytes, content_type: str, channel_id: Optional[str] = None) -> dict:
    """A file sent to the bot, stored and processed like an upload on the web; returns the message file entry."""
    from fastapi import UploadFile

    from open_webui.routers.files import upload_file_handler

    metadata = {'channel_id': channel_id} if channel_id else {}
    item = await upload_file_handler(
        request,
        file=UploadFile(file=io.BytesIO(data), filename=name, headers={'content-type': content_type}),
        metadata=metadata,
        process=True,
        process_in_background=False,
        user=user,
    )
    # The handler returns the file as a dict when it processed it, as a model otherwise.
    file = item if isinstance(item, dict) else (item.model_dump() if item else None)
    if not file or not file.get('id'):
        raise ValueError('Không lưu được file')
    file.pop('status', None)
    file_id = file['id']
    meta = file.get('meta') or {}
    # The same entry as an upload on the web (MessageInput): the chat pipeline reads the processed file from it.
    return {
        'type': 'file',  # images too: the pipeline recognises them by content type, as for web uploads
        'file': file,
        'id': file_id,
        'url': file_id,
        'name': name,
        'size': len(data),
        'collection_name': meta.get('collection_name') or file.get('collection_name'),
        'content_type': meta.get('content_type') or content_type,
        'status': 'uploaded',
    }


# ---------------------------------------------------------------- direct


def _history(messages_map: dict, last_id: Optional[str]) -> list[dict]:
    """The conversation up to `last_id` (oldest first) as chat-completion messages."""
    chain, seen = [], set()
    current = last_id
    while current and current in messages_map and current not in seen:
        seen.add(current)
        message = messages_map[current]
        chain.append(message)
        current = message.get('parentId')
    out = []
    for message in reversed(chain):
        content = message.get('content') or ''
        if message.get('role') == 'assistant':
            content = fmt.clean(content)
        if content:
            out.append({'role': message.get('role') or 'user', 'content': content})
    return out[-40:]


async def ask_direct(app, request, user: UserModel, session, model_id: str, text: str, settings: dict,
                     files: Optional[list] = None, separate: bool = False, instructions: str = '',
                     title: str = '') -> dict:
    """Start an answer in the user's current Telegram chat (or a new one). Returns the handle to follow it.

    `separate`: always a new chat (the current one is left as it is); `instructions`: a system message for this
    request only (not saved as part of the user's question)."""
    from open_webui.models.chats import Chats
    from open_webui.utils.automations import _resolve_model_defaults

    chat_id, parent_id, history = None, None, []
    fresh = session.updated_at and time.time_ns() - session.updated_at < settings['telegram.new_chat_hours'] * 3600 * 10**9
    if not separate and session.chat_ref and session.last_message_id and fresh:
        chat = await Chats.get_chat_by_id_and_user_id(session.chat_ref, user.id)
        if chat:
            messages_map = await Chats.get_messages_map_by_chat_id(chat.id) or {}
            if session.last_message_id in messages_map:
                chat_id, parent_id = chat.id, session.last_message_id
                history = _history(messages_map, parent_id)

    user_message_id, assistant_id = str(uuid.uuid4()), str(uuid.uuid4())
    user_message = {
        'id': user_message_id,
        'parentId': parent_id,
        'childrenIds': [assistant_id],
        'role': 'user',
        'content': text,
        'timestamp': int(time.time()),
        'models': [model_id],
        'meta': {'source': 'telegram'},
    }
    if files:
        user_message['files'] = files
    form_data = {
        **await _resolve_model_defaults(app, model_id),
        'model': model_id,
        'messages': [*([{'role': 'system', 'content': instructions}] if instructions else []), *history,
                     {'role': 'user', 'content': text}],
        'stream': True,
        'parent_id': parent_id,
        'id': assistant_id,
        'user_message': user_message,
        'session_id': f'telegram:{session.chat_id}',
        'background_tasks': {},
    }
    if chat_id:
        form_data['chat_id'] = chat_id
    if files:
        form_data['files'] = files
    ui = (user.settings.ui or {}) if getattr(user, 'settings', None) else {}
    if ui.get('memory'):
        form_data.setdefault('features', {})['memory'] = True

    result = await app.state.CHAT_COMPLETION_HANDLER(request, form_data, user=user)
    new_chat_id = (result or {}).get('chat_id') if isinstance(result, dict) else None
    if not new_chat_id:
        raise RuntimeError('Không tạo được cuộc trò chuyện')
    if not chat_id:
        try:
            await Chats.update_chat_title_by_id(new_chat_id, f'Telegram · {fmt.short(title or text, 50)}')
        except Exception:
            log.debug('Telegram: could not set the chat title', exc_info=True)
    return {
        'kind': 'chat',
        'chat_id': new_chat_id,
        'message_id': assistant_id,
        'task_ids': (result or {}).get('task_ids', []),
        'new': not chat_id,
    }


# ---------------------------------------------------------------- channel


async def ask_channel(app, request, user: UserModel, channel, model: dict, text: str,
                      thread_id: Optional[str] = None, files: Optional[list] = None) -> dict:
    """Post the question in the channel (under the user's name, the model mentioned) and start the answer."""
    from open_webui.models.channels import Channels
    from open_webui.models.messages import MessageForm
    from open_webui.routers.channels import (
        check_channels_access,
        model_response_handler,
        new_message_handler,
        send_notification,
    )
    from open_webui.socket.main import get_user_ids_from_room

    await check_channels_access(request, user)
    name = (model.get('name') or model['id']).replace('|', ' ').replace('>', ' ')
    content = f'<@M:{model["id"]}|{name}> {text}'
    form = MessageForm(content=content, parent_id=thread_id, data={'files': files} if files else {}, meta={'source': 'telegram'})
    message, channel = await new_message_handler(request, channel.id, form, user, None)
    for file in files or []:
        try:
            await Channels.set_file_message_id_in_channel_by_id(channel.id, file.get('id', ''), message.id)
        except Exception:
            log.debug('Telegram: could not link a file to its channel message', exc_info=True)
    active = await get_user_ids_from_room(f'channel:{channel.id}')
    collected: list = []
    await model_response_handler(request, channel, message, user, collect=collected)
    try:
        await send_notification(request, channel, message, active)
    except Exception:
        log.debug('Telegram: channel notification failed', exc_info=True)
    if not collected:
        raise RuntimeError('AI không trả lời được trong kênh này (model không còn quyền dùng?)')
    answer = collected[0]
    return {
        'kind': 'channel',
        'channel_id': channel.id,
        'message_id': answer['message_id'],
        'thread_id': answer.get('thread_id') or message.id,
        'question_id': message.id,
        'task_ids': answer.get('task_ids') or [],
    }


# ---------------------------------------------------------------- follow the answer


async def fetch(handle: dict) -> dict:
    """{'content', 'done', 'error', 'files'} of the answer now."""
    if handle['kind'] == 'channel':
        from open_webui.models.messages import Messages

        message = await Messages.get_message_by_id(handle['message_id'], include_thread_replies=False)
        if not message or message.channel_id != handle['channel_id']:
            return {'content': '', 'done': True, 'error': 'Tin trả lời đã bị xóa', 'files': []}
        meta = message.meta or {}
        data = message.data or {}
        error = meta.get('error') or data.get('error')
        if isinstance(error, dict):
            error = error.get('content') or error.get('message') or str(error)
        return {'content': message.content or '', 'done': bool(meta.get('done')), 'error': error, 'files': data.get('files') or []}

    from open_webui.models.chats import Chats
    from open_webui.utils.misc import get_output_text

    message = await Chats.get_message_by_id_and_message_id(handle['chat_id'], handle['message_id']) or {}
    error = message.get('error')
    if isinstance(error, dict):
        error = error.get('content') or error.get('message') or str(error)
    content = message.get('content') or get_output_text(message.get('output')) or ''
    return {'content': content, 'done': bool(message.get('done')) or bool(error), 'error': error, 'files': message.get('files') or []}


async def stop(app, handle: dict) -> None:
    from open_webui.tasks import stop_task

    for task_id in handle.get('task_ids') or []:
        try:
            await stop_task(app.state.redis, task_id)
        except Exception:
            log.debug('Telegram: stop task %s failed', task_id, exc_info=True)


async def readable_file(user: UserModel, entry: dict) -> Optional[tuple[str, bytes, str]]:
    """(name, bytes, content type) of an attachment the user may read, else None."""
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    file_id = entry.get('id')
    if not file_id:
        return None
    item = await Files.get_file_by_id(file_id)
    if not item:
        return None
    allowed = item.user_id == user.id or user.role == 'admin'
    if not allowed:
        meta = item.meta or {}
        channel_id = (meta.get('data') or {}).get('channel_id') or meta.get('channel_id')
        if channel_id:
            from open_webui.models.channels import Channels

            channel = await Channels.get_channel_by_id(channel_id)
            allowed = bool(channel) and await can_post(user, channel)
    if not allowed or not item.path:
        return None
    import asyncio

    path = await asyncio.to_thread(Storage.get_file, item.path)
    with open(path, 'rb') as handle:
        data = handle.read()
    content_type = (item.meta or {}).get('content_type') or entry.get('content_type') or 'application/octet-stream'
    return item.filename or entry.get('name') or 'file', data, content_type


async def export_answer(app, request, user: UserModel, handle_target: dict, content: str, question: str, kind: str) -> tuple[str, bytes, str]:
    """The answer as a PDF / Word file (no AI call), also attached to the answer on the web."""
    import asyncio
    import datetime as dt
    import re

    from open_webui.socket.main import get_event_emitter
    from open_webui.tools.builtin import attach_to_reply
    from open_webui.tracker.export import to_docx, to_pdf

    title = fmt.short(question or 'Câu trả lời NCS AI', 80)
    markdown = fmt.clean(content)
    slug = re.sub(r'[^\w]+', '-', fmt.short(question or 'tra-loi', 40), flags=re.U).strip('-')[:40] or 'tra-loi'
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M')
    if kind == 'pdf':
        try:
            data = await asyncio.to_thread(to_pdf, markdown, title)
        except Exception as error:
            log.warning('Telegram: PDF export failed, sending Word (%s)', error)
            kind = 'docx'
    if kind == 'docx':
        data = await asyncio.to_thread(to_docx, markdown, title)
    name = f'NCS-AI_{slug}_{stamp}.{kind}'
    chat_id = f"channel:{handle_target['target_id']}" if handle_target['target'] == 'channel' else handle_target['target_id']
    try:
        emitter = await get_event_emitter({'chat_id': chat_id, 'message_id': handle_target['message_id'], 'user_id': user.id})
        await attach_to_reply(request, user, chat_id, handle_target['message_id'], emitter, name, data, MIME[kind])
    except Exception:
        log.warning('Telegram: could not attach the export to the web message', exc_info=True)
    return name, data, MIME[kind]
