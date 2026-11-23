"""Report schedules, the queue of sends (comments written ahead), Telegram chats linked to users, and named leases
(one Telegram poller, a limited number of report writers across workers)."""

from __future__ import annotations

import secrets
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, BigInteger, Boolean, Column, Index, Integer, Text, UniqueConstraint, and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from open_webui.internal.db import Base, get_async_db_context


def now_ns() -> int:
    return time.time_ns()


class ReportSchedule(Base):
    __tablename__ = 'report_schedule'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    # Everything the user sets: channels, timing, content, delivery (see schedule.normalize_config).
    config = Column(JSON, nullable=True)
    next_run_at = Column(BigInteger, nullable=True)
    last_run_at = Column(BigInteger, nullable=True)
    last_status = Column(Text, nullable=True)  # sent | skipped | error
    last_error = Column(Text, nullable=True)
    last_digest = Column(Text, nullable=True)
    lease_id = Column(Text, nullable=True)
    lease_until = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_report_schedule_user', 'user_id'),
        Index('ix_report_schedule_next_run', 'next_run_at'),
    )


class ReportTelegramLink(Base):
    __tablename__ = 'report_telegram_link'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    code = Column(Text, nullable=True)  # set until the chat sends it to the bot
    chat_id = Column(Text, nullable=True)
    chat_title = Column(Text, nullable=True)
    chat_type = Column(Text, nullable=True)  # private | group | supergroup | channel
    created_at = Column(BigInteger, nullable=False)
    linked_at = Column(BigInteger, nullable=True)

    __table_args__ = (Index('ix_report_telegram_link_user', 'user_id'), Index('ix_report_telegram_link_code', 'code'))


class ReportLock(Base):
    __tablename__ = 'report_lock'

    name = Column(Text, primary_key=True)
    lease_id = Column(Text, nullable=True)
    lease_until = Column(BigInteger, nullable=True)
    data = Column(JSON, nullable=True)


class ReportJob(Base):
    """One send of a schedule: queued ahead of its time, its AI comments written when the model is free, sent at
    run_at with the figures counted at that moment."""

    __tablename__ = 'report_job'

    id = Column(Text, primary_key=True)
    schedule_id = Column(Text, nullable=False)
    run_at = Column(BigInteger, nullable=False)  # the send time of the schedule
    # queued -> writing -> ready -> sending -> sent | skipped | error; cancelled when the schedule changed
    status = Column(Text, nullable=False)
    lease_id = Column(Text, nullable=True)
    lease_until = Column(BigInteger, nullable=True)
    retry_at = Column(BigInteger, nullable=True)  # a failed write is tried again from this time
    attempts = Column(Integer, nullable=False, default=0)
    comment = Column(Text, nullable=True)
    comment_digest = Column(Text, nullable=True)  # digest of the data the comments were written from
    written_at = Column(BigInteger, nullable=True)
    note = Column(Text, nullable=True)  # why it waits / what happened, for the monitor
    result = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('schedule_id', 'run_at', name='uq_report_job_schedule_run'),
        Index('ix_report_job_status', 'status', 'run_at'),
    )


class ScheduleModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    enabled: bool
    config: dict = {}
    next_run_at: Optional[int] = None
    last_run_at: Optional[int] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    last_digest: Optional[str] = None
    created_at: int
    updated_at: int


class LinkModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    code: Optional[str] = None
    chat_id: Optional[str] = None
    chat_title: Optional[str] = None
    chat_type: Optional[str] = None
    created_at: int
    linked_at: Optional[int] = None


class JobModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schedule_id: str
    run_at: int
    status: str
    lease_id: Optional[str] = None
    lease_until: Optional[int] = None
    retry_at: Optional[int] = None
    attempts: int = 0
    comment: Optional[str] = None
    comment_digest: Optional[str] = None
    written_at: Optional[int] = None
    note: Optional[str] = None
    result: Optional[dict] = None
    created_at: int
    updated_at: int


def _model(row) -> ScheduleModel:
    model = ScheduleModel.model_validate(row)
    model.config = row.config or {}
    return model


class ScheduleTable:
    async def list(self, user_id: Optional[str] = None) -> list[ScheduleModel]:
        async with get_async_db_context() as db:
            query = select(ReportSchedule).order_by(ReportSchedule.created_at)
            if user_id:
                query = query.where(ReportSchedule.user_id == user_id)
            return [_model(r) for r in (await db.execute(query)).scalars().all()]

    async def get(self, schedule_id: str) -> Optional[ScheduleModel]:
        async with get_async_db_context() as db:
            row = await db.get(ReportSchedule, schedule_id)
            return _model(row) if row else None

    async def count(self, user_id: str) -> int:
        return len(await self.list(user_id))

    async def create(self, user_id: str, name: str, enabled: bool, config: dict, next_run_at: Optional[int]) -> ScheduleModel:
        now = now_ns()
        row = ReportSchedule(
            id=str(uuid.uuid4()), user_id=user_id, name=name, enabled=enabled, config=config,
            next_run_at=next_run_at, created_at=now, updated_at=now,
        )
        async with get_async_db_context() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return _model(row)

    async def update(self, schedule_id: str, **fields) -> Optional[ScheduleModel]:
        async with get_async_db_context() as db:
            row = await db.get(ReportSchedule, schedule_id)
            if not row:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = now_ns()
            await db.commit()
            await db.refresh(row)
            return _model(row)

    async def delete(self, schedule_id: str) -> bool:
        async with get_async_db_context() as db:
            result = await db.execute(delete(ReportSchedule).where(ReportSchedule.id == schedule_id))
            await db.commit()
            return bool(result.rowcount)

    async def due_for_queue(self, until: int) -> list[ScheduleModel]:
        """Enabled schedules sending by `until` that have no job yet for that send time."""
        async with get_async_db_context() as db:
            rows = (
                await db.execute(
                    select(ReportSchedule)
                    .outerjoin(
                        ReportJob,
                        and_(ReportJob.schedule_id == ReportSchedule.id, ReportJob.run_at == ReportSchedule.next_run_at),
                    )
                    .where(
                        ReportSchedule.enabled.is_(True),
                        ReportSchedule.next_run_at.is_not(None),
                        ReportSchedule.next_run_at <= until,
                        ReportJob.id.is_(None),
                    )
                    .order_by(ReportSchedule.next_run_at)
                    .limit(200)
                )
            ).scalars().all()
            return [_model(r) for r in rows]


class LinkTable:
    async def list(self, user_id: str, linked_only: bool = True) -> list[LinkModel]:
        async with get_async_db_context() as db:
            query = select(ReportTelegramLink).where(ReportTelegramLink.user_id == user_id)
            if linked_only:
                query = query.where(ReportTelegramLink.chat_id.is_not(None))
            rows = (await db.execute(query.order_by(ReportTelegramLink.created_at))).scalars().all()
            return [LinkModel.model_validate(r) for r in rows]

    async def by_ids(self, ids: list[str]) -> list[LinkModel]:
        if not ids:
            return []
        async with get_async_db_context() as db:
            rows = (await db.execute(select(ReportTelegramLink).where(ReportTelegramLink.id.in_(ids)))).scalars().all()
            return [LinkModel.model_validate(r) for r in rows]

    async def new_code(self, user_id: str) -> LinkModel:
        """A one-time code the user sends to the bot (privately or in a group) to link that chat."""
        async with get_async_db_context() as db:
            # Codes not used within a day are dropped.
            await db.execute(
                delete(ReportTelegramLink).where(
                    ReportTelegramLink.chat_id.is_(None), ReportTelegramLink.created_at < now_ns() - 86_400 * 10**9
                )
            )
            row = ReportTelegramLink(
                # Whoever opens the link receives the reports: long enough that nobody can guess one.
                id=str(uuid.uuid4()), user_id=user_id, code=secrets.token_hex(12), created_at=now_ns()
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return LinkModel.model_validate(row)

    async def confirm(self, code: str, chat_id: str, title: str, chat_type: str) -> tuple[str, Optional[LinkModel]]:
        """Link the chat that sent `code`: ('linked', link), ('already', link) when this chat opens a link it
        already used, or ('invalid', None). One link is for one chat: when two people open the same link at the
        same moment, only the first one gets it (the update only applies while the link is still free)."""
        if not code or len(code) > 64:
            return 'invalid', None
        async with get_async_db_context() as db:
            rows = (await db.execute(select(ReportTelegramLink).where(ReportTelegramLink.code == code))).scalars().all()
            used = next((r for r in rows if r.chat_id == chat_id), None)
            if used:
                return 'already', LinkModel.model_validate(used)
            row = next((r for r in rows if r.chat_id is None and r.created_at >= now_ns() - 86_400 * 10**9), None)
            if not row:
                return 'invalid', None
            result = await db.execute(
                update(ReportTelegramLink)
                .where(ReportTelegramLink.id == row.id, ReportTelegramLink.chat_id.is_(None))
                .values(chat_id=chat_id, chat_title=title, chat_type=chat_type, linked_at=now_ns())
            )
            if result.rowcount != 1:
                await db.rollback()
                return 'invalid', None
            # The same chat linked again by the same user replaces the earlier link.
            await db.execute(
                delete(ReportTelegramLink).where(
                    and_(
                        ReportTelegramLink.user_id == row.user_id,
                        ReportTelegramLink.chat_id == chat_id,
                        ReportTelegramLink.id != row.id,
                    )
                )
            )
            await db.commit()
            # The code stays on the link so that opening the same link again is recognised ('already').
            return 'linked', LinkModel(
                id=row.id, user_id=row.user_id, code=code, chat_id=chat_id, chat_title=title, chat_type=chat_type,
                created_at=row.created_at, linked_at=now_ns(),
            )
    async def get_code(self, link_id: str, user_id: str) -> Optional[LinkModel]:
        async with get_async_db_context() as db:
            row = await db.get(ReportTelegramLink, link_id)
            return LinkModel.model_validate(row) if row and row.user_id == user_id else None

    async def delete(self, link_id: str, user_id: str) -> bool:
        async with get_async_db_context() as db:
            result = await db.execute(
                delete(ReportTelegramLink).where(ReportTelegramLink.id == link_id, ReportTelegramLink.user_id == user_id)
            )
            await db.commit()
            return bool(result.rowcount)

    async def forget_chat(self, chat_id: str) -> int:
        """The bot was removed from a chat (or blocked), or the person there stopped receiving: links to it are
        removed. Returns how many."""
        async with get_async_db_context() as db:
            result = await db.execute(delete(ReportTelegramLink).where(ReportTelegramLink.chat_id == chat_id))
            await db.commit()
            return result.rowcount or 0


OPEN_JOBS = ('queued', 'writing', 'ready')


class JobTable:
    async def enqueue(self, schedule_id: str, run_at: int) -> bool:
        """Queue one send; False when another worker queued it first."""
        now = now_ns()
        async with get_async_db_context() as db:
            db.add(ReportJob(id=str(uuid.uuid4()), schedule_id=schedule_id, run_at=run_at, status='queued',
                             attempts=0, created_at=now, updated_at=now))
            try:
                await db.commit()
                return True
            except IntegrityError:
                await db.rollback()
                return False

    async def _claim(self, condition, now: int, lease_seconds: int, status: str) -> Optional[JobModel]:
        """Take the job sending soonest that matches `condition` (atomic: one worker only)."""
        async with get_async_db_context() as db:
            ids = (
                await db.execute(select(ReportJob.id).where(condition).order_by(ReportJob.run_at).limit(5))
            ).scalars().all()
            for job_id in ids:
                lease_id = uuid.uuid4().hex
                result = await db.execute(
                    update(ReportJob)
                    .where(ReportJob.id == job_id, condition)
                    .values(status=status, lease_id=lease_id, lease_until=now + lease_seconds * 10**9, updated_at=now)
                )
                await db.commit()
                if result.rowcount:
                    row = await db.get(ReportJob, job_id)
                    await db.refresh(row)
                    return JobModel.model_validate(row)
        return None

    async def claim_write(self, now: int, lease_seconds: int) -> Optional[JobModel]:
        """The queued job sending soonest (or one whose writer stopped)."""
        condition = or_(
            and_(ReportJob.status == 'queued', or_(ReportJob.retry_at.is_(None), ReportJob.retry_at <= now)),
            and_(ReportJob.status == 'writing', ReportJob.lease_until < now),
        )
        return await self._claim(condition, now, lease_seconds, 'writing')

    async def claim_send(self, now: int, late_ns: int, lease_seconds: int) -> Optional[JobModel]:
        """A job whose time has come: written, or not written and already late (then sent without comments).

        Late counts from the send time, or from the queueing for a send missed while the app was down."""
        late = and_(ReportJob.run_at <= now - late_ns, ReportJob.created_at <= now - late_ns)
        condition = and_(
            ReportJob.run_at <= now,
            or_(
                ReportJob.status == 'ready',
                and_(ReportJob.status == 'queued', late),
                and_(ReportJob.status == 'writing', ReportJob.lease_until < now, late),
            ),
        )
        return await self._claim(condition, now, lease_seconds, 'sending')

    async def update(self, job_id: str, held_by: Optional[str] = None, **fields) -> bool:
        """Change a job; with held_by (a lease id) only while that lease still holds it."""
        async with get_async_db_context() as db:
            query = update(ReportJob).where(ReportJob.id == job_id)
            if held_by:
                query = query.where(ReportJob.lease_id == held_by)
            result = await db.execute(query.values(updated_at=now_ns(), **fields))
            await db.commit()
            return bool(result.rowcount)

    async def note_waiting(self, note: str) -> None:
        """Why the queued jobs wait (shown in the monitor)."""
        async with get_async_db_context() as db:
            await db.execute(
                update(ReportJob)
                .where(ReportJob.status == 'queued', or_(ReportJob.note.is_(None), ReportJob.note != note))
                .values(note=note)
            )
            await db.commit()

    async def cancel(self, schedule_id: str) -> int:
        """Drop the jobs not sent yet of a schedule that was edited, paused or deleted; the next round queues the
        new send time (a job being written finds its row gone and stops)."""
        async with get_async_db_context() as db:
            result = await db.execute(
                delete(ReportJob).where(ReportJob.schedule_id == schedule_id, ReportJob.status.in_(OPEN_JOBS))
            )
            await db.commit()
            return result.rowcount or 0

    async def has_writable(self, now: int) -> bool:
        async with get_async_db_context() as db:
            row = (
                await db.execute(
                    select(ReportJob.id)
                    .where(
                        or_(
                            and_(ReportJob.status == 'queued', or_(ReportJob.retry_at.is_(None), ReportJob.retry_at <= now)),
                            and_(ReportJob.status == 'writing', ReportJob.lease_until < now),
                        )
                    )
                    .limit(1)
                )
            ).first()
            return row is not None

    async def stalled_sends(self, now: int) -> list[JobModel]:
        """Sends whose worker stopped in the middle (lease over)."""
        async with get_async_db_context() as db:
            rows = (
                await db.execute(select(ReportJob).where(ReportJob.status == 'sending', ReportJob.lease_until < now))
            ).scalars().all()
            return [JobModel.model_validate(r) for r in rows]

    async def list(self, limit: int = 200) -> list[JobModel]:
        """Open jobs first (soonest first), then the latest finished ones."""
        async with get_async_db_context() as db:
            open_rows = (
                await db.execute(
                    select(ReportJob).where(ReportJob.status.in_((*OPEN_JOBS, 'sending'))).order_by(ReportJob.run_at)
                )
            ).scalars().all()
            done_rows = (
                await db.execute(
                    select(ReportJob)
                    .where(ReportJob.status.not_in((*OPEN_JOBS, 'sending')))
                    .order_by(ReportJob.updated_at.desc())
                    .limit(max(limit - len(open_rows), 20))
                )
            ).scalars().all()
            return [JobModel.model_validate(r) for r in [*open_rows, *done_rows]]

    async def open_by_schedule(self, schedule_ids: list[str]) -> dict[str, JobModel]:
        """The open job of each schedule, if any."""
        if not schedule_ids:
            return {}
        async with get_async_db_context() as db:
            rows = (
                await db.execute(
                    select(ReportJob)
                    .where(ReportJob.schedule_id.in_(schedule_ids), ReportJob.status.in_((*OPEN_JOBS, 'sending')))
                    .order_by(ReportJob.run_at)
                )
            ).scalars().all()
            out: dict[str, JobModel] = {}
            for row in rows:
                out.setdefault(row.schedule_id, JobModel.model_validate(row))
            return out

    async def prune(self, before: int) -> None:
        """Finished jobs older than `before` are dropped."""
        async with get_async_db_context() as db:
            await db.execute(
                delete(ReportJob).where(ReportJob.status.not_in((*OPEN_JOBS, 'sending')), ReportJob.updated_at < before)
            )
            await db.commit()


class LockTable:
    async def acquire(self, name: str, lease_seconds: int, lease_id: Optional[str] = None) -> Optional[dict]:
        """Take (or renew, with the same lease_id) a named lease; returns its data, or None when someone holds it."""
        now = now_ns()
        lease_id = lease_id or uuid.uuid4().hex
        async with get_async_db_context() as db:
            row = await db.get(ReportLock, name)
            if row is None:
                db.add(ReportLock(name=name, lease_id=lease_id, lease_until=now + lease_seconds * 10**9, data={}))
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
                    return None
                return {'lease_id': lease_id, 'data': {}}
            result = await db.execute(
                update(ReportLock)
                .where(
                    ReportLock.name == name,
                    or_(ReportLock.lease_until.is_(None), ReportLock.lease_until < now, ReportLock.lease_id == lease_id),
                )
                .values(lease_id=lease_id, lease_until=now + lease_seconds * 10**9)
            )
            await db.commit()
            if not result.rowcount:
                return None
            row = await db.get(ReportLock, name)
            await db.refresh(row)
            return {'lease_id': lease_id, 'data': dict(row.data or {})}

    async def release(self, name: str, lease_id: str) -> None:
        async with get_async_db_context() as db:
            await db.execute(
                update(ReportLock).where(ReportLock.name == name, ReportLock.lease_id == lease_id).values(lease_until=None)
            )
            await db.commit()

    async def save(self, name: str, lease_id: str, data: dict) -> bool:
        """Save the lease's data; False when this lease no longer holds the lock (nothing saved)."""
        async with get_async_db_context() as db:
            result = await db.execute(
                update(ReportLock).where(ReportLock.name == name, ReportLock.lease_id == lease_id).values(data=data)
            )
            await db.commit()
            return bool(result.rowcount)


Schedules = ScheduleTable()
Jobs = JobTable()
TelegramLinks = LinkTable()
Locks = LockTable()
