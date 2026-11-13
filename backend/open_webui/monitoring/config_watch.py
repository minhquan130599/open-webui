"""Apply settings changed from the monitor page in every worker, without a restart.

The monitor writes the settings to the config table and bumps `monitor.config_version`
({'version': n, 'groups': [...]}). Each worker polls that key and rebuilds what it keeps in memory:
the embedding/reranking functions (knowledge), the local speech-to-text model, the tracker settings cache,
the cached model lists after a connection change.
Everything else is read from the config table on every use and needs nothing.
"""

import asyncio
import logging

from open_webui.models.config import Config

log = logging.getLogger(__name__)

VERSION_KEY = 'monitor.config_version'
POLL_SECONDS = 15


async def apply(app, groups: set[str]) -> None:
    if 'tracker' in groups:
        from open_webui.tracker.config import reset_runtime_cache

        reset_runtime_cache()
    if groups & {'embedding', 'reranker'}:
        from open_webui.main import load_rag_functions

        await load_rag_functions(app)
    if 'stt' in groups:
        app.state.faster_whisper_model = None  # loaded again with the new model on the next transcription
    if 'connections' in groups:
        # Same as saving connections in Admin > Settings: forget the cached model lists of this worker.
        from starlette.datastructures import Headers
        from starlette.requests import Request

        from open_webui.routers.openai import clear_models_cache

        request = Request(
            {
                'type': 'http',
                'method': 'GET',
                'path': '/internal',
                'query_string': b'',
                'headers': Headers({}).raw,
                'client': ('', 0),
                'server': ('127.0.0.1', 80),
                'scheme': 'http',
                'app': app,
            }
        )
        await clear_models_cache(request)


async def config_watch_loop(app) -> None:
    seen = None
    while True:
        try:
            current = await Config.get(VERSION_KEY)
            version = (current or {}).get('version') if isinstance(current, dict) else None
            if seen is None:
                seen = version or 0
            elif version and version != seen:
                seen = version
                groups = set((current or {}).get('groups') or [])
                await apply(app, groups)
                log.info('Applied settings changed from the monitor page: %s', ', '.join(sorted(groups)) or '-')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning('Could not apply settings from the monitor page: %s', e)
        await asyncio.sleep(POLL_SECONDS)
