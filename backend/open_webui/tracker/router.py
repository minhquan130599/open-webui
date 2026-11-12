import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.channels import Channels
from open_webui.models.users import Users
from open_webui.tracker import mywork, reminders
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
    # Closed ('dong') and cancelled ('huy') work items, newest first.
    archive: list[dict] = []
    # The viewer owns the channel or is an admin: may close, cancel and reopen items.
    can_manage: bool = False


class ArchiveForm(BaseModel):
    ids: list[str]
    kind: Literal['dong', 'huy']


def _can_manage(channel, user) -> bool:
    return user.role == 'admin' or channel.user_id == user.id


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
    channel = await _get_readable_channel(channel_id, user)
    row = await Trackers.get_state(channel_id)
    if not row:
        return TrackerStateResponse(channel_id=channel_id, can_manage=_can_manage(channel, user))
    if row.state and any(not item.get('id') for item in row.state.get('hang_muc') or []):
        await Trackers.ensure_item_ids(channel_id)  # state stored before items had ids
        row = await Trackers.get_state(channel_id)
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
        archive=sorted(row.archive or [], key=lambda e: e.get('archived_at') or 0, reverse=True),
        can_manage=_can_manage(channel, user),
    )


async def _get_managed_channel(channel_id: str, user):
    channel = await _get_readable_channel(channel_id, user)
    if not _can_manage(channel, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    return channel


@router.post('/channels/{channel_id}/items/archive')
async def archive_work_items(channel_id: str, form: ArchiveForm, user=Depends(get_verified_user)):
    """Close ('dong') or cancel ('huy') work items; they leave the panel, reports and the LLM's view."""
    await _get_managed_channel(channel_id, user)
    return {'archived': await Trackers.archive_items(channel_id, form.ids, form.kind, user.id)}


@router.post('/channels/{channel_id}/items/close-done')
async def close_done_work_items(channel_id: str, user=Depends(get_verified_user)):
    """Close every completed work item at once."""
    await _get_managed_channel(channel_id, user)
    row = await Trackers.get_state(channel_id)
    done = (
        [i.get('id') for i in (row.state or {}).get('hang_muc') or [] if i.get('trang_thai') == 'hoan_thanh']
        if row
        else []
    )
    return {'archived': await Trackers.archive_items(channel_id, [i for i in done if i], 'dong', user.id)}


@router.post('/channels/{channel_id}/items/{item_id}/reopen')
async def reopen_work_item(channel_id: str, item_id: str, user=Depends(get_verified_user)):
    await _get_managed_channel(channel_id, user)
    if not await Trackers.reopen_item(channel_id, item_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return {'status': True}


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


def _require_tracker():
    if not ENABLE_PROJECT_TRACKER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)


@router.get('/my-work')
async def get_my_work(user_id: Optional[str] = None, user=Depends(get_verified_user)):
    """Open work items of the user, or of a member when the viewer owns their channels (admins: anyone)."""
    _require_tracker()
    target_id = user_id or user.id
    entries = await mywork.work_of(user, target_id)
    if entries is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    target = user if target_id == user.id else await Users.get_user_by_id(target_id)
    return {
        'user': {'id': target_id, 'name': target.name if target else ''},
        'today': mywork.today(),
        'items': [e.to_dict() for e in entries],
        'can_view_team': bool(await mywork.managed_channels(user)),
    }


@router.get('/team')
async def get_team(user=Depends(get_verified_user)):
    """People in the channels the viewer owns (admins: all channels) with counts of their open items."""
    _require_tracker()
    return await mywork.team_of(user)


class ReminderSettingsForm(BaseModel):
    enable: bool
    time: str
    day_before: bool


@router.get('/reminders/settings')
async def get_reminder_settings(user=Depends(get_admin_user)):
    _require_tracker()
    return await reminders.get_settings()


@router.post('/reminders/settings')
async def update_reminder_settings(form: ReminderSettingsForm, user=Depends(get_admin_user)):
    _require_tracker()
    try:
        return await reminders.set_settings(form.enable, form.time, form.day_before)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post('/reminders/send')
async def send_reminders_now(user=Depends(get_admin_user)):
    """Send today's reminders now; owners already reminded today are not notified again."""
    _require_tracker()
    settings = await reminders.get_settings()
    return {'created': await reminders.send_due_reminders(settings['day_before'])}


@router.get('/stats')
async def get_stats(user=Depends(get_admin_user)):
    if not ENABLE_PROJECT_TRACKER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return await Trackers.get_stats(time.time_ns())
