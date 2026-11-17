"""Tables of the file pipeline.

`file_job` is both the queue and the record of each file's processing: workers claim queued rows with a lease
(FOR UPDATE SKIP LOCKED on PostgreSQL), so several processes never work on the same file. `file_page` keeps every
page of a PDF: the text first extracted, the text in use after a vision repair, and the scores of both, so the
monitor can show how each page was judged and what the model changed.
"""

import time
import uuid
from typing import Optional

from sqlalchemy import JSON, BigInteger, Column, Float, Index, Integer, Text, and_, delete, func, or_, select

from open_webui.internal.db import Base, get_async_db_context

STAGES = ('pages', 'repair', 'rebuild', 'index', 'summary')


class FileJob(Base):
    __tablename__ = 'file_job'

    file_id = Column(Text, primary_key=True)
    status = Column(Text, nullable=False)  # queued | running | done | failed | skipped
    stage = Column(Text, nullable=True)
    progress = Column(JSON, nullable=True)  # {'done': n, 'total': n, 'label': str}
    stats = Column(JSON, nullable=True)  # timings and counts per stage
    options = Column(JSON, nullable=True)  # {'pages': [...]} to repair only those pages, {'force': True}

    lease_id = Column(Text, nullable=True)
    lease_until = Column(BigInteger, nullable=True)
    fail_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(BigInteger, nullable=True)
    last_error = Column(Text, nullable=True)

    queued_at = Column(BigInteger, nullable=False)
    started_at = Column(BigInteger, nullable=True)
    finished_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_file_job_status', 'status'), Index('ix_file_job_queued_at', 'queued_at'))


class FilePage(Base):
    __tablename__ = 'file_page'

    file_id = Column(Text, primary_key=True)
    page = Column(Integer, primary_key=True)  # 1-based
    method = Column(Text, nullable=True)  # text_layer | ocr | vision
    status = Column(Text, nullable=False)  # ok | low | repaired | unverified | failed | checked (by a person)
    score = Column(Float, nullable=True)  # score of the text in use
    raw_score = Column(Float, nullable=True)  # score of the first extraction
    issues = Column(JSON, nullable=True)
    raw_text = Column(Text, nullable=True)  # first extraction (OCR or text layer)
    text = Column(Text, nullable=True)  # text in use
    repair = Column(JSON, nullable=True)  # {'model', 'ms', 'at', 'error', 'tokens'}
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_file_page_status', 'file_id', 'status'),)


def _now_ns() -> int:
    return time.time_ns()


class FileJobTable:
    async def enqueue(self, file_id: str, options: Optional[dict] = None) -> None:
        """Queue a file (again). A running job keeps its lease; it is re-queued when it finishes."""
        now = _now_ns()
        async with get_async_db_context() as db:
            job = await db.get(FileJob, file_id)
            if job is None:
                db.add(
                    FileJob(
                        file_id=file_id,
                        status='queued',
                        options=options or {},
                        fail_count=0,
                        queued_at=now,
                        updated_at=now,
                    )
                )
            else:
                job.status = 'queued'
                job.options = options or {}
                job.fail_count = 0
                job.next_attempt_at = None
                job.last_error = None
                job.queued_at = now
                job.updated_at = now
            await db.commit()

    async def claim(self, limit: int, lease_seconds: int, max_active: int) -> list[tuple[str, str, dict]]:
        """(file_id, lease_id, options) of queued files this worker may process now."""
        now = _now_ns()
        async with get_async_db_context() as db:
            active = (
                await db.execute(
                    select(func.count()).select_from(FileJob).where(FileJob.lease_until.isnot(None), FileJob.lease_until > now)
                )
            ).scalar() or 0
            limit = min(limit, max(max_active - active, 0))
            if limit <= 0:
                return []
            query = (
                select(FileJob)
                .where(
                    FileJob.status == 'queued',
                    or_(FileJob.lease_until.is_(None), FileJob.lease_until < now),
                    or_(FileJob.next_attempt_at.is_(None), FileJob.next_attempt_at <= now),
                )
                .order_by(FileJob.queued_at)
                .limit(limit)
            )
            if db.bind.dialect.name == 'postgresql':
                query = query.with_for_update(skip_locked=True)
            jobs = (await db.execute(query)).scalars().all()
            claimed = []
            for job in jobs:
                if job.queued_at == 1 and (job.stats or {}).get('queued_at'):
                    job.queued_at = job.stats['queued_at']  # restore the real time after a priority bump
                job.lease_id = uuid.uuid4().hex
                job.lease_until = now + lease_seconds * 1_000_000_000
                job.status = 'running'
                job.started_at = now
                job.finished_at = None
                job.updated_at = now
                claimed.append((job.file_id, job.lease_id, dict(job.options or {})))
            await db.commit()
            return claimed

    async def update(self, file_id: str, lease_id: str, lease_seconds: Optional[int] = None, **fields) -> bool:
        """Write progress for a held lease (and extend it); False when the lease was lost."""
        async with get_async_db_context() as db:
            job = await db.get(FileJob, file_id)
            if not job or job.lease_id != lease_id:
                return False
            for key, value in fields.items():
                setattr(job, key, value)
            if lease_seconds:
                job.lease_until = _now_ns() + lease_seconds * 1_000_000_000
            job.updated_at = _now_ns()
            await db.commit()
            return True

    async def finish(self, file_id: str, lease_id: str, status: str, stats: dict, error: Optional[str] = None) -> None:
        now = _now_ns()
        async with get_async_db_context() as db:
            job = await db.get(FileJob, file_id)
            if not job or job.lease_id != lease_id:
                return
            job.lease_id = None
            job.lease_until = None
            job.stats = stats
            job.finished_at = now
            job.updated_at = now
            if status == 'failed':
                job.fail_count = (job.fail_count or 0) + 1
                job.last_error = (error or '')[:2000]
                # Retry with backoff up to 3 times, then stay failed until re-queued from the monitor.
                if job.fail_count < 3:
                    job.status = 'queued'
                    job.next_attempt_at = now + min(60 * 2 ** (job.fail_count - 1), 1800) * 1_000_000_000
                else:
                    job.status = 'failed'
            else:
                job.status = status
                job.fail_count = 0
                job.last_error = error
                job.stage = None
                job.progress = None
            await db.commit()

    async def prioritize(self, file_id: str) -> None:
        """Move a queued job to the front of the queue (a user is waiting for it)."""
        async with get_async_db_context() as db:
            job = await db.get(FileJob, file_id)
            if job and job.status == 'queued' and job.queued_at > 1:
                # queued_at orders the queue; keep it as the time shown on the monitor in stats.
                job.stats = {**(job.stats or {}), 'queued_at': job.queued_at}
                job.queued_at = 1
                job.next_attempt_at = None
                await db.commit()

    async def get(self, file_id: str) -> Optional[FileJob]:
        async with get_async_db_context() as db:
            return await db.get(FileJob, file_id)

    async def release_stale(self) -> int:
        """Re-queue jobs whose worker died (lease expired while running)."""
        now = _now_ns()
        async with get_async_db_context() as db:
            jobs = (
                await db.execute(
                    select(FileJob).where(
                        FileJob.status == 'running', FileJob.lease_until.isnot(None), FileJob.lease_until < now
                    )
                )
            ).scalars().all()
            for job in jobs:
                job.status = 'queued'
                job.lease_id = None
                job.lease_until = None
                job.updated_at = now
            await db.commit()
            return len(jobs)


class FilePageTable:
    async def replace_all(self, file_id: str, pages: list[dict]) -> None:
        now = _now_ns()
        async with get_async_db_context() as db:
            await db.execute(delete(FilePage).where(FilePage.file_id == file_id))
            for page in pages:
                db.add(FilePage(file_id=file_id, updated_at=now, **page))
            await db.commit()

    async def list(self, file_id: str) -> list[FilePage]:
        async with get_async_db_context() as db:
            return (
                await db.execute(select(FilePage).where(FilePage.file_id == file_id).order_by(FilePage.page))
            ).scalars().all()

    async def save(self, file_id: str, page: int, **fields) -> None:
        async with get_async_db_context() as db:
            row = await db.get(FilePage, (file_id, page))
            if row is None:
                return
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = _now_ns()
            await db.commit()

    async def delete_file(self, file_id: str) -> None:
        async with get_async_db_context() as db:
            await db.execute(delete(FilePage).where(FilePage.file_id == file_id))
            await db.execute(delete(FileJob).where(FileJob.file_id == file_id))
            await db.commit()


FileJobs = FileJobTable()
FilePages = FilePageTable()
__all__ = ['FileJob', 'FilePage', 'FileJobs', 'FilePages', 'STAGES', 'and_']
