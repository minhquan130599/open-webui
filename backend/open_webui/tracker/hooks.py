"""Entry points called from upstream code. Each is a no-op when the tracker is disabled and never raises."""

import logging
from typing import Optional

from open_webui.tracker.config import ENABLE_PROJECT_TRACKER

log = logging.getLogger(__name__)


async def on_channel_activity(channel_id: Optional[str]) -> None:
    """A message was posted, edited or deleted in the channel."""
    if not ENABLE_PROJECT_TRACKER or not channel_id:
        return
    try:
        from open_webui.tracker.models import Trackers

        await Trackers.mark_dirty(channel_id)
    except Exception:
        log.exception('Tracker: failed to mark channel %s dirty', channel_id)


async def on_message_deleted(message) -> None:
    """Called before a channel message is deleted, while its content is still available."""
    if not ENABLE_PROJECT_TRACKER or not getattr(message, 'channel_id', None):
        return
    try:
        from open_webui.models.users import Users
        from open_webui.tracker.models import Trackers

        user = await Users.get_user_by_id(message.user_id) if message.user_id else None
        await Trackers.add_deletion(
            message.channel_id,
            {
                'id': message.id,
                'user_name': user.name if user else 'Không rõ',
                'content': (message.content or '')[:1000],
                'created_at': message.created_at,
            },
        )
    except Exception:
        log.exception('Tracker: failed to record deletion of message %s', getattr(message, 'id', None))


async def get_channel_prompt_context(channel_id: str, exclude_message_ids: Optional[set[str]] = None) -> str:
    """Text appended to the system prompt when a model is mentioned in the channel; '' when disabled."""
    if not ENABLE_PROJECT_TRACKER:
        return ''
    try:
        from open_webui.tracker.service import build_channel_context

        return await build_channel_context(channel_id, exclude_message_ids)
    except Exception:
        log.exception('Tracker: failed to build context for channel %s', channel_id)
        return ''
