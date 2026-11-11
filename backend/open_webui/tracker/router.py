import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.channels import Channels
from open_webui.tracker.config import ENABLE_PROJECT_TRACKER
from open_webui.tracker.models import TrackerItemModel, Trackers
from open_webui.tracker.schema import load_state, render_state_markdown
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()


class TrackerStateResponse(BaseModel):
    channel_id: str
    state: Optional[dict] = None
    markdown: str = ''
    version: int = 0
    processed_at: Optional[int] = None
    pending_count: int = 0
    # A run holds a lease while it calls the LLM.
    processing: bool = False
    # Set after a failed run: the channel is not retried before this time (ns), except on refresh.
    next_attempt_at: Optional[int] = None
    last_error: Optional[str] = None


async def _get_readable_channel(channel_id: str, user):
    if not ENABLE_PROJECT_TRACKER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    if user.role == 'admin':
        channel = await Channels.get_channel_by_id(channel_id)
    else:
        channel = await Channels.get_channel_by_id_and_user_id(channel_id, user.id)
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return channel


@router.get('/channels/{channel_id}', response_model=TrackerStateResponse)
async def get_channel_state(channel_id: str, user=Depends(get_verified_user)):
    await _get_readable_channel(channel_id, user)
    row = await Trackers.get_state(channel_id)
    if not row:
        return TrackerStateResponse(channel_id=channel_id)
    return TrackerStateResponse(
        channel_id=channel_id,
        state=row.state,
        markdown=render_state_markdown(load_state(row.state)) if row.state else '',
        version=row.version,
        processed_at=row.processed_at,
        pending_count=row.pending_count,
        processing=bool(row.lease_until and row.lease_until > time.time_ns()),
        # next_attempt_at is also set while waiting for file extraction; only report it for failures.
        next_attempt_at=row.next_attempt_at if row.fail_count else None,
        last_error=row.last_error if user.role == 'admin' else None,
    )


@router.post('/channels/{channel_id}/refresh')
async def refresh_channel_state(channel_id: str, user=Depends(get_verified_user)):
    await _get_readable_channel(channel_id, user)
    # Not mark_dirty: a refresh is not a message, and it must also skip the retry backoff of a failed run.
    await Trackers.request_refresh(channel_id)
    return {'status': True}


@router.get('/channels/{channel_id}/items', response_model=list[TrackerItemModel])
async def get_channel_items(
    channel_id: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
    limit: int = 50,
    user=Depends(get_verified_user),
):
    """Processed batches with their summaries; start/end are unix seconds."""
    await _get_readable_channel(channel_id, user)
    start_ns = (start or 0) * 1_000_000_000
    end_ns = (end or int(time.time()) + 1) * 1_000_000_000
    return await Trackers.get_items(channel_id, start_ns, end_ns, min(max(limit, 1), 200))


@router.get('/stats')
async def get_stats(user=Depends(get_admin_user)):
    if not ENABLE_PROJECT_TRACKER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return await Trackers.get_stats(time.time_ns())
