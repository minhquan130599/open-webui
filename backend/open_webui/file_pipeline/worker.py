"""Background loop that runs queued file jobs inside the Open WebUI process."""

import asyncio
import logging
import random

from open_webui.file_pipeline.models import FileJobs
from open_webui.file_pipeline.pipeline import LEASE_SECONDS, run_job
from open_webui.file_pipeline.settings import file_settings

log = logging.getLogger(__name__)

POLL_SECONDS = 5


async def enqueue_file(file_id: str, options: dict | None = None) -> None:
    """Queue a file after upload; never raises (the upload already succeeded)."""
    try:
        if (await file_settings())['files.pipeline.enable'] or options:
            await FileJobs.enqueue(file_id, options)
    except Exception:
        log.exception('Could not queue file %s for processing', file_id)


async def file_pipeline_loop(app) -> None:
    running: set[asyncio.Task] = set()
    log.info('File pipeline worker started')
    while True:
        try:
            await FileJobs.release_stale()
            settings = await file_settings()
            free = settings['files.pipeline.concurrency'] - len(running)
            if free > 0:
                for file_id, lease_id, options in await FileJobs.claim(
                    free, LEASE_SECONDS, settings['files.pipeline.concurrency']
                ):
                    task = asyncio.create_task(run_job(app, file_id, lease_id, options))
                    running.add(task)
                    task.add_done_callback(running.discard)
        except asyncio.CancelledError:
            for task in running:
                task.cancel()
            raise
        except Exception:
            log.exception('File pipeline worker poll failed')
        await asyncio.sleep(POLL_SECONDS + random.uniform(0, 1))
