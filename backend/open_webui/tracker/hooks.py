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
