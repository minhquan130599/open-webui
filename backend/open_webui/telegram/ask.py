"""Posting into channels from Telegram as the linked user, through the same code paths as the web.

The bot no longer talks with the AI: a message sent to the bot is posted into the channel the user chose, under
the user's name, as an ordinary message (marked meta.source = 'telegram' so the web shows where it came from).
It never starts an AI answer, even when it mentions a model, and nothing posted in the channel is sent back to
Telegram. Permissions are the web's: channel write access checked by new_message_handler.
"""

import io
import logging
from typing import Optional

from open_webui.models.users import UserModel

log = logging.getLogger(__name__)


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
    # Processed now (not in the background): a file that could not be read is reported to the sender instead of
    # being handed to the AI, which would only find it empty.
    from open_webui.models.files import Files

    stored = await Files.get_file_by_id(file_id)
    if stored and (stored.data or {}).get('status') == 'failed':
        log.warning('Telegram: file %s could not be read: %s', file_id, (stored.data or {}).get('error'))
        raise ValueError('Không đọc được nội dung file (file hỏng, bị khóa mật khẩu hoặc định dạng chưa hỗ trợ). '
                         'Hãy kiểm tra file rồi gửi lại.')
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


# ---------------------------------------------------------------- posting


async def post_to_channel(request, user: UserModel, channel, text: str, files: Optional[list] = None):
    """Post the message in the channel under the user's name; no AI answer is started (model_response_handler and
    the channel apps are not called), members are notified like for a message written on the web."""
    from open_webui.models.channels import Channels
    from open_webui.models.messages import MessageForm
    from open_webui.routers.channels import check_channels_access, new_message_handler, send_notification
    from open_webui.socket.main import get_user_ids_from_room

    await check_channels_access(request, user)
    form = MessageForm(content=text, data={'files': files} if files else {}, meta={'source': 'telegram'})
    message, channel = await new_message_handler(request, channel.id, form, user, None)
    for file in files or []:
        try:
            await Channels.set_file_message_id_in_channel_by_id(channel.id, file.get('id', ''), message.id)
        except Exception:
            log.debug('Telegram: could not link a file to its channel message', exc_info=True)
    try:
        await send_notification(request, channel, message, await get_user_ids_from_room(f'channel:{channel.id}'))
    except Exception:
        log.debug('Telegram: channel notification failed', exc_info=True)
    return message
