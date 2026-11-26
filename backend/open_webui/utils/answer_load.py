"""Answers people are waiting for, across workers, so background model work gives way to them.

Each worker publishes how many answers it is writing right now (model calls for a chat, channel or API reply,
counted by monitoring/llm.py) under its own Redis key; background jobs that call the model (file summaries, OCR
repair) wait before each call while any answer is being written. A user waiting for one file's summary marks that
file as wanted: its job then goes on without waiting.
"""

import asyncio
import logging
import os
import socket
import time
from typing import Awaitable, Callable, Optional

from open_webui.monitoring.llm import answering_now

log = logging.getLogger(__name__)

ANSWERING_PREFIX = 'ncs:answering:'
WANTED_PREFIX = 'ncs:file_wanted:'
PUBLISH_SECONDS = 2
KEY_TTL = 10  # a worker that stops publishing stops counting after this
WANTED_TTL = 30
CHECK_SECONDS = 2
# Background work waits at most this long for one model call, so a busy day still finishes the summaries.
MAX_WAIT_SECONDS = 600

_WORKER = f'{socket.gethostname()}:{os.getpid()}'


def _redis(app):
    return getattr(getattr(app, 'state', None), 'redis', None)


async def publish_loop(app) -> None:
    """Keep this worker's count of answers being written in Redis (nothing to do without Redis)."""
    key = ANSWERING_PREFIX + _WORKER
    last = None
    while True:
        try:
            redis = _redis(app)
            if redis is not None:
                count = answering_now()
                if count:
                    await redis.set(key, count, ex=KEY_TTL)
                elif last:
                    await redis.delete(key)
                last = count
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.debug('answer load publish failed: %s', error)
        await asyncio.sleep(PUBLISH_SECONDS)


async def answers_in_progress(app) -> int:
    """Answers being written right now, all workers together."""
    redis = _redis(app)
    if redis is None:
        return answering_now()
    total = 0
    try:
        async for key in redis.scan_iter(match=ANSWERING_PREFIX + '*', count=100):
            value = await redis.get(key)
            total += int(value or 0)
    except Exception as error:
        log.debug('answer load read failed: %s', error)
        return answering_now()
    return total


async def mark_wanted(app, file_id: str) -> None:
    """A user is waiting for this file's background work: it should not give way."""
    redis = _redis(app)
    if redis is None:
        _LOCAL_WANTED[file_id] = time.monotonic() + WANTED_TTL
        return
    try:
        await redis.set(WANTED_PREFIX + file_id, '1', ex=WANTED_TTL)
    except Exception as error:
        log.debug('mark wanted failed: %s', error)


_LOCAL_WANTED: dict[str, float] = {}


async def is_wanted(app, file_id: str) -> bool:
    redis = _redis(app)
    if redis is None:
        return _LOCAL_WANTED.get(file_id, 0) > time.monotonic()
    try:
        return bool(await redis.exists(WANTED_PREFIX + file_id))
    except Exception:
        return False


async def give_way(
    app, file_id: str, note: Optional[Callable[[str], Awaitable[None]]] = None, max_wait: float = MAX_WAIT_SECONDS
) -> float:
    """Wait while answers are being written (unless a user waits for this file); returns the seconds waited."""
    if app is None:
        return 0.0
    started = time.monotonic()
    noted = False
    while time.monotonic() - started < max_wait:
        if await is_wanted(app, file_id):
            break
        busy = await answers_in_progress(app)
        if not busy:
            break
        if note and not noted:
            noted = True
            try:
                await note(f'Tạm dừng để ưu tiên {busy} câu trả lời đang chạy')
            except Exception:
                pass
        await asyncio.sleep(CHECK_SECONDS)
    return time.monotonic() - started
