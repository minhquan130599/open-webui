"""Builtin tools exposing tracker state. Access is limited to channels the user can read."""

import logging
import time
from typing import Optional

from fastapi import Request

from open_webui.models.channels import Channels
from open_webui.tracker.models import Trackers
from open_webui.tracker.prompts import format_time
from open_webui.tracker.schema import load_state, normalize_key
from open_webui.tracker.service import TZ, build_channel_context
from open_webui.utils.json_codec import JSONCodec

log = logging.getLogger(__name__)


async def _accessible_channels(user_id: str) -> list:
    return [c for c in await Channels.get_channels_by_user_id(user_id) if c.type != 'dm']


async def get_project_status(
    project: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get the current status of a project (one channel = one project): summary, work items with owner,
    progress, due date and blockers, decisions, risks, next steps, plus messages not yet summarized.

    :param project: Channel ID or channel name of the project
    :return: The project status as text
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        channels = await _accessible_channels(__user__.get('id'))
        key = normalize_key(project)
        match = next((c for c in channels if c.id == project), None) or next(
            (c for c in channels if normalize_key(c.name) == key), None
        )
        if not match:
            candidates = [c for c in channels if key and key in normalize_key(c.name)]
            if len(candidates) == 1:
                match = candidates[0]
            elif candidates:
                return JSONCodec.dumps(
                    {
                        'error': 'Ambiguous project',
                        'candidates': [{'id': c.id, 'name': c.name} for c in candidates[:10]],
                    },
                    ensure_ascii=False,
                )
        if not match:
            return JSONCodec.dumps({'error': 'Project not found'})

        return f'Dự án: {match.name} (id: {match.id})\n\n' + await build_channel_context(match.id)
    except Exception as e:
        log.exception(f'get_project_status error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def list_my_projects(
    query: Optional[str] = None,
    only_with_issues: bool = False,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List projects (channels) the user can access with a short status: overview, number of work items,
    blocked items, overdue items and risks. Use it for questions spanning several projects.

    :param query: Optional text to filter project names
    :param only_with_issues: If true, only return projects with blocked/overdue items or high risks
    :return: JSON list of projects with their short status
    """
    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        channels = await _accessible_channels(__user__.get('id'))
        if query:
            key = normalize_key(query)
            channels = [c for c in channels if key in normalize_key(c.name)]

        states = {s.channel_id: s for s in await Trackers.get_states([c.id for c in channels])}
        today = format_time(time.time_ns(), TZ)[:10]

        projects = []
        for channel in channels:
            row = states.get(channel.id)
            state = load_state(row.state if row else None)
            blocked = [i.ten for i in state.hang_muc if i.trang_thai == 'bi_chan']
            overdue = [i.ten for i in state.hang_muc if i.han and i.han < today and i.trang_thai != 'hoan_thanh']
            high_risks = [r.noi_dung for r in state.rui_ro if r.muc_do == 'cao']
            if only_with_issues and not (blocked or overdue or high_risks):
                continue
            projects.append(
                {
                    'id': channel.id,
                    'name': channel.name,
                    'overview': state.tong_quan,
                    'work_items': len(state.hang_muc),
                    'done': sum(1 for i in state.hang_muc if i.trang_thai == 'hoan_thanh'),
                    'blocked': blocked,
                    'overdue': overdue,
                    'high_risks': high_risks,
                    'updated_at': format_time(row.processed_at, TZ) if row and row.processed_at else None,
                    'pending_messages': row.pending_count if row else 0,
                }
            )
        return JSONCodec.dumps(projects[:100], ensure_ascii=False)
    except Exception as e:
        log.exception(f'list_my_projects error: {e}')
        return JSONCodec.dumps({'error': str(e)})
