"""Rebuild knowledge and memory vectors, started from the monitor page.

Same work as Admin > Settings > Documents > "Reindex knowledge and memory vectors": files of every knowledge base,
the knowledge-base search index, then memories. It runs in the background of one worker; the state is kept in the
config table (`monitor.reindex`) so the monitor page can follow it and a second run is refused while one is going.
"""

import asyncio
import logging
import time

from open_webui.models.config import Config

log = logging.getLogger(__name__)

STATE_KEY = 'monitor.reindex'
STALE_SECONDS = 6 * 3600  # a run that never finished (worker restarted) stops blocking after this


def internal_request(app):
    from starlette.datastructures import Headers
    from starlette.requests import Request

    return Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/internal',
            'query_string': b'',
            'headers': Headers({}).raw,
            'client': ('', 0),
            'server': ('127.0.0.1', 80),
            'scheme': 'http',
            'app': app,
        }
    )


async def _set(**state) -> None:
    current = await Config.get(STATE_KEY) or {}
    await Config.upsert({STATE_KEY: {**(current if isinstance(current, dict) else {}), **state}})


async def _run(app) -> None:
    from open_webui.internal.db import get_async_db
    from open_webui.models.users import Users
    from open_webui.routers.knowledge import reindex_knowledge_base_metadata_embeddings, reindex_knowledge_files
    from open_webui.routers.memories import reindex_memories_from_vector_db

    request = internal_request(app)
    admin = await Users.get_super_admin_user()
    if admin is None:
        await _set(state='failed', finished_at=int(time.time()), error='Không có tài khoản admin')
        return
    try:
        await _set(step='knowledge_files')
        async with get_async_db() as db:
            await reindex_knowledge_files(request, user=admin, db=db)
        await _set(step='knowledge_search')
        await reindex_knowledge_base_metadata_embeddings(request, user=admin)
        await _set(step='memories')
        await reindex_memories_from_vector_db(request, user=admin)
        await _set(state='done', step=None, finished_at=int(time.time()), error=None)
        log.info('Reindex started from the monitor page finished')
    except Exception as e:
        log.exception('Reindex started from the monitor page failed')
        await _set(state='failed', finished_at=int(time.time()), error=str(e)[:500])


async def start(app) -> dict:
    """Start a run unless one is going. Returns the state."""
    current = await Config.get(STATE_KEY) or {}
    if (
        isinstance(current, dict)
        and current.get('state') == 'running'
        and time.time() - (current.get('started_at') or 0) < STALE_SECONDS
    ):
        return {**current, 'already_running': True}
    state = {'state': 'running', 'step': 'starting', 'started_at': int(time.time()), 'finished_at': None, 'error': None}
    await Config.upsert({STATE_KEY: state})
    app.state.monitor_reindex_task = asyncio.create_task(_run(app))
    return state
