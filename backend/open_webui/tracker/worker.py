"""Tracker worker loop.

Runs inside Open WebUI (TRACKER_WORKER_MODE=app) or standalone:
    python -m open_webui.tracker.worker
"""

import asyncio
import logging
import random
import time

from open_webui.tracker.config import (
    ENABLE_PROJECT_TRACKER,
    TRACKER_LEASE_SECONDS,
    TRACKER_POLL_INTERVAL,
)
from open_webui.tracker.config import runtime as tracker_runtime
from open_webui.tracker.models import Trackers
from open_webui.tracker.service import process_channel

log = logging.getLogger(__name__)


async def tracker_worker_loop() -> None:
    log.info('Project tracker worker started (max concurrency %s)', (await tracker_runtime())['max_concurrency'])
    running: set[asyncio.Task] = set()

    while True:
        try:
            # Re-read every poll: these can be changed from the monitor page while running.
            settings = await tracker_runtime()
            claimed = await Trackers.claim_due(
                now=time.time_ns(),
                batch_size=settings['batch_size'],
                idle_ns=settings['idle_seconds'] * 1_000_000_000,
                lease_ns=TRACKER_LEASE_SECONDS * 1_000_000_000,
                max_active=settings['max_concurrency'],
            )
            for channel_id, lease_id in claimed:
                task = asyncio.create_task(process_channel(channel_id, lease_id))
                running.add(task)
                task.add_done_callback(running.discard)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Tracker worker poll failed')

        # Jitter spreads polls when several processes run the loop.
        await asyncio.sleep(TRACKER_POLL_INTERVAL + random.uniform(0, 1))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    if not ENABLE_PROJECT_TRACKER:
        raise SystemExit('ENABLE_PROJECT_TRACKER is not true')
    asyncio.run(tracker_worker_loop())
