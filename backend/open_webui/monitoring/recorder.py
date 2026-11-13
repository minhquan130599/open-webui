"""Record performance events without slowing requests down.

`record()` only appends to an in-memory buffer; a background task in each worker writes the buffer to the
`perf_event` table every few seconds and deletes events older than PERF_RETENTION_DAYS. Recording never
raises: a monitoring failure must not break a chat.

Kinds:
  llm      one model response (chat, channel @mention, background task): duration, time to first token, tokens
  tool     one function/tool call inside a response: name, duration, status
  rag      one knowledge retrieval at question time: embedding, vector search, rerank timings
  ingest   one file processed for knowledge: extraction, embedding, characters, chunks
  tracker  one call of the project tracker to its LLM: endpoint, tokens, failover
"""

import asyncio
import logging
import os
import time
import uuid
from typing import Optional

from sqlalchemy import BigInteger, Column, Index, Integer, JSON, Text, delete, insert

from open_webui.internal.db import Base, get_async_db

log = logging.getLogger(__name__)

ENABLE_PERF_MONITOR = os.getenv('ENABLE_PERF_MONITOR', 'True').lower() == 'true'
PERF_RETENTION_DAYS = max(int(os.getenv('PERF_RETENTION_DAYS', '90') or 90), 1)
FLUSH_SECONDS = 3
MAX_BUFFER = 20_000  # events dropped beyond this if the database is unreachable
CLEANUP_SECONDS = 6 * 3600


class PerfEvent(Base):
    __tablename__ = 'perf_event'

    id = Column(Text, primary_key=True)
    ts = Column(BigInteger, nullable=False)  # epoch milliseconds, when the operation started
    kind = Column(Text, nullable=False)
    source = Column(Text, nullable=True)  # chat | channel | task | tracker | upload ...
    user_id = Column(Text, nullable=True)
    model_id = Column(Text, nullable=True)
    chat_id = Column(Text, nullable=True)
    message_id = Column(Text, nullable=True)
    name = Column(Text, nullable=True)  # tool name, knowledge/file name, endpoint
    status = Column(Text, nullable=False, default='ok')  # ok | error
    duration_ms = Column(Integer, nullable=True)
    ttft_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    count = Column(Integer, nullable=True)  # tool calls in a response, chunks retrieved, ...
    meta = Column(JSON, nullable=True)

    __table_args__ = (
        Index('ix_perf_event_kind_ts', 'kind', 'ts'),
        Index('ix_perf_event_ts', 'ts'),
    )


_COLUMNS = {c.name for c in PerfEvent.__table__.columns}
_buffer: list[dict] = []
_task: Optional[asyncio.Task] = None
_last_cleanup = 0.0


def now_ms() -> int:
    return int(time.time() * 1000)


def record(kind: str, **fields) -> None:
    """Queue one event. Unknown fields go into meta; never raises."""
    if not ENABLE_PERF_MONITOR:
        return
    try:
        row = {'id': uuid.uuid4().hex, 'kind': kind, 'ts': fields.pop('ts', None) or now_ms(), 'status': 'ok'}
        meta = dict(fields.pop('meta', None) or {})
        for key, value in fields.items():
            if key in _COLUMNS:
                row[key] = value
            elif value is not None:
                meta[key] = value
        for key in ('duration_ms', 'ttft_ms', 'input_tokens', 'output_tokens', 'count'):
            if row.get(key) is not None:
                row[key] = int(row[key])
        row['meta'] = meta or None
        if len(_buffer) < MAX_BUFFER:
            _buffer.append(row)
        _ensure_task()
    except Exception as e:  # monitoring must never break the caller
        log.debug('perf record failed: %s', e)


def _ensure_task() -> None:
    global _task
    if _task and not _task.done():
        return
    try:
        _task = asyncio.get_running_loop().create_task(_flush_loop())
    except RuntimeError:
        pass  # no running loop (sync context); the next async record starts it


async def flush() -> int:
    global _buffer
    if not _buffer:
        return 0
    rows, _buffer = _buffer, []
    try:
        async with get_async_db() as db:
            await db.execute(insert(PerfEvent), rows)
            await db.commit()
        return len(rows)
    except Exception as e:
        log.warning('Could not write %d performance events: %s', len(rows), e)
        if len(_buffer) + len(rows) <= MAX_BUFFER:
            _buffer = rows + _buffer  # retry on the next flush
        return 0


async def _cleanup() -> None:
    global _last_cleanup
    if time.monotonic() - _last_cleanup < CLEANUP_SECONDS:
        return
    _last_cleanup = time.monotonic()
    cutoff = now_ms() - PERF_RETENTION_DAYS * 86_400_000
    try:
        async with get_async_db() as db:
            await db.execute(delete(PerfEvent).where(PerfEvent.ts < cutoff))
            await db.commit()
    except Exception as e:
        log.warning('Could not delete old performance events: %s', e)


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(FLUSH_SECONDS)
        await flush()
        await _cleanup()


class Timer:
    """`with Timer() as t: ...` then t.ms; also usable as start/stop for async code."""

    def __init__(self):
        self.started = time.perf_counter()
        self.ts = now_ms()
        self.ms: Optional[int] = None

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def stop(self) -> int:
        self.ms = int((time.perf_counter() - self.started) * 1000)
        return self.ms

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False
